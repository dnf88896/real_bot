"""Platform-independent recording of generated content and delivery outcomes."""
import json,os,sqlite3,time
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path('/opt/qq-output-memory')
MOUTH=Path('/opt/qq-mouth/mouth.sqlite3')

def initialize(db):
    db.executescript('''PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS events(event_key TEXT PRIMARY KEY,event_time TEXT NOT NULL,platform TEXT NOT NULL,account_id TEXT NOT NULL,chat_type TEXT NOT NULL,conversation_id TEXT NOT NULL,phase TEXT NOT NULL,raw_json TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS output_time ON events(event_time,event_key);''')

def record_output(db,*,event_key,timestamp,platform,account,chat_type,conversation,phase,text,images=None,receipt=None,delivery_metadata=None):
    """Future platform adapters use stable event keys and report confirmed outcomes."""
    if phase not in ('generated','sent','failed','uncertain','skipped'):raise ValueError('Invalid output phase')
    if not all(isinstance(x,str) and x for x in (event_key,platform,account,chat_type,conversation)):raise ValueError('Missing output identity')
    stamp=datetime.fromtimestamp(timestamp,timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
    raw={'schema_version':1,'direction':'outgoing','source':'mouth','event_key':event_key,'received_at':stamp,'platform':platform,'account_id':account,'chat_type':chat_type,'conversation_id':conversation,'phase':phase,'text':text,'image_paths':images or [],'receipt':receipt}
    if delivery_metadata:raw.update(delivery_metadata)
    encoded=json.dumps(raw,ensure_ascii=False)
    old=db.execute('SELECT raw_json FROM events WHERE event_key=?',(event_key,)).fetchone()
    if old:
        if old[0]!=encoded:raise ValueError('Conflicting output event identity')
        return False
    db.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?,?)',(event_key,stamp,platform,account,chat_type,conversation,phase,encoded));return True

def collect(db):
    src=sqlite3.connect(MOUTH.resolve().as_uri()+'?mode=ro',uri=True)
    # Missing events are selected in SQL; already recorded events are not scanned into Python.
    db.execute('ATTACH DATABASE ? AS mouth',(MOUTH.resolve().as_uri()+'?mode=ro',))
    added=0
    try:
        generated=db.execute("SELECT o.batch_id,o.created,o.text,j.packet FROM mouth.outputs o JOIN mouth.jobs j ON j.batch_id=o.batch_id WHERE NOT EXISTS(SELECT 1 FROM events e WHERE e.event_key='mouth:'||o.batch_id||':generated') ORDER BY o.rowid LIMIT 500").fetchall()
        terminal=db.execute("SELECT d.batch_id,d.updated,d.status,d.message_id,d.record_json,o.text,j.packet FROM mouth.deliveries d JOIN mouth.outputs o ON o.batch_id=d.batch_id JOIN mouth.jobs j ON j.batch_id=d.batch_id WHERE d.status IN ('sent','failed','uncertain','skipped') AND NOT EXISTS(SELECT 1 FROM events e WHERE e.event_key='mouth:'||d.batch_id||':'||d.status) ORDER BY d.updated LIMIT 500").fetchall()
        relayed=[]
        if src.execute("SELECT 1 FROM sqlite_master WHERE name='relay_deliveries'").fetchone():
            relayed=db.execute("SELECT d.id,d.updated,d.status,d.message_id,d.record_json,d.destination,d.parts,o.text FROM mouth.relay_deliveries d JOIN mouth.outputs o ON o.batch_id=d.batch_id WHERE d.status IN ('sent','failed','uncertain') AND NOT EXISTS(SELECT 1 FROM events e WHERE e.event_key='relay:'||d.id||':'||d.status) ORDER BY d.updated LIMIT 500").fetchall()
        with db:
            for batch,created,text,packet in generated:
                scene=json.loads(packet)['scene']
                media=src.execute('SELECT paths_json FROM media_outputs WHERE batch_id=?',(batch,)).fetchone()
                added+=record_output(db,event_key='mouth:'+batch+':generated',timestamp=created,platform=scene['platform'],account=str(scene['account']),chat_type=scene['chat_type'],conversation=str(scene['conversation']),phase='generated',text=text,images=json.loads(media[0]) if media else [])
            for batch,updated,status,receipt,raw,text,packet in terminal:
                scene=json.loads(packet)['scene'];sent=json.loads(raw) if raw else {}
                added+=record_output(db,event_key='mouth:'+batch+':'+status,timestamp=updated,platform=scene['platform'],account=str(scene['account']),chat_type=scene['chat_type'],conversation=str(scene['conversation']),phase=status,text=text,images=sent.get('image_paths',[]),receipt=receipt)
            for did,updated,status,receipt,raw,destination,parts,source in relayed:
                dest=json.loads(destination);sent=json.loads(raw) if raw else {}
                text=sent.get('text',''.join(source[p['start']:p['end']] for p in json.loads(parts) if p['kind']=='text'))
                meta={k:sent[k] for k in ('delivery_mode','actual_destination','logical_destination') if k in sent}
                added+=record_output(db,event_key='relay:'+did+':'+status,timestamp=updated,platform=dest['platform'],account=dest['account'],chat_type=dest['chat_type'],conversation=dest['id'],phase=status,text=text,images=sent.get('image_paths',[]),receipt=receipt,delivery_metadata=meta)
    finally:db.execute('DETACH DATABASE mouth');src.close()
    return added

def main():
    os.umask(0o077);ROOT.mkdir(mode=0o700,exist_ok=True)
    db=sqlite3.connect(ROOT/'output.sqlite3',uri=True);initialize(db)
    while True:
        n=collect(db)
        if n:print(f'Recorded {n} output events.',flush=True)
        time.sleep(1)

if __name__=='__main__':main()
