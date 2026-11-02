"""Durable global admission for timed, idle and interest activity. No model calls."""
import json,os,sqlite3,time
from pathlib import Path
from contextlib import closing

DEFAULT={'enabled':False,'cooldown_seconds':600,'idle_enabled':True,'interest_enabled':True,
         'interest_min_seconds':3000,'interest_max_seconds':4200,'failure_retry_seconds':600}

def config(root):
    p=Path(root)/'active-system-config.json'
    return DEFAULT | (json.loads(p.read_text('utf8')) if p.exists() else {})

def category(packet):
    if not packet.get('internal_wake'):return None
    if packet.get('active_type'):return packet['active_type']
    kind=packet['internal_wake'].get('kind')
    if kind in ('bili_notification','heybox_notification','post_activity'):return None
    if kind=='autonomous':return 'interest'
    if kind=='proactive':return 'idle'
    return 'timed'

def _conversation_deadline(root,scene,exclude=None):
    """Confirmed proactive delivery to this logical conversation, never preview target."""
    if not scene or scene.get('chat_type')=='autonomous':return 0
    path=Path(root)/'mouth.sqlite3'
    if not path.exists():return 0
    with closing(sqlite3.connect(path)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='relay_deliveries'").fetchone():return 0
        last=db.execute("""SELECT max(d.updated) FROM relay_deliveries d JOIN jobs j ON j.batch_id=d.batch_id
            WHERE d.status='sent' AND d.batch_id!=? AND d.record_json IS NOT NULL
            AND json_extract(j.packet,'$.internal_wake') IS NOT NULL
            AND (coalesce(json_extract(j.packet,'$.internal_wake.kind'),'') NOT IN ('bili_notification','heybox_notification','post_activity') OR json_extract(j.packet,'$.active_type')='posting')
            AND cast(json_extract(d.record_json,'$.platform') AS TEXT)=?
            AND cast(json_extract(d.record_json,'$.account_id') AS TEXT)=?
            AND cast(json_extract(d.record_json,'$.chat_type') AS TEXT)=?
            AND cast(json_extract(d.record_json,'$.conversation_id') AS TEXT)=?""",
            (exclude or '',str(scene['platform']),str(scene['account']),str(scene['chat_type']),str(scene['conversation']))).fetchone()[0]
    return (last+config(root)['cooldown_seconds']) if last else 0

def cooldown_deadline(root,scene,exclude=None):
    if not scene or scene.get('chat_type')!='private':return _conversation_deadline(root,scene,exclude)
    if __package__:
        from .identity_idle import cooldown_scenes
    else:
        from identity_idle import cooldown_scenes
    return max((_conversation_deadline(root,s,exclude) for s in cooldown_scenes(root,scene)),default=0)

def destination_deadline(root,packet,destination):
    if not category(packet):return 0
    scene={'platform':destination['platform'],'account':destination['account'],
           'chat_type':destination['chat_type'],'conversation':destination['id']}
    return cooldown_deadline(root,scene,packet['batch_id'])

def release_delivery_wait(root,batch):
    # A cold destination cannot occupy the global generation slot for ten minutes.
    with closing(sqlite3.connect(Path(root)/'active-system.sqlite3',timeout=20)) as db,db:
        db.execute("UPDATE admissions SET phase='delivering' WHERE batch=? AND phase IN ('running','delivering')",(batch,))
        db.execute('UPDATE control SET running=NULL,owner_pid=NULL WHERE running=?',(batch,))

def ready(root,batch,default=True,now=None):
    p=Path(root)/'active-system.sqlite3';now=time.time() if now is None else now
    if not p.exists():return default
    with closing(sqlite3.connect(p)) as db:
        row=db.execute('SELECT phase,due FROM admissions WHERE batch=?',(batch,)).fetchone()
    return default if row is None else row[0]=='deferred' and row[1]<=now

class Gate:
    def __init__(self,root,roster=None):
        from active_roster import Roster
        self.roster=roster if roster is not None else Roster(root=root)
        self.root=Path(root);self.path=self.root/'active-system.sqlite3'
        with closing(self.connect()) as db,db:
            db.executescript('''PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS control(id INTEGER PRIMARY KEY,last_start REAL,running TEXT,owner_pid INTEGER,touched REAL);
            INSERT OR IGNORE INTO control(id,last_start,running,owner_pid,touched) VALUES(1,-1000000000,NULL,NULL,0);
            CREATE TABLE IF NOT EXISTS admissions(batch TEXT PRIMARY KEY,phase TEXT,due REAL,started REAL,updated REAL,error TEXT);
            ''')

            if 'enqueued' not in {r[1] for r in db.execute('PRAGMA table_info(admissions)')}:
                db.execute('ALTER TABLE admissions ADD COLUMN enqueued REAL')
                db.execute('UPDATE admissions SET enqueued=coalesce(started,updated)')
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='active_tasks'").fetchone():
                    db.execute('UPDATE admissions SET enqueued=coalesce((SELECT created FROM active_tasks WHERE id=batch),enqueued)')
                    db.execute("UPDATE admissions SET due=min(due,max(?,(SELECT last_start FROM control WHERE id=1)+?)) WHERE phase='deferred' AND batch IN (SELECT id FROM active_tasks WHERE status='queued')",(time.time(),config(self.root)['cooldown_seconds']))

            if 'last_sent' not in {r[1] for r in db.execute('PRAGMA table_info(control)')}:
                db.execute('ALTER TABLE control ADD COLUMN last_sent REAL DEFAULT -1000000000')
                mouth=self.root/'mouth.sqlite3'
                if mouth.exists():
                    with closing(sqlite3.connect(mouth)) as receipts:
                        if receipts.execute("SELECT 1 FROM sqlite_master WHERE name='relay_deliveries'").fetchone():
                            latest=receipts.execute("SELECT max(updated) FROM relay_deliveries WHERE status='sent' AND batch_id IN (SELECT batch_id FROM outputs WHERE batch_id LIKE 'active:%' OR batch_id LIKE 'wake:%' OR batch_id LIKE 'autonomous:%')").fetchone()[0]
                            if latest:db.execute('UPDATE control SET last_sent=? WHERE id=1',(latest,))
                db.execute("UPDATE admissions SET due=max(?,(SELECT last_sent FROM control WHERE id=1)+?) WHERE phase='deferred' AND coalesce(error,'')!='summary_retry'",(time.time(),config(self.root)['cooldown_seconds']))

            db.execute('CREATE TABLE IF NOT EXISTS gate_meta(key TEXT PRIMARY KEY,value TEXT)')
            if not db.execute("SELECT 1 FROM gate_meta WHERE key='conversation_cooldown_v1'").fetchone():
                # Remove only the inherited global gate delay. Summary retries retain their own deadline.
                db.execute("UPDATE admissions SET due=? WHERE phase='deferred' AND coalesce(error,'')!='summary_retry'",(time.time(),))
                db.execute("INSERT INTO gate_meta VALUES('conversation_cooldown_v1','1')")

    def connect(self):
        db=sqlite3.connect(self.path,timeout=20);db.row_factory=sqlite3.Row;return db

    def claim(self,packet,now=None):
        now=time.time() if now is None else now;cfg=config(self.root)
        if not cfg['enabled'] or not category(packet):return True
        batch=packet['batch_id']
        if packet.get('active_type')=='idle' and packet.get('scene') and self.roster(packet['scene']) is not True:
            self.postpone(packet,seconds=60,now=now)
            return False
        with closing(self.connect()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT * FROM admissions WHERE batch=?',(batch,)).fetchone()
            if old and (old['phase']!='deferred' or old['due']>now):return False
            control=db.execute('SELECT * FROM control WHERE id=1').fetchone()
            enqueued=old['enqueued'] if old and old['enqueued'] is not None else now
            earlier=db.execute("SELECT batch FROM admissions WHERE phase='deferred' AND due<=? AND batch!=? AND (enqueued<? OR (enqueued=? AND rowid<(SELECT rowid FROM admissions WHERE batch=?))) ORDER BY enqueued,rowid LIMIT 1",(now,batch,enqueued,enqueued,batch)).fetchone()
            if old is None:
                earlier=db.execute("SELECT batch FROM admissions WHERE phase='deferred' AND due<=? ORDER BY enqueued,rowid LIMIT 1",(now,)).fetchone()
            deadline=cooldown_deadline(self.root,packet.get('scene'),batch) if category(packet)!='interest' else 0
            if control['running'] or now<deadline or earlier:
                db.execute("INSERT INTO admissions(batch,phase,due,started,updated,error) VALUES(?,'deferred',?,NULL,?,NULL) ON CONFLICT(batch) DO UPDATE SET due=excluded.due,updated=excluded.updated",(batch,max(now+1,deadline),now))
                db.execute('UPDATE admissions SET enqueued=coalesce(enqueued,?) WHERE batch=?',(enqueued,batch))
                return False
            db.execute("INSERT INTO admissions(batch,phase,due,started,updated,error) VALUES(?,'running',?,?,?,NULL) ON CONFLICT(batch) DO UPDATE SET phase='running',due=excluded.due,started=excluded.started,updated=excluded.updated,error=NULL",(batch,now,now,now))
            db.execute('UPDATE admissions SET enqueued=coalesce(enqueued,?) WHERE batch=?',(enqueued,batch))
            db.execute('UPDATE control SET last_start=?,running=?,owner_pid=?,touched=? WHERE id=1',(now,batch,os.getpid(),now))
        return True

    def generation_done(self,packet,committed,now=None):
        if not config(self.root)['enabled'] or not category(packet):return
        now=time.time() if now is None else now
        with closing(self.connect()) as db,db:
            if committed:
                db.execute("UPDATE admissions SET phase='delivering',updated=? WHERE batch=? AND phase='running'",(now,packet['batch_id']))
            else:
                db.execute("UPDATE admissions SET phase='failed',error='generation_not_completed',updated=? WHERE batch=? AND phase='running'",(now,packet['batch_id']))
                db.execute('UPDATE control SET running=NULL,owner_pid=NULL WHERE running=?',(packet['batch_id'],))

    def postpone(self,packet,seconds=60,now=None):
        now=time.time() if now is None else now
        with closing(self.connect()) as db,db:
            row=db.execute('SELECT phase FROM admissions WHERE batch=?',(packet['batch_id'],)).fetchone()
            if row and row['phase']!='deferred':return
            db.execute("INSERT INTO admissions(batch,phase,due,started,updated,error) VALUES(?,'deferred',?,NULL,?,NULL) ON CONFLICT(batch) DO UPDATE SET due=excluded.due,updated=excluded.updated",(packet['batch_id'],now+seconds,now))
            db.execute('UPDATE admissions SET enqueued=coalesce(enqueued,?) WHERE batch=?',(now,packet['batch_id']))

    def reconcile(self,live=None,now=None):
        """Hold the one active slot through generation and delivery; recover after restart."""
        now=time.time() if now is None else now;live=live or {}
        # A cancelled source must not remain the oldest deferred job and starve all others.
        import importlib
        wake=importlib.import_module((__package__+'.idle_wake') if __package__ else 'idle_wake')
        with closing(self.connect()) as db:
            pending=[r['batch'] for r in db.execute("SELECT batch FROM admissions WHERE phase='deferred'")]
        for batch in pending:
            wid=batch[5:] if batch.startswith('wake:') else batch
            if not (batch.startswith(('active:','autonomous:','wake:'))):continue
            # Automatic idle/interest jobs are retained across sleep by their own clocks.
            if batch.startswith('active:'):
                from_module=importlib.import_module((__package__+'.active_system') if __package__ else 'active_system')
                if not from_module.awake(self.root,now):continue
            try:source_ok=wake.valid(self.root,wid)
            except sqlite3.OperationalError:continue
            if not source_ok:
                with closing(self.connect()) as db,db:db.execute("UPDATE admissions SET phase='cancelled',updated=? WHERE batch=? AND phase='deferred'",(now,batch))
        with closing(self.connect()) as db,db:
            for delivery in db.execute("SELECT batch FROM admissions WHERE phase='delivering'").fetchall():
                batch=delivery['batch'];outcome=delivery_outcome(self.root,batch)
                if not outcome:continue
                ok,stamp,error=outcome
                db.execute('UPDATE admissions SET phase=?,updated=?,error=? WHERE batch=?',('done' if ok else 'failed',stamp,error,batch))
                db.execute('UPDATE control SET running=NULL,owner_pid=NULL,touched=? WHERE running=?',(now,batch))
            row=db.execute('SELECT * FROM control WHERE id=1').fetchone()
            if not row['running']:return
            batch=row['running'];task=live.get(batch)
            if task is not None and not task.done():
                db.execute('UPDATE control SET touched=? WHERE id=1',(now,));return
            outcome=delivery_outcome(self.root,batch)
            if outcome:
                ok,stamp,error=outcome
                db.execute('UPDATE admissions SET phase=?,updated=?,error=? WHERE batch=?',('done' if ok else 'failed',stamp,error,batch))
                db.execute('UPDATE control SET running=NULL,owner_pid=NULL,touched=? WHERE id=1',(now,));return
            with closing(sqlite3.connect(self.root/'mouth.sqlite3')) as mouth:
                generated=mouth.execute('SELECT 1 FROM outputs WHERE batch_id=?',(batch,)).fetchone()
            if not generated and (row['owner_pid']!=os.getpid() or now-row['touched']>300):
                db.execute("UPDATE admissions SET phase='deferred',due=?,updated=?,error='recovered_incomplete_generation' WHERE batch=?",(now+config(self.root)['cooldown_seconds'],now,batch))
                db.execute('UPDATE control SET running=NULL,owner_pid=NULL WHERE id=1')

def delivery_outcome(root,batch):
    p=Path(root)/'mouth.sqlite3'
    if not p.exists():return None
    with closing(sqlite3.connect(p)) as db:
        row=db.execute('SELECT o.text,o.created,j.packet FROM outputs o JOIN jobs j ON j.batch_id=o.batch_id WHERE o.batch_id=?',(batch,)).fetchone()
        if not row:return None
        packet=json.loads(row[2])
        if packet.get('output_protocol_error'):return False,row[1],'invalid_submission'
        if row[0].strip()=='【保持沉默】':return True,row[1],None
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='relay_jobs'").fetchone():return None
        relay=db.execute('SELECT status FROM relay_jobs WHERE batch_id=?',(batch,)).fetchone()
        if not relay:return None
        if relay[0] in ('delivered','control_only'):
            stamp=db.execute("SELECT max(updated) FROM relay_deliveries WHERE batch_id=? AND status='sent'",(batch,)).fetchone()[0]
            return True,stamp or row[1],None
        if relay[0] in ('partial','delivery_failed','blocked','failed'):
            return False,time.time(),'delivery_'+relay[0]
    return None
