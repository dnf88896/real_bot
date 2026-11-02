"""Durable Heybox reply/@ ingress; polling and context assembly use no model."""
import asyncio,hashlib,html,json,logging,math,re,secrets,sqlite3,time
from contextlib import closing
from datetime import datetime,UTC
from pathlib import Path
import aiohttp
if __package__:
    from . import heybox_context as targets,heybox_refs as refs,wakefulness
    from .store import Store
    from .memory_engine import normalize
else:
    import heybox_context as targets,heybox_refs as refs,wakefulness
    from store import Store
    from memory_engine import normalize


def config(root):
    p=Path(root)/'heybox-account.json'
    return json.loads(p.read_text('utf8')) if p.exists() else {}


def enabled(root):
    c=config(root)
    return bool(c.get('enabled') and c.get('comments_enabled') and c.get('notifications_enabled'))


def connect(root):
    db=sqlite3.connect(Path(root)/'heybox-notifications.sqlite3',timeout=20);db.row_factory=sqlite3.Row
    db.executescript('''PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS feeds(account TEXT,feed TEXT,due REAL,offset INTEGER,failures INTEGER DEFAULT 0,error TEXT,PRIMARY KEY(account,feed));
    CREATE TABLE IF NOT EXISTS seen(account TEXT,feed TEXT,nid TEXT,PRIMARY KEY(account,feed,nid));
    CREATE TABLE IF NOT EXISTS notices(id TEXT PRIMARY KEY,account TEXT,pid TEXT,cid TEXT,raw TEXT,status TEXT,focus TEXT,scan TEXT DEFAULT '{}',attempts INTEGER DEFAULT 0,next_retry REAL DEFAULT 0,error TEXT,batch TEXT,received REAL,UNIQUE(account,pid,cid));
    CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY,account TEXT,pid TEXT,members TEXT,focus TEXT,status TEXT,emitted REAL DEFAULT 0,attempts INTEGER DEFAULT 0,error TEXT);
    CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);''')
    db.execute("INSERT OR IGNORE INTO meta VALUES('start_at',?)",(str(time.time()),));db.commit();return db


def text(value):
    return html.unescape(re.sub(r'<[^>]*>','',str(value or '')).replace('<br>','\n'))


def number(value):
    value=str(value or '')
    return value if re.fullmatch(r'[1-9][0-9]{0,24}',value) else ''


def parse_notice(raw,feed,account):
    kind=int(raw.get('message_type') or 0)
    if kind not in (1,2,16,17):return None
    pid=number(raw.get('linkid') or (raw.get('link') or {}).get('linkid'))
    cid=number(raw.get('comment_a_id'));actor=raw.get('user_a') or {}
    uid=number(raw.get('userid_a') or actor.get('userid'))
    if not pid or (not cid and kind!=16) or not uid or uid==str(account):return None
    recipient=number(raw.get('userid_b'))
    if recipient and recipient!=str(account):raise ValueError('Notification recipient mismatch')
    stamp=float(raw.get('timestamp') or 0)
    if stamp>1e12:stamp/=1000
    if not math.isfinite(stamp) or stamp<=0 or stamp>time.time()+300:raise ValueError('Invalid notification timestamp')
    return {'pid':pid,'cid':cid,'root':number(raw.get('root_comment_id')),
        'parent':number(raw.get('comment_b_id')),'uid':uid,'name':str(actor.get('username') or uid),
        'text':text(raw.get('comment_a_text') or raw.get('link_desc') or raw.get('text')),'timestamp':stamp,'kind':kind,
        'title':str(raw.get('link_title') or ''),'state':int(raw.get('state') or 0),
        'trigger':'有人@了你' if kind in (16,17) else '有人回复或评论了你'}


def notice_id(account,pid,cid):
    return 'heybox-notice:'+hashlib.sha256(json.dumps([str(account),pid,cid]).encode()).hexdigest()


