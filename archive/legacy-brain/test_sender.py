import json,sqlite3,tempfile,unittest
from pathlib import Path
import qq_sender as sender

class SenderTest(unittest.TestCase):
    def packet(self,i=2,conversation='100000001'):
        return {'scene':{'platform':'qq','account':sender.ACCOUNT,'chat_type':'private','conversation':conversation},'new_messages':[{'id':i}]}
    def test_cutover_scope_exact_text_and_no_duplicate(self):
        self.assertEqual(sender.eligible(self.packet(1),{'cutover_observation_id':1,'allow_private':['100000001']}),'before_cutover')
        self.assertEqual(sender.eligible(self.packet(conversation='other'),{'cutover_observation_id':1}),'not_allowed')
        config={'cutover_observation_id':1,'allow_all_private':True,'all_private_cutover_observation_id':1}
        with tempfile.TemporaryDirectory() as d:
            sender.ARCHIVE=Path(d)/'archive.db';a=sqlite3.connect(sender.ARCHIVE)
            a.execute('CREATE TABLE observations(id INTEGER,account_id TEXT,chat_type TEXT,conversation_id TEXT)')
            recipient='400000011'
            a.execute('INSERT INTO observations VALUES(2,?,?,?)',(sender.ACCOUNT,'private',recipient))
            a.execute('INSERT INTO observations VALUES(99,?,?,?)',(sender.ACCOUNT,'private','100000001'));a.commit();a.close()
            db=sqlite3.connect(':memory:')
            db.executescript('CREATE TABLE outputs(batch_id TEXT,text TEXT,sent_to_qq INTEGER,created REAL); CREATE TABLE jobs(batch_id TEXT,packet TEXT);')
            db.execute('INSERT INTO outputs VALUES(?,?,0,1)',('b','hello [CQ:image,file=x]'))
            db.execute('INSERT INTO jobs VALUES(?,?)',('b',json.dumps(self.packet(conversation=recipient))));db.commit();sender.initialize(db)
            calls=[]
            def fake(action,params):
                calls.append((action,params));return {'user_id':int(sender.ACCOUNT)} if action=='get_login_info' else {'message_id':123}
            self.assertTrue(sender.send_one(db,config,fake))
            self.assertFalse(sender.send_one(db,config,fake))
            self.assertEqual(calls[1][1]['user_id'],int(recipient))
            self.assertEqual(calls[1][1]['message'][0]['type'],'text')
            self.assertEqual(db.execute('SELECT status FROM deliveries').fetchone()[0],'sent');db.close()

    def test_private_expansion_keeps_groups_closed_and_honors_cutover(self):
        cfg={'cutover_observation_id':1,'allow_private':['100000001'],'allow_all_private':True,'all_private_cutover_observation_id':10}
        self.assertIsNone(sender.eligible(self.packet(11,'400000011'),cfg))
        self.assertEqual(sender.eligible(self.packet(10,'400000011'),cfg),'before_cutover')
        self.assertIsNone(sender.eligible(self.packet(2),cfg))
        group=self.packet(11,'400000011');group['scene']['chat_type']='group'
        self.assertEqual(sender.eligible(group,cfg),'not_allowed')
        self.assertEqual(sender.eligible(self.packet(11,sender.ACCOUNT),cfg),'not_allowed')
        self.assertEqual(sender.eligible(self.packet(11,'invalid'),cfg),'not_allowed')
        cfg['allow_all_private']=False
        self.assertEqual(sender.eligible(self.packet(11,'400000011'),cfg),'not_allowed')

if __name__=='__main__':unittest.main()
