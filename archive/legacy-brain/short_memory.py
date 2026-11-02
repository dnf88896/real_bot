"""Bounded, objective observation window rebuilt from the read-only archive."""
import argparse,json,os,sqlite3,time
from pathlib import Path

ROOT=Path('/opt/qq-short-memory')
ARCHIVE=Path('/opt/qq-brain-memory/data/observations.sqlite3')
OUTPUTS=Path('/opt/qq-output-memory/output.sqlite3')

def initialize(db):
    db.executescript('''PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
    CREATE TABLE IF NOT EXISTS observations(
      id INTEGER PRIMARY KEY,modality TEXT NOT NULL,platform TEXT NOT NULL,
      account_id TEXT NOT NULL,chat_type TEXT NOT NULL,conversation_id TEXT NOT NULL,
      received_at TEXT NOT NULL,raw_json TEXT NOT NULL,archive_row_json TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS short_conversation ON observations(platform,account_id,chat_type,conversation_id,id);
    CREATE TABLE IF NOT EXISTS state(id INTEGER PRIMARY KEY CHECK(id=1),archive_head INTEGER NOT NULL,capacity INTEGER NOT NULL,updated REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS entries(event_key TEXT PRIMARY KEY,event_time TEXT NOT NULL,account_id TEXT NOT NULL,chat_type TEXT NOT NULL,conversation_id TEXT NOT NULL,direction TEXT NOT NULL,raw_json TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS long_entries(event_key TEXT PRIMARY KEY,event_time TEXT NOT NULL,account_id TEXT NOT NULL,chat_type TEXT NOT NULL,conversation_id TEXT NOT NULL,direction TEXT NOT NULL,raw_json TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS long_time ON long_entries(event_time,event_key);
    CREATE TABLE IF NOT EXISTS eviction_events(seq INTEGER PRIMARY KEY AUTOINCREMENT,event_key TEXT UNIQUE NOT NULL,event_time TEXT NOT NULL,account_id TEXT NOT NULL,chat_type TEXT NOT NULL,conversation_id TEXT NOT NULL,direction TEXT NOT NULL,raw_json TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS partition_cursor(id INTEGER PRIMARY KEY CHECK(id=1),input_id INTEGER NOT NULL,output_id INTEGER NOT NULL);
    INSERT OR IGNORE INTO partition_cursor VALUES(1,0,0);
    CREATE INDEX IF NOT EXISTS short_entries_conversation ON entries(account_id,chat_type,conversation_id,event_time);
    ''')
    db.execute('INSERT OR IGNORE INTO eviction_events(event_key,event_time,account_id,chat_type,conversation_id,direction,raw_json) SELECT * FROM long_entries ORDER BY event_time,event_key');db.commit()

def sync_once(db,archive,capacity):
    if type(capacity) is not int or not 1<=capacity<=20000:raise ValueError('max_records must be an integer from 1 to 20000')
    src=sqlite3.connect(Path(archive).resolve().as_uri()+'?mode=ro',uri=True)
    src.row_factory=sqlite3.Row
    try:
        src.execute('BEGIN')
        head=src.execute('SELECT coalesce(max(id),0) FROM observations').fetchone()[0]
        previous=db.execute('SELECT archive_head,capacity FROM state WHERE id=1').fetchone()
        if previous and head<previous[0]:raise ValueError('Archive head moved backward; refusing to replace the short-term window')
        if previous and (head,capacity)==tuple(previous):return {'added':0,'evicted':0,'unchanged':True}
        rows=src.execute('SELECT * FROM observations ORDER BY id DESC LIMIT ?',(capacity,)).fetchall()
    finally:src.close()
    added=0
    with db:
        for row in rows:
            r=dict(row)
            added+=db.execute('INSERT OR IGNORE INTO observations VALUES(?,?,?,?,?,?,?,?,?)',(r['id'],r.get('modality','eye'),r['platform'],str(r['account_id']),r['chat_type'],str(r['conversation_id']),r['received_at'],r['raw_json'],json.dumps(r,ensure_ascii=False))).rowcount
        evicted=db.execute('DELETE FROM observations WHERE id NOT IN (SELECT id FROM observations ORDER BY id DESC LIMIT ?)',(capacity,)).rowcount
        db.execute('INSERT INTO state VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET archive_head=excluded.archive_head,capacity=excluded.capacity,updated=excluded.updated',(head,capacity,time.time()))
    return {'added':added,'evicted':evicted,'unchanged':False}

