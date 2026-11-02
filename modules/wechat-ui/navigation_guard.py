"""Durable navigation barriers; manual resume only, with receipt checks."""
import hashlib,json,sqlite3,time,uuid
from pathlib import Path


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS navigation_pauses(target TEXT PRIMARY KEY,name TEXT,roomid TEXT,kind TEXT,error TEXT,created REAL,notice TEXT)')
    db.commit()


def target(payload):
    return payload.get('roomid') or payload['chat_type']+':'+payload['conversation']


def blocked(db,payload):
    initialize(db)
    return db.execute('SELECT error FROM navigation_pauses WHERE target=?',(target(payload),)).fetchone()


def defer_blocked(db,key,payload):
    barrier=blocked(db,payload)
    if not barrier:return False
    with db:db.execute("UPDATE jobs SET status='paused_navigation',phase='paused_navigation',error=?,updated=? WHERE id=? AND status='queued'",(barrier['error'],time.time(),key))
    return True


def failed_before_input(db,key,receipts):
    phase=db.execute('SELECT phase FROM jobs WHERE id=?',(key,)).fetchone()[0]
    return phase=='navigating' and not any(r['state'] in ('sent','sending','uncertain') for r in receipts)


def pause(db,payload,error):
    initialize(db);notice='wechat-navigation:'+uuid.uuid4().hex
    with db:
        added=db.execute('INSERT OR IGNORE INTO navigation_pauses VALUES(?,?,?,?,?,?,?)',
             (target(payload),payload['conversation'],payload.get('roomid'),payload['chat_type'],str(error)[:500],time.time(),notice)).rowcount
    if added:
        try:
            import sys
            sys.path.insert(0,'/opt/astrbot-mouth')
            from transport_alerts import emit
            emit('/opt/astrbot-mouth',notice,payload['conversation'])
        except Exception as exc:
            print(json.dumps({'event':'navigation_alert_failed','error':type(exc).__name__}),flush=True)


def pause_followers(db,payload,error):
    """Never touch already-sent, sending, uncertain or partially-sent jobs."""
    for row in db.execute("SELECT id,payload FROM jobs WHERE status='queued'").fetchall():
        if target(json.loads(row['payload']))!=target(payload):continue
        with db:db.execute("UPDATE jobs SET status='paused_navigation',phase='paused_navigation',error=?,updated=? WHERE id=? AND status='queued'",(str(error)[:500],time.time(),row['id']))


def resume(db,key,mouth_path=Path('/opt/astrbot-mouth/mouth.sqlite3')):
    """Administrative operation. Jobs re-run strict navigation before any input."""
    initialize(db);pause_row=db.execute('SELECT target,roomid,kind FROM navigation_pauses WHERE target=?',(key,)).fetchone()
    if not pause_row:raise ValueError('Target is not paused')
    safe=[]
    for row in db.execute("SELECT id,payload FROM jobs WHERE status='paused_navigation'").fetchall():
        if target(json.loads(row['payload']))!=key:continue
        if db.execute("SELECT 1 FROM messages WHERE job=? AND state IN ('sent','sending','uncertain')",(row['id'],)).fetchone():
            raise ValueError('Receipt reconciliation required before resume')
        safe.append(row['id'])
    with db:
        db.execute('DELETE FROM navigation_pauses WHERE target=?',(key,))
        for job in safe:
            db.execute('DELETE FROM messages WHERE job=?',(job,))
            db.execute("UPDATE jobs SET status=?,phase='queued',cancel=0,error=NULL,updated=? WHERE id=?",('retryable' if job.startswith('relay-') else 'queued',time.time(),job))
    restored=0
    if mouth_path.exists() and pause_row['roomid']:
        with sqlite3.connect(mouth_path,timeout=20) as m:
            for did, in m.execute("SELECT id FROM relay_deliveries WHERE status='paused_navigation' AND json_extract(destination,'$.platform')='wechat' AND json_extract(destination,'$.id')=? AND json_extract(destination,'$.chat_type')=?",(pause_row['roomid'],pause_row['kind'])).fetchall():
                ui='relay-'+hashlib.sha256(did.encode()).hexdigest()
                row=db.execute('SELECT status FROM jobs WHERE id=?',(ui,)).fetchone()
                if row and row['status'] not in ('retryable','queued','sent'):continue
                m.execute("UPDATE relay_deliveries SET status='pending',error=NULL,updated=? WHERE id=?",(time.time(),did));restored+=1
    return {'ui_jobs_restored':len(safe),'relay_deliveries_restored':restored}


if __name__=='__main__':
    import argparse
    from unified_sender import connect
    parser=argparse.ArgumentParser();parser.add_argument('--resume');args=parser.parse_args()
    with connect() as db:
        initialize(db)
        if args.resume:print(json.dumps(resume(db,args.resume)))
        else:print(json.dumps([dict(r) for r in db.execute('SELECT * FROM navigation_pauses')],ensure_ascii=False))
