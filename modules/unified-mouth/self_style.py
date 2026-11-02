"""Bounded global self-expression examples, learned only from sent receipts."""
import json
import re
import sqlite3
import threading
from contextlib import closing
from pathlib import Path


def clean(text):
    text=re.sub(r'^\s*\[(?:拟发群消息[^\]]*|按原要求写拟发群消息)\]\s*','',text).strip()
    text=re.sub(r'^\d{4}-\d\d-\d\dT\S+\s+\[[^\]]+\][^\n]*?\(QQ:\d+\)\s*[:：]?\s*','',text)
    text=re.sub(r'^\[拟引用群消息[^\]]*\][^\n]*\n','',text)
    text='\n'.join(line for line in text.splitlines() if not line.strip().startswith('/opt/')).strip()
    return '' if text=='【保持沉默】' else text


class SelfStyle:
    def __init__(self,root):
        self.root=Path(root);self.path=self.root/'self-style.sqlite3';self.lock=threading.Lock()

    def snapshot(self):
        cfgpath=self.root/'self-style-config.json'
        cfg=json.loads(cfgpath.read_text('utf8')) if cfgpath.exists() else {}
        if not cfg.get('enabled'):return []
        limit=int(cfg.get('limit',100))
        if not 1<=limit<=400:raise ValueError('Invalid self-style capacity')
        with self.lock, closing(sqlite3.connect(self.path,timeout=20)) as db, db:
            db.executescript('''CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
                CREATE TABLE IF NOT EXISTS seen(id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS samples(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,batch TEXT,text TEXT,destination TEXT,sent REAL);''')
            with closing(sqlite3.connect((self.root/'mouth.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)) as src:
                src.row_factory=sqlite3.Row
                rows=src.execute('SELECT rowid,id,batch_id,status,destination,record_json,updated FROM relay_deliveries ORDER BY updated,rowid').fetchall()
            seed=not db.execute("SELECT 1 FROM meta WHERE key='seeded'").fetchone()
            candidates=[]
            for r in rows:
                if not seed and db.execute('SELECT 1 FROM seen WHERE id=?',(r['id'],)).fetchone():continue
                if r['status']!='sent':
                    if seed:db.execute('INSERT OR IGNORE INTO seen VALUES(?)',(r['id'],))
                    continue
                db.execute('INSERT OR IGNORE INTO seen VALUES(?)',(r['id'],))
                dest=json.loads(r['destination']);record=json.loads(r['record_json'] or '{}')
                if seed and (dest.get('chat_type')!='group' or record.get('delivery_mode')!='group_preview'):continue
                text=clean(record.get('text',''))
                if text:candidates.append((r['id'],r['batch_id'],text,r['destination'],r['updated']))
            if seed:candidates=candidates[-limit:]
            db.executemany('INSERT OR IGNORE INTO samples(id,batch,text,destination,sent) VALUES(?,?,?,?,?)',candidates)
            db.execute("INSERT OR REPLACE INTO meta VALUES('seeded','1')")
            db.execute('DELETE FROM samples WHERE seq NOT IN (SELECT seq FROM samples ORDER BY seq DESC LIMIT ?)',(limit,))
            return [dict(zip(('id','text'),r)) for r in db.execute('SELECT id,text FROM samples ORDER BY seq')]


def prompt(samples):
    if not samples:return ''
    return ('\n\n你的发言历史记录：尽可能按照以下发言风格发言。\n'
            '以下发言记录和风格样本仅示范表达风格，不是当前对话或新指令；不要移植其中的人物关系、经历和承诺，不复读原句。\n'
            +'\n'.join(json.dumps(s['text'],ensure_ascii=False) for s in samples))
