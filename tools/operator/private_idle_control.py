"""Operator switch for other private idle triggers. Never invokes a model or sends."""
import argparse,fcntl,json,sqlite3,time
from pathlib import Path
from group_input_control import replace,service
from private_idle_policy import FLAG,enabled

ROOT=Path('/opt/astrbot-mouth')

def status(root=ROOT):
    cfg=json.loads((root/'active-system-config.json').read_text('utf8'))
    return {'other_private_idle_enabled':bool(cfg.get(FLAG,True)),
            'owner_qq_private_preserved':True}

def set_enabled(value,root=ROOT,control=service):
    with (root/'.private-idle-control.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        path=root/'active-system-config.json';raw=path.read_bytes();stat=path.stat();cfg=json.loads(raw)
        changed=bool(cfg.get(FLAG,True))!=value
        cfg[FLAG]=value;was_active=control('active');stopped=False;cancelled=[]
        try:
            if changed:
                backup=root/'operator-backups'/'private-idle'/str(time.time_ns())
                backup.mkdir(parents=True,mode=0o700)
                copy=backup/path.name;copy.write_bytes(raw);copy.chmod(0o600)
                # Stop generation before invalidating already admitted idle jobs.
                if was_active:control('stop');stopped=True
                replace(path,(json.dumps(cfg,ensure_ascii=False,indent=2)+'\n').encode(),stat)
            dbpath=root/'active-system.sqlite3'
            if dbpath.exists():
                from active_system import interval
                from wakefulness import after_awake
                now=time.time();offcfg={**cfg,FLAG:False}
                with sqlite3.connect(dbpath,timeout=30) as db:
                    if not value:
                        for batch,scene in db.execute("SELECT id,scene FROM active_tasks WHERE kind='idle' AND status='queued'").fetchall():
                            if enabled(cfg,json.loads(scene),root):continue
                            cancelled.append(batch)
                            db.execute("UPDATE active_tasks SET status='cancelled',finished=?,error='other_private_idle_disabled_by_operator' WHERE id=?",(now,batch))
                            db.execute("UPDATE admissions SET phase='cancelled',updated=?,error='other_private_idle_disabled_by_operator' WHERE batch=?",(now,batch))
                            db.execute('UPDATE control SET running=NULL,owner_pid=NULL WHERE running=?',(batch,))
                            db.execute('UPDATE idle_sessions SET inflight=NULL WHERE inflight=?',(batch,))
                            db.execute('UPDATE identity_idle_clocks SET inflight=NULL WHERE inflight=?',(batch,))
                    if changed:
                        # Resume from a fresh delay, instead of releasing every overdue session at once.
                        for scope,scene,level in db.execute('SELECT scope,scene,level FROM idle_sessions').fetchall():
                            scene=json.loads(scene)
                            if not enabled(offcfg,scene,root):
                                db.execute('UPDATE idle_sessions SET due=? WHERE scope=?',(after_awake(root,now,interval(level,scene)),scope))
                        for key,scene,level in db.execute('SELECT key,scene,level FROM identity_idle_clocks').fetchall():
                            scene=json.loads(scene)
                            if not enabled(offcfg,scene,root):
                                db.execute('UPDATE identity_idle_clocks SET due=? WHERE key=?',(after_awake(root,now,interval(level,scene)),key))
            if cancelled and (root/'mouth.sqlite3').exists():
                with sqlite3.connect(root/'mouth.sqlite3',timeout=30) as db:
                    for batch in cancelled:
                        db.execute("UPDATE relay_deliveries SET status='failed',updated=?,error='Other private idle triggers disabled by operator' WHERE batch_id=? AND status='pending'",(time.time(),batch))
                        db.execute("UPDATE relay_jobs SET status='blocked',error='Other private idle triggers disabled by operator' WHERE batch_id=? AND status IN ('pending','planned')",(batch,))
        except Exception:
            replace(path,raw,stat)
            raise
        finally:
            if stopped:control('start')
        return {**status(root),'changed':changed,'cancelled_tasks':len(cancelled),'service_was_active':was_active}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['on','off','status'])
    action=parser.parse_args().action
    print(json.dumps(status() if action=='status' else set_enabled(action=='on'),ensure_ascii=False))

if __name__=='__main__':main()