def record_incoming(root,account,n):
    """One objective source comment; no model invocation and no temporary H评 ID."""
    store=Store(root);conversation=n['pid']+(':'+n['cid'] if n['cid'] else '');kind='comment' if n['cid'] else 'post';mid=n['cid'] or ('mention-'+n['pid'])
    key='astrbot:'+hashlib.sha256(json.dumps(['heybox',str(account),kind,conversation,mid]).encode()).hexdigest()
    raw={'platform':'heybox','account_id':str(account),'chat_type':kind,'conversation_id':conversation,
        'sender_id':n['uid'],'message_id':mid,'received_at':datetime.fromtimestamp(n['timestamp'],UTC).isoformat(),
        'observed_at':datetime.now(UTC).isoformat(),'post_title':n['title'],'post_url':refs.post_url(n['pid']),
        'raw_event':{'post_type':'message','message_type':kind,'self_id':str(account),'user_id':n['uid'],
            'message_id':mid,'time':n['timestamp'],'sender':{'user_id':n['uid'],'nickname':n['name']},
            'message':[{'type':'text','data':{'text':n['text']}}]},'mentions_self':n['kind'] in (16,17)}
    with closing(sqlite3.connect(store.archive)) as db,db:
        db.execute('INSERT OR IGNORE INTO observations(event_key,account_id,chat_type,conversation_id,sender_id,received_at,raw_json) VALUES(?,?,?,?,?,?,?)',
            (key,str(account),kind,conversation,n['uid'],raw['received_at'],json.dumps(raw,ensure_ascii=False)))
    return normalize(raw,'incoming',mid)['key']


def accept_page(root,account,feed,rows,offset,limit,now):
    if not isinstance(rows,list):raise ValueError('Notification messages must be a list')
    with closing(connect(root)) as db,db:
        start=float(db.execute("SELECT value FROM meta WHERE key='start_at'").fetchone()[0]);fresh=0;old=False
        for raw in rows:
            nid=number(raw.get('message_id'))
            if not nid:raise ValueError('Notification lacks stable message ID')
            changed=db.execute('INSERT OR IGNORE INTO seen VALUES(?,?,?)',(str(account),feed,nid)).rowcount
            fresh+=changed
            raw_stamp=float(raw.get('timestamp') or 0)
            if raw_stamp>1e12:raw_stamp/=1000
            old=old or (0<raw_stamp<start-86400)
            n=parse_notice(raw,feed,account)
            if not n:continue
            old=old or n['timestamp']<start-86400
            # At startup recover recent unread replies; later accept every newly observed notice.
            if not changed or n['timestamp']<start-86400 or (n['timestamp']<start and n['state']!=0):continue
            wid=notice_id(account,n['pid'],n['cid'])
            db.execute("INSERT OR IGNORE INTO notices(id,account,pid,cid,raw,status,received) VALUES(?,?,?,?,?,'pending',?)",
                (wid,str(account),n['pid'],n['cid'],json.dumps(n,ensure_ascii=False),now))
        if offset:
            next_offset=offset+max(1,len(rows)-5) if len(rows)==limit and not old else 0
            db.execute('UPDATE feeds SET offset=? WHERE account=? AND feed=?',(next_offset,str(account),feed))
        elif fresh and len(rows)==limit and not old:
            db.execute('UPDATE feeds SET offset=? WHERE account=? AND feed=?',(limit-5,str(account),feed))


def node(raw,root):
    user=raw.get('user') or {};cid=number(raw.get('commentid'));uid=number(user.get('userid'))
    if not cid or not uid:raise ValueError('Invalid comment identity')
    return {'评论ID':cid,'根评论ID':root,'父评论ID':number(raw.get('replyid')) or (root if cid!=root else ''),
        '作者':{'UID':uid,'名称':str(user.get('username') or uid)},'正文':text(raw.get('text')),
        '图片':[x['url'] for x in raw.get('imgs') or [] if isinstance(x,dict) and str(x.get('url','')).startswith('https://')],
        '时间':raw.get('create_at')}


def cache_chain(root,pid,detail,chain):
    def converted(n):return {'comment_id':n['评论ID'],'author':{'user_id':n['作者']['UID'],'name':n['作者']['名称']},'content':n['正文'],'images':[{'url':u} for u in n['图片']]}
    first=converted(chain[0]);first['child_comments']=[converted(n) for n in chain[1:]]
    targets.remember(root,pid,detail,[first])


