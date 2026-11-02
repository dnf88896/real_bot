import json
import tempfile
import unittest
from pathlib import Path
from email.message import EmailMessage
from contextlib import closing
from receiver import parse_mail, poll, database


def sample():
    m = EmailMessage(); m['From'] = '测试 <test@example.com>'
    m['To'] = 'bot@example.invalid'; m['Subject'] = '收件测试'
    m.set_content('明天九点见'); m.add_alternative('<p>明天九点见</p>', subtype='html')
    m.add_attachment(b'attachment', maintype='application', subtype='octet-stream', filename='../../bad.exe')
    return m.as_bytes()


class FakeIMAP:
    def __init__(self, uids, fail=False): self.uids = uids; self.fail = fail; self.calls = []
    def response(self, key): return key, [b'100']
    def logout(self): pass
    def uid(self, command, *args):
        self.calls.append((command, args))
        if command == 'search': return 'OK', [' '.join(map(str, self.uids)).encode()]
        if 'SIZE' in args[1]: return 'OK', [b'1 (UID 2 RFC822.SIZE 900)']
        if self.fail: return 'NO', []
        return 'OK', [(b'1 (BODY[] {900}', sample()), b')']


class ReceiverTests(unittest.TestCase):
    def test_mime_prefers_plain_and_keeps_attachment_metadata(self):
        record, attachments = parse_mail(sample())
        self.assertEqual(record['subject'], '收件测试')
        self.assertEqual(record['text'].strip(), '明天九点见')
        self.assertEqual(len(attachments), 1)

    def test_html_is_text_not_external_fetch_or_script(self):
        m = EmailMessage(); m.set_content('<p>hello</p><script>bad()</script><img src="https://example.com/a">', subtype='html')
        record, _ = parse_mail(m.as_bytes())
        self.assertEqual(record['text'].strip(), 'hello')

    def test_empty_plain_part_does_not_hide_html_body(self):
        m = EmailMessage(); m.set_content(''); m.add_alternative('<p>真实正文</p>', subtype='html')
        record, _ = parse_mail(m.as_bytes())
        self.assertEqual(record['text'].strip(), '真实正文')

    def test_first_run_checkpoint_new_mail_dedup_and_no_path_traversal(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root/'mail-secret.json').write_text(json.dumps({'authorization_code': 'fake'}))
            self.assertEqual(poll(root, lambda _: FakeIMAP([1]))['status'], 'initialized_new_mail_only')
            client = FakeIMAP([1, 2]); self.assertEqual(poll(root, lambda _: client)['received'], 1)
            self.assertIn(('fetch', ('2', '(BODY.PEEK[])')), client.calls)
            self.assertEqual(poll(root, lambda _: FakeIMAP([1, 2]))['received'], 0)
            with closing(database(root)) as db:
                row = json.loads(db.execute('SELECT record_json FROM messages').fetchone()[0])
            attachment = Path(row['attachments'][0]['path'])
            self.assertTrue(attachment.is_relative_to(root/'messages'))
            self.assertEqual(attachment.read_bytes(), b'attachment')

    def test_failed_fetch_does_not_advance_checkpoint(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root/'mail-secret.json').write_text(json.dumps({'authorization_code': 'fake'}))
            poll(root, lambda _: FakeIMAP([]))
            with self.assertRaises(RuntimeError): poll(root, lambda _: FakeIMAP([1], fail=True))
            self.assertEqual(poll(root, lambda _: FakeIMAP([1]))['received'], 1)


if __name__ == '__main__': unittest.main()
