"""Durable, single-owner WeChat UI jobs. Legacy UI API stays read-only."""
import argparse,base64,fcntl,hashlib,json,os,re,shutil,signal,sqlite3,subprocess,sys,time,uuid
from contextlib import closing
from pathlib import Path
from PIL import Image
from session_selector import HOME,CONTAINER,Policy,Desktop,OCR,Navigator,NavigationError
import navigation_guard
from text_input import Clipboard,Composer
from mention_input import AvatarMentionInput

DB=HOME/'send-jobs.sqlite3'
ASSETS=Path('/opt/wechat-webox/state/ui-outbox')
ROOTS=[Path('/opt/astrbot-mouth/images'),Path('/opt/astrbot-mouth/incoming-images'),Path('/opt/astrbot-mouth/incoming-videos'),HOME/'test-assets',Path('/opt/astrbot-mouth/wechat-outgoing'),Path('/opt/astrbot-mouth/outgoing-voice')]

class Uncertain(RuntimeError):pass

def connect():
    db=sqlite3.connect(DB,timeout=20);db.row_factory=sqlite3.Row
    db.executescript('''PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,hash TEXT,payload TEXT,status TEXT,phase TEXT,cancel INTEGER DEFAULT 0,created REAL,updated REAL,result TEXT,error TEXT);
    CREATE TABLE IF NOT EXISTS messages(job TEXT,ordinal INTEGER,state TEXT,baseline TEXT,receipt TEXT,error TEXT,PRIMARY KEY(job,ordinal));''')
    db.execute('CREATE TABLE IF NOT EXISTS audits(job TEXT,ordinal INTEGER,PRIMARY KEY(job,ordinal))')
    navigation_guard.initialize(db)
    return db

