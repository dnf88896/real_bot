import unittest,tempfile,json,sqlite3,random
from pathlib import Path
from unittest.mock import patch
import behavior,self_state,mouth,relay
import test_relay as relay_tests
class BehaviorTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.cfg=self.root/'behavior.json';self.cfg.write_text('{"enabled":true}')
  self.p=patch.object(behavior,'CONFIG',self.cfg);self.p.start()
 def tearDown(self):self.p.stop();self.tmp.cleanup()
 def configure(self,**extra):self.cfg.write_text(json.dumps({'enabled':True,**extra}))
 def item(self,i=1,kind='group',uid='other',parts=None):return {'id':i,'observation':{'chat_type':kind,'account_id':'a','sender_id':uid,'raw_event':{'message':parts or []}}}
 def test_global_state_refresh_and_no_automatic_transition(self):
  state=self.root/'state.json';state.write_text(json.dumps(self_state.DEFAULT,ensure_ascii=False),encoding='utf8')
  with patch.object(self_state,'STATE',state):
   self.assertEqual(self_state.load(),{'情绪':'开心','清醒程度':'清醒'})
   state.write_text(json.dumps({'emotion':{'value':'生气'},'wakefulness':{'value':'迷糊'}},ensure_ascii=False),encoding='utf8')
   self.assertEqual(self_state.load(),{'情绪':'生气','清醒程度':'迷糊'})
 def test_dynamic_probabilities_decay_cooldown_and_density(self):
  db=sqlite3.connect(':memory:');scene={'account':'a','chat_type':'group','conversation':'g'}
  with patch.object(behavior,'reply_times',return_value=[980]),patch.object(behavior.secrets,'randbelow',return_value=0) as draw:
   r=behavior.participation(db,[self.item()],scene,'b','missing','owner',now=1000)
   self.assertTrue(r['allowed']);self.assertGreater(r['probability'],0.02)
   again=behavior.participation(db,[self.item()],scene,'b','missing','owner',now=1001)
   self.assertEqual(draw.call_count,1)
  with patch.object(behavior,'reply_times',return_value=[995]):
   r=behavior.participation(db,[self.item()],scene,'b','missing','owner',now=1000);self.assertFalse(r['allowed'])
   self.assertTrue(behavior.participation(db,[self.item(uid='owner')],scene,'b','missing','owner',now=1000)['allowed'])
   self.assertTrue(behavior.participation(db,[self.item(parts=[{'type':'at','data':{'qq':'a'}}])],scene,'b','missing','owner',now=1000)['allowed'])
  with patch.object(behavior,'reply_times',return_value=[950,960,970]):
   self.assertFalse(behavior.participation(db,[self.item()],scene,'b','missing','owner',now=1000)['allowed'])
  with patch.object(behavior,'reply_times',return_value=[800]):
   self.assertEqual(behavior.participation(db,[self.item(2)],scene,'c','missing','owner',now=1000)['probability'],0.02)
  self.assertTrue(behavior.participation(db,[self.item(kind='private')],{**scene,'chat_type':'private'},'b','missing','owner',1000)['allowed']);db.close()
 def test_typo_protection_and_single_bounded_change(self):
  for text in ['https://example.com/呀','`嗯`','12345','不要取消约定','嗯同学']:
   sent,changes=behavior.variant(text,['嗯同学'],random.Random(1),1)
   self.assertEqual(sent,text);self.assertEqual(changes,[])
  sent,changes=behavior.variant('嗯，今天真开心呀。',[],random.Random(2),1)
  self.assertEqual(len(changes),1);self.assertNotEqual(sent,'嗯，今天真开心呀。')
  self.assertEqual(behavior.variant('嗯。',[],random.Random(2),0),('嗯。',[]))
 def test_persisted_rendering_is_stable_and_preserves_original_audit(self):
  fixture=relay_tests.RelayTest(methodName='test_image_path_span_uses_only_offered_file_and_no_path_in_text');fixture.setUp()
  try:
   self.configure(typo_probability=1)
   text='嗯，今天开心呀。\n第二条';fixture.add(text)
   relay.plan_one(fixture.db,lambda *a:{'segments':[fixture.segment(0,len(text))]})
   calls=[]
   def fake(action,args):
    if action=='get_login_info':return {'user_id':int(relay.ACCOUNT)}
    calls.append(args);return {'message_id':str(len(calls))}
   with patch.object(relay.time,'time',return_value=1000):
    self.assertTrue(relay.send_one(fixture.db,fixture.config,fake));self.assertEqual(calls,[])
   snapshot=fixture.db.execute('SELECT message,not_before FROM delivery_renderings').fetchone()
   self.assertGreater(snapshot[1],1000)
   with patch.object(relay.time,'time',return_value=1001):relay.send_one(fixture.db,fixture.config,fake)
   self.assertEqual(snapshot,fixture.db.execute('SELECT message,not_before FROM delivery_renderings').fetchone())
   with patch.object(relay.time,'time',return_value=1010):relay.send_one(fixture.db,fixture.config,fake)
   self.assertEqual(len(calls),1)
   record=json.loads(fixture.db.execute("SELECT record_json FROM relay_deliveries WHERE status='sent'").fetchone()[0])
   self.assertEqual(record['text'],calls[0]['message'][0]['data']['text']);self.assertNotEqual(record['text'],record['original_text'])
   out=sqlite3.connect(':memory:',uri=True);relay_tests.output_memory.initialize(out)
   with patch.object(relay_tests.output_memory,'MOUTH',fixture.path):relay_tests.output_memory.collect(out)
   remembered=json.loads(out.execute("SELECT raw_json FROM events WHERE phase='sent'").fetchone()[0])
   self.assertEqual(remembered['text'],record['text']);out.close()
   self.assertEqual(fixture.db.execute("SELECT count(*) FROM relay_deliveries WHERE status='pending'").fetchone()[0],1)
  finally:fixture.tearDown()
 def test_delayed_conversation_does_not_block_other_recipient(self):
  fixture=relay_tests.RelayTest(methodName='test_multi_recipient_order_exact_text_sticker_and_destination_memory');fixture.setUp()
  try:
   fixture.add('hello world')
   relay.plan_one(fixture.db,lambda *a:{'segments':[fixture.segment(0,5),fixture.segment(6,11,dest=fixture.destination('22222'))]})
   with patch.object(relay.time,'time',return_value=1000):
    relay.send_one(fixture.db,fixture.config,lambda *a:None)
    relay.send_one(fixture.db,fixture.config,lambda *a:None)
   self.assertEqual(fixture.db.execute('SELECT count(*) FROM delivery_renderings').fetchone()[0],2)
   # Keep the first recipient waiting; the second must still be deliverable.
   fixture.db.execute('UPDATE delivery_renderings SET not_before=CASE WHEN delivery_id=(SELECT id FROM relay_deliveries ORDER BY rowid LIMIT 1) THEN 2000 ELSE 1000 END');fixture.db.commit()
   calls=[]
   def fake(action,args):
    if action=='get_login_info':return {'user_id':int(relay.ACCOUNT)}
    calls.append(args);return {'message_id':'x'}
   with patch.object(relay.time,'time',return_value=1010):relay.send_one(fixture.db,fixture.config,fake)
   self.assertEqual([x['user_id'] for x in calls],[22222])
  finally:fixture.tearDown()
if __name__=='__main__':unittest.main()
