"""Configurable, per-conversation scheduling for exactly one brain session."""
import json,sqlite3,time,uuid
from datetime import datetime
from pathlib import Path
DEFAULTS={'private_coalesce_seconds':1,'group_coalesce_seconds':8,'group_max_wait_seconds':45,'max_private_batches_before_group':3,'private_max_batch':12,'group_max_batch':80,'private_max_chars':12000,'group_max_chars':24000,'ingest_chunk':2000,'private_quiet_seconds':0,'group_quiet_seconds':0,'private_coalesce_cap_seconds':3,'group_coalesce_cap_seconds':12}

def config(path):
    result=dict(DEFAULTS)
    if Path(path).exists():
        supplied=json.loads(Path(path).read_text('utf8'))
        if not isinstance(supplied,dict) or set(supplied)-set(DEFAULTS):raise ValueError('Unknown scheduler configuration')
        result.update(supplied)
    for key,value in result.items():
        if type(value) not in (int,float) or not 0<=value<=100000:raise ValueError('Invalid scheduler configuration')
        if key.endswith(('_batch','_chars')) or key in ('ingest_chunk','max_private_batches_before_group'):
            if type(value) is not int or value<1:raise ValueError('Invalid scheduler integer')
    return result

def initialize(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS scheduler_state(id INTEGER PRIMARY KEY CHECK(id=1),last_ingested INTEGER NOT NULL,private_streak INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS scheduler_claims(observation_id INTEGER PRIMARY KEY,batch_id TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS scheduler_queue(observation_id INTEGER PRIMARY KEY,account TEXT,kind TEXT,conversation TEXT,received REAL,characters INTEGER,batch_id TEXT);
    CREATE INDEX IF NOT EXISTS scheduler_pending ON scheduler_queue(batch_id,account,kind,conversation,observation_id);
    CREATE TABLE IF NOT EXISTS scheduler_internal_queue(id TEXT PRIMARY KEY,due REAL,account TEXT,kind TEXT,conversation TEXT,event TEXT,status TEXT DEFAULT 'pending',batch_id TEXT);
    CREATE TABLE IF NOT EXISTS internal_wakes(batch_id TEXT PRIMARY KEY,wake_id TEXT,event TEXT);
    CREATE TABLE IF NOT EXISTS scheduler_conversations(account TEXT,kind TEXT,conversation TEXT,last_served INTEGER NOT NULL,PRIMARY KEY(account,kind,conversation));''')
    if not db.execute('SELECT 1 FROM scheduler_state').fetchone():
        with db:
            # Import exact historical assignments, not max(id): priority dispatch is nonmonotonic.
            last_kind=None
            for seq,bid,ids,account,kind,conv in db.execute('SELECT rowid,id,ids,account,kind,conversation FROM batches ORDER BY rowid').fetchall():
                db.executemany('INSERT OR IGNORE INTO scheduler_claims VALUES(?,?)',[(i,bid) for i in json.loads(ids)])
                db.execute('INSERT INTO scheduler_conversations VALUES(?,?,?,?) ON CONFLICT(account,kind,conversation) DO UPDATE SET last_served=excluded.last_served',(account,kind,conv,seq));last_kind=kind
            db.execute('INSERT INTO scheduler_state VALUES(1,0,?)',(1 if last_kind=='private' else 0,))

def ingest(db,source,cfg,now):
    cursor=db.execute('SELECT last_ingested FROM scheduler_state').fetchone()[0]
    columns={r[1] for r in source.execute('PRAGMA table_info(observations)')}
    stamp='received_at' if 'received_at' in columns else 'NULL'
    head=source.execute('SELECT coalesce(max(id),0) FROM observations').fetchone()[0]
    if head<cursor:raise ValueError('Observation archive moved backwards')
    while cursor<head:
        rows=source.execute(f'SELECT id,account_id,chat_type,conversation_id,{stamp},length(raw_json) FROM observations WHERE id>? AND id<=? ORDER BY id LIMIT ?',(cursor,head,cfg['ingest_chunk'])).fetchall()
        if not rows:raise ValueError('Archive cursor could not advance')
        with db:
            for oid,account,kind,conv,received,chars in rows:
                if db.execute('SELECT 1 FROM scheduler_claims WHERE observation_id=?',(oid,)).fetchone():continue
                try:arrival=min(now,datetime.fromisoformat(received.replace('Z','+00:00')).timestamp())
                except (ValueError,AttributeError):arrival=now
                db.execute('INSERT OR IGNORE INTO scheduler_queue VALUES(?,?,?,?,?,?,NULL)',(oid,str(account),kind,str(conv),arrival,chars or 0))
            cursor=rows[-1][0];db.execute('UPDATE scheduler_state SET last_ingested=? WHERE id=1',(cursor,))

def poll(db,source,cfg,now=None):
    now=time.time() if now is None else now;initialize(db);ingest(db,source,cfg,now)
    db.execute('BEGIN IMMEDIATE')
    try:
        active=db.execute('SELECT * FROM batches WHERE done=0 ORDER BY rowid LIMIT 1').fetchone()
        if active:db.commit();return active
        candidates=[]
        for account,kind,conv,oldest,newest,count,first_id,chars in db.execute('SELECT account,kind,conversation,min(received),max(received),count(*),min(observation_id),sum(characters) FROM scheduler_queue WHERE batch_id IS NULL GROUP BY account,kind,conversation').fetchall():
            if kind not in ('private','group'):continue
            last=db.execute('SELECT last_served FROM scheduler_conversations WHERE account=? AND kind=? AND conversation=?',(account,kind,conv)).fetchone()
            if ((now-oldest>=cfg[kind+'_coalesce_seconds'] and now-newest>=cfg[kind+'_quiet_seconds']) or now-oldest>=max(cfg[kind+'_coalesce_seconds'],cfg[kind+'_coalesce_cap_seconds'])) or count>=cfg[kind+'_max_batch'] or chars>=cfg[kind+'_max_chars']:
                candidates.append({'scope':(account,kind,conv),'oldest':oldest,'first':first_id,'last':last[0] if last else 0})
        for task in db.execute("SELECT id,due,account,kind,conversation,event FROM scheduler_internal_queue WHERE status='pending' AND due<=? ORDER BY due,id",(now,)).fetchall():
            key,due,account,kind,conv,event=task
            candidates.append({'scope':(account,kind,conv),'oldest':due,'first':0,'last':0,'internal':key,'event':event})
        private=sorted([c for c in candidates if c.get('internal') or c['scope'][1]=='private'],key=lambda c:(c['oldest'],c['first']))
        groups=sorted([c for c in candidates if c['scope'][1]=='group' and not c.get('internal')],key=lambda c:(c['last'],c['oldest'],c['first']))
        streak=db.execute('SELECT private_streak FROM scheduler_state').fetchone()[0]
        urgent=any(now-c['oldest']>=cfg['group_max_wait_seconds'] for c in groups)
        choose_group=groups and (not private or streak>=cfg['max_private_batches_before_group'] or (urgent and streak>0))
        overdue=[c for c in groups if now-c['oldest']>=cfg['group_max_wait_seconds']]
        selected=((overdue[0] if private and urgent and overdue else groups[0]) if choose_group else private[0] if private else None)
        if not selected:db.commit();return None
        account,kind,conv=selected['scope']
        if selected.get('internal'):
            bid='wake:'+selected['internal']
            db.execute('INSERT INTO batches(id,ids,account,kind,conversation) VALUES(?,?,?,?,?)',(bid,'[]',account,kind,conv))
            db.execute('INSERT OR IGNORE INTO internal_wakes VALUES(?,?,?)',(bid,selected['internal'],selected['event']))
            db.execute("UPDATE scheduler_internal_queue SET status='dispatched',batch_id=? WHERE id=?",(bid,selected['internal']))
            db.execute('UPDATE scheduler_state SET private_streak=? WHERE id=1',(streak+1,))
            db.commit();return db.execute('SELECT * FROM batches WHERE id=?',(bid,)).fetchone()
        ids=[];characters=0
        for oid,size in db.execute('SELECT observation_id,characters FROM scheduler_queue WHERE account=? AND kind=? AND conversation=? AND batch_id IS NULL ORDER BY observation_id LIMIT ?',(*selected['scope'],cfg[kind+'_max_batch'])).fetchall():
            if ids and characters+size>cfg[kind+'_max_chars']:break
            ids.append(oid);characters+=size
        # One oversized observation is handled alone, never truncated or silently skipped.
        bid=str(uuid.uuid4());seq=db.execute('INSERT INTO batches(id,ids,account,kind,conversation) VALUES(?,?,?,?,?)',(bid,json.dumps(ids),account,kind,conv)).lastrowid
        for oid in ids:
            db.execute('INSERT INTO scheduler_claims VALUES(?,?)',(oid,bid))
            db.execute('UPDATE scheduler_queue SET batch_id=? WHERE observation_id=?',(bid,oid))
        db.execute('INSERT INTO scheduler_conversations VALUES(?,?,?,?) ON CONFLICT(account,kind,conversation) DO UPDATE SET last_served=excluded.last_served',(account,kind,conv,seq))
        db.execute('UPDATE scheduler_state SET private_streak=? WHERE id=1',(0 if kind=='group' else streak+1,))
        db.commit();return db.execute('SELECT * FROM batches WHERE id=?',(bid,)).fetchone()
    except Exception:db.rollback();raise

def stats(db,now=None):
    now=time.time() if now is None else now
    waiting=[{'account':a,'chat_type':k,'conversation':c,'count':n,'oldest_wait_seconds':round(max(0,now-t),2)} for a,k,c,n,t in db.execute('SELECT account,kind,conversation,count(*),min(received) FROM scheduler_queue WHERE batch_id IS NULL GROUP BY account,kind,conversation')]
    internal=[{'wake_id':r[0],'due':r[1],'priority':'private','chat_type':r[2],'conversation':r[3]} for r in db.execute("SELECT id,due,kind,conversation FROM scheduler_internal_queue WHERE status='pending' ORDER BY due,id")]
    return {'waiting':waiting,'internal_waiting':internal,'active_batches':db.execute('SELECT count(*) FROM batches WHERE done=0').fetchone()[0],'last_ingested':db.execute('SELECT last_ingested FROM scheduler_state').fetchone()[0]}
