import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import message_identity as identity
from memory_context import render

class IdentityTest(unittest.TestCase):
    def test_private_and_group_labels_with_live_cache_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'contacts.json'
            path.write_text(json.dumps({'account_id':'1','users':{'2':'果冻','1':'机器人昵称'},'groups':{'3':'测试群'}}),encoding='utf8')
            with patch.object(identity,'CACHE',path):
                private={'account_id':'1','chat_type':'private','conversation_id':'2','sender_id':'2','raw_event':{'sender':{'nickname':''},'message':'你好'}}
                result=render(private);self.assertEqual(result['display'],'[私聊]果冻： 你好')
                self.assertEqual(result['speaker'],'2');self.assertEqual(result['text'],'你好')
                group={**private,'chat_type':'group','conversation_id':'3','raw_event':{'sender':{'nickname':'群友'},'message':'在吗'}}
                self.assertEqual(render(group)['display'],'[测试群]群友： 在吗')
                self.assertEqual(render(group)['group_name'],'测试群')
                group['raw_event']['group_name']='<$ÿĀ\x11\x10>事件中的原群名'
                self.assertEqual(render(group)['source_label'],'[事件中的原群名]群友：')
                outgoing={'direction':'outgoing','account_id':'1','chat_type':'private','conversation_id':'2','text':'收到'}
                self.assertEqual(render(outgoing)['display'],'[私聊]机器人昵称： 收到')

    def test_unknown_names_account_isolation_and_plain_text_labels(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'contacts.json';path.write_text(json.dumps({'account_id':'other','users':{'2':'错误账号昵称'},'groups':{'3':'错误群名'}}))
            with patch.object(identity,'CACHE',path):
                raw={'account_id':'1','chat_type':'group','conversation_id':'3','sender_id':'2','raw_event':{'message':'原文'}}
                self.assertEqual(render(raw)['display'],'[群 3]QQ 2： 原文')
                raw['raw_event']['sender']={'nickname':'名字\n第二行'}
                self.assertEqual(render(raw)['nickname'],'名字 第二行')
                path.write_text('broken')
                self.assertEqual(render(raw)['group_name'],'群 3')

if __name__=='__main__':unittest.main()
