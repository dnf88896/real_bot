"""Original QQ video bytes and durable, source-scoped paths; no video inference."""
import asyncio,base64,hashlib,json,logging,os,re,sqlite3,time,urllib.request
from contextlib import closing
from pathlib import Path
import incoming_images

ROOT=Path('/opt/astrbot-mouth')
INCOMING=ROOT/'incoming-videos'
MAX_BYTES=64*1024*1024


class Rejected(ValueError):definite_failure=True


def checked_video(value,folder=None):
    if not isinstance(value,str):raise Rejected('Video path must be text')
    p=Path(value).resolve();folder=Path(folder or INCOMING).resolve()
    if p.parent!=folder or not p.is_file() or p.suffix.lower() not in ('.mp4','.mov','.m4v','.webm','.mkv'):
        raise Rejected('Video is outside the allowed cache or missing')
    if not 0<p.stat().st_size<=MAX_BYTES:raise Rejected('Video exceeds 64 MiB or is empty')
    with p.open('rb') as f:head=f.read(32)
    if not (head[4:8]==b'ftyp' or head.startswith(b'\x1a\x45\xdf\xa3')):raise Rejected('Not an accepted video container')
    return p


def fetch(url,identity,folder=None):
    folder=Path(folder or INCOMING);folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    key=hashlib.sha256(identity.encode()).hexdigest()
    for suffix in ('.mp4','.webm'):
        existing=folder/(key+suffix)
        if existing.exists():return str(checked_video(str(existing),folder))
    incoming_images.validate_url(url)
    request=urllib.request.Request(url,headers={'User-Agent':'QQ-video-cache/1.0'})
    temporary=folder/(key+'.'+os.urandom(5).hex()+'.part');total=0;started=time.monotonic();head=b''
    try:
        with urllib.request.build_opener(incoming_images.Redirects()).open(request,timeout=10) as response,temporary.open('xb') as output:
            temporary.chmod(0o600)
            length=response.headers.get('Content-Length')
            if length and int(length)>MAX_BYTES:raise Rejected('Video exceeds 64 MiB')
            while chunk:=response.read(256*1024):
                total+=len(chunk)
                if total>MAX_BYTES or time.monotonic()-started>60:raise Rejected('Video size/time limit reached')
                if not head:head=chunk[:32]
                output.write(chunk)
        suffix='.webm' if head.startswith(b'\x1a\x45\xdf\xa3') else '.mp4'
        target=folder/(key+suffix);temporary.replace(target)
        try:return str(checked_video(str(target),folder))
        except Exception:target.unlink(missing_ok=True);raise
    finally:temporary.unlink(missing_ok=True)


class VideoEnricher:
    def __init__(self,inner,root=ROOT,downloader=None):
        self.inner=inner;self.root=Path(root);self.downloader=downloader or fetch;self.slots=asyncio.Semaphore(2)
    async def enrich(self,raw):
        prepared=await self.inner.enrich(raw)
        if prepared.get('post_type')!='message' or prepared.get('_qq_video') or not isinstance(prepared.get('message'),list):return prepared
        parts=prepared['message']
        if not any(isinstance(p,dict) and p.get('type')=='video' for p in parts):return prepared
        prepared=json.loads(json.dumps(prepared,ensure_ascii=False));status=[]
        for part in prepared['message']:
            if not isinstance(part,dict) or part.get('type')!='video':continue
            data=part.setdefault('data',{})
            try:
                if data.get('cached_path'):path=str(checked_video(data['cached_path'],self.root/'incoming-videos'))
                else:
                    if not isinstance(data.get('url'),str):raise Rejected('No downloadable QQ video URL')
                    identity=str(raw.get('self_id',''))+':'+str(data.get('file') or data['url'])
                    async with self.slots:path=await asyncio.to_thread(self.downloader,data['url'],identity,self.root/'incoming-videos')
                data.update(cached_path=path,cache_status='available',file_size=Path(path).stat().st_size)
                status.append({'status':'available','path':path})
            except Exception as exc:
                data['cache_status']='unavailable';status.append({'status':'unavailable','error':type(exc).__name__})
                logging.getLogger(__name__).warning('QQ video cache unavailable message_id=%s error=%s',raw.get('message_id'),type(exc).__name__)
        prepared['_qq_video']=status
        return prepared


