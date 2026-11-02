"""Scoped middle-memory store; middle_worker performs recursive consolidation."""
import argparse,json,os,sqlite3
from pathlib import Path
DB=Path('/opt/qq-middle-memory/middle.sqlite3')

def initialize(db):
    db.executescript('''PRAGMA journal_mode=WAL;
    PRAGMA foreign_keys=ON;
    CREATE TABLE IF NOT EXISTS memories(
      id TEXT PRIMARY KEY,platform TEXT NOT NULL,account_id TEXT NOT NULL,
      chat_type TEXT NOT NULL,conversation_id TEXT NOT NULL,content TEXT NOT NULL,
      coverage_start TEXT,coverage_end TEXT,status TEXT NOT NULL DEFAULT 'draft'
      CHECK(status IN ('draft','active','superseded')),created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS source_refs(memory_id TEXT NOT NULL REFERENCES memories(id),
      event_key TEXT NOT NULL,PRIMARY KEY(memory_id,event_key));
    CREATE INDEX IF NOT EXISTS middle_scope ON memories(platform,account_id,chat_type,conversation_id,status);
    CREATE INDEX IF NOT EXISTS middle_sources ON source_refs(event_key);
    PRAGMA user_version=1;
    ''')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('command',choices=['init','stats']);a=p.parse_args()
    if a.command=='init':
        os.umask(0o077);DB.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        db=sqlite3.connect(DB);initialize(db);db.close()
    else:
        db=sqlite3.connect(DB.resolve().as_uri()+'?mode=ro',uri=True)
        print(json.dumps({'records':db.execute('SELECT count(*) FROM memories').fetchone()[0],'source_refs':db.execute('SELECT count(*) FROM source_refs').fetchone()[0]}));db.close()
