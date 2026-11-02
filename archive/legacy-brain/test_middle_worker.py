import json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import middle_worker as worker
from short_memory import initialize as partition_init

class MiddleTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.partition=Path(self.temp.name)/'short.db'
        self.src=sqlite3.connect(self.partition);partition_init(self.src)
        self.db=sqlite3.connect(Path(self.temp.name)/'middle.db');worker.initialize(self.db)
        self.patcher=patch.object(worker,'PARTITION',self.partition);self.patcher.start()

    def tearDown(self):
        self.patcher.stop();self.db.close();self.src.close();self.temp.cleanup()

    def add(self,i,conv='u'):
        raw={'platform':'qq','sender_id':conv,'raw_event':{'message':[{'type':'text','data':{'text':f'事件{i}'}}]}}
        self.src.execute('INSERT INTO eviction_events(event_key,event_time,account_id,chat_type,conversation_id,direction,raw_json) VALUES(?,?,?,?,?,?,?)',
                         (f'input:{i}',f'2026-10-21T00:{i:02d}:00Z','bot','private',conv,'incoming',json.dumps(raw)))
        self.src.commit()

    def test_recursive_batches_and_restart(self):
        self.add(1);self.assertIsNone(worker.plan(self.db,2))
        self.add(2);worker.plan(self.db,2)
        calls=[]
        def api(old,events,target):
            calls.append((old,[e['event_key'] for e in events],target));return f'概括{len(calls)}'
        worker.process_one(self.db,500,api)
        self.assertEqual(calls[0],([],["input:1","input:2"],500))
        worker.initialize(self.db)
        self.add(3);self.add(4);worker.plan(self.db,2);worker.process_one(self.db,500,api)
        self.assertEqual(calls[1][0],['概括1'])
        self.assertEqual(self.db.execute("SELECT content FROM memories WHERE status='active'").fetchall(),[('概括2',)])
        self.assertEqual(self.db.execute("SELECT count(*) FROM memories WHERE status='superseded'").fetchone()[0],1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM source_refs r JOIN memories m ON r.memory_id=m.id WHERE m.status='active'").fetchone()[0],4)
        self.assertIsNone(worker.plan(self.db,2));self.assertFalse(worker.process_one(self.db,500,api))
        self.assertEqual(len(calls),2)

    def test_failure_preserves_previous_and_stalls_cursor_until_recovery(self):
        self.add(1);worker.plan(self.db,1);worker.process_one(self.db,500,lambda *a:'旧概括')
        self.add(2);bid=worker.plan(self.db,1)
        def fail(*a):raise TimeoutError()
        for _ in range(3):
            self.db.execute('UPDATE consolidation_jobs SET next_retry=0');self.db.commit()
            worker.process_one(self.db,500,fail)
        self.assertEqual(self.db.execute("SELECT content FROM memories WHERE status='active'").fetchone()[0],'旧概括')
        self.assertEqual(self.db.execute("SELECT status FROM consolidation_jobs WHERE batch_id=?",(bid,)).fetchone()[0],'failed')
        self.add(3);self.assertEqual(worker.plan(self.db,1),bid)
        self.assertEqual(self.db.execute('SELECT max(end_seq) FROM consolidation_batches WHERE done=1').fetchone()[0],1)
        # Explicit recovery retries the same sealed source, never skips ahead.
        self.db.execute("UPDATE consolidation_jobs SET status='pending',next_retry=0 WHERE batch_id=?",(bid,));self.db.commit()
        worker.process_one(self.db,500,lambda *a:'恢复概括')
        self.assertEqual(self.db.execute('SELECT max(end_seq) FROM consolidation_batches WHERE done=1').fetchone()[0],2)

    def test_conversations_never_share_api_context(self):
        self.add(1,'alice');self.add(2,'bob');worker.plan(self.db,2)
        calls=[]
        def api(old,events,target):
            calls.append((old,{e['conversation_id'] for e in events}));return '各自的概括'
        worker.process_one(self.db,500,api);worker.process_one(self.db,500,api)
        self.assertEqual(calls,[([] ,{'alice'}),([] ,{'bob'})])
        self.assertEqual(self.db.execute('SELECT done FROM consolidation_batches').fetchone()[0],1)

    def test_rendering_preserves_delivery_status_and_does_not_read_images(self):
        e={'event_time':'now','direction':'outgoing','raw_json':json.dumps({'phase':'generated','text':'你好','image_paths':['/image.png']})}
        self.assertEqual(worker.material([e])[0]['content'],{'phase':'generated','text':'你好','images':['/image.png']})

    def test_default_threshold_exactly_200(self):
        for i in range(1,200):self.add(i)
        self.assertIsNone(worker.plan(self.db,200))
        self.add(200);worker.plan(self.db,200)
        sizes=[]
        def api(old,events,target):sizes.append(len(events));return '已归纳重点'
        worker.process_one(self.db,500,api)
        self.add(201);self.assertIsNone(worker.plan(self.db,200))
        self.assertEqual(sizes,[200])

if __name__=='__main__':unittest.main()
