"""Operator-only group ingress switch. No model calls or message sends."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
import uuid

ROOT=Path('/opt/astrbot-mouth')
UNIT='astrbot-unified'

def service(action):
    if action=='active':
        return subprocess.run(['systemctl','is-active','--quiet',UNIT]).returncode==0
    subprocess.run(['systemctl',action,UNIT],check=True)

def status(root=ROOT):
    social=json.loads((root/'social-config.json').read_text())
    wechat=json.loads((root/'wechat-config.json').read_text())
    return {'qq':bool(social.get('group_preview',False)),
            'wechat':bool(wechat.get('group_input_enabled',False))}

def replace(path,raw,stat):
    temp=path.with_name(path.name+'.group-'+uuid.uuid4().hex)
    try:
        with temp.open('wb') as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.chown(temp,stat.st_uid,stat.st_gid)
        os.chmod(temp,stat.st_mode & 0o777)
        os.replace(temp,path)
    finally:temp.unlink(missing_ok=True)

def set_enabled(enabled,root=ROOT,control=service):
    with (root/'.group-input-control.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        originals={};updates={}
        for name in ('social-config.json','wechat-config.json'):
            path=root/name;raw=path.read_bytes();originals[path]=(raw,path.stat())
            cfg=json.loads(raw);cfg['group_input_enabled']=enabled
            if name=='social-config.json':cfg['group_preview']=enabled
            updates[path]=(json.dumps(cfg,ensure_ascii=False,indent=2)+'\n').encode()
        changed=any(json.loads(originals[p][0])!=json.loads(v) for p,v in updates.items())
        was_active=control('active');stopped=False
        try:
            if changed:
                backup=root/'operator-backups'/'group-input'/str(time.time_ns())
                backup.mkdir(parents=True,mode=0o700)
                for path,(raw,_) in originals.items():
                    dst=backup/path.name;dst.write_bytes(raw);dst.chmod(0o600)
                if was_active:control('stop');stopped=True
                for path,raw in updates.items():replace(path,raw,originals[path][1])
            ignored=0
            if not enabled and (root/'social.sqlite3').exists():
                with sqlite3.connect(root/'social.sqlite3') as db:
                    ignored=db.execute("UPDATE queue SET status='ignored',error='group_input_paused_by_operator' WHERE kind='group' AND status IN ('pending','processing') AND json_extract(packet,'$.scene.platform') IN ('qq','wechat')").rowcount
        except Exception:
            for path,(raw,stat) in originals.items():replace(path,raw,stat)
            raise
        finally:
            if stopped:control('start')
        cancelled=0
        if not enabled and (root/'mouth.sqlite3').exists():
            with sqlite3.connect(root/'mouth.sqlite3') as db:
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='relay_deliveries'").fetchone():
                    batches=[r[0] for r in db.execute("SELECT batch_id FROM jobs WHERE json_extract(packet,'$.scene.chat_type')='group' AND json_extract(packet,'$.scene.platform') IN ('qq','wechat') AND json_extract(packet,'$.internal_wake') IS NULL")]
                    for batch in batches:
                        cancelled+=db.execute("UPDATE relay_deliveries SET status='failed',error='Group message replies disabled by operator',updated=? WHERE batch_id=? AND status='pending'",(time.time(),batch)).rowcount
                        db.execute("UPDATE relay_jobs SET status='blocked',error='Group message replies disabled by operator' WHERE batch_id=? AND status='pending'",(batch,))
                        states=[row[0] for row in db.execute('SELECT status FROM relay_deliveries WHERE batch_id=?',(batch,))]
                        if states and not any(state in ('pending','sending') for state in states) and any(state=='failed' for state in states):
                            db.execute('UPDATE relay_jobs SET status=? WHERE batch_id=?',('partial' if 'sent' in states else 'delivery_failed',batch))
        return {**status(root),'changed':changed,'ignored_pending_groups':ignored,'cancelled_pending_bubbles':cancelled,'service_was_active':was_active}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['on','off','status'])
    action=parser.parse_args().action
    result=status() if action=='status' else set_enabled(action=='on')
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
