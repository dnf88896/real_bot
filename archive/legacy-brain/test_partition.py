import json,sqlite3,tempfile,unittest
from pathlib import Path
from short_memory import initialize,refresh_entries

class PartitionTest(unittest.TestCase):
    def test_burst_and_restart_do_not_lose_or_duplicate_evicted_records(self):
        with tempfile.TemporaryDirectory() as d:
            srcpath=Path(d)/'raw.db';src=sqlite3.connect(srcpath)
            src.execute('CREATE TABLE observations(id INTEGER PRIMARY KEY,received_at TEXT,account_id TEXT,chat_type TEXT,conversation_id TEXT,raw_json TEXT)')
            for i in range(1,8):src.execute('INSERT INTO observations VALUES(?,?,?,?,?,?)',(i,f'2026-10-21T00:00:0{i}Z','1','private','u',json.dumps({'original':i},ensure_ascii=False)))
            src.commit();file=Path(d)/'partition.db';db=sqlite3.connect(file);initialize(db)
            for _ in range(2):refresh_entries(db,Path(d)/'none',2,srcpath)
            self.assertEqual(db.execute('SELECT count(*) FROM entries').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT count(*) FROM long_entries').fetchone()[0],5)
            self.assertEqual(db.execute('SELECT event_key FROM entries ORDER BY event_key').fetchall(),[('input:6',),('input:7',)])
            self.assertEqual(db.execute('SELECT raw_json FROM long_entries WHERE event_key=?',('input:1',)).fetchone()[0],src.execute('SELECT raw_json FROM observations WHERE id=1').fetchone()[0])
            db.close();db=sqlite3.connect(file);initialize(db)
            refresh_entries(db,Path(d)/'none',3,srcpath)
            self.assertEqual(db.execute('SELECT count(*) FROM entries').fetchone()[0],3)
            self.assertEqual(db.execute('SELECT count(*) FROM long_entries').fetchone()[0],4)
            self.assertEqual(db.execute('SELECT count(*) FROM entries s JOIN long_entries l ON s.event_key=l.event_key').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM eviction_events').fetchone()[0],5)
            refresh_entries(db,Path(d)/'none',2,srcpath)
            self.assertEqual(db.execute('SELECT count(*) FROM eviction_events').fetchone()[0],5)
            src.close();db.close()

if __name__=='__main__':unittest.main()
