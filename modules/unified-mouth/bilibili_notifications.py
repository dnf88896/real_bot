"""Durable reply/@ ingress. No participation model, no unsolicited targets."""
import hashlib,json,sqlite3,time,logging
from contextlib import closing
from datetime import datetime,UTC
from pathlib import Path
from urllib.parse import urlsplit,parse_qs,urlencode
if __package__:
    from . import bilibili_video as video, wakefulness
else:
    import bilibili_video as video, wakefulness


class MorePages(Exception):pass
class MissingComment(ValueError):pass


def connect(root):
    db=sqlite3.connect(Path(root)/'bilibili-notifications.sqlite3',timeout=20);db.row_factory=sqlite3.Row
    db.executescript('''PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE IF NOT EXISTS seen(account TEXT,feed TEXT,nid TEXT,PRIMARY KEY(account,feed,nid));
        CREATE TABLE IF NOT EXISTS inbox(account TEXT,feed TEXT,nid TEXT,parsed TEXT,attempts INTEGER DEFAULT 0,next_retry REAL DEFAULT 0,error TEXT,PRIMARY KEY(account,feed,nid));
        CREATE TABLE IF NOT EXISTS cursors(account TEXT,feed TEXT,cursor TEXT,created REAL,PRIMARY KEY(account,feed,cursor));
        CREATE TABLE IF NOT EXISTS notices(id TEXT PRIMARY KEY,account TEXT,bvid TEXT,rpid TEXT,
            raw TEXT,status TEXT,scan TEXT,focus TEXT,attempts INTEGER DEFAULT 0,next_retry REAL DEFAULT 0,
            emitted REAL DEFAULT 0,error TEXT,UNIQUE(account,bvid,rpid));''')
    db.execute('INSERT OR IGNORE INTO meta VALUES(?,?)',('start_at',str(time.time())));db.commit()
    return db


def enabled(root):
    try:
        cfg=video.account_config(root)
        return bool(cfg.get('comments_enabled') and cfg.get('notifications_enabled'))
    except (OSError,ValueError):return False


def is_notification(packet):
    return packet.get('internal_wake',{}).get('kind')=='bili_notification'


def valid(root,wid):
    if not enabled(root):return False
    with closing(connect(root)) as db:
        row=db.execute('SELECT status,account FROM notices WHERE id=?',(wid,)).fetchone()
    return bool(row and row[0] in ('ready','queued','generated') and row[1]==str(video.account_config(root)['account_id']))


def validate_expression(packet,scopes):
    """Notification origin is factual context; shared target validation owns routing."""
    return None


def finish(root,packet,committed=False,error=None):
    if not is_notification(packet):return
    with closing(connect(root)) as db,db:
        db.execute("UPDATE notices SET status=?,error=?,scan='{}' WHERE id=? AND status='queued'",
            ('generated' if committed else 'failed',error or (None if committed else 'generation_not_completed'),packet['internal_wake']['wake_id']))


def parse_notice(feed,notice):
    item=notice.get('item') or {}
    if str(item.get('business_id'))!='1' or item.get('type','reply') not in ('reply','video'):
        return None
    rpid=str(item.get('source_id') or item.get('item_id') or '')
    if not rpid.isascii() or not rpid.isdecimal() or int(rpid)<=0:return None
    uri=str(item.get('uri') or '')
    ids=video.refs.references(uri)
    link=next((x for x in ids if x.startswith('http')),'')
    if not link:return None
    oid=str(item.get('subject_id') or item.get('oid') or '')
    root=str(item.get('root_id') or '0')
    if root=='0':
        q=parse_qs(urlsplit(uri).query)
        root=(q.get('comment_root_id') or q.get('comment_root') or [rpid])[0]
    author=notice.get('user') or {}
    if not isinstance(author,dict):author={}
    if not author and isinstance(notice.get('users'),list) and len(notice['users'])==1:author=notice['users'][0]
    return {'feed':feed,'link':link,'rpid':rpid,'root':str(root),'oid':oid,
            'parent_hint':str(item.get('target_id') or '0'),'author':str(author.get('mid') or ''),
            'timestamp':float(notice.get('reply_time') or notice.get('at_time') or 0)}


