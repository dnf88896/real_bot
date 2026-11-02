import json,sqlite3,tempfile,unittest
from pathlib import Path
from short_memory import initialize,sync_once,query

class ShortMemoryTest(unittest.TestCase):
    def test_rolling_eviction_raw_fidelity_restart_growth_and_all_sources(self):
        with tempfile.TemporaryDirectory() as d:
            archive=Path(d)/'archive.sqlite3';src=sqlite3.connect(archive)
            src.execute('CREATE TABLE observations(id INTEGER PRIMARY KEY,modality TEXT,platform TEXT,account_id TEXT,chat_type TEXT,conversation_id TEXT,received_at TEXT,raw_json TEXT)')
            for i,modality,platform in [(1,'eye','qq'),(2,'ear','audio'),(3,'eye','bilibili')]:
                raw=json.dumps({'text':'完全原文\n'+str(i),'source':platform},ensure_ascii=False)
                src.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?)',(i,modality,platform,'1','private','a','time',raw))
            src.commit();dbfile=Path(d)/'short.sqlite3';db=sqlite3.connect(dbfile);initialize(db)
            sync_once(db,archive,2)
            self.assertEqual([r[0] for r in db.execute('SELECT id FROM observations ORDER BY id')],[2,3])
            self.assertEqual(db.execute('SELECT raw_json FROM observations WHERE id=3').fetchone(),src.execute('SELECT raw_json FROM observations WHERE id=3').fetchone())
            self.assertEqual(len(query(db,'1','private','a')),2);self.assertEqual(query(db,'1','private','other'),[])
            db.close();db=sqlite3.connect(dbfile);initialize(db)
            self.assertTrue(sync_once(db,archive,2)['unchanged'])
            sync_once(db,archive,3);self.assertEqual(db.execute('SELECT count(*) FROM observations').fetchone()[0],3)
            src.execute("INSERT INTO observations VALUES(4,'eye','qq','1','private','a','time','{}')");src.commit()
            result=sync_once(db,archive,2)
            self.assertEqual([r[0] for r in db.execute('SELECT id FROM observations ORDER BY id')],[3,4])
            self.assertEqual(src.execute('SELECT count(*) FROM observations').fetchone()[0],4)
            self.assertEqual(result['evicted'],2)
            for cap in [0,-1,True,20001]:
                with self.assertRaises(ValueError):sync_once(db,archive,cap)
            src.close();db.close()

if __name__=='__main__':unittest.main()
