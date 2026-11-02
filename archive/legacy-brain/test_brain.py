import gc,json, sqlite3, tempfile, unittest
from pathlib import Path
import brain_backend as brain
from unittest.mock import patch

class BrainTest(unittest.TestCase):
    def test_owner_new_group_message_forces_reply_without_direction(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);archive=root/'raw.db';db=sqlite3.connect(archive)
            db.execute('CREATE TABLE observations(id INTEGER PRIMARY KEY,account_id TEXT,chat_type TEXT,conversation_id TEXT,text_content TEXT,raw_json TEXT)')
            raw={'sender_id':brain.OWNER,'account_id':'100000002','chat_type':'group','conversation_id':'g','raw_event':{'message':'好'}}
            db.execute('INSERT INTO observations VALUES(1,?,?,?,?,?)',('100000002','group','g','好',json.dumps(raw)));db.commit();db.close()
            (root/'scheduler-config.json').write_text('{"group_coalesce_seconds":0}')
            with patch.object(brain,'ROOT',root),patch.object(brain,'DB',str(archive)):
                b=brain.run('poll',{})['batch_id']
                self.assertTrue(brain.run('read',{'batch_id':b})['reply_constraint']['must_reply'])
                result=brain.run('finish',{'batch_id':b,'should_reply':False,'reason':'没有@','related_ids':[]})
                self.assertEqual(result['status'],'awaiting_mouth')
                state=sqlite3.connect(root/'state.sqlite3');reply,payload=state.execute('SELECT should_reply,payload FROM decisions').fetchone();state.close()
                self.assertEqual(reply,1);self.assertNotIn('reply_constraint',json.loads(payload));self.assertNotIn('reply_intent',json.loads(payload))
                self.assertFalse(brain.mandatory_reply([{'observation':{'sender_id':'other'}}]))
                gc.collect()
    def test_dynamic_eligibility_preserves_brain_memory_without_forcing_reply(self):
        import behavior
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);archive=root/'raw.db';part=root/'long.db'
            old={'sender_id':'other','account_id':'999','chat_type':'group','conversation_id':'testgroup','raw_event':{'message':'之前约好去踢球'}}
            new={**old,'raw_event':{'message':'后来踢球怎么样了'}}
            db=sqlite3.connect(archive);db.execute('CREATE TABLE observations(id INTEGER PRIMARY KEY,account_id TEXT,chat_type TEXT,conversation_id TEXT,text_content TEXT,raw_json TEXT)')
            for oid,raw in [(1,old),(2,new)]:db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?)',(oid,'999','group','testgroup','踢球',json.dumps(raw)))
            db.commit();db.close()
            db=sqlite3.connect(part);db.execute('CREATE TABLE long_entries(event_key TEXT,raw_json TEXT,account_id TEXT,chat_type TEXT,conversation_id TEXT,event_time TEXT)')
            db.execute('INSERT INTO long_entries VALUES(?,?,?,?,?,?)',('input:1',json.dumps(old,ensure_ascii=False),'999','group','testgroup','2026-01-01'));db.commit();db.close()
            (root/'scheduler-config.json').write_text('{"group_coalesce_seconds":0}')
            behavior_path=root/'behavior.json';behavior_path.write_text('{"enabled":true}')
            with patch.object(behavior,'CONFIG',behavior_path),patch.object(brain,'ROOT',root),patch.object(brain,'DB',str(archive)),patch.object(brain,'PARTITION',part),patch.object(behavior.secrets,'randbelow',return_value=0) as draw:
                brain.run('scheduler_stats',{})
                state=sqlite3.connect(root/'state.sqlite3');state.execute("INSERT INTO scheduler_claims VALUES(1,'old')");state.commit();state.close()
                batch=brain.run('poll',{})['batch_id'];r=brain.run('read',{'batch_id':batch})
                self.assertEqual(r['participation_state']['eligible_ids'],[2]);self.assertFalse(r['reply_constraint']['must_reply'])
                memory=brain.run('query',{'batch_id':batch,'query':'踢球'})
                self.assertEqual(memory['messages'][0]['id'],1)
                brain.run('finish',{'batch_id':batch,'should_reply':False,'reason':'无人点名','related_ids':[1],'related_event_keys':['input:1']})
                state=sqlite3.connect(root/'state.sqlite3');should,raw=state.execute('SELECT should_reply,payload FROM decisions').fetchone();state.close()
                payload=json.loads(raw);self.assertEqual(should,0);self.assertEqual(payload['related_memories'][0]['id'],1)
                self.assertNotIn('must_reply',payload['new_messages'][0]);self.assertNotIn('reply_intent',payload);self.assertEqual(draw.call_count,1)
                gc.collect()

    def test_only_confirmed_prior_replies_from_same_conversation(self):
        with tempfile.TemporaryDirectory() as d:
            old=brain.MOUTH_DB;brain.MOUTH_DB=Path(d)/'mouth.db'
            try:
                db=sqlite3.connect(brain.MOUTH_DB)
                db.execute('CREATE TABLE deliveries(status TEXT,updated REAL,record_json TEXT)')
                for status,t,conv,text in [('sent',50,'a','earlier'),('sent',150,'a','future'),('sent',60,'b','other'),('uncertain',70,'a','unknown')]:
                    db.execute('INSERT INTO deliveries VALUES(?,?,?)',(status,t,json.dumps({'account_id':'1','chat_type':'private','conversation_id':conv,'text':text,'sent_at':t})))
                db.commit();db.close()
                rows=brain.sent_context({'account':'1','kind':'private','conversation':'a'},[{'observation':{'received_at':'1970-01-01T00:01:40Z'}}])
                self.assertEqual([r['text'] for r in rows],['earlier'])
            finally:brain.MOUTH_DB=old

    def test_batch_scope_reference_validation_and_idempotent_finish(self):
        with tempfile.TemporaryDirectory() as d:
            brain.ROOT=Path(d);brain.DB=str(Path(d)/'archive.db')
            (brain.ROOT/'scheduler-config.json').write_text('{"private_coalesce_seconds":0}')
            db=sqlite3.connect(brain.DB)
            db.execute('CREATE TABLE observations(id INTEGER PRIMARY KEY,account_id TEXT,chat_type TEXT,conversation_id TEXT,text_content TEXT,raw_json TEXT)')
            for i,conv in [(1,'a'),(2,'b'),(3,'a')]:
                db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?)',(i,'100000002','private',conv,'hello',json.dumps({'text':'hello','conversation':conv})))
            db.commit();db.close()
            batch=brain.run('poll',{});b=batch['batch_id']
            self.assertEqual(batch['count'],2)
            self.assertEqual(brain.run('read',{'batch_id':b})['messages'][0]['id'],1)
            with self.assertRaises(ValueError):brain.run('read',{'batch_id':'wrong'})
            self.assertEqual(brain.run('query',{'batch_id':b,'query':''})['messages'],[])
            args={'batch_id':b,'should_reply':True,'reason':'direct message','related_ids':[2]}
            with self.assertRaises(ValueError):brain.run('finish',args)
            args['related_ids']=[]
            self.assertEqual(brain.run('finish',args)['status'],'awaiting_mouth')
            state=sqlite3.connect(brain.ROOT/'state.sqlite3')
            payload=json.loads(state.execute('SELECT payload FROM decisions WHERE batch_id=?',(b,)).fetchone()[0])
            self.assertNotIn('reply_intent',payload);self.assertNotIn('reason',payload)
            self.assertEqual(payload['new_messages'][0]['id'],1)
            state.close()
            self.assertTrue(brain.run('finish',args)['already_recorded'])
            second=brain.run('poll',{});self.assertNotEqual(second['batch_id'],b)
            brain.run('finish',{'batch_id':second['batch_id'],'should_reply':False,'reason':'no reply needed','related_ids':[]})
            self.assertFalse(brain.run('poll',{})['pending'])
            # In-process calls retain closure cycles; the deployed CLI exits per call.
            gc.collect()

if __name__=='__main__':unittest.main()
