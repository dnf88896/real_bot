"""Append-only observation archive; no model calls or interpretation."""
import argparse
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

DEFAULT_DB = '/opt/qq-brain-memory/data/observations.sqlite3'
DEFAULT_SOURCE = '/opt/qq-eyes/data/qq-received.jsonl'


def initialize(db):
    db.executescript('''
    PRAGMA journal_mode=WAL;
    PRAGMA synchronous=FULL;
    CREATE TABLE IF NOT EXISTS observations (
      id INTEGER PRIMARY KEY,
      source_file TEXT NOT NULL,
      source_offset INTEGER NOT NULL,
      sha256 TEXT NOT NULL,
      modality TEXT NOT NULL,
      platform TEXT NOT NULL,
      account_id TEXT NOT NULL,
      chat_type TEXT NOT NULL,
      conversation_id TEXT NOT NULL,
      sender_id TEXT NOT NULL,
      received_at TEXT NOT NULL,
      event_time,
      message_id TEXT,
      text_content TEXT NOT NULL,
      raw_json TEXT NOT NULL,
      UNIQUE(source_file, source_offset)
    );
    CREATE INDEX IF NOT EXISTS observation_conversation
      ON observations(platform,account_id,chat_type,conversation_id,id);
    CREATE INDEX IF NOT EXISTS observation_time ON observations(received_at,id);
    CREATE TABLE IF NOT EXISTS checkpoints(source_file TEXT PRIMARY KEY, byte_offset INTEGER NOT NULL);
    CREATE TRIGGER IF NOT EXISTS observations_no_update BEFORE UPDATE ON observations
      BEGIN SELECT RAISE(ABORT, 'observations are append-only'); END;
    CREATE TRIGGER IF NOT EXISTS observations_no_delete BEFORE DELETE ON observations
      BEGIN SELECT RAISE(ABORT, 'observations are append-only'); END;
    PRAGMA user_version=1;
    ''')


def sync_once(db, source):
    source = str(Path(source).resolve())
    row = db.execute('SELECT byte_offset FROM checkpoints WHERE source_file=?', (source,)).fetchone()
    offset = row[0] if row else 0
    if not Path(source).exists():
        if offset: raise RuntimeError('Source disappeared; refusing to skip data')
        return 0
    if Path(source).stat().st_size < offset:
        raise RuntimeError('Source was truncated; manual reconciliation required')
    count = 0
    with db, open(source, 'rb') as stream:
        stream.seek(offset)
        for _ in range(500):
            start = stream.tell()
            line = stream.readline(16 * 1024 * 1024 + 1)
            if len(line) > 16 * 1024 * 1024:
                raise RuntimeError('Observation exceeds ingestion size limit')
            if not line.endswith(b'\n'): break  # Writer may still be appending.
            raw = line[:-1].decode('utf-8')
            r = json.loads(raw)  # Malformed records stop ingestion, never silently skip.
            for key in ('platform','account_id','chat_type','conversation_id','sender_id','received_at','raw_event'):
                if key not in r: raise ValueError('Observation missing required field: ' + key)
            event = r['raw_event']
            text = event.get('raw_message')
            if not isinstance(text, str):
                text = ''.join(s.get('data', {}).get('text', '') for s in event.get('message', []) if isinstance(s, dict) and s.get('type') == 'text')
            digest = hashlib.sha256(line).hexdigest()
            old = db.execute('SELECT sha256 FROM observations WHERE source_file=? AND source_offset=?', (source,start)).fetchone()
            if old:
                if old[0] != digest: raise RuntimeError('Previously archived source record changed')
            else:
                db.execute('''INSERT INTO observations
                  (source_file,source_offset,sha256,modality,platform,account_id,chat_type,conversation_id,sender_id,received_at,event_time,message_id,text_content,raw_json)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                  (source,start,digest,r.get('modality','eye'),r['platform'],str(r['account_id']),r['chat_type'],str(r['conversation_id']),str(r['sender_id']),r['received_at'],r.get('event_time'),r.get('message_id'),text,raw))
                count += 1
            offset = stream.tell()
        db.execute('INSERT INTO checkpoints VALUES (?,?) ON CONFLICT(source_file) DO UPDATE SET byte_offset=excluded.byte_offset', (source,offset))
    return count


def main():
    p = argparse.ArgumentParser()
    p.add_argument('command', choices=['sync','watch','stats','query'])
    p.add_argument('--db', default=DEFAULT_DB)
    p.add_argument('--source', default=DEFAULT_SOURCE)
    p.add_argument('--account', default='100000002')
    p.add_argument('--conversation')
    p.add_argument('--chat-type', choices=['private','group'])
    p.add_argument('--text')
    p.add_argument('--limit', type=int, default=20)
    a = p.parse_args()
    if a.command in ('stats','query'):
        db = sqlite3.connect(Path(a.db).resolve().as_uri() + '?mode=ro', uri=True)
        db.execute('PRAGMA query_only=ON')
        if a.command == 'stats':
            print(json.dumps(dict(zip(('records','first_received','last_received'),db.execute('SELECT count(*),min(received_at),max(received_at) FROM observations').fetchone()))))
        else:
            clauses, params = ['account_id=?'], [a.account]
            for column, value in [('conversation_id',a.conversation),('chat_type',a.chat_type)]:
                if value is not None: clauses.append(column+'=?'); params.append(value)
            if a.text is not None: clauses.append('instr(text_content,?)>0'); params.append(a.text)
            params.append(max(1,min(200,a.limit)))
            rows = db.execute('SELECT raw_json FROM observations WHERE '+' AND '.join(clauses)+' ORDER BY id DESC LIMIT ?', params).fetchall()
            for row in reversed(rows): print(row[0])
        db.close()
        return
    os.umask(0o077)
    Path(a.db).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(a.db)
    initialize(db)
    try:
        while True:
            n = sync_once(db,a.source)
            if n: print(f'Archived {n} observations.', flush=True)
            if a.command == 'sync': break
            time.sleep(1)
    finally: db.close()


if __name__ == '__main__': main()
