"""Persistent idle intentions; no model calls or direct message sending."""
import json,sqlite3,time,uuid,math
from pathlib import Path
from datetime import datetime,timezone
MOUTH=Path('/opt/astrbot-mouth/mouth.sqlite3')
ARCHIVE=Path('/opt/astrbot-mouth/observations.sqlite3')

def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS intent_wakeups(id TEXT PRIMARY KEY,batch_id TEXT,start INTEGER,end INTEGER,intent TEXT,minutes REAL,watch_json TEXT,origin_json TEXT,status TEXT,created REAL,baseline_json TEXT,due REAL,depth INTEGER,brain_batch_id TEXT,error TEXT)""")
    db.commit()

def resolve(watch,contacts,current):
    if watch.strip() in ('对方','当前会话','当前对话','本会话','本对话'):return current
    matches={}
    platform=None
    for prefix,name in [('微信的','wechat'),('微信','wechat'),('wechat的','wechat'),('QQ的','qq'),('qq的','qq'),('B站的','bilibili'),('b站的','bilibili'),('bilibili的','bilibili')]:
        if watch.startswith(prefix):
            platform=name;watch=watch[len(prefix):];break
    for row in contacts:
        if row['platform'] not in ('qq','bilibili','wechat') or (platform and row['platform']!=platform):continue
        if watch==str(row['id']) or any(watch in str(row.get(k) or '') for k in ('name','remark')):
            dest={k:str(row[k]) for k in ('platform','account','chat_type','id')}
            matches[tuple(dest.values())]=dest
    if len(matches)!=1:raise ValueError('Wake watch target missing or ambiguous')
    return next(iter(matches.values()))

def validate(plan,text,contacts,scene):
    raw=plan.get('wakeups',[])
    if not isinstance(raw,list) or len(raw)>4:raise ValueError('Invalid wake intentions')
    current={'platform':scene['platform'],'account':str(scene['account']),'chat_type':scene['chat_type'],'id':str(scene['conversation'])}
    result=[];targets=set();ranges=[]
    for item in raw:
        if not isinstance(item,dict) or set(item)-{'start','end','idle_minutes','watch'} or not {'start','end','idle_minutes'}<=set(item):raise ValueError('Invalid wake annotation')
        start,end,minutes=item['start'],item['end'],item['idle_minutes'];watch=item.get('watch','对方')
        if type(start) is not int or type(end) is not int or not 0<=start<end<=len(text):raise ValueError('Invalid wake range')
        if type(minutes) not in (int,float) or not math.isfinite(minutes) or not 1/60<=minutes<=525600:raise ValueError('Invalid idle minutes')
        if not isinstance(watch,str) or not watch.strip():raise ValueError('Invalid watch name')
        for part in plan['segments']:
            if part['kind']!='omit' and max(start,part['start'])<min(end,part['end']):raise ValueError('Wake intention overlaps sent content')
        if any(max(start,a)<min(end,b) for a,b in ranges):raise ValueError('Overlapping wake intentions')
        dest=resolve(watch,contacts,current);key=json.dumps(dest,sort_keys=True)
        if key in targets:raise ValueError('Duplicate watch target')
        targets.add(key);ranges.append((start,end))
        result.append({'start':start,'end':end,'intent':text[start:end],'minutes':minutes,'watch':dest,'origin':current})
    return result

def activity(db,archive,dest,exclude_batch=None):
    row=archive.execute("SELECT max(id) FROM observations WHERE account_id=? AND chat_type=? AND conversation_id=? AND coalesce(json_extract(raw_json,'$.platform'),'qq')=?",(dest['account'],dest['chat_type'],dest['id'],dest['platform'])).fetchone()
    incoming=row[0] or 0;outgoing=[]
    for raw, in db.execute("SELECT record_json FROM relay_deliveries WHERE status='sent' AND record_json IS NOT NULL"):
        obj=json.loads(raw)
        if exclude_batch and obj.get('batch_id')==exclude_batch:continue
        if obj.get('platform','qq')==dest['platform'] and str(obj.get('account_id'))==dest['account'] and obj.get('chat_type')==dest['chat_type'] and str(obj.get('conversation_id'))==dest['id']:outgoing.append(str(obj.get('message_id')))
    return [incoming,sorted(outgoing)]

def register(db,batch,items,packet,archive,now=None):
    now=time.time() if now is None else now
    if packet.get('wake_depth',0)>=1:return
    for n,item in enumerate(items):
        watch=json.dumps(item['watch'],sort_keys=True)
        db.execute("UPDATE intent_wakeups SET status='cancelled',error='superseded' WHERE watch_json=? AND status IN ('awaiting_delivery','waiting','queued')",(watch,))
        base=activity(db,archive,item['watch'],batch)
        db.execute('INSERT INTO intent_wakeups VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(batch+':wake:'+str(n),batch,item['start'],item['end'],item['intent'],item['minutes'],watch,json.dumps(item['origin']), 'awaiting_delivery',now,json.dumps(base),None,0,None,None))

def tick(db,archive,now=None):
    now=time.time() if now is None else now;initialize(db)
    db.row_factory=sqlite3.Row
    for row in db.execute("SELECT * FROM intent_wakeups WHERE status IN ('awaiting_delivery','waiting','queued') ORDER BY created").fetchall():
        dest=json.loads(row['watch_json']);base=json.loads(row['baseline_json'])
        current=activity(db,archive,dest,row['batch_id'] if row['status']=='awaiting_delivery' else None)
        if current!=base:
            with db:db.execute("UPDATE intent_wakeups SET status='cancelled',error='new_conversation_activity' WHERE id=?",(row['id'],))
            continue
        if row['status']=='awaiting_delivery':
            state=db.execute('SELECT status FROM relay_jobs WHERE batch_id=?',(row['batch_id'],)).fetchone()
            if not state:continue
            if state[0] in ('delivered','control_only'):
                base=activity(db,archive,dest)
                stamp=db.execute("SELECT max(updated) FROM relay_deliveries WHERE batch_id=? AND status='sent'",(row['batch_id'],)).fetchone()[0] or row['created']
                with db:db.execute("UPDATE intent_wakeups SET status='waiting',baseline_json=?,due=? WHERE id=?",(json.dumps(base),max(stamp,row['created'])+row['minutes']*60,row['id']))
            elif state[0] in ('partial','delivery_failed','blocked','failed'):
                with db:db.execute("UPDATE intent_wakeups SET status='cancelled',error='source_delivery_failed' WHERE id=?",(row['id'],))
    return [dict(r) for r in db.execute("SELECT * FROM intent_wakeups WHERE status IN ('waiting','queued') AND due<=? ORDER BY due",(now,))]

def prepare(brain,archive):
    import message_scheduler as scheduler
    scheduler.initialize(brain)
    if not MOUTH.exists():return
    db=sqlite3.connect(MOUTH);ready=tick(db,archive);ready_ids={r['id'] for r in ready}
    with brain:
        for key, in brain.execute("SELECT id FROM scheduler_internal_queue WHERE status='pending'").fetchall():
            if key not in ready_ids:brain.execute("UPDATE scheduler_internal_queue SET status='cancelled' WHERE id=?",(key,))
    for row in ready:
        bid='wake:'+row['id'];dest=json.loads(row['watch_json'])
        previous=brain.execute('SELECT done FROM batches WHERE id=?',(bid,)).fetchone()
        if previous:
            with db:db.execute("UPDATE intent_wakeups SET status=?,brain_batch_id=? WHERE id=?",('completed' if previous[0] else 'queued',bid,row['id']))
            continue
        event={'kind':'self_wake','wake_id':row['id'],'intent':row['intent'],'watch':dest,'origin':json.loads(row['origin_json']),'idle_minutes':row['minutes'],'due':row['due']}
        with brain:brain.execute("INSERT OR IGNORE INTO scheduler_internal_queue(id,due,account,kind,conversation,event) VALUES(?,?,?,?,?,?)",(row['id'],row['due'],dest['account'],dest['chat_type'],dest['id'],json.dumps(event)))
        with db:db.execute("UPDATE intent_wakeups SET status='queued',brain_batch_id=? WHERE id=? AND status IN ('waiting','queued')",(bid,row['id']))
    db.close()

def poll(brain,archive):
    # Compatibility helper; all selection goes through the unified scheduler.
    import message_scheduler as scheduler
    prepare(brain,archive)
    return scheduler.poll(brain,archive,scheduler.DEFAULTS)

def event(brain,batch):
    if not brain.execute("SELECT 1 FROM sqlite_master WHERE name='internal_wakes'").fetchone():return None
    row=brain.execute('SELECT event FROM internal_wakes WHERE batch_id=?',(batch,)).fetchone()
    return json.loads(row[0]) if row else None

def valid(event,archive):
    db=sqlite3.connect(MOUTH);tick(db,archive)
    row=db.execute('SELECT status FROM intent_wakeups WHERE id=?',(event['wake_id'],)).fetchone();db.close()
    return bool(row and row[0] in ('waiting','queued','completed') and activity_check(event,archive))

def new_messages(event,archive):
    now=datetime.now(timezone.utc).isoformat();dest=event['watch']
    oid=archive.execute('SELECT coalesce(max(id),0) FROM observations').fetchone()[0]
    return [{'id':oid,'observation':{'internal_event':True,'received_at':now,'account_id':dest['account'],'chat_type':dest['chat_type'],'conversation_id':dest['id'],'sender_id':dest['account'],'raw_event':{'message':[]}},'message_context':{'internal_event':True,'text':event['intent']}}]

def complete(event):
    db=sqlite3.connect(MOUTH)
    with db:db.execute("UPDATE intent_wakeups SET status='completed' WHERE id=? AND status='queued'",(event['wake_id'],))
    db.close()

def activity_check(event,archive):
    db=sqlite3.connect(MOUTH)
    row=db.execute('SELECT baseline_json,watch_json FROM intent_wakeups WHERE id=?',(event['wake_id'],)).fetchone()
    result=bool(row and activity(db,archive,json.loads(row[1]))==json.loads(row[0]));db.close();return result
