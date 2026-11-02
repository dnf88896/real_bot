"""Posting context, one-shot tasks and persistent awake-time recurring opportunities."""
import argparse
import secrets
import json
import math
import sqlite3
import time
import uuid
from contextlib import closing
from datetime import datetime, UTC, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

ROOT = Path('/opt/astrbot-mouth')
CADENCE_DEFAULT={'enabled':False,'min_seconds':5*3600,'max_seconds':7*3600,'failure_retry_seconds':600}
POST_INTENT=('现在到了个人动态的定期主动时间，可以主动找一个适合在自己B站主页分享的话题。'
             '结合当前时间、自己的状态、全局记忆和过去已发布的动态，自主决定想发什么；可以搜索和读取感兴趣的资料。'
             '避免重复近期已发内容，不编造亲历或来源。没有适合发表的内容也可以保持沉默。')

HEYBOX_POST_INTENT=POST_INTENT.replace('个人动态','小黑盒发帖').replace('自己B站主页','自己的小黑盒主页').replace('动态','帖子')

def platform_for(wid):
    return 'heybox' if str(wid).startswith('heybox-post:') else 'bilibili'

def cadence_config(root, platform='bilibili'):
    path=Path(root)/(platform+'-post-proactive.json')
    cfg=CADENCE_DEFAULT | (json.loads(path.read_text('utf8')) if path.exists() else {})
    if not all(type(cfg[k]) is int for k in ('min_seconds','max_seconds','failure_retry_seconds')) or not 0<cfg['min_seconds']<=cfg['max_seconds'] or cfg['failure_retry_seconds']<=0:
        raise ValueError('Invalid '+platform+' proactive cadence')
    return cfg

def awake_elapsed(root,start,end):
    if end<=start:return 0.0
    if __package__:from . import wakefulness
    else:import wakefulness
    cfg=wakefulness.config(root)
    if not cfg['enabled']:return end-start
    tz=ZoneInfo(cfg['timezone']);day=datetime.fromtimestamp(start,tz).replace(hour=0,minute=0,second=0,microsecond=0)
    total=0.0
    while day.timestamp()<end:
        for a,b,label in cfg['periods']:
            if label=='睡眠中':continue
            left=(day+timedelta(minutes=a)).timestamp();right=(day+timedelta(minutes=b)).timestamp()
            total+=max(0.0,min(end,right)-max(start,left))
        day+=timedelta(days=1)
    return total

def random_interval(cfg):return cfg['min_seconds']+secrets.randbelow(cfg['max_seconds']-cfg['min_seconds']+1)



def is_post(packet):
    return packet.get('internal_wake', {}).get('kind') == 'post_activity'


def enabled(root, account, platform='bilibili'):
    root = Path(root)
    try:
        if platform not in ('bilibili','heybox'):return False
        cfg = json.loads((root/(platform+'-account.json')).read_text('utf8'))
        if platform=='heybox':
            return bool(cfg.get('enabled') and cfg.get('posting_enabled') and str(cfg.get('account_id',''))==str(account))
        delivery = json.loads((root/'delivery.json').read_text('utf8'))
        return (cfg.get('enabled') and cfg.get('posting_enabled')
                and str(cfg.get('account_id', '')) == str(account)
                and str(account) in [str(x) for x in delivery.get('bilibili_post_accounts', [])])
    except (OSError, ValueError):
        return False


def connect(root, platform='bilibili'):
    db = sqlite3.connect(Path(root)/('heybox-post-activities.sqlite3' if platform=='heybox' else 'post-activities.sqlite3'), timeout=20)
    db.row_factory = sqlite3.Row
    db.execute('''CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,account TEXT,
        intent TEXT,due REAL,status TEXT,batch TEXT UNIQUE,created REAL,error TEXT)''')
    db.executescript("""
      CREATE TABLE IF NOT EXISTS cadence(account TEXT PRIMARY KEY,remaining REAL,checked REAL,inflight TEXT);
      CREATE TABLE IF NOT EXISTS automatic_tasks(task TEXT PRIMARY KEY,account TEXT);
    """)
    db.commit()
    return db


def enqueue(root, account, intent, due=None, platform='bilibili'):
    if not enabled(root, account, platform):
        raise ValueError(platform+' own posting account is not enabled')
    due = time.time() if due is None else due
    if type(due) not in (float, int) or not math.isfinite(due):
        raise ValueError('Invalid posting time')
    if not isinstance(intent, str) or not intent.strip() or len(intent) > 10000:
        raise ValueError('Invalid posting intention')
    wid = ('heybox-post:' if platform=='heybox' else 'post:') + uuid.uuid4().hex
    with closing(connect(root,platform)) as db, db:
        db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,NULL)',
                   (wid, str(account), intent, due, 'pending', 'wake:'+wid, time.time()))
    return wid


