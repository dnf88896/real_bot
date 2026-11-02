import json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import profile_worker as w
import basic_info

class ProfileTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.archive=Path(self.tmp.name)/'raw.db';self.path=Path(self.tmp.name)/'basic.db'
        self.src=sqlite3.connect(self.archive);self.src.execute('CREATE TABLE observations(id INTEGER PRIMARY KEY,account_id TEXT,sender_id TEXT,conversation_id TEXT,chat_type TEXT,received_at TEXT,raw_json TEXT)')
        self.db=sqlite3.connect(self.path);w.initialize(self.db)
        self.patch=patch.object(w,'ARCHIVE',self.archive);self.patch.start()
    def tearDown(self):
        self.patch.stop();self.db.close();self.src.close();self.tmp.cleanup()
    def add(self,i,user='u',kind='private'):
        self.src.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?)',(i,'bot',user,user if kind=='private' else 'g',kind,'2026-10-22',json.dumps({'raw_event':{'message':'喜欢天文'}})));self.src.commit()
    def fake(self,old,events,basic,target):
        return {'summary':'喜欢天文','facts':old['facts']+[{'kind':'preference','text':'本人说喜欢天文','source_ids':[events[0]['id']]}]}
    def test_exact_30_per_person_recursion_sparse_ids_and_private_only(self):
        for i in range(1,30):self.add(i*3)
        for i in range(1,30):self.add(i*3+1,kind='group')
        self.add(2,'v');w.plan(self.db);self.assertEqual(self.db.execute('SELECT count(*) FROM profile_jobs').fetchone()[0],0)
        self.add(90);w.plan(self.db)
        events=json.loads(self.db.execute('SELECT events FROM profile_jobs').fetchone()[0]);self.assertEqual(len(events),30)
        self.assertEqual([e['id'] for e in events],list(range(3,91,3)))
        w.process_one(self.db,api=self.fake)
        for i in range(31,61):self.add(i*3)
        w.plan(self.db);seen=[]
        def api(old,events,basic,target):seen.append(old);return self.fake(old,events,basic,target)
        w.process_one(self.db,api=api);self.assertEqual(len(seen[0]['facts']),1)
        self.assertEqual(self.db.execute('SELECT last_observation FROM profile_heads').fetchone()[0],180)
        self.assertEqual(self.db.execute('SELECT count(*) FROM profile_versions').fetchone()[0],2)
        w.plan(self.db);self.assertFalse(w.process_one(self.db,api=api))
        with patch.object(basic_info,'DB',self.path):
            packet={'scene':{'platform':'qq','account':'bot','chat_type':'private','conversation':'u'},'new_messages':[{'id':181,'observation':{'sender_id':'u'}}]}
            p=basic_info.load_for_packet(packet)['人物资料'][0]['交流资料'];self.assertEqual(len(p['facts']),2)
            packet['new_messages'][0]['id']=91
            p=basic_info.load_for_packet(packet)['人物资料'][0]['交流资料'];self.assertEqual(len(p['facts']),1)
    def test_failure_keeps_cursor_and_other_person_can_progress(self):
        for i in range(1,31):self.add(i)
        w.plan(self.db);w.process_one(self.db,api=self.fake)
        for i in range(31,61):self.add(i)
        w.plan(self.db)
        def invalid(*a):return {'summary':'瞎猜','facts':[{'kind':'identity','text':'伪造','source_ids':[99999]}]}
        w.process_one(self.db,api=invalid)
        self.assertEqual(self.db.execute("SELECT last_observation FROM profile_heads WHERE user_id='u'").fetchone()[0],30)
        for i in range(61,91):self.add(i,'v')
        w.plan(self.db);w.process_one(self.db,api=self.fake)
        self.assertEqual(self.db.execute("SELECT last_observation FROM profile_heads WHERE user_id='v'").fetchone()[0],90)
        self.db.execute('UPDATE profile_jobs SET next_retry=0');self.db.commit()
        w.process_one(self.db,api=self.fake)
        self.assertEqual(self.db.execute("SELECT last_observation FROM profile_heads WHERE user_id='u'").fetchone()[0],60)

if __name__=='__main__':unittest.main()
