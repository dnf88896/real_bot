"""Brain processing state and scoped read-only access to objective observations."""
import json, sqlite3, sys, time, uuid
from datetime import datetime
from pathlib import Path
from incoming_images import references
from memory_context import load_layers,PARTITION,render
import message_scheduler as scheduler
import wakeups
import behavior

ROOT = Path('/opt/qq-brain')
DB = '/opt/qq-brain-memory/data/observations.sqlite3'
MOUTH_DB=Path('/opt/qq-mouth/mouth.sqlite3')
OWNER='100000001'

def mandatory_reply(items):
    return any(str(x['observation'].get('sender_id',x['observation'].get('raw_event',{}).get('user_id','')))==OWNER for x in items)

def sent_context(b,new):
    if not MOUTH_DB.exists():return []
    times=[r['observation'].get('received_at') for r in new if r['observation'].get('received_at')]
    cutoff=max(datetime.fromisoformat(t.replace('Z','+00:00')).timestamp() for t in times) if times else 0
    db=sqlite3.connect(MOUTH_DB.resolve().as_uri()+'?mode=ro',uri=True)
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='deliveries'").fetchone():return []
        data=db.execute("SELECT record_json FROM deliveries WHERE status='sent' AND updated<=? ORDER BY updated DESC LIMIT 200",(cutoff,)).fetchall()
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='relay_deliveries'").fetchone():
            data.extend(db.execute("SELECT record_json FROM relay_deliveries WHERE status='sent' AND updated<=? ORDER BY updated DESC LIMIT 200",(cutoff,)).fetchall())
            data.sort(key=lambda r:json.loads(r[0]).get('sent_at',0),reverse=True)
    finally:db.close()
    result=[]
    for (raw,) in data:
        r=json.loads(raw)
        if str(r.get('account_id'))==b['account'] and r.get('chat_type')==b['kind'] and str(r.get('conversation_id'))==b['conversation']:result.append(r)
        if len(result)>=12:break
    return list(reversed(result))

