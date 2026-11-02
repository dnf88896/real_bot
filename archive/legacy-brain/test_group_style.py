import json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import group_style_worker as w
import basic_info

class GroupStyleTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.archive=Path(self.temp.name)/'raw.db';self.path=Path(self.temp.name)/'basic.db'
        self.src=sqlite3.connect(self.archive);self.src.execute('CREATE TABLE observations(id INTEGER PRIMARY KEY,account_id TEXT,sender_id TEXT,conversation_id TEXT,chat_type TEXT,received_at TEXT,raw_json TEXT)')
        self.db=sqlite3.connect(self.path);w.initialize(self.db)
        self.patcher=patch.object(w,'ARCHIVE',self.archive);self.patcher.start()
    def tearDown(self):
        self.patcher.stop();self.db.close();self.src.close();self.temp.cleanup()
    def add(self,i,gid='g',kind='group'):
        self.src.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?)',(i,'bot','speaker',gid,kind,'2026-10-22',json.dumps({'sender_id':'speaker','raw_event':{'message':'短句闲聊'}})));self.src.commit()
    def fake(self,old,events,group,target):
        return {'summary':'多用简短闲聊','facts':old['facts']+[{'kind':'tone','text':'样本内多用短句','source_ids':[events[0]['id']]}]}
    def test_300_per_group_recursion_scope_and_automatic_loading(self):
        for i in range(1,300):self.add(i)
        self.add(10000,'speaker','private');self.add(10001,'other')
        w.plan(self.db);self.assertEqual(self.db.execute('SELECT count(*) FROM group_style_jobs').fetchone()[0],0)
        self.add(300);w.plan(self.db)
        sealed=json.loads(self.db.execute('SELECT events FROM group_style_jobs').fetchone()[0]);self.assertEqual(len(sealed),300)
        w.process_one(self.db,api=self.fake)
        for i in range(301,601):self.add(i)
        seen=[]
        def recursive(old,*args):seen.append(old);return self.fake(old,*args)
        w.plan(self.db);w.process_one(self.db,api=recursive)
        self.assertEqual(len(seen[0]['facts']),1)
        w.plan(self.db);self.assertFalse(w.process_one(self.db,api=recursive))
        self.assertEqual(self.db.execute('SELECT last_observation FROM group_style_heads').fetchone()[0],600)
        self.assertEqual(self.db.execute('SELECT count(*) FROM members').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM contacts').fetchone()[0],0)
        with patch.object(basic_info,'DB',self.path):
            packet={'scene':{'platform':'qq','account':'bot','chat_type':'group','conversation':'g'},'new_messages':[{'id':601,'observation':{'sender_id':'speaker'}}]}
            result=basic_info.load_for_packet(packet)
            self.assertEqual(len(result['群资料']['交流资料']['facts']),2)
            self.assertNotIn('人物资料',result)
            packet['new_messages'][0]['id']=301
            self.assertEqual(len(basic_info.load_for_packet(packet)['群资料']['交流资料']['facts']),1)
            packet['scene']['conversation']='other'
            self.assertNotIn('交流资料',basic_info.load_for_packet(packet)['群资料'])
    def test_invalid_personal_fact_cannot_replace_group_style(self):
        self.add(1);w.plan(self.db,1);w.process_one(self.db,api=self.fake)
        self.add(2);w.plan(self.db,1)
        def invalid(old,events,group,target):return {'summary':'个人档案','facts':[{'kind':'identity','text':'某人身份','source_ids':[2]}]}
        w.process_one(self.db,api=invalid)
        self.assertEqual(self.db.execute('SELECT last_observation FROM group_style_heads').fetchone()[0],1)
        self.add(3,'other');w.plan(self.db,1);w.process_one(self.db,api=self.fake)
        self.assertEqual(self.db.execute("SELECT last_observation FROM group_style_heads WHERE group_id='other'").fetchone()[0],3)

if __name__=='__main__':unittest.main()
