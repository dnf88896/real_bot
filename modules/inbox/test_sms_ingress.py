import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from sms_ingress import authorize, save_sms, save_heartbeat


class SmsTests(unittest.TestCase):
    def test_separate_tokens_and_exact_routes(self):
        cfg = {kind+'_hash': hashlib.sha256(token.encode()).hexdigest()
               for kind, token in [('sms', 'secret1'), ('heartbeat', 'secret2')]}
        self.assertEqual(authorize('/bot-phone/sms/secret1', cfg), 'sms')
        self.assertIsNone(authorize('/bot-phone/sms/secret2', cfg))
        self.assertIsNone(authorize('/bot-phone/heartbeat/secret2/extra', cfg))
        self.assertIsNone(authorize('/bot-phone/sms/wrong', cfg))

    def test_duplicate_retry_and_distinct_timestamps(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            payload = {'from': 'test', 'text': '中文 🙂', 'sim': 2, 'sentStamp': '123'}
            self.assertTrue(save_sms(root, payload))
            self.assertFalse(save_sms(root, dict(payload, receivedStamp='456')))
            self.assertTrue(save_sms(root, dict(payload, sentStamp='124')))
            db = sqlite3.connect(root/'phone.sqlite3')
            try:
                self.assertEqual(db.execute('SELECT count(*) FROM sms').fetchone()[0], 2)
                row = json.loads(db.execute('SELECT record_json FROM sms LIMIT 1').fetchone()[0])
                self.assertEqual(row['text'], '中文 🙂')
            finally: db.close()

    def test_heartbeat_does_not_create_sms(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); save_heartbeat(root); save_heartbeat(root)
            db = sqlite3.connect(root/'phone.sqlite3')
            try:
                self.assertEqual(db.execute('SELECT count FROM heartbeat').fetchone()[0], 2)
                self.assertEqual(db.execute('SELECT count(*) FROM sms').fetchone()[0], 0)
            finally: db.close()

    def test_reject_invalid_sms(self):
        with tempfile.TemporaryDirectory() as d:
            for payload in [[], {}, {'from': 'a', 'text': None}, {'from': '', 'text': 'a'}]:
                with self.assertRaises(ValueError): save_sms(Path(d), payload)


if __name__ == '__main__': unittest.main()