def detail_info(n,link):
    if number(link.get('linkid'))!=n['pid']:raise ValueError('Notification post identity mismatch')
    u=link.get('user') or {};serialized=link.get('text') or '';body=''
    try:
        blocks=json.loads(serialized) if isinstance(serialized,str) and serialized.startswith('[') else None
        body='\n'.join(text(x.get('text')) for x in blocks if x.get('type')=='text') if blocks else text(link.get('description') or serialized)
    except (ValueError,TypeError):body=text(link.get('description'))
    return {'post_id':n['pid'],'title':str(link.get('title') or n['title']),'author':{'user_id':str(u.get('userid') or ''),'name':str(u.get('username') or '')},'content':body}


async def prepare_focus(root,row,fetch):
    n=json.loads(row['raw']);scan=json.loads(row['scan'] or '{}');page=scan.get('page',1);raw_root=scan.get('root');collected=scan.get('comments',{})
    link=scan.get('link') or {};cursor=scan.get('cursor','')
    if not n['cid']:
        result=(await fetch('/api/notifications/tree/'+n['pid'],{'page':1,'limit':20})).get('result') or {}
        detail=detail_info(n,result.get('link') or {})
        if detail['author']['user_id']!=n['uid']:raise ValueError('Post mention author mismatch')
        targets.remember(root,n['pid'],detail,[])
        return {'平台':'heybox','帖子信息':targets.brief(n['pid'],detail),'帖子正文':detail['content'][:12000],
            '对应回复链':[],'触发评论ID':'','触发者':{'UID':n['uid'],'名称':n['name']},'触发类型':'有人在帖子中@了你'}
    for _ in range(4):
        children_page=False
        if raw_root is None:
            result=(await fetch('/api/notifications/tree/'+n['pid'],{'page':page,'limit':20})).get('result') or {}
            link=result.get('link') or link
            if number(link.get('linkid'))!=n['pid']:raise ValueError('Notification post identity mismatch')
            for group in result.get('comments') or []:
                items=group.get('comment') or []
                if not items:continue
                rid=number(items[0].get('commentid'))
                if (n['root'] and rid==n['root']) or any(number(x.get('commentid'))==n['cid'] for x in items):
                    if n['root'] and n['root']!=rid:raise ValueError('Notification root mismatch')
                    raw_root=items[0];collected={number(x.get('commentid')):x for x in items};break
            if raw_root is None:
                if not result.get('has_more_floors'):raise LookupError('Trigger root unavailable or deleted')
                page+=1
        else:
            children_page=True
            result=(await fetch('/api/notifications/replies/'+n['pid']+'/'+number(raw_root['commentid']),{'cursor':cursor,'limit':20})).get('result') or {}
            children=result.get('comments') or []
            for child in children:collected[number(child.get('commentid'))]=child
            previous=cursor;cursor=str(result.get('lastval') or (children[-1].get('commentid') if children else '') or '')
            if result.get('has_more') and (not cursor or cursor==previous):raise ValueError('Comment cursor did not advance')
        if raw_root:
            rid=number(raw_root['commentid']);chain=[];current=n['cid'];visited=set()
            while current and current not in visited and current in collected:
                visited.add(current);item=node(collected[current],rid);chain.append(item)
                if current==rid:break
                current=item['父评论ID']
            if chain and chain[-1]['评论ID']==rid:
                chain.reverse();trigger=chain[-1]
                if trigger['作者']['UID']!=n['uid']:raise ValueError('Notification author mismatch')
                if n['parent'] and trigger['父评论ID']!=n['parent']:raise ValueError('Notification parent mismatch')
                detail=detail_info(n,link);body=detail['content']
                cache_chain(root,n['pid'],detail,chain)
                return {'平台':'heybox','帖子信息':targets.brief(n['pid'],detail),'帖子正文':body[:12000],
                    '对应回复链':chain,'触发评论ID':n['cid'],'触发者':{'UID':n['uid'],'名称':n['name']},'触发类型':n['trigger']}
            if children_page and not result.get('has_more'):raise LookupError('Trigger comment or parent unavailable')
    with closing(connect(root)) as db,db:
        db.execute('UPDATE notices SET scan=? WHERE id=?',(json.dumps({'page':page,'root':raw_root,'comments':collected,'link':link,'cursor':cursor},ensure_ascii=False),row['id']))
    return None


def is_notification(packet):return packet.get('internal_wake',{}).get('kind')=='heybox_notification'


def valid(root,wid):
    if not enabled(root):return False
    with closing(connect(root)) as db:
        r=db.execute('SELECT status,account FROM batches WHERE id=?',(wid,)).fetchone()
    return bool(r and r['status'] in ('queued','generated') and r['account']==str(config(root)['account_id']))


