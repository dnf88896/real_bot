import json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import basic_info as info

class BasicInfoTest(unittest.TestCase):
    def test_scoped_profiles_relationship_provenance_and_only_current_group_speakers(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'basic.db';db=sqlite3.connect(path);info.initialize(db)
            info.sync(db,'1',[{'user_id':'2','nickname':'果冻','remark':'备注'},{'user_id':'3','nickname':'儿子'},{'user_id':'9','nickname':'无关联系人'}],[{'group_id':'g','group_name':'<$x>测试群'}],{'user_id':'1','nickname':'机器人'})
            db.execute("UPDATE contacts SET preferred_name='阿姨',preferred_name_source='explicit:test' WHERE account='1' AND user_id='2'");db.commit()
            info.sync(db,'1',[{'user_id':'2','nickname':'新昵称'}],[],{'user_id':'1','nickname':'机器人'})
            info.sync(db,'other',[{'user_id':'2','nickname':'别的账号资料'}],[],{'user_id':'other'})
            info.record_relationship(db,account='1',subject='2',object_id='3',relation='母亲',status='reported',source_actor='3',source_ref='input:123',observed_at='2026-10-22')
            db.commit()
            packet={'scene':{'platform':'qq','account':'1','chat_type':'private','conversation':'2'},'new_messages':[{'observation':{'sender_id':'2'}}]}
            with patch.object(info,'DB',path):
                result=info.load_for_packet(packet)
                self.assertEqual(result['人物资料'][0]['nickname'],'新昵称')
                self.assertEqual(result['人物资料'][0]['preferred_name'],'阿姨')
                self.assertEqual(result['人物关系'][0]['source_actor'],'3')
                self.assertEqual(result['人物关系'][0]['status'],'reported')
                self.assertEqual(result['关系涉及人物'][0]['user_id'],'3')
                self.assertNotIn('无关联系人',json.dumps(result,ensure_ascii=False))
                packet['scene'].update(chat_type='group',conversation='g')
                result=info.load_for_packet(packet)
                self.assertEqual(result['群资料']['name'],'测试群')
                self.assertEqual(result['群资料']['reply_allowed'],0)
                self.assertNotIn('人物资料',result)
                self.assertNotIn('人物关系',result)
                self.assertNotIn('本轮发言者群资料',result)
            db.close()

    def test_first_contact_comes_from_events_and_unknown_relationship_not_invented(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'basic.db';archive=Path(d)/'raw.db';db=sqlite3.connect(path);info.initialize(db)
            src=sqlite3.connect(archive);src.execute('CREATE TABLE observations(id INTEGER,sender_id TEXT,received_at TEXT,chat_type TEXT,conversation_id TEXT,raw_json TEXT,account_id TEXT)')
            src.execute('INSERT INTO observations VALUES(1,?,?,?,?,?,?)',('u','2026-10-22T00:00:00Z','private','u',json.dumps({'raw_event':{'sender':{'nickname':'首次昵称'}}}),'bot'));src.commit();src.close()
            src=sqlite3.connect(archive)
            src.execute('INSERT INTO observations VALUES(2,?,?,?,?,?,?)',('group-only','2026-10-22T00:01:00Z','group','g',json.dumps({'raw_event':{'sender':{'nickname':'群成员'}}}),'bot'));src.commit();src.close()
            info.observe(db,'bot',archive);info.observe(db,'bot',archive)
            with patch.object(info,'DB',path):
                r=info.load_for_packet({'scene':{'platform':'qq','account':'bot','chat_type':'private','conversation':'u'},'new_messages':[]})
                self.assertEqual(r['人物资料'][0]['first_contact'],'2026-10-22T00:00:00Z')
                self.assertEqual(r['人物关系'],[])
            self.assertEqual(db.execute('SELECT count(*) FROM contacts').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM members').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT last_observation FROM ingestion').fetchone()[0],2);db.close()

if __name__=='__main__':unittest.main()