def valid(root, wid):
    platform=platform_for(wid)
    with closing(connect(root,platform)) as db:
        row = db.execute('SELECT * FROM tasks WHERE id=?', (wid,)).fetchone()
        automatic=db.execute('SELECT 1 FROM automatic_tasks WHERE task=?',(wid,)).fetchone()
    if automatic:
        if __package__:from . import active_gate
        else:import active_gate
        if not cadence_config(root,platform)['enabled'] or not active_gate.config(root)['enabled']:return False
    return bool(row and row['status'] in ('queued','generated') and enabled(root,row['account'],platform))


def finish(root, packet, committed=False, error=None):
    if not is_post(packet):
        return
    with closing(connect(root,packet['scene']['platform'])) as db, db:
        db.execute("UPDATE tasks SET status=?,error=? WHERE id=? AND status='queued'",
                   ('generated' if committed else 'failed', error or (None if committed else 'generation_not_completed'),
                    packet['internal_wake']['wake_id']))


def history(memory, account, limit, platform='bilibili'):
    """Use only confirmed own posts, across short and long layers, oldest first."""
    with closing(memory.connect()) as db:
        rows = db.execute("""SELECT * FROM events WHERE platform=? AND account=?
            AND kind='post' AND direction='outgoing' AND conversation=?
            AND json_extract(raw,'$.post_url') IS NOT NULL
            AND coalesce(json_extract(raw,'$.delivery_mode'),'') NOT IN ('group_preview','owner_preview')
            ORDER BY time DESC,key DESC LIMIT ?""", (platform,str(account),str(account),limit)).fetchall()
    # A newly accepted post can precede the asynchronous output-memory collector.
    # Merge its immutable receipt without waiting for that collector or inventing
    # a second memory identity. The normal collector remains the durable writer.
    if __package__:
        from .memory_engine import normalize
    else:
        from memory_engine import normalize
    merged = {r['key']: dict(r) for r in rows}
    with closing(sqlite3.connect(memory.root/'mouth.sqlite3')) as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='relay_deliveries'").fetchone():
            receipts = db.execute("""SELECT message_id,record_json FROM relay_deliveries
                WHERE status='sent' AND json_extract(destination,'$.platform')=?
                AND json_extract(destination,'$.chat_type')='post'
                AND json_extract(destination,'$.account')=? AND json_extract(destination,'$.id')=?
                ORDER BY updated DESC LIMIT ?""",(platform,str(account),str(account),limit)).fetchall()
            for mid, encoded in receipts:
                raw=json.loads(encoded) if encoded else {}
                if not raw.get('post_url') or raw.get('delivery_mode') in ('group_preview','owner_preview'):
                    continue
                raw={**raw,'phase':'sent','receipt':str(mid),
                     'received_at':datetime.fromtimestamp(raw['sent_at'],UTC).isoformat()}
                event=normalize(raw,'outgoing',str(mid))
                if event:
                    merged.setdefault(event['key'],{**event,'raw':json.dumps(raw)})
    rows=sorted(merged.values(),key=lambda r:(r['time'],r['key']))[-limit:] if limit else []
    contexts, refs, images = [], [], []
    for row in rows:
        record = memory.render(row)
        record.pop('来源标识',None)
        contexts.append({'role':'assistant','content':('[自己已发布的小黑盒帖子] ' if platform=='heybox' else '[自己已发布的B站帖子] ')+json.dumps(record,ensure_ascii=False)})
        refs.append([row['key']])
        images.extend(json.loads(row['raw']).get('image_paths', []))
    return contexts, refs, list(dict.fromkeys(images))