def finish(root,packet,committed=False,error=None):
    if not is_notification(packet):return
    with closing(connect(root)) as db,db:
        row=db.execute('SELECT attempts FROM batches WHERE id=?',(packet['internal_wake']['wake_id'],)).fetchone()
        attempts=(row['attempts'] if row else 0)+(0 if committed else 1)
        status='generated' if committed else ('failed' if attempts>=3 else 'queued')
        emitted=time.time()+(min(600,30*2**attempts) if not committed else 0)
        db.execute('UPDATE batches SET status=?,error=?,attempts=?,emitted=? WHERE id=?',(status,error,attempts,emitted,packet['internal_wake']['wake_id']))
        db.execute('UPDATE notices SET status=? WHERE batch=?',(status,packet['batch_id']))


def history(memory,packet):
    focus=packet['heybox_notification_focus'];account=packet['scene']['account'];pid=focus['帖子信息']['帖子ID']
    contexts=[{'role':'user','content':'[当前帖子资料] '+json.dumps({'基本信息':focus['帖子信息'],'正文':focus['帖子正文']},ensure_ascii=False)}];sources=[[]]
    for c in focus['对应回复链']:
        own=c['作者']['UID']==account;shown={**c,'评论目标编号':refs.number(pid,c['评论ID'])}
        contexts.append({'role':'assistant' if own else 'user','content':'[对应回复链] '+json.dumps(shown,ensure_ascii=False)})
        with closing(memory.connect()) as db:
            keys=[r[0] for r in db.execute("SELECT key FROM events WHERE platform='heybox' AND account=? AND kind='comment' AND message_id=?",(account,c['评论ID']))]
        sources.append(keys)
    return contexts,sources


