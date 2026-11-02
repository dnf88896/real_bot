import json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import relay
import mouth
import image_library as lib
import output_memory

class RelayTest(unittest.TestCase):
    def destination(self,uid='11111'):
        return {'platform':'qq','account':relay.ACCOUNT,'chat_type':'private','id':uid}
    def segment(self,start,end,kind='text',dest=None):
        return {'start':start,'end':end,'kind':kind,'destination':dest or self.destination() if kind!='omit' else None}
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name)
        self.source=root/'archive.db';src=sqlite3.connect(self.source)
        src.execute('CREATE TABLE observations(id INTEGER,account_id TEXT,chat_type TEXT,conversation_id TEXT)')
        src.execute('INSERT INTO observations VALUES(1,?,?,?)',(relay.ACCOUNT,'private','11111'))
        src.execute('INSERT INTO observations VALUES(99,?,?,?)',(relay.ACCOUNT,'private','22222'));src.commit();src.close()
        self.path=root/'mouth.db';self.db=sqlite3.connect(self.path);mouth.initialize(self.db);relay.initialize(self.db)
        self.db.execute('CREATE TABLE deliveries(batch_id TEXT,status TEXT,updated REAL,message_id TEXT,record_json TEXT)');self.db.commit()
        self.patches=[patch.object(relay,'ARCHIVE',self.source),patch.object(lib,'LIBRARY',root/'images'),patch.object(lib,'INCOMING',root/'incoming')]
        for p in self.patches:p.start()
        lib.LIBRARY.mkdir();lib.INCOMING.mkdir()
        image=lib.LIBRARY/'07.jpg';image.write_bytes(b'\xff\xd8\xfftest')
        self.options=[{'kind':'sticker','description':'比心','path':str(image)}]
        self.contacts=[dict(self.destination(),name='当前用户'),dict(self.destination('22222'),name='果冻')]
        self.config={'allow_all_private':True,'allow_groups':[]}
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.db.close();self.temp.cleanup()
    def add(self,text):
        packet={'scene':{'platform':'qq','account':relay.ACCOUNT,'chat_type':'private','conversation':'11111'},'new_messages':[{'id':1}],'image_options':self.options}
        self.db.execute('INSERT INTO jobs(batch_id,packet,status) VALUES(?,?,?)',('b',json.dumps(packet),'generated'))
        self.db.execute('INSERT INTO outputs VALUES(?,?,?,?,?,?,?,?,0)',('b',1,text,'p','h','flash','{}','{}'))
        self.db.execute('INSERT INTO relay_jobs(batch_id,status,source_hash,directory) VALUES(?,?,?,?)',('b','pending',relay.digest(text),json.dumps(self.contacts)));self.db.commit()
    def test_multi_recipient_order_exact_text_sticker_and_destination_memory(self):
        text='给当前用户：\n你好🙂\n给果冻：\n今天晚点回家。\n【比心】';self.add(text)
        p1=text.index('你好');p2=text.index('给果冻');p3=text.index('今天');p4=text.index('【')
        plan={'segments':[self.segment(0,p1,'omit'),self.segment(p1,p2),self.segment(p2,p3,'omit'),self.segment(p3,p4,dest=self.destination('22222')),self.segment(p4,len(text),'sticker',self.destination('22222'))]}
        relay.plan_one(self.db,lambda *a:plan)
        calls=[]
        def fake(action,params):calls.append((action,params));return {'user_id':int(relay.ACCOUNT)} if action=='get_login_info' else {'message_id':len(calls)}
        while relay.send_one(self.db,self.config,fake):pass
        sent=[p for action,p in calls if action=='send_private_msg']
        self.assertEqual([p['user_id'] for p in sent],[11111,22222,22222])
        self.assertEqual(sent[0]['message'][0]['data']['text'],text[p1:p2].rstrip('\n'))
        self.assertEqual(sent[1]['message'][0]['data']['text'],text[p3:p4].rstrip('\n'))
        self.assertEqual(sent[2]['message'][0]['type'],'image')
        self.assertFalse(relay.send_one(self.db,self.config,fake))
        self.assertEqual(self.db.execute('SELECT status FROM relay_jobs').fetchone()[0],'delivered')
        self.assertEqual(self.db.execute('SELECT sent_to_qq FROM outputs').fetchone()[0],1)
        out=sqlite3.connect(':memory:',uri=True);output_memory.initialize(out)
        with patch.object(output_memory,'MOUTH',self.path):output_memory.collect(out)
        rows=out.execute("SELECT conversation_id,raw_json FROM events WHERE phase='sent' ORDER BY event_key").fetchall()
        self.assertEqual([r[0] for r in rows],['11111','22222','22222'])
        self.assertEqual(json.loads(rows[1][1])['text'],text[p3:p4].rstrip('\n'));out.close()
    def test_newline_bubbles_blank_lines_literal_slash_n_and_memory(self):
        text='第一条🙂\n\n  \n第二条\\n仍在同一条\r\n第三条';self.add(text)
        relay.plan_one(self.db,lambda *a:{'segments':[self.segment(0,len(text))]})
        calls=[]
        def fake(action,params):
            if action=='get_login_info':return {'user_id':int(relay.ACCOUNT)}
            calls.append(params);return {'message_id':len(calls)}
        while relay.send_one(self.db,self.config,fake):pass
        expected=['第一条🙂','第二条\\n仍在同一条','第三条']
        self.assertEqual([x['message'][0]['data']['text'] for x in calls],expected)
        records=[json.loads(x[0]) for x in self.db.execute('SELECT record_json FROM relay_deliveries ORDER BY ordinal')]
        self.assertEqual([r['text'] for r in records],expected)
        self.assertEqual(len({r['message_id'] for r in records}),3)
        for r in records:
            self.assertEqual(r['text'],''.join(text[p['start']:p['end']] for p in r['source_spans']))
        out=sqlite3.connect(':memory:',uri=True);output_memory.initialize(out)
        with patch.object(output_memory,'MOUTH',self.path):output_memory.collect(out)
        self.assertEqual(out.execute("SELECT count(*) FROM events WHERE phase='sent'").fetchone()[0],3);out.close()
        self.assertFalse(relay.send_one(self.db,self.config,fake))

    def test_later_bubble_transport_failure_does_not_repeat_first(self):
        text='第一条\n第二条\n第三条';self.add(text)
        relay.plan_one(self.db,lambda *a:{'segments':[self.segment(0,len(text))]})
        sent=[]
        def fake(action,params):
            if action=='get_login_info':return {'user_id':int(relay.ACCOUNT)}
            sent.append(params['message'][0]['data']['text'])
            if len(sent)==2:raise TimeoutError()
            return {'message_id':len(sent)}
        relay.send_one(self.db,self.config,fake);relay.send_one(self.db,self.config,fake)
        relay.initialize(self.db)
        while relay.send_one(self.db,self.config,fake):pass
        self.assertEqual(sent,['第一条','第二条','第三条'])
        self.assertEqual([x[0] for x in self.db.execute('SELECT status FROM relay_deliveries ORDER BY ordinal')],['sent','uncertain','sent'])
        self.assertEqual(self.db.execute('SELECT sent_to_qq FROM outputs').fetchone()[0],0)

    def test_annotation_gaps_preserve_newline_boundaries_and_named_sticker(self):
        text='第一条\n\n表情包7（比心）\n\n第三条';self.add(text)
        start=text.index('表情包');end=text.index('）')+1;last=text.index('第三条')
        relay.plan_one(self.db,lambda *a:{'segments':[self.segment(0,3),self.segment(start,end,'sticker'),self.segment(last,len(text))]})
        calls=[]
        def fake(action,params):
            if action=='get_login_info':return {'user_id':int(relay.ACCOUNT)}
            calls.append(params);return {'message_id':len(calls)}
        while relay.send_one(self.db,self.config,fake):pass
        self.assertEqual(len(calls),3)
        self.assertEqual([p['message'][0]['type'] for p in calls],['text','image','text'])
        self.assertEqual(calls[0]['message'][0]['data']['text'],'第一条')
        self.assertEqual(calls[2]['message'][0]['data']['text'],'第三条')
        self.assertEqual(relay.sticker_path('表情包7（哭泣）',self.options),self.options[0]['path'])

    def test_sticker_number_keywords_override_description_without_prefix_collision(self):
        for reference in ['【表情包7：比心】','【表情包07: 比心】','表情包7（比心）','表情包7(比心)','【比心】','表情包7','表情包7（比心','【表情包7：哭泣】','请发表情包7：比心','【表情包7：比心】其他文字']:
            self.assertEqual(relay.sticker_path(reference,self.options),self.options[0]['path'])
        for reference in ['【表情包99：比心】','表情包70','表情包7 表情包8']:
            with self.assertRaises(relay.Unavailable):relay.sticker_path(reference,self.options)

    def test_only_positions_allowed_and_gaps_unknown_recipient_rejected(self):
        text='重复🙂重复'
        s=self.segment(0,len(text));s['text']='改写'
        with self.assertRaises(ValueError):relay.validate({'segments':[s]},text,self.contacts)
        with self.assertRaises(ValueError):relay.validate({'segments':[self.segment(0,3),self.segment(2,len(text))]},text,self.contacts)
        with self.assertRaises(ValueError):relay.validate({'segments':[self.segment(0,len(text),dest=self.destination('99999'))]},text,self.contacts)
        with self.assertRaises(relay.Unavailable):relay.validate({'segments':[self.segment(0,len(text),'omit')]},text,self.contacts)
    def test_voice_unsupported_is_not_sent_and_changed_source_rejected(self):
        text='语音：我在说话哦';self.add(text)
        relay.plan_one(self.db,lambda *a:{'segments':[self.segment(0,3,'omit'),self.segment(3,len(text),'voice')]})
        calls=[];relay.send_one(self.db,self.config,lambda *a:calls.append(a))
        self.assertEqual(calls,[]);self.assertEqual(self.db.execute('SELECT status FROM relay_deliveries').fetchone()[0],'failed')
        self.db.execute("UPDATE relay_deliveries SET status='pending'");self.db.execute("UPDATE outputs SET text='被修改'");self.db.commit()
        relay.send_one(self.db,self.config,lambda *a:calls.append(a));self.assertEqual(calls,[])
    def test_no_automatic_repeat_after_ambiguous_transport_and_restart(self):
        self.add('原样');relay.plan_one(self.db,lambda *a:{'segments':[self.segment(0,2)]})
        def lost(action,params):
            if action=='get_login_info':return {'user_id':int(relay.ACCOUNT)}
            raise TimeoutError()
        relay.send_one(self.db,self.config,lost);relay.initialize(self.db)
        self.assertEqual(self.db.execute('SELECT status FROM relay_deliveries').fetchone()[0],'uncertain')
        self.assertFalse(relay.send_one(self.db,self.config,lost))
    def test_image_path_span_uses_only_offered_file_and_no_path_in_text(self):
        path=self.options[0]['path']
        msg,_,images=relay.compile_parts(path,[{'kind':'image','start':0,'end':len(path)}],self.options)
        self.assertEqual(msg[0]['type'],'image');self.assertEqual(images,[path])
        with self.assertRaises(relay.Unavailable):relay.compile_parts('/opt/private/a.png',[{'kind':'text','start':0,'end':18}],self.options)
        with self.assertRaises(relay.Unavailable):relay.destination_allowed({**self.destination(),'chat_type':'group'},self.config)

    def test_same_body_can_be_sent_to_two_people_but_not_twice_to_same_person(self):
        plan={'segments':[self.segment(0,2),self.segment(0,2,dest=self.destination('22222'))]}
        self.assertEqual(len(relay.validate(plan,'你好',self.contacts)),2)
        plan['segments'].append(self.segment(0,2))
        with self.assertRaises(ValueError):relay.validate(plan,'你好',self.contacts)

    def test_group_preview_text_sticker_private_transport_group_memory(self):
        text='群聊测试🙂\n【比心】';self.add(text)
        dest={**self.destination('33333'),'chat_type':'group'}
        packet=json.loads(self.db.execute('SELECT packet FROM jobs').fetchone()[0]);packet['scene'].update(chat_type='group',conversation='33333')
        self.db.execute('UPDATE jobs SET packet=?',(json.dumps(packet),))
        self.db.execute('UPDATE relay_jobs SET directory=?',(json.dumps(self.contacts+[dest]),));self.db.commit()
        src=sqlite3.connect(self.source);src.execute('INSERT INTO observations VALUES(101,?,?,?)',(relay.ACCOUNT,'group','33333'));src.commit();src.close()
        boundary=text.index('【')
        relay.plan_one(self.db,lambda *a:{'segments':[self.segment(0,boundary,dest=dest),self.segment(boundary,len(text),'sticker',dest)]})
        cfg={**self.config,'group_delivery_mode':'owner_preview','group_preview_recipient':'100000001','preview_groups':['33333']}
        calls=[]
        def fake(action,params):calls.append((action,params));return {'user_id':int(relay.ACCOUNT)} if action=='get_login_info' else {'message_id':123}
        while relay.send_one(self.db,cfg,fake):pass
        sent=[x for x in calls if x[0]=='send_private_msg'][0];self.assertEqual(sent[0],'send_private_msg');self.assertEqual(sent[1]['user_id'],100000001)
        self.assertIn('群聊预览',sent[1]['message'][0]['data']['text'])
        self.assertEqual(sent[1]['message'][1]['data']['text'],text[:boundary].rstrip('\n'));self.assertEqual(calls[-1][1]['message'][1]['type'],'image')
        out=sqlite3.connect(':memory:',uri=True);output_memory.initialize(out)
        with patch.object(output_memory,'MOUTH',self.path):output_memory.collect(out)
        conv,raw=out.execute("SELECT conversation_id,raw_json FROM events WHERE phase='sent' ORDER BY rowid").fetchone();r=json.loads(raw)
        self.assertEqual(conv,'33333');self.assertEqual(r['chat_type'],'group');self.assertEqual(r['delivery_mode'],'group_preview')
        self.assertEqual(r['actual_destination']['id'],'100000001');self.assertEqual(r['text'],text[:boundary].rstrip('\n'))
        from memory_context import render
        self.assertIn('未实际发入群聊',render(r)['delivery_note']);out.close()

if __name__=='__main__':unittest.main()