def canonical(request):
    if not isinstance(request,dict) or set(request)-{'id','conversation','chat_type','messages','roomid','record_memory'}:raise ValueError('Invalid request fields')
    if not isinstance(request.get('conversation'),str) or not request['conversation'].strip():raise ValueError('Conversation name required')
    if request.get('chat_type') not in ('private','group'):raise ValueError('Invalid conversation type')
    messages=request.get('messages')
    if not isinstance(messages,list) or not 1<=len(messages)<=20:raise ValueError('One to twenty messages required')
    normalized=[]
    for message in messages:
        if not isinstance(message,dict):raise ValueError('Invalid message')
        if set(message)=={'voice','text'}:
            path=checked_media(message['voice'],'voice')
            if not isinstance(message['text'],str) or len(message['text'])>1000:raise ValueError('Invalid voice text')
            normalized.append({'type':'voice','path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'text':message['text']});continue
        if set(message)=={'image'} or set(message)=={'video'}:
            kind=next(iter(message));path=checked_media(message[kind],kind)
            normalized.append({'type':kind,'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()});continue
        if set(message)-{'text','mentions','parts'}:raise ValueError('Unsupported message fields')
        if 'parts' in message:
            if set(message)!={'parts'}:raise ValueError('Use either parts or text/mentions')
            parts=message['parts']
        else:
            if not isinstance(message.get('mentions',[]),list) or any(not isinstance(x,str) for x in message.get('mentions',[])):raise ValueError('Mention names must be a list')
            parts=[{'type':'mention','name':x} for x in message.get('mentions',[])]+([{'type':'text','text':message['text']}] if message.get('text') else [])
        if not isinstance(parts,list) or not 1<=len(parts)<=40:raise ValueError('Message parts required')
        size=0
        for part in parts:
            if not isinstance(part,dict):raise ValueError('Invalid part')
            kind=part.get('type');key='name' if kind=='mention' else 'text'
            if kind not in ('text','mention') or set(part)!={'type',key} or not isinstance(part[key],str) or not part[key] or any(ord(c)<32 and c not in '\n\t' for c in part[key]):raise ValueError('Invalid text/mention part')
            if kind=='mention' and request['chat_type']!='group':raise ValueError('@ requires a group')
            size+=len(part[key])
        if size>5000:raise ValueError('Text exceeds 5000 characters')
        normalized.append({'type':'text','parts':parts})
    if 'roomid' in request and (not isinstance(request['roomid'],str) or not request['roomid']):raise ValueError('Invalid room identity')
    if type(request.get('record_memory',True)) is not bool:raise ValueError('Invalid memory flag')
    return {'conversation':request['conversation'].strip(),'chat_type':request['chat_type'],'messages':normalized, 'roomid':request.get('roomid'), 'record_memory':request.get('record_memory',True)}

def checked_media(value,kind):
    if not isinstance(value,str):raise ValueError('Media path required')
    p=Path(value).resolve()
    if not any(p.is_relative_to(root.resolve()) for root in ROOTS) or not p.is_file():raise ValueError('Media outside allowed folders')
    limit=4*1024*1024 if kind=='image' else 64*1024*1024
    if not 0<p.stat().st_size<=limit:raise ValueError('Media size limit exceeded')
    if kind=='image':
        with Image.open(p) as im:
            if getattr(im,'n_frames',1)!=1 or im.width*im.height>24000000:raise ValueError('Animated/oversized image not supported')
            im.verify()
    elif kind=='voice':
        if p.parent!=Path('/opt/astrbot-mouth/outgoing-voice') or p.suffix!='.mp3' or p.stat().st_size>8*1024*1024:raise ValueError('Invalid voice file')
    else:
        with p.open('rb') as f:head=f.read(32)
        if p.suffix.lower() not in ('.mp4','.mov','.m4v') or head[4:8]!=b'ftyp':raise ValueError('Expected an MP4/MOV video')
    return p

def submit(request):
    value=canonical(request);encoded=json.dumps(value,sort_keys=True,ensure_ascii=False);sha=hashlib.sha256(encoded.encode()).hexdigest();key=request.get('id') or uuid.uuid4().hex
    if not isinstance(key,str) or not re.fullmatch(r'[a-zA-Z0-9:_-]{1,160}',key):raise ValueError('Invalid job id')
    with closing(connect()) as db,db:
        row=db.execute('SELECT hash FROM jobs WHERE id=?',(key,)).fetchone()
        if row and row['hash']!=sha:raise ValueError('Job id already has a different request')
        db.execute("INSERT OR IGNORE INTO jobs(id,hash,payload,status,phase,created,updated) VALUES(?,?,?,'queued','queued',?,?)",(key,sha,encoded,time.time(),time.time()))
        if not navigation_guard.blocked(db,value):
            db.execute("UPDATE jobs SET status='queued',phase='queued',error=NULL,updated=? WHERE id=? AND status='retryable'",(time.time(),key))
    return key

def text_matches(parts,seen):
    pattern=''
    for part in parts:
        if part['type']=='text':pattern+=re.escape(part['text'])
        else:pattern+=r'[\s\u2005]*@'+re.escape(part['name'])+r'[\s\u2005]*'
    return re.fullmatch(pattern,seen) is not None

def receipt_text_matches(parts,seen):
    """WeChat strips trailing layout whitespace on send; drafts stay exact."""
    if text_matches(parts,seen):return True
    normalized=[dict(part) for part in parts]
    if normalized and normalized[-1]['type']=='text':
        normalized[-1]['text']=normalized[-1]['text'].rstrip(' \t\r\n')
    return text_matches(normalized,seen.rstrip(' \t\r\n'))


def audit_confirmed(db):
    """Every sending entry point records confirmed output in shared memory."""
    rows=db.execute("SELECT m.job,m.ordinal,m.receipt,j.payload,j.updated FROM messages m JOIN jobs j ON j.id=m.job WHERE m.state='sent' AND NOT EXISTS(SELECT 1 FROM audits a WHERE a.job=m.job AND a.ordinal=m.ordinal) LIMIT 50").fetchall()
    if not rows:return
    sys.path.insert(0,'/opt/astrbot-mouth')
    from output_memory import initialize,record_output
    from wechat_client import config
    folder=Path('/opt/astrbot-mouth/output-memory');folder.mkdir(parents=True,exist_ok=True)
    for row in rows:
        payload=json.loads(row['payload']);message=payload['messages'][row['ordinal']];receipt=json.loads(row['receipt'])
        if payload.get('record_memory') is False and db.execute('SELECT status FROM jobs WHERE id=?',(row['job'],)).fetchone()[0]=='sent':
            with db:db.execute('INSERT OR IGNORE INTO audits VALUES(?,?)',(row['job'],row['ordinal']))
            continue
        stamp=receipt.get('confirmed_at')
        if stamp is None:
            path=HOME/'runs'/('send-'+row['job'])/'trace.jsonl'
            if path.exists():
                for line in path.read_text().splitlines():
                    event=json.loads(line)
                    if event.get('event')=='sent' and event.get('ordinal')==row['ordinal']:stamp=event['time'];break
        stamp=stamp or row['updated']
        # The resolved real destination is durable in the job's success result
        # or re-resolved by a read-only database call after a partial send.
        result=db.execute('SELECT result FROM jobs WHERE id=?',(row['job'],)).fetchone()[0]
        room=receipt.get('roomid') or (json.loads(result).get('roomid') if result else None)
        if not room:room=Observer().baseline(payload['conversation'])['contact']['roomid']
        metadata={'platform_receipt':receipt,'delivery_mode':'wechat_ui_normal','ui_job':row['job']}
        if message['type']=='video':metadata['video_paths']=[message['path']]
        if message['type']=='voice':metadata.update(voice_paths=[message['path']],voice_text=message['text'])
        with closing(sqlite3.connect(folder/'output.sqlite3',timeout=20)) as out,out:
            initialize(out)
            record_output(out,event_key='wechat-ui:'+row['job']+':'+str(row['ordinal'])+':sent',timestamp=stamp,platform='wechat',account=config()['account_id'],chat_type=payload['chat_type'],conversation=room,phase='sent',text=message['text'] if message['type']=='voice' else receipt.get('Value','') if message['type']=='text' else '',images=[message['path']] if message['type']=='image' else [],receipt=receipt.get('MessageID'),delivery_metadata=metadata)
        for file in folder.glob('output.sqlite3*'):os.chown(file,990,990)
        with db:db.execute('INSERT OR IGNORE INTO audits VALUES(?,?)',(row['job'],row['ordinal']))

class Observer:
    def __init__(self):
        sys.path.insert(0,'/opt/astrbot-mouth')
        from wechat_client import Client
        self.api=Client().backend.api
    def ready(self):
        if self.api('/ilink/bot/automation').get('read_only') is not True:raise NavigationError('Legacy UI API must be read-only')
    def baseline(self,name):return self.api('/ilink/bot/receipt-probe',{'name':name})
    def items(self,room,positions):return self.api('/ilink/bot/receipt-probe',{'roomid':room,'positions':positions}).get('items') or []

class MediaClipboard(Clipboard):
    def set_bytes(self,data,mime):
        if mime not in ('image/png','text/uri-list'):raise ValueError('Invalid clipboard MIME')
        self.close();self.process=subprocess.Popen(['docker','exec','-i','-e','DISPLAY=:1',CONTAINER,'xclip','-selection','clipboard','-target',mime,'-loops','0','-quiet'],stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        self.process.stdin.write(data);self.process.stdin.close();time.sleep(0.15)

class Executor:
    def __init__(self):
        self.policy=Policy(**json.loads((HOME/'policy.json').read_text()));self.ocr=OCR();self.ui=Desktop(self.policy);self.clip=MediaClipboard();self.observer=Observer();self.cached=None
    def close(self):self.clip.close();self.ocr.close()
    def update(self,db,key,phase):
        with db:db.execute('UPDATE jobs SET phase=?,updated=? WHERE id=?',(phase,time.time(),key))
    def cancelled(self,db,key):return bool(getattr(self,'stop_requested',lambda:False)() or db.execute('SELECT cancel FROM jobs WHERE id=?',(key,)).fetchone()[0])
    def render(self,message,nav,composer,group):
        _,g=self.ui.frame();composer.focus(g)
        if composer.read():raise NavigationError('Existing draft preserved; refusing to overwrite/send it')
        if message['type']=='text':
            mention=AvatarMentionInput(self.ui,self.ocr,nav,composer,json.loads((HOME/'mention-offsets.json').read_text()),group)
            for part in message['parts']:
                if part['type']=='mention':
                    result=mention.add(part['name'])
                    if not result['success']:raise NavigationError('Mention failed after bounded attempts: '+json.dumps(result,ensure_ascii=False))
                else:
                    composer.focus(g);self.clip.set(part['text']);composer.keys('ctrl+v')
            actual=composer.read()
            if not text_matches(message['parts'],actual):raise NavigationError('Draft does not match requested text/@ sequence')
        else:
            path=checked_media(message['path'],message['type'])
            if hashlib.sha256(path.read_bytes()).hexdigest()!=message['sha256']:raise NavigationError('Media changed after submission')
            if message['type']=='voice':return g
            if message['type']=='image':
                import io
                buf=io.BytesIO()
                with Image.open(path) as im:im.convert('RGB').save(buf,'PNG')
                self.clip.set_bytes(buf.getvalue(),'image/png')
            else:
                ASSETS.mkdir(parents=True,exist_ok=True);ASSETS.chmod(0o711)
                destination=ASSETS/(message['sha256']+path.suffix.lower())
                if not destination.exists():shutil.copyfile(path,destination);destination.chmod(0o644)
                uri='file:///webox/state/ui-outbox/'+destination.name+'\r\n'
                self.clip.set_bytes(uri.encode(),'text/uri-list')
            composer.keys('ctrl+v');time.sleep(0.5)
        return g
    def match(self,message,items):
        for item in items:
            kind=item.get('Kind');value=item.get('Value','')
            if message['type']=='text' and kind=='text' and receipt_text_matches(message['parts'],value):return item
            if message['type']=='voice' and kind=='voice':return item
            if message['type']=='image' and kind=='image':return item
            if message['type']=='video' and kind=='video':return item
            if message['type']=='video' and kind=='file' and value==message['sha256']+Path(message['path']).suffix.lower():return {**item,'presentation':'video_file_attachment'}
        return None
    def execute(self,db,key,payload):
        folder=HOME/'runs'/('send-'+key);folder.mkdir(parents=True,exist_ok=True)
        with (folder/'trace.jsonl').open('a') as trace:
            def log(event):trace.write(json.dumps({**event,'time':time.time()},ensure_ascii=False)+'\n');trace.flush()
            nav=Navigator(self.ui,self.ocr,self.policy,log);composer=Composer(self.ui,nav,self.clip);name=payload['conversation'];kind=payload['chat_type'];sent=[]
            self.observer.ready();resolved=self.observer.baseline(name);room=resolved['contact']['roomid']
            if payload.get('roomid') and payload['roomid']!=room:raise NavigationError('Recipient name no longer matches requested identity')
            if room.endswith('@chatroom')!=(kind=='group'):raise NavigationError('Resolved recipient type differs')
            self.update(db,key,'navigating')
            if self.cached!=room or not nav.verify(name,kind):
                self.cached=None;nav.select(name,kind);self.cached=room
            else:log({'event':'reuse_verified_conversation','room':room})
            # No upstream timeout/cancel is consulted until navigation/return-top completes.
            if self.cancelled(db,key):return {'cancelled':True,'receipts':sent}
            for index,message in enumerate(payload['messages']):
                if self.cancelled(db,key):return {'cancelled':True,'receipts':sent}
                self.update(db,key,'preparing:'+str(index));baseline=self.observer.baseline(name)['positions']
                with db:db.execute("INSERT INTO messages VALUES(?,?,'preparing',?,NULL,NULL)",(key,index,json.dumps(baseline)))
                try:
                    g=self.render(message,nav,composer,name)
                    self.ui.frame()[0].save(folder/(str(index)+'-draft.png'))
                    receipt=self.match(message,self.observer.items(room,baseline))
                    if not receipt:
                        if self.cancelled(db,key):return {'cancelled':True,'receipts':sent,'draft_retained':True}
                        if not nav.verify(name,kind):raise NavigationError('Wrong conversation before send')
                        if self.ui.geometry()!=g:raise NavigationError('Window changed before send')
                        with db:db.execute("UPDATE messages SET state='sending' WHERE job=? AND ordinal=?",(key,index))
                        self.update(db,key,'sending:'+str(index))
                        _,x,y,w,h=g
                        # Click the actual Send button, never press Return/Escape.
                        if message['type']=='voice':
                            from voice_recording import send as record_voice
                            record_voice(self.ui,nav,name,kind,g,message['path'])
                        else:self.ui.run(['xdotool','mousemove',str(x+w-45),str(y+h-31),'click','1'])
                        deadline=time.monotonic()+(60 if message['type']=='video' else 20)
                        while time.monotonic()<deadline:
                            receipt=self.match(message,self.observer.items(room,baseline))
                            if receipt:break
                            time.sleep(0.75)
                        if not receipt:raise Uncertain('No confirmed outgoing database receipt; automatic resend forbidden')
                    receipt={**receipt,'confirmed_at':time.time(),'roomid':room}
                    sent.append({'ordinal':index,'type':message['type'],'roomid':room,'receipt':receipt})
                    with db:db.execute("UPDATE messages SET state='sent',receipt=? WHERE job=? AND ordinal=?",(json.dumps(receipt,ensure_ascii=False),key,index))
                    log({'event':'sent','ordinal':index,'receipt':receipt})
                    self.ui.frame()[0].save(folder/(str(index)+'-sent.png'))
                except Exception as exc:
                    state=db.execute('SELECT state FROM messages WHERE job=? AND ordinal=?',(key,index)).fetchone()[0]
                    # A confirmed send is never relabelled unsent if a later screenshot fails.
                    if state!='sent':
                        with db:db.execute('UPDATE messages SET state=?,error=? WHERE job=? AND ordinal=?',('uncertain' if state=='sending' or isinstance(exc,Uncertain) else 'failed',str(exc)[:500],key,index))
                    raise
            return {'conversation':name,'roomid':room,'receipts':sent,'cancelled':False}

def _worker():
    stopping=[False]
    def stop(*args):stopping[0]=True
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    with closing(connect()) as db:
        with db:db.execute("UPDATE jobs SET status='interrupted',error='Worker restart: inspect draft/receipts before any resubmission' WHERE status='running'")
        executor=None
        try:
            while not stopping[0]:
                try:audit_confirmed(db)
                except Exception as exc:print('Confirmed memory audit pending: '+type(exc).__name__+': '+str(exc)[:300],flush=True)
                row=db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
                if not row:time.sleep(0.25);continue
                key=row['id']
                payload=json.loads(row['payload'])
                if navigation_guard.defer_blocked(db,key,payload):continue
                if row['cancel']:
                    with db:db.execute("UPDATE jobs SET status='cancelled',updated=? WHERE id=?",(time.time(),key))
                    continue
                with (HOME/'ui.lock').open('w') as lock:
                    fcntl.flock(lock,fcntl.LOCK_EX)
                    with db:claimed=db.execute("UPDATE jobs SET status='running',updated=? WHERE id=? AND status='queued'",(time.time(),key))
                    if claimed.rowcount!=1:continue
                    try:
                        if executor is None:executor=Executor()
                        executor.stop_requested=lambda:stopping[0]
                        result=executor.execute(db,key,json.loads(row['payload']))
                        with db:db.execute('UPDATE jobs SET status=?,phase=?,result=?,updated=? WHERE id=?',('cancelled' if result.get('cancelled') else 'sent','finished',json.dumps(result,ensure_ascii=False),time.time(),key))
                    except Exception as exc:
                        if executor:executor.cached=None
                        receipts=[dict(r) for r in db.execute("SELECT ordinal,state,receipt,error FROM messages WHERE job=? ORDER BY ordinal",(key,))]
                        status='uncertain' if any(r['state']=='uncertain' for r in receipts) else 'partial' if any(r['state']=='sent' for r in receipts) else 'failed'
                        navigation_failed=navigation_guard.failed_before_input(db,key,receipts)
                        if navigation_failed:status='paused_navigation'
                        with db:db.execute('UPDATE jobs SET status=?,result=?,error=?,updated=? WHERE id=?',(status,json.dumps({'messages':receipts},ensure_ascii=False),type(exc).__name__+': '+str(exc)[:800],time.time(),key))
                        if navigation_failed:
                            navigation_guard.pause(db,payload,exc)
                            navigation_guard.pause_followers(db,payload,exc)
                        print(json.dumps({'job':key,'error':type(exc).__name__+': '+str(exc)[:800]},ensure_ascii=False),flush=True)
        finally:
            if executor:executor.close()

def worker():
    with (HOME/'worker.lock').open('w') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('A WeChat worker already owns the queue')
        return _worker()

def main():
    os.umask(0o077);HOME.mkdir(parents=True,exist_ok=True)
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['worker','submit','status','cancel']);parser.add_argument('value',nargs='?');args=parser.parse_args()
    if args.action=='worker':return worker()
    if args.action=='submit':print(json.dumps({'job_id':submit(json.loads(Path(args.value).read_text('utf8')))}));return
    with closing(connect()) as db:
        if args.action=='cancel':
            with db:db.execute('UPDATE jobs SET cancel=1 WHERE id=?',(args.value,))
        row=db.execute('SELECT id,status,phase,error,result FROM jobs WHERE id=?',(args.value,)).fetchone()
        print(json.dumps(dict(row) if row else {'error':'Unknown job'},ensure_ascii=False))

if __name__=='__main__':main()