def run(action, a):
    s=sqlite3.connect(ROOT/'state.sqlite3'); s.row_factory=sqlite3.Row
    s.executescript('''CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY, ids TEXT NOT NULL, account TEXT, kind TEXT, conversation TEXT, notified REAL DEFAULT 0, done INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS decisions(batch_id TEXT PRIMARY KEY, created REAL, should_reply INTEGER, reason TEXT, payload TEXT);
    CREATE TABLE IF NOT EXISTS retrieved(batch_id TEXT, observation_id INTEGER, PRIMARY KEY(batch_id,observation_id));
    CREATE TABLE IF NOT EXISTS retrieved_events(batch_id TEXT,event_key TEXT,PRIMARY KEY(batch_id,event_key));''')
    m=sqlite3.connect(Path(DB).as_uri()+'?mode=ro',uri=True);m.row_factory=sqlite3.Row
    def rows(sql,args=()):return [dict(r) for r in m.execute(sql,args)]
    def messages(items):
        return [dict(id=r['id'],observation=json.loads(r['raw_json']),message_context=render(json.loads(r['raw_json']))) for r in items]
    b=s.execute('SELECT * FROM batches WHERE done=0 ORDER BY rowid LIMIT 1').fetchone()
    if action=='poll':
        wakeups.prepare(s,m)
        b=scheduler.poll(s,m,scheduler.config(ROOT/'scheduler-config.json'))
        if b is None:return {'pending':False}
        return {'pending':True,'batch_id':b['id'],'count':max(1,len(json.loads(b['ids']))),'notified':b['notified'],'chat_type':b['kind'],'conversation':b['conversation']}
    if action=='scheduler_stats':
        scheduler.initialize(s);return scheduler.stats(s)
    if action=='finish' and s.execute('SELECT 1 FROM decisions WHERE batch_id=?',(a.get('batch_id'),)).fetchone():return {'ok':True,'already_recorded':True}
    if b is None:
        if action=='finish' and s.execute('SELECT 1 FROM decisions WHERE batch_id=?',(a.get('batch_id'),)).fetchone():return {'ok':True,'already_recorded':True}
        raise ValueError('No pending batch')
    if a.get('batch_id')!=b['id']:raise ValueError('Wrong batch id')
    if action=='notified':
        s.execute('UPDATE batches SET notified=? WHERE id=?',(time.time(),b['id']));s.commit();return {'ok':True}
    ids=json.loads(b['ids']); placeholders=','.join('?' for _ in ids)
    internal=wakeups.event(s,b['id'])
    def current_messages():
        return wakeups.new_messages(internal,m) if internal else messages(rows('SELECT * FROM observations WHERE id IN ('+placeholders+') ORDER BY id',ids))
    if action=='read':
        new=current_messages()
        participation=behavior.participation(s,new,{'account':b['account'],'chat_type':b['kind'],'conversation':b['conversation']},b['id'],MOUTH_DB,OWNER)
        guide=ROOT/'v2_judgment.txt'
        constraint=('本批是自身此前留下的内部唤醒意愿，不是对方的新发言。结合当前最新记忆判断是否仍有必要交流；意愿失效则不回复。只传递原始意愿与相关记忆，不写回复方向。\n' if internal else '')+'硬性规则：本批新消息只要有发送者QQ号3231181613，任何私聊或群聊都必须回复，should_reply必须为true；不能以没有@、只是确认、无提问或不宜接话为由不回复。仅判断是否回复，不向口传递回复方向。历史检索中的旧消息不单独触发。'
        if participation['applicable']:constraint+='\n程序动态参与筛选结果见participation_state；允许参与也不代表必须回复，不允许时不主动插话。主人消息必回不受限制。'
        return {'batch_id':b['id'],'messages':new,'reply_constraint':{'must_reply':mandatory_reply(new),'owner_qq':OWNER},'participation_state':participation,'internal_wake':internal,'internal_wake_active':wakeups.valid(internal,m) if internal else None,'incoming_images':references(new),'short_term_context':load_layers({'scene':{'platform':'qq','account':b['account'],'chat_type':b['kind'],'conversation':b['conversation']},'new_messages':new})['近期记忆'],'previous_own_replies':sent_context(b,new),'judgment_guidance':constraint+'\n'+(guide.read_text('utf8') if guide.exists() else '')}
    if action=='query':
        limit=max(1,min(30,int(a.get('limit',10))))
        q=a.get('query','')
        result=[];events=[]
        if PARTITION.exists():
            part=sqlite3.connect(PARTITION.resolve().as_uri()+'?mode=ro',uri=True)
            try:
                selected=part.execute('SELECT event_key,raw_json FROM long_entries WHERE account_id=? AND chat_type=? AND conversation_id=? AND instr(raw_json,?)>0 ORDER BY event_time DESC LIMIT ?',(b['account'],b['kind'],b['conversation'],q,limit)).fetchall()
                for key,raw in selected:
                    s.execute('INSERT OR IGNORE INTO retrieved_events VALUES(?,?)',(b['id'],key));events.append({'event_key':key,'observation':json.loads(raw),'message_context':render(json.loads(raw))})
                    if key.startswith('input:') and int(key[6:])<=max(ids,default=m.execute("SELECT coalesce(max(id),0) FROM observations").fetchone()[0]):
                        source=rows('SELECT * FROM observations WHERE id=?',(int(key[6:]),))
                        result.extend(source)
            finally:part.close()
        for r in result:s.execute('INSERT OR IGNORE INTO retrieved VALUES(?,?)',(b['id'],r['id']))
        s.commit();return {'messages':messages(list(reversed(result))),'long_term_events':list(reversed(events)),'search':'Only evicted long-term records; recent context is in read_new_messages.'}
    if action=='finish':
        if not isinstance(a.get('should_reply'),bool):raise ValueError('should_reply must be boolean')
        if not a.get('reason'):raise ValueError('A short decision reason is required')
        related=a.get('related_ids',[])
        permitted={r[0] for r in s.execute('SELECT observation_id FROM retrieved WHERE batch_id=?',(b['id'],))}
        if any(i not in permitted for i in related):raise ValueError('Memory references must have been retrieved for this conversation')
        new=current_messages()
        participation=behavior.participation(s,new,{'account':b['account'],'chat_type':b['kind'],'conversation':b['conversation']},b['id'],MOUTH_DB,OWNER)
        if not participation['allowed'] and not mandatory_reply(new):a=dict(a,should_reply=False,reason='程序动态参与筛选或冷却未通过。原判断：'+a['reason'])
        if internal and not wakeups.valid(internal,m):a=dict(a,should_reply=False,reason='内部意愿因会话新活动失效。')
        if mandatory_reply(new) and not a['should_reply']:
            a=dict(a,should_reply=True,reason='用户硬性规则：新消息含QQ号3231181613，必须回复。原判断：'+a['reason'])
        context=messages(rows('SELECT * FROM observations WHERE id IN ('+','.join('?' for _ in related)+') ORDER BY id',related)) if related else []
        payload={'batch_id':b['id'],'scene':{'platform':'qq','account':b['account'],'chat_type':b['kind'],'conversation':b['conversation']},'new_messages':new,'related_memories':context,'status':'awaiting_mouth' if a['should_reply'] else 'no_reply'}
        event_keys=a.get('related_event_keys',[])
        allowed_keys={r[0] for r in s.execute('SELECT event_key FROM retrieved_events WHERE batch_id=?',(b['id'],))}
        if any(k not in allowed_keys for k in event_keys):raise ValueError('Unretrieved long-term event')
        if internal:payload.update(internal_wake=internal,wake_depth=1)
        payload['related_event_keys']=event_keys
        payload['recent_bot_replies']=sent_context(b,new)
        payload['incoming_images']=references(new+context)
        with s:
            s.execute('INSERT INTO decisions VALUES(?,?,?,?,?)',(b['id'],time.time(),int(a['should_reply']),a['reason'],json.dumps(payload,ensure_ascii=False)))
            s.execute('UPDATE batches SET done=1 WHERE id=?',(b['id'],))
        if internal:wakeups.complete(internal)
        return {'ok':True,'status':payload['status'],'sent_to_qq':False,'mouth_mode':'text_api_with_qq_delivery'}
    raise ValueError('Unknown action')

if __name__=='__main__':
    try:print(json.dumps(run(sys.argv[1],json.load(sys.stdin)),ensure_ascii=False))
    except Exception as e:print(json.dumps({'error':str(e)},ensure_ascii=False));sys.exit(1)
