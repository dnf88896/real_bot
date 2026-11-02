import json,sqlite3,tempfile,unittest
from pathlib import Path
import memory_context as ctx
from short_memory import initialize
from middle_memory import initialize as middle_init

class ContextTest(unittest.TestCase):
    def test_layers_scope_future_and_source_dedup(self):
        with tempfile.TemporaryDirectory() as d:
            old=(ctx.PARTITION,ctx.MIDDLE);ctx.PARTITION=Path(d)/'p.db';ctx.MIDDLE=Path(d)/'m.db'
            try:
                db=sqlite3.connect(ctx.PARTITION);initialize(db)
                def incoming(i,stamp,text):return {'received_at':stamp,'sender_id':'u','raw_event':{'message':[{'type':'text','data':{'text':text}}]}}
                now='2026-10-21T10:00:00Z';new=incoming(2,now,'本次消息')
                for key,conv,stamp,raw in [('input:2','u',now,new),('input:4','other',now,incoming(4,now,'别人的话')),('input:5','u','2026-10-22T10:00:00Z',incoming(5,now,'未来消息'))]:
                    db.execute('INSERT INTO entries VALUES(?,?,?,?,?,?,?)',(key,stamp,'1','private',conv,'incoming',json.dumps(raw)))
                raw=incoming(3,'2026-10-19T10:00:00Z','旧事')
                db.execute('INSERT INTO long_entries VALUES(?,?,?,?,?,?,?)',('input:3',raw['received_at'],'1','private','u','incoming',json.dumps(raw)));db.commit();db.close()
                mid=sqlite3.connect(ctx.MIDDLE);middle_init(mid)
                for key,content,ref in [('summary','旧事概括','input:3'),('overlap','重复近期','input:2')]:
                    mid.execute('INSERT INTO memories VALUES(?,?,?,?,?,?,?,?,?,?)',(key,'qq','1','private','u',content,'2026-10-18','2026-10-20','active','2026-10-20'))
                    mid.execute('INSERT INTO source_refs VALUES(?,?)',(key,ref))
                mid.commit();mid.close()
                packet={'scene':{'platform':'qq','account':'1','chat_type':'private','conversation':'u'},'new_messages':[{'id':2,'observation':new}],'related_memories':[{'id':3,'observation':raw}]}
                result=ctx.load_layers(packet)
                self.assertEqual(len(result['近期记忆']),1);self.assertTrue(result['近期记忆'][0]['current'])
                self.assertEqual(result['概括记忆'],['旧事概括']);self.assertEqual(result['相关记忆'],[])
            finally:ctx.PARTITION,ctx.MIDDLE=old

if __name__=='__main__':unittest.main()
