import json,sqlite3,tempfile,unittest
from pathlib import Path
from output_memory import initialize as output_init,record_output
from short_memory import initialize,refresh_entries,query

class OutputMemoryTest(unittest.TestCase):
    def test_multiplatform_lifecycle_idempotency_and_shared_capacity(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'output.db';out=sqlite3.connect(path);output_init(out)
            args=dict(event_key='bili:1:generated',timestamp=2,platform='bilibili',account='a',chat_type='comment',conversation='video1',phase='generated',text='原文',images=['pic'])
            self.assertTrue(record_output(out,**args));out.commit()
            self.assertFalse(record_output(out,**args))
            args.update(event_key='bili:1:sent',phase='sent',timestamp=3,receipt='r')
            record_output(out,**args);out.commit()
            short=sqlite3.connect(':memory:');initialize(short)
            raw=json.dumps({'text':'输入原文'})
            short.execute('INSERT INTO observations VALUES(1,?,?,?,?,?,?,?,?)',('eye','qq','a','private','u','1970-01-01T00:00:01.000Z',raw,'{}'));short.commit()
            refresh_entries(short,path,2)
            self.assertEqual(short.execute('SELECT count(*) FROM entries').fetchone()[0],2)
            self.assertEqual(short.execute('SELECT count(*) FROM long_entries').fetchone()[0],1)
            self.assertEqual(short.execute('SELECT count(*) FROM entries s JOIN long_entries l ON s.event_key=l.event_key').fetchone()[0],0)
            rows=query(short,'a','comment','video1')
            self.assertEqual([r['phase'] for r in rows],['generated','sent'])
            self.assertEqual(rows[1]['text'],'原文')
            self.assertEqual(out.execute('SELECT count(*) FROM events').fetchone()[0],2)
            refresh_entries(short,path,3)
            self.assertEqual(short.execute('SELECT count(*) FROM entries').fetchone()[0],3)
            self.assertEqual(short.execute('SELECT count(*) FROM long_entries').fetchone()[0],0)
            out.close();short.close()

if __name__=='__main__':unittest.main()
