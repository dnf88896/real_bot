import json,sqlite3,unittest
from mouth import build_request,initialize,process_one,expression_card,TASK

class MouthTest(unittest.TestCase):
    def packet(self):
        item={'id':1,'observation':{'account_id':'1','chat_type':'private','conversation_id':'2','sender_id':'2','raw_event':{'message':[{'type':'text','data':{'text':'今天很累'}},{'type':'image','data':{'url':'private-image-url'}}]}}}
        return {'status':'awaiting_mouth','scene':{'platform':'qq','account':'1','chat_type':'private','conversation':'2'},'new_messages':[item],'related_memories':[item]}
    def test_task_persona_stateless_scope_and_single_output(self):
        p=self.packet();r=build_request(p,'人格测试')
        self.assertEqual(r['thinking'],{'type':'enabled'});self.assertEqual(r['reasoning_effort'],'high');self.assertEqual(r['max_tokens'],8192)
        self.assertIn('人格测试',r['messages'][0]['content']);self.assertNotIn('tools',r)
        context=json.loads(r['messages'][1]['content']);self.assertEqual(context['相关记忆'],[])
        self.assertNotIn('private-image-url',r['messages'][1]['content'])
        db=sqlite3.connect(':memory:');initialize(db)
        db.execute('INSERT INTO jobs(batch_id,packet) VALUES(?,?)',('batch',json.dumps(p)));db.commit()
        calls=[]
        def fake(request):calls.append(request);return '嗯，先歇一会儿吧。',{}
        process_one(db,fake,lambda:('test','人格测试'));self.assertFalse(process_one(db,fake,lambda:('test','人格测试')))
        self.assertEqual(len(calls),1);self.assertEqual(db.execute('SELECT sent_to_qq FROM outputs').fetchone()[0],0)
        p['new_messages'][0]['observation']['conversation_id']='other'
        with self.assertRaises(ValueError):build_request(p,'人格测试')
        db.close()

    def test_expression_only_prompt(self):
        self.assertNotIn('系统',TASK)
        self.assertNotIn('脑',TASK)
        self.assertNotIn('QQ',TASK)
        card=expression_card('温柔、认真。\n## 使用范围\n查记忆、工具配置。')
        self.assertEqual(card,'温柔、认真。')
        request=build_request(self.packet(),'温柔、认真。')
        scene=json.loads(request['messages'][1]['content'])['scene']
        self.assertNotIn('account',scene)
        self.assertNotIn('conversation',scene)
        self.assertEqual(scene['category'],'qq_private')
        self.assertNotIn('tools',request)

    def test_legacy_directions_and_decision_reason_cannot_enter_api(self):
        p=self.packet();p['reply_intent']='LEGACY_DIRECTION_DO_NOT_PASS';p['reason']='PRIVATE_DECISION_DO_NOT_PASS'
        request=build_request(p,'自然交流')
        encoded=json.dumps(request,ensure_ascii=False)
        self.assertNotIn('LEGACY_DIRECTION_DO_NOT_PASS',encoded)
        self.assertNotIn('PRIVATE_DECISION_DO_NOT_PASS',encoded)
        self.assertNotIn('reply_intent',encoded)
        self.assertNotIn('回应方向',TASK)

    def test_sent_replies_are_context_without_internal_delivery_fields(self):
        p=self.packet()
        p['recent_bot_replies']=[{'account_id':'1','chat_type':'private','conversation_id':'2','sent_at':1,'text':'昨天踢球后来怎样了？','message_id':'internal'}]
        r=build_request(p,'自然交流')
        context=json.loads(r['messages'][1]['content'])
        self.assertNotIn('previous_own_replies',context)
        self.assertNotIn('message_id',str(context['近期记忆']))
        p['recent_bot_replies'][0]['conversation_id']='other'
        with self.assertRaises(ValueError):build_request(p,'自然交流')

if __name__=='__main__':unittest.main()