def accept_page(root,account,feed,data,cursor=None):
    """Seen IDs and pending work commit together; reply/@ share one comment key."""
    all_seen=True;reached_start=False
    with closing(connect(root)) as db,db:
        start=float(db.execute("SELECT value FROM meta WHERE key='start_at'").fetchone()[0])
        for raw in data.get('items') or []:
            nid=str(raw.get('id') or '')
            if not nid:continue
            changed=db.execute('INSERT OR IGNORE INTO seen VALUES(?,?,?)',(str(account),feed,nid)).rowcount
            all_seen=all_seen and not changed
            parsed=parse_notice(feed,raw)
            stamp=float(raw.get('reply_time') or raw.get('at_time') or 0)
            if stamp and stamp<start:reached_start=True
            if changed and parsed and parsed['timestamp']>=start and parsed['author']!=str(account):
                db.execute('INSERT OR IGNORE INTO inbox(account,feed,nid,parsed) VALUES(?,?,?,?)',
                    (str(account),feed,nid,json.dumps(parsed,ensure_ascii=False)))
        next_cursor=data.get('cursor') or {}
        if not (all_seen or reached_start) and not next_cursor.get('is_end',True) and next_cursor.get('id'):
            db.execute('INSERT OR IGNORE INTO cursors VALUES(?,?,?,?)',(account,feed,json.dumps(next_cursor,sort_keys=True),time.time()))
        if cursor and cursor!=next_cursor:
            db.execute('DELETE FROM cursors WHERE account=? AND feed=? AND cursor=?',(account,feed,json.dumps(cursor,sort_keys=True)))
    return all_seen or reached_start


async def ingest(root,client,account,parsed):
    bvid,_=await video.resolve(client,parsed['link'])
    wid='bili-notify:'+hashlib.sha256(json.dumps([str(account),bvid,parsed['rpid']]).encode()).hexdigest()
    with closing(connect(root)) as db,db:
        db.execute("INSERT OR IGNORE INTO notices(id,account,bvid,rpid,raw,status,scan) VALUES(?,?,?,?,?,'pending','{}')",
            (wid,str(account),bvid,parsed['rpid'],json.dumps(parsed,ensure_ascii=False)))
    return wid


def checked_row(raw,aid,root):
    if str(raw.get('oid'))!=str(aid) or str(raw.get('type'))!='1':raise MissingComment('Comment belongs to a different video')
    row=video.comment_info(raw)
    if row['根评论ID'] not in ('0',str(root)):raise MissingComment('Comment belongs to a different root')
    return row


