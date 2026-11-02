import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import unified_sender as u

class Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.db=patch.object(u,'DB',self.root/'jobs.sqlite3');self.db.start()
        self.allowed=patch.object(u,'ROOTS',[self.root]);self.allowed.start()
    def tearDown(self):self.allowed.stop();self.db.stop();self.temp.cleanup()
    def payload(self):return {'id':'a','conversation':'群聊4','chat_type':'group','messages':[{'text':'测试','mentions':['ExampleFriend']}]}
    def test_compile_mention_and_text_in_order(self):
        out=u.canonical(self.payload());self.assertEqual([p['type'] for p in out['messages'][0]['parts']],['mention','text'])
    def test_inline_parts_order_preserved(self):
        p=self.payload();parts=[{'type':'text','text':'你好 '},{'type':'mention','name':'ExampleFriend'},{'type':'text','text':' 测试'}];p['messages']=[{'parts':parts}]
        self.assertEqual(u.canonical(p)['messages'][0]['parts'],parts)
    def test_stable_id_is_idempotent(self):
        self.assertEqual(u.submit(self.payload()),u.submit(self.payload()))
        with u.connect() as db:self.assertEqual(db.execute('select count(*) from jobs').fetchone()[0],1)
    def test_changed_body_same_id_rejected(self):
        u.submit(self.payload());p=self.payload();p['messages'][0]['text']='changed'
        with self.assertRaises(ValueError):u.submit(p)
    def test_image_bytes_hashed_and_validated(self):
        image=self.root/'image.png';Image.new('RGB',(8,8)).save(image);p=self.payload();p['messages']=[{'image':str(image)}]
        self.assertEqual(len(u.canonical(p)['messages'][0]['sha256']),64)
    def test_invalid_media_and_path_rejected(self):
        p=self.root/'bad.mp4';p.write_bytes(b'bad')
        with self.assertRaises(ValueError):u.checked_media(str(p),'video')
        with self.assertRaises(ValueError):u.checked_media('/etc/passwd','image')
    def test_private_mention_rejected(self):
        p=self.payload();p['chat_type']='private'
        with self.assertRaises(ValueError):u.canonical(p)
    def test_mentions_cannot_be_a_string(self):
        p=self.payload();p['messages'][0]['mentions']='ExampleFriend'
        with self.assertRaises(ValueError):u.canonical(p)
    def test_native_mention_spacing_and_exact_body(self):
        parts=[{'type':'mention','name':'ExampleFriend'},{'type':'text','text':'你好🙂\n第二行'}]
        self.assertTrue(u.text_matches(parts,'\u2005@ExampleFriend\u2005你好🙂\n第二行'))
        self.assertFalse(u.text_matches(parts,'@ExampleFriend2 你好🙂\n第二行'))
    def test_cancel_does_not_remove_queued_payload(self):
        u.submit(self.payload())
        with u.connect() as db:
            with db:db.execute('update jobs set cancel=1 where id=?',('a',))
            row=db.execute('select cancel,status,payload from jobs').fetchone()
            self.assertEqual(row['cancel'],1);self.assertEqual(row['status'],'queued');self.assertEqual(json.loads(row['payload'])['conversation'],'群聊4')
    def test_receipt_kind_must_match(self):
        ex=u.Executor.__new__(u.Executor)
        self.assertIsNone(ex.match({'type':'image'},[{'Kind':'text','Value':'image','MessageID':'1'}]))
        self.assertEqual(ex.match({'type':'image'},[{'Kind':'image','MessageID':'2'}])['MessageID'],'2')
    def test_navigation_finishes_before_honoring_cancellation(self):
        from unittest.mock import Mock
        import contextlib
        u.submit(self.payload());state={'finished':False}
        ex=u.Executor.__new__(u.Executor);ex.policy=None;ex.ocr=None;ex.ui=None;ex.clip=None;ex.cached=None
        ex.observer=Mock();ex.observer.baseline.return_value={'contact':{'roomid':'room@chatroom'}}
        nav=Mock()
        def select(*args):
            with u.connect() as other,other:other.execute('update jobs set cancel=1 where id=?',('a',))
            state['finished']=True
        nav.select.side_effect=select
        with patch.object(u,'HOME',self.root),patch.object(u,'Navigator',return_value=nav),u.connect() as db:
            result=ex.execute(db,'a',u.canonical(self.payload()))
        self.assertTrue(state['finished']);self.assertTrue(result['cancelled']);ex.observer.items.assert_not_called()
    def test_same_recipient_cache_still_verifies_title(self):
        from unittest.mock import Mock
        u.submit(self.payload());ex=u.Executor.__new__(u.Executor);ex.policy=None;ex.ocr=None;ex.ui=None;ex.clip=None;ex.cached='room@chatroom'
        ex.observer=Mock();ex.observer.baseline.return_value={'contact':{'roomid':'room@chatroom'}}
        nav=Mock();nav.verify.return_value=True
        with u.connect() as db,db:db.execute('update jobs set cancel=1 where id=?',('a',))
        with patch.object(u,'HOME',self.root),patch.object(u,'Navigator',return_value=nav),u.connect() as db:ex.execute(db,'a',u.canonical(self.payload()))
        nav.verify.assert_called_once_with('群聊4','group');nav.select.assert_not_called()

if __name__=='__main__':unittest.main()
