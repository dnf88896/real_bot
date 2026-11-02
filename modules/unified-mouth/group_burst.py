"""Temporary pacing bypass with a durable delivered-message quota."""
import json
import os
import sqlite3
import time
import threading
from pathlib import Path
from contextlib import closing


LOCK=threading.Lock()


def active(root):
    with LOCK:
        return _active(root)


def _active(root):
    root=Path(root);path=root/'group-burst.json'
    if not path.exists():return False
    cfg=json.loads(path.read_text('utf8'))
    if not cfg.get('enabled'):return False
    with closing(sqlite3.connect((root/'mouth.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)) as db:
        count=db.execute("""SELECT count(*) FROM relay_deliveries WHERE rowid>? AND status='sent'
            AND json_extract(destination,'$.chat_type')='group'
            AND json_extract(record_json,'$.delivery_mode')='group_preview'
            AND length(trim(coalesce(json_extract(record_json,'$.text'),'')))>0""",(int(cfg['after_rowid']),)).fetchone()[0]
    if count<int(cfg.get('target',300)):return True
    cfg.update(enabled=False,completed_at=time.time(),delivered_count=count)
    temp=path.with_suffix('.json.tmp')
    temp.write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding='utf8')
    temp.chmod(0o640);os.replace(temp,path)
    return False