async def prepare_focus(root,client,row):
    parsed=json.loads(row['raw']);root_id=parsed['root'];rpid=row['rpid']
    if not root_id.isascii() or not root_id.isdecimal() or int(root_id)<=0:raise MissingComment('Missing valid root comment')
    info=video.video_info(await video.fetch_info(client,row['bvid']))
    if parsed['oid'] and parsed['oid']!=info['AV号']:raise MissingComment('Notification video identity mismatch')
    scan=json.loads(row['scan'] or '{}');page=scan.get('page',1);collected=scan.get('rows',{})
    for _ in range(5):
        data=await video.get(client,'/x/v2/reply/reply',{'type':1,'oid':info['AV号'],'root':root_id,'pn':page,'ps':20})
        root_raw=data.get('root')
        if not root_raw or str(root_raw.get('rpid_str') or root_raw.get('rpid'))!=root_id:
            raise MissingComment('Root comment is deleted or unavailable')
        for raw in [root_raw]+(data.get('replies') or []):
            normalized=checked_row(raw,info['AV号'],root_id);collected[normalized['评论ID']]=normalized
        if rpid in collected:
            trigger=collected[rpid]
            if trigger['作者']['UID']==str(row['account']):raise MissingComment('Self notification ignored')
            if parsed['author'] and trigger['作者']['UID']!=parsed['author']:raise MissingComment('Notification author mismatch')
            parent_hint=parsed['parent_hint']
            if parent_hint!='0' and trigger['父评论ID']!=parent_hint:raise MissingComment('Notification parent mismatch')
            chain=[];current=rpid;seen=set()
            while current not in seen:
                seen.add(current)
                if current not in collected:break
                node=collected[current];chain.append(node)
                if current==root_id:break
                current=node['父评论ID'] if node['父评论ID']!='0' else root_id
            if chain and chain[-1]['评论ID']==root_id:
                chain.reverse()
                video.remember_video(root,info);video.cache_comments(root,row['bvid'],chain)
                link=info['链接']+'?'+urlencode({'comment_root':root_id,'comment_id':rpid})
                return {'视频资料':info,'对应回复链':chain,'触发评论ID':rpid,'目标评论链接':link,
                        '触发类型':'有人回复了你' if parsed['feed']=='reply' else '有人@了你',
                        '读取范围':'仅本视频资料、触发评论及必要的根评论/父评论；未观看视频'}
        count=int((data.get('page') or {}).get('count') or 0)
        if not data.get('replies') or page*20>=count:raise MissingComment('Trigger comment or parent chain unavailable')
        page+=1
        with closing(connect(root)) as db,db:
            db.execute('UPDATE notices SET scan=? WHERE id=?',(json.dumps({'page':page,'rows':collected},ensure_ascii=False),row['id']))
    raise MorePages('Continue locating exact comment next poll')


def history(memory,packet):
    focus=packet['notification_focus'];info=focus['视频资料'];account=str(packet['scene']['account'])
    contexts=[{'role':'user','content':'[当前视频基本资料] '+json.dumps(info,ensure_ascii=False)}]
    with closing(memory.connect()) as db:
        found=db.execute("SELECT key FROM events WHERE platform='bilibili' AND account=? AND kind='video' AND conversation=? ORDER BY time DESC LIMIT 1",(account,info['BV号'])).fetchone()
        refs=[[found[0]] if found else []]
        for comment in focus['对应回复链']:
            own=comment['作者']['UID']==account
            contexts.append({'role':'assistant' if own else 'user','content':'[对应回复链] '+json.dumps(video.refs.present_comments(comment,info['BV号']),ensure_ascii=False)})
            found=db.execute("SELECT key FROM events WHERE platform='bilibili' AND account=? AND kind='comment' AND direction='outgoing' AND message_id=? LIMIT 1",(account,comment['评论ID'])).fetchone() if own else None
            refs.append([found[0]] if found else [])
    return contexts,refs