class Posting:
    def __init__(self, root, platform='bilibili'):
        if platform not in ('bilibili','heybox'):raise ValueError('Unsupported posting platform')
        self.platform=platform
        self.root = Path(root)
        self.enqueued = {}
        with closing(connect(root,self.platform)):
            pass

    def tick_cadence(self,now):
        if __package__:from . import active_gate,wakefulness
        else:import active_gate,wakefulness
        cfg=cadence_config(self.root,self.platform)
        try:account=str(json.loads((self.root/(self.platform+'-account.json')).read_text('utf8'))['account_id'])
        except (OSError,ValueError,KeyError):return
        running=cfg['enabled'] and active_gate.config(self.root)['enabled'] and enabled(self.root,account,self.platform)
        with closing(connect(self.root,self.platform)) as db,db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM cadence WHERE account=?',(account,)).fetchone()
            if not running:
                if row:db.execute('UPDATE cadence SET checked=? WHERE account=?',(now,account))
                return
            if row is None:
                db.execute('INSERT INTO cadence VALUES(?,?,?,NULL)',(account,random_interval(cfg),now))
                return
            remaining=row['remaining'];checked=row['checked'];inflight=row['inflight']
            if inflight:
                task=db.execute('SELECT * FROM tasks WHERE id=?',(inflight,)).fetchone()
                outcome=active_gate.delivery_outcome(self.root,task['batch']) if task else None
                if not outcome and task and (self.root/'active-system.sqlite3').exists():
                    with closing(sqlite3.connect(self.root/'active-system.sqlite3')) as gate:
                        admission=gate.execute('SELECT phase FROM admissions WHERE batch=?',(task['batch'],)).fetchone()
                    if admission and admission[0] in ('failed','cancelled'):
                        outcome=(False,now,'posting_admission_'+admission[0])
                        db.execute("UPDATE tasks SET status='failed',error=? WHERE id=?",(outcome[2],inflight))
                if not outcome and task and task['status'] not in ('failed','cancelled'):
                    # No interval accumulates while this opportunity is still being handled.
                    db.execute('UPDATE cadence SET checked=? WHERE account=?',(now,account));return
                ok,at,error=outcome if outcome else (False,now,'posting_generation_failed')
                remaining=random_interval(cfg) if ok else cfg['failure_retry_seconds']
                checked=min(now,at);inflight=None
            remaining=max(0.0,remaining-awake_elapsed(self.root,checked,now))
            scene={'platform':self.platform,'account':account,'chat_type':'post','conversation':account}
            if remaining<=0 and wakefulness.allowed(self.root,scene,now):
                wid=('heybox-post:' if self.platform=='heybox' else 'post:')+uuid.uuid4().hex
                db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,NULL)',(wid,account,HEYBOX_POST_INTENT if self.platform=='heybox' else POST_INTENT,now,'pending','wake:'+wid,now))
                db.execute('INSERT INTO automatic_tasks VALUES(?,?)',(wid,account));inflight=wid
            db.execute('UPDATE cadence SET remaining=?,checked=?,inflight=? WHERE account=?',(remaining,now,inflight,account))

    def due(self, now=None):
        now = time.time() if now is None else now
        if __package__:
            from . import wakefulness
        else:
            import wakefulness
        self.tick_cadence(now)
        packets = []
        with closing(connect(self.root,self.platform)) as db, db:
            rows = db.execute("SELECT * FROM tasks WHERE status IN ('pending','queued') AND due<=? ORDER BY due,created",(now,)).fetchall()
            for row in rows:
                automatic=db.execute('SELECT 1 FROM automatic_tasks WHERE task=?',(row['id'],)).fetchone()
                if automatic:
                    if __package__:from . import active_gate
                    else:import active_gate
                    if not cadence_config(self.root,self.platform)['enabled'] or not active_gate.config(self.root)['enabled'] or not active_gate.ready(self.root,row['batch'],now=now):continue
                with closing(sqlite3.connect(self.root/'mouth.sqlite3')) as mouth:
                    generated = mouth.execute('SELECT 1 FROM outputs WHERE batch_id=?',(row['batch'],)).fetchone()
                if generated:
                    db.execute("UPDATE tasks SET status='generated' WHERE id=?",(row['id'],))
                    continue
                scene = {'platform':self.platform,'account':row['account'],'chat_type':'post','conversation':row['account']}
                if not enabled(self.root,row['account'],self.platform) or not wakefulness.allowed(self.root,scene,now):
                    continue
                if now-self.enqueued.get(row['id'],float('-inf')) < 30:
                    continue
                packet = {'batch_id':row['batch'],'scene':scene,'wake_depth':1,'engine':'astrbot','image_options':[],
                    'internal_wake':{'kind':'post_activity','wake_id':row['id'],'intent':row['intent'],'due':row['due']},
                    'new_messages':[{'id':0,'observation':{'internal_event':True,'platform':self.platform,
                        'account_id':row['account'],'chat_type':'post','conversation_id':row['account'],
                        'sender_id':row['account'],'received_at':datetime.fromtimestamp(now,UTC).isoformat(),
                        'raw_event':{'post_type':'message','message':[]}}}]}
                if automatic:packet['active_type']='posting'
                db.execute("UPDATE tasks SET status='queued' WHERE id=?",(row['id'],))
                self.enqueued[row['id']]=now;packets.append(packet)
        return packets

    def retry_emit(self, packet):
        self.enqueued.pop(packet['internal_wake']['wake_id'],None)


if __name__ == '__main__':
    p=argparse.ArgumentParser(description='Explicit one-shot platform posting activity')
    p.add_argument('--root',default=str(ROOT));p.add_argument('--account',required=True)
    p.add_argument('--platform',choices=['bilibili','heybox'],default='bilibili')
    p.add_argument('--intent',required=True);p.add_argument('--at',help='ISO time, including timezone; omitted means now')
    args=p.parse_args()
    due=None
    if args.at:
        stamp=datetime.fromisoformat(args.at)
        if stamp.tzinfo is None:p.error('--at must include a timezone')
        due=stamp.timestamp()
    print(enqueue(args.root,args.account,args.intent,due,platform=args.platform))