class Videos:
    def __init__(self,root=ROOT):
        self.root=Path(root);self.path=self.root/'received-media.sqlite3'
        with closing(self.connect()) as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS videos(source TEXT,ordinal INTEGER,platform TEXT,account TEXT,kind TEXT,conversation TEXT,sender TEXT,nickname TEXT,time TEXT,path TEXT,status TEXT,PRIMARY KEY(source,ordinal))')
    def connect(self):return sqlite3.connect(self.path,timeout=20)
    def capture(self,records,download=False):
        if __package__:from .memory_engine import normalize
        else:from memory_engine import normalize
        with closing(self.connect()) as db,db:
            for record in records:
                raw=record['observation'];event=raw.get('raw_event',{});parts=event.get('message') or []
                if not isinstance(parts,list):continue
                item=normalize(raw,'incoming',str(record['id']))
                if not item:continue
                ordinal=0
                for part in parts:
                    if not isinstance(part,dict) or part.get('type')!='video':continue
                    index=ordinal;ordinal+=1;data=part.get('data') or {};path=None
                    old=db.execute('SELECT path,status FROM videos WHERE source=? AND ordinal=?',(item['key'],index)).fetchone()
                    if old and old[1]=='available':
                        try:checked_video(old[0],self.root/'incoming-videos');continue
                        except (ValueError,OSError):pass
                    try:
                        if data.get('cached_path'):path=str(checked_video(data['cached_path'],self.root/'incoming-videos'))
                        elif download and raw.get('platform','qq')=='qq' and isinstance(data.get('url'),str):
                            path=fetch(data['url'],str(raw['account_id'])+':'+str(data.get('file') or data['url']),self.root/'incoming-videos')
                    except Exception:pass
                    if not path and not download:continue
                    db.execute('INSERT OR REPLACE INTO videos VALUES(?,?,?,?,?,?,?,?,?,?,?)',(item['key'],index,raw.get('platform','qq'),item['account'],item['kind'],item['conversation'],str(raw.get('sender_id','')),str(event.get('sender',{}).get('nickname','')),raw.get('received_at',''),path,'available' if path else 'unavailable'))
    def offer(self,packet,visible_keys):
        if __package__:from .memory_engine import normalize
        else:from memory_engine import normalize
        scene=packet['scene']
        with closing(sqlite3.connect(self.root/'observations.sqlite3')) as db:
            rows=db.execute("SELECT id,raw_json FROM observations WHERE account_id=? AND chat_type=? AND conversation_id=? AND coalesce(json_extract(raw_json,'$.platform'),'qq')=? ORDER BY id DESC LIMIT 400",(scene['account'],scene['chat_type'],scene['conversation'],scene['platform'])).fetchall()
        self.capture([{'id':oid,'observation':json.loads(raw)} for oid,raw in rows])
        self.capture(packet['new_messages'],download=False)
        keys=set(visible_keys)
        keys.update(normalize(x['observation'],'incoming',str(x['id']))['key'] for x in packet['new_messages'] if not x['observation'].get('internal_event'))
        # Include visible global/long-term source records, even when their original
        # group never woke the mouth, plus videos in our own visible send history.
        own=[];memory=self.root/'memory/memory.sqlite3'
        if keys and memory.exists():
            with closing(sqlite3.connect(memory)) as db:
                source_rows=db.execute('SELECT direction,raw,message_id FROM events WHERE key IN ('+','.join('?' for _ in keys)+')',list(keys)).fetchall()
            records=[]
            for direction,encoded,mid in source_rows:
                raw=json.loads(encoded)
                if direction=='incoming':records.append({'id':mid,'observation':raw})
                else:
                    for path in raw.get('video_paths',[]):
                        try:checked_video(path,self.root/'incoming-videos')
                        except (ValueError,OSError):continue
                        own.append({'kind':'incoming_video','media_type':'video','path':path,'description':'自己此前发送过的原视频，可通过QQ或微信再次转发；未识别内容'})
            self.capture(records)
        result=[];seen=set()
        with closing(self.connect()) as db:rows=db.execute("SELECT source,path,sender,nickname,time FROM videos WHERE status='available' ORDER BY time,ordinal").fetchall()
        for source,path,sender,name,stamp in rows:
            if source not in keys or path in seen:continue
            try:checked_video(path,self.root/'incoming-videos')
            except (OSError,ValueError):continue
            seen.add(path);result.append({'kind':'incoming_video','media_type':'video','path':path,'description':'收到的原视频，可通过QQ或微信原样转发；未观看或识别内容；来源'+(name or sender)+'(用户ID:'+sender+') '+stamp})
        for item in own:
            if item['path'] not in seen:seen.add(item['path']);result.append(item)
        return result


def offered_path(value,options):
    choices=list(dict.fromkeys(str(x['path']) for x in options if x.get('media_type')=='video' and str(x['path']) in value))
    if len(choices)!=1:raise Rejected('Video span must contain exactly one offered video path')
    path=choices[0];i=value.index(path);before=value[:i].strip().lstrip('【[「(（"\'` ');after=value[i+len(path):].strip().strip('】]」)）"\'` ')
    before=re.sub(r'^(?:视频路径|视频|路径|video|file)\s*[:：]\s*','',before,flags=re.I)
    if before.strip() or after:raise Rejected('Video span contains non-resource text')
    return str(checked_video(path))


def component(value,options,destination):
    if not destination or destination.get('platform') not in ('qq','wechat') or destination.get('chat_type') not in ('private','group'):
        raise Rejected('Video files support enabled QQ/WeChat private/group only')
    return {'type':'video','data':{'file':offered_path(value,options)}}


def wire(message):
    result=[]
    for part in message:
        if part.get('type')!='video':result.append(part);continue
        path=checked_video(part['data']['file'])
        result.append({'type':'video','data':{'file':'base64://'+base64.b64encode(path.read_bytes()).decode('ascii')}})
    return result