class Notifications:
    def __init__(self,root):
        self.root=Path(root)
        with closing(connect(root)):pass

    async def page(self,client,account,feed,cursor=None):
        params={'platform':'web','mobi_app':'web'}
        if cursor:params.update(id=cursor['id'],**{'reply_time' if feed=='reply' else 'at_time':cursor['time']})
        data=await video.get(client,'/x/msgfeed/'+feed,params)
        accept_page(self.root,account,feed,data,cursor)

    async def poll(self):
        if not enabled(self.root):return
        client,cfg=video.client_for(self.root);account=str(cfg['account_id'])
        try:
            for feed in ('reply','at'):
                try:
                    await self.page(client,account,feed)
                    with closing(connect(self.root)) as db:
                        cursors=db.execute('SELECT cursor FROM cursors WHERE account=? AND feed=? ORDER BY created LIMIT 2',(account,feed)).fetchall()
                    for cursor in cursors:await self.page(client,account,feed,json.loads(cursor[0]))
                except Exception as exc:
                    logging.getLogger(__name__).warning('Bilibili %s notification checkpoint retained: %s',feed,type(exc).__name__)
            with closing(connect(self.root)) as db:
                inbox=db.execute('SELECT * FROM inbox WHERE account=? AND next_retry<=? ORDER BY rowid LIMIT 10',(account,time.time())).fetchall()
            for item in inbox:
                try:
                    await ingest(self.root,client,account,json.loads(item['parsed']))
                    with closing(connect(self.root)) as db,db:
                        db.execute('DELETE FROM inbox WHERE account=? AND feed=? AND nid=?',(account,item['feed'],item['nid']))
                except Exception as exc:
                    with closing(connect(self.root)) as db,db:
                        db.execute('UPDATE inbox SET attempts=attempts+1,next_retry=?,error=? WHERE account=? AND feed=? AND nid=?',
                            (time.time()+min(600,30*2**min(item['attempts'],5)),type(exc).__name__,account,item['feed'],item['nid']))
            if not wakefulness.allowed(self.root,{'platform':'bilibili','account':account,'chat_type':'comment'}):return
            with closing(connect(self.root)) as db:
                rows=db.execute("SELECT * FROM notices WHERE account=? AND status='pending' AND next_retry<=? ORDER BY rowid LIMIT 3",(account,time.time())).fetchall()
            for row in rows:
                try:
                    focus=await prepare_focus(self.root,client,row)
                    with closing(connect(self.root)) as db,db:
                        db.execute("UPDATE notices SET status='ready',focus=?,scan='{}',error=NULL WHERE id=?",(json.dumps(focus,ensure_ascii=False),row['id']))
                except MorePages:pass
                except MissingComment as exc:
                    with closing(connect(self.root)) as db,db:db.execute("UPDATE notices SET status='ignored',error=?,scan='{}' WHERE id=?",(str(exc),row['id']))
                except Exception as exc:
                    with closing(connect(self.root)) as db,db:
                        db.execute('UPDATE notices SET status=?,attempts=attempts+1,next_retry=?,error=? WHERE id=?',('blocked' if row['attempts']>=9 else 'pending',time.time()+min(600,30*2**row['attempts']),type(exc).__name__,row['id']))
        finally:await client.close()

    def due(self):
        if not enabled(self.root):return []
        packets=[];now=time.time()
        with closing(connect(self.root)) as db,db:
            for row in db.execute("SELECT * FROM notices WHERE account=? AND status IN ('ready','queued') AND emitted<? ORDER BY rowid LIMIT 3",(str(video.account_config(self.root)['account_id']),now-30)).fetchall():
                batch='wake:'+row['id']
                with closing(sqlite3.connect(self.root/'mouth.sqlite3')) as mouth:
                    generated=mouth.execute('SELECT 1 FROM outputs WHERE batch_id=?',(batch,)).fetchone()
                if generated:
                    db.execute("UPDATE notices SET status='generated' WHERE id=?",(row['id'],));continue
                scene={'platform':'bilibili','account':row['account'],'chat_type':'comment','conversation':row['bvid']+':'+row['rpid']}
                if not wakefulness.allowed(self.root,scene):continue
                focus=json.loads(row['focus']);intent=focus['触发类型']+'，请回复所指定的触发评论。'
                packet={'batch_id':batch,'scene':scene,'engine':'astrbot','wake_depth':1,'image_options':[],
                    'notification_focus':focus,'internal_wake':{'kind':'bili_notification','wake_id':row['id'],'intent':intent},
                    'new_messages':[{'id':0,'observation':{'internal_event':True,'platform':'bilibili','account_id':row['account'],
                        'chat_type':'comment','conversation_id':scene['conversation'],'sender_id':focus['对应回复链'][-1]['作者']['UID'],
                        'received_at':datetime.now(UTC).isoformat(),'raw_event':{'post_type':'message','message':[]}}}]}
                db.execute("UPDATE notices SET status='queued',emitted=? WHERE id=?",(now,row['id']));packets.append(packet)
        return packets