class Notifications:
    def __init__(self,root,fetch=None):
        self.root=Path(root);self.fetch_override=fetch
        with closing(connect(root)):pass

    async def fetch(self,path,params=None):
        if self.fetch_override:return await self.fetch_override(path,params)
        async with aiohttp.ClientSession() as session:
            async with session.get('http://127.0.0.1:8010'+path,params=params,timeout=aiohttp.ClientTimeout(total=35)) as response:
                if response.status!=200:raise ValueError('Heybox notification read HTTP '+str(response.status))
                value=await response.json()
        if not value.get('ok'):raise ValueError('Invalid notification bridge response')
        return value['data']

    async def poll(self,now=None):
        if not enabled(self.root):return
        now=time.time() if now is None else now;account=str(config(self.root)['account_id'])
        for feed in ('reply','at16','at17'):
            with closing(connect(self.root)) as db,db:
                db.execute('INSERT OR IGNORE INTO feeds VALUES(?,?,0,0,0,NULL)',(account,feed))
                state=dict(db.execute('SELECT * FROM feeds WHERE account=? AND feed=?',(account,feed)).fetchone())
            if state['due']>now:continue
            try:
                # Always cover the newest head first. A full new head restarts
                # overlapping catch-up at 15, so a burst cannot create a gap
                # between a stale deep offset and the newly arrived messages.
                offsets=[0]
                for step in range(2):
                    offset=offsets[-1]
                    result=await self.fetch('/api/notifications',{'feed':feed,'offset':offset,'limit':20})
                    if str(result.get('account_id'))!=account:raise ValueError('Notification account mismatch')
                    messages=(result.get('result') or {}).get('messages')
                    accept_page(self.root,account,feed,messages,offset,20,now)
                    with closing(connect(self.root)) as db:
                        next_offset=db.execute('SELECT offset FROM feeds WHERE account=? AND feed=?',(account,feed)).fetchone()[0]
                    if not next_offset:break
                    offsets.append(next_offset)
                with closing(connect(self.root)) as db,db:db.execute('UPDATE feeds SET due=?,failures=0,error=NULL WHERE account=? AND feed=?',(now+60+secrets.randbelow(31),account,feed))
            except Exception as exc:
                with closing(connect(self.root)) as db,db:db.execute('UPDATE feeds SET due=?,failures=failures+1,error=? WHERE account=? AND feed=?',(now+min(1800,120*2**min(state['failures'],4)),str(exc)[:300],account,feed))
                logging.getLogger(__name__).warning('Heybox %s notification checkpoint retained: %s',feed,str(exc)[:200])
        with closing(connect(self.root)) as db:
            pending=db.execute("SELECT * FROM notices WHERE account=? AND status='pending' AND next_retry<=? ORDER BY received LIMIT 4",(account,now)).fetchall()
        for row in pending:
            n=json.loads(row['raw']);record_incoming(self.root,account,n)
            try:
                focus=await prepare_focus(self.root,row,self.fetch)
                if focus:
                    with closing(connect(self.root)) as db,db:db.execute("UPDATE notices SET status='ready',focus=?,scan='{}',error=NULL WHERE id=?",(json.dumps(focus,ensure_ascii=False),row['id']))
            except Exception as exc:
                with closing(connect(self.root)) as db,db:db.execute('UPDATE notices SET attempts=attempts+1,next_retry=?,error=?,status=? WHERE id=?',(now+min(1800,30*2**min(row['attempts'],6)),str(exc)[:300],'blocked' if row['attempts']>=9 else 'pending',row['id']))

    def due(self,now=None):
        if not enabled(self.root):return []
        now=time.time() if now is None else now;account=str(config(self.root)['account_id']);packets=[]
        scene={'platform':'heybox','account':account,'chat_type':'comment'}
        if not wakefulness.allowed(self.root,scene,now):return []
        with closing(connect(self.root)) as db,db:
            db.execute('BEGIN IMMEDIATE')
            ready=db.execute("SELECT * FROM notices WHERE account=? AND status='ready' AND received<=? ORDER BY received LIMIT 20",(account,now-5)).fetchall()
            groups={}
            for n in ready:groups.setdefault(n['pid'],[]).append(n)
            for pid,members in list(groups.items())[:3]:
                ids=[m['id'] for m in members];wid='heybox-notify:'+hashlib.sha256(json.dumps([account,pid,ids]).encode()).hexdigest();focus=json.loads(members[-1]['focus'])
                union={}
                for member in members:
                    for c in json.loads(member['focus'])['对应回复链']:union[c['评论ID']]=c
                focus['对应回复链']=list(union.values());focus['触发评论ID列表']=[m['cid'] for m in members]
                db.execute("INSERT OR IGNORE INTO batches(id,account,pid,members,focus,status) VALUES(?,?,?,?,?,'queued')",(wid,account,pid,json.dumps(ids),json.dumps(focus,ensure_ascii=False)))
                for m in members:db.execute("UPDATE notices SET status='queued',batch=? WHERE id=?",('wake:'+wid,m['id']))
            for row in db.execute("SELECT * FROM batches WHERE account=? AND status='queued' AND emitted<? ORDER BY rowid LIMIT 3",(account,now-30)).fetchall():
                batch='wake:'+row['id']
                with closing(sqlite3.connect(self.root/'mouth.sqlite3')) as mouth:
                    if mouth.execute('SELECT 1 FROM outputs WHERE batch_id=?',(batch,)).fetchone():
                        db.execute("UPDATE batches SET status='generated' WHERE id=?",(row['id'],));db.execute("UPDATE notices SET status='generated' WHERE batch=?",(batch,));continue
                social=self.root/'social.sqlite3'
                if social.exists():
                    with closing(sqlite3.connect(social)) as queue:
                        if queue.execute("SELECT 1 FROM queue WHERE key=? AND status='processing'",(batch,)).fetchone():continue
                focus=json.loads(row['focus']);scene={'platform':'heybox','account':account,'chat_type':'comment','conversation':row['pid']+(':'+focus['触发评论ID'] if focus['触发评论ID'] else '')}
                packets.append({'batch_id':batch,'scene':scene,'engine':'astrbot','wake_depth':1,'image_options':[],
                    'heybox_notification_focus':focus,'internal_wake':{'kind':'heybox_notification','wake_id':row['id'],'intent':focus['触发类型']},
                    'new_messages':[{'id':0,'observation':{'internal_event':True,'platform':'heybox','account_id':account,'chat_type':'comment','conversation_id':scene['conversation'],'sender_id':focus['触发者']['UID'],'received_at':datetime.now(UTC).isoformat(),'raw_event':{'post_type':'message','message':[]}}}]})
                db.execute('UPDATE batches SET emitted=? WHERE id=?',(now,row['id']))
        return packets
