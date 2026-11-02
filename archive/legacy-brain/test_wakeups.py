import unittest,sqlite3,tempfile,json
from pathlib import Path
from unittest.mock import patch
import wakeups,relay,mouth,memory_context
class WakeTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'mouth.db';self.db=sqlite3.connect(self.path);mouth.initialize(self.db);relay.initialize(self.db)
  self.archive=sqlite3.connect(':memory:');self.archive.execute('CREATE TABLE observations(id INTEGER,account_id TEXT,chat_type TEXT,conversation_id TEXT,raw_json TEXT,received_at TEXT)');self.archive.execute("INSERT INTO observations VALUES(1,'1','private','2','{}',NULL)")
  self.scene={'platform':'qq','account':'1','chat_type':'private','conversation':'2'};self.dest={'platform':'qq','account':'1','chat_type':'private','id':'2'}
  self.contacts=[dict(self.dest,name='果冻',remark=''),dict(self.dest,id='3',chat_type='group',name='测试群',remark='')]
  self.patch=patch.object(wakeups,'MOUTH',self.path);self.patch.start()
 def tearDown(self):self.patch.stop();self.archive.close();self.db.close();self.tmp.cleanup()
 def add(self,batch='b',minutes=5,depth=0,watch='对方'):
  text='唤醒意愿：等对方空闲再想想';plan={'segments':[],'wakeups':[{'start':0,'end':len(text),'idle_minutes':minutes,'watch':watch}]}
  items=wakeups.validate(plan,text,self.contacts,self.scene)
  self.db.execute("INSERT INTO relay_jobs(batch_id,status) VALUES(?,'control_only')",(batch,))
  wakeups.register(self.db,batch,items,{'wake_depth':depth},self.archive,now=100);self.db.commit();return plan
 def test_time_units_idle_and_restart(self):
  self.add(minutes=4320);self.assertEqual(wakeups.tick(self.db,self.archive,100),[])
  due=self.db.execute('SELECT due FROM intent_wakeups').fetchone()[0];self.assertEqual(due,100+3*24*60*60)
  self.db.close();self.db=sqlite3.connect(self.path)
  self.assertEqual(len(wakeups.tick(self.db,self.archive,due)),1)
 def test_new_inbound_or_outbound_cancels(self):
  self.add();wakeups.tick(self.db,self.archive,100)
  self.archive.execute("INSERT INTO observations VALUES(2,'1','private','2','{}',NULL)")
  self.assertEqual(wakeups.tick(self.db,self.archive,500),[])
  self.assertEqual(self.db.execute('SELECT status FROM intent_wakeups').fetchone()[0],'cancelled')
  self.add('c');wakeups.tick(self.db,self.archive,100)
  raw={'account_id':'1','chat_type':'private','conversation_id':'2','batch_id':'other','message_id':'99'}
  self.db.execute("INSERT INTO relay_deliveries(id,batch_id,ordinal,status,record_json) VALUES('out','other',0,'sent',?)",(json.dumps(raw),));self.db.commit()
  self.assertEqual(wakeups.tick(self.db,self.archive,500),[])
 def test_unique_target_and_overlap_and_duration_validation(self):
  self.assertEqual(wakeups.resolve('测试',self.contacts,self.dest)['id'],'3')
  with self.assertRaises(ValueError):wakeups.resolve('果冻',self.contacts+[dict(self.dest,id='4',name='果冻')],self.dest)
  for minutes in [0,-1,True,float('nan')]:
   with self.assertRaises(ValueError):self.add(minutes=minutes)
  with self.assertRaises(ValueError):wakeups.validate({'segments':[{'kind':'text','start':0,'end':3}],'wakeups':[{'start':0,'end':3,'idle_minutes':5}]},'意愿段',self.contacts,self.scene)
 def test_depth_guard_and_superseding(self):
  self.add(depth=1);self.assertEqual(self.db.execute('SELECT count(*) FROM intent_wakeups').fetchone()[0],0)
  self.add('c');self.add('d');self.assertEqual(self.db.execute("SELECT count(*) FROM intent_wakeups WHERE status='awaiting_delivery'").fetchone()[0],1)
 def test_no_repeat_single_brain_and_internal_event(self):
  self.add(minutes=1);wakeups.tick(self.db,self.archive,100)
  self.db.execute('UPDATE intent_wakeups SET due=0');self.db.commit()
  brain=sqlite3.connect(':memory:');brain.row_factory=sqlite3.Row;brain.execute('CREATE TABLE batches(id TEXT PRIMARY KEY,ids TEXT,account TEXT,kind TEXT,conversation TEXT,notified REAL DEFAULT 0,done INTEGER DEFAULT 0)')
  b=wakeups.poll(brain,self.archive);self.assertIsNotNone(b)
  self.assertEqual(wakeups.poll(brain,self.archive)['id'],b['id'])
  e=wakeups.event(brain,b['id']);new=wakeups.new_messages(e,self.archive)
  self.assertTrue(new[0]['observation']['internal_event']);self.assertTrue(wakeups.valid(e,self.archive))
  wakeups.complete(e);brain.execute('UPDATE batches SET done=1');brain.execute("INSERT OR IGNORE INTO scheduler_claims VALUES(1,'fixture')");brain.execute('DELETE FROM scheduler_queue');brain.commit()
  self.assertIsNone(wakeups.poll(brain,self.archive))
  with patch.object(memory_context,'PARTITION',Path(self.tmp.name)/'none'),patch.object(memory_context,'MIDDLE',Path(self.tmp.name)/'none'):
   layers=memory_context.load_layers({'scene':self.scene,'new_messages':new})
  self.assertEqual(layers['近期记忆'],[]);brain.close()
 def test_brain_event_runs_normal_judgment_without_fake_input_history(self):
  import brain_backend as brain,gc
  self.add(minutes=1);wakeups.tick(self.db,self.archive,100);self.db.execute('UPDATE intent_wakeups SET due=0');self.db.commit()
  root=Path(self.tmp.name);archive=root/'archive.db';a=sqlite3.connect(archive)
  a.execute('CREATE TABLE observations(id INTEGER PRIMARY KEY,account_id TEXT,chat_type TEXT,conversation_id TEXT,text_content TEXT,raw_json TEXT)')
  a.execute("INSERT INTO observations VALUES(1,'1','private','2','old','{}')");a.commit();a.close()
  with patch.object(brain,'ROOT',root),patch.object(brain,'DB',str(archive)),patch.object(brain,'MOUTH_DB',self.path):
   batch=brain.run('poll',{})['batch_id'];self.assertTrue(batch.startswith('wake:'))
   result=brain.run('read',{'batch_id':batch});self.assertTrue(result['internal_wake_active']);self.assertFalse(result['reply_constraint']['must_reply'])
   brain.run('finish',{'batch_id':batch,'should_reply':True,'reason':'仍有必要考虑','related_ids':[]})
   state=sqlite3.connect(root/'state.sqlite3');payload=json.loads(state.execute('SELECT payload FROM decisions').fetchone()[0]);state.close()
   self.assertEqual(payload['wake_depth'],1);self.assertEqual(payload['internal_wake']['intent'],'唤醒意愿：等对方空闲再想想')
   request=mouth.build_request(payload,'自然交流');context=json.loads(request['messages'][1]['content'])
   self.assertIn('当前唤醒意愿',context);self.assertFalse(any(x.get('current') for x in context['近期记忆']))
   gc.collect()
  a=sqlite3.connect(archive);self.assertEqual(a.execute('SELECT count(*) FROM observations').fetchone()[0],1);a.close()
 def test_internal_control_not_deliverable(self):
  plan=self.add();self.assertEqual(relay.validate(plan,'唤醒意愿：等对方空闲再想想',self.contacts),[])
if __name__=='__main__':unittest.main()