def refresh_entries(db,output_path,capacity,input_archive=None):
    if type(capacity) is not int or not 1<=capacity<=20000:raise ValueError('Invalid capacity')
    cursor=db.execute('SELECT input_id,output_id FROM partition_cursor WHERE id=1').fetchone()
    src=sqlite3.connect(Path(input_archive).resolve().as_uri()+'?mode=ro',uri=True) if input_archive else db
    try:
        data=src.execute('SELECT id,received_at,account_id,chat_type,conversation_id,raw_json FROM observations WHERE id>? ORDER BY id LIMIT 500',(cursor[0],)).fetchall()
    finally:
        if src is not db:src.close()
    rows=[('input:'+str(i),stamp,account,kind,conv,'incoming',raw) for i,stamp,account,kind,conv,raw in data]
    outgoing=[]
    if Path(output_path).exists():
        out=sqlite3.connect(Path(output_path).resolve().as_uri()+'?mode=ro',uri=True)
        try:outgoing=out.execute('SELECT rowid,event_key,event_time,account_id,chat_type,conversation_id,raw_json FROM events WHERE rowid>? ORDER BY rowid LIMIT 500',(cursor[1],)).fetchall()
        finally:out.close()
        rows.extend(('output:'+key,stamp,account,kind,conv,'outgoing',raw) for _,key,stamp,account,kind,conv,raw in outgoing)
    with db:
        for row in rows:
            if not db.execute('SELECT 1 FROM long_entries WHERE event_key=?',(row[0],)).fetchone():
                db.execute('INSERT OR IGNORE INTO entries VALUES(?,?,?,?,?,?,?)',row)
        selected=db.execute('SELECT * FROM (SELECT * FROM entries UNION ALL SELECT * FROM long_entries) ORDER BY event_time DESC,event_key DESC LIMIT ?',(capacity,)).fetchall()
        keys={r[0] for r in selected}
        for row in db.execute('SELECT * FROM entries ORDER BY event_time,event_key').fetchall():
            if row[0] not in keys:
                db.execute('INSERT OR IGNORE INTO eviction_events(event_key,event_time,account_id,chat_type,conversation_id,direction,raw_json) VALUES(?,?,?,?,?,?,?)',row)
                db.execute('INSERT OR IGNORE INTO long_entries VALUES(?,?,?,?,?,?,?)',row)
                db.execute('DELETE FROM entries WHERE event_key=?',(row[0],))
        for row in selected:
            db.execute('INSERT OR IGNORE INTO entries VALUES(?,?,?,?,?,?,?)',row)
            db.execute('DELETE FROM long_entries WHERE event_key=?',(row[0],))
        db.execute('UPDATE partition_cursor SET input_id=?,output_id=? WHERE id=1',(data[-1][0] if data else cursor[0],outgoing[-1][0] if outgoing else cursor[1]))


def query(db,account,kind,conversation,limit=200,layer="short"):
    if not all(isinstance(x,str) and x for x in (account,kind,conversation)):raise ValueError('Account, chat type, and conversation are required')
    table='long_entries' if layer=='long' else ('entries' if db.execute('SELECT count(*) FROM entries').fetchone()[0] else 'observations')
    order='event_time DESC,event_key DESC' if table in ('entries','long_entries') else 'id DESC'
    rows=db.execute('SELECT raw_json FROM '+table+' WHERE account_id=? AND chat_type=? AND conversation_id=? ORDER BY '+order+' LIMIT ?',(account,kind,conversation,max(1,min(20000,limit)))).fetchall()
    return [json.loads(r[0]) for r in reversed(rows)]

def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['watch','sync','stats','query'])
    p.add_argument('--db',default=str(ROOT/'short.sqlite3'));p.add_argument('--archive',default=str(ARCHIVE));p.add_argument('--config',default=str(ROOT/'config.json'))
    p.add_argument('--layer',choices=['short','long'],default='short');p.add_argument('--account',default='100000002');p.add_argument('--chat-type',choices=['private','group']);p.add_argument('--conversation');p.add_argument('--limit',type=int,default=200)
    a=p.parse_args()
    if a.command in ('stats','query'):
        db=sqlite3.connect(Path(a.db).resolve().as_uri()+'?mode=ro',uri=True);db.execute('PRAGMA query_only=ON')
        if a.command=='query':
            for record in query(db,a.account,a.chat_type,a.conversation,a.limit,a.layer):print(json.dumps(record,ensure_ascii=False))
        else:
            state=db.execute('SELECT archive_head,capacity,updated FROM state WHERE id=1').fetchone()
            print(json.dumps({'records':db.execute('SELECT count(*) FROM entries').fetchone()[0],'long_records':db.execute('SELECT count(*) FROM long_entries').fetchone()[0],'directions':dict(db.execute('SELECT direction,count(*) FROM entries GROUP BY direction').fetchall()),'state':dict(zip(['archive_head','max_records','updated'],state)) if state else None}))
        db.close();return
    os.umask(0o077);Path(a.db).parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    db=sqlite3.connect(a.db);initialize(db)
    try:
        while True:
            config=json.loads(Path(a.config).read_text('utf8'))
            result=sync_once(db,a.archive,config['max_records'])
            refresh_entries(db,OUTPUTS,config['max_records'],a.archive)
            if not result['unchanged']:print(json.dumps(result),flush=True)
            if a.command=='sync':break
            time.sleep(1)
    finally:db.close()

if __name__=='__main__':main()
