import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from memory import initialize, sync_once


class ArchiveTest(unittest.TestCase):
    def test_exact_archive_restart_partial_line_and_immutability(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)
            source = p/'eye.jsonl'
            record = dict(platform='qq',account_id='1',chat_type='private',conversation_id='2',sender_id='2',received_at='2026-10-21T00:00:00Z',raw_event={'message':[{'type':'image','data':{'file':'img'}},{'type':'text','data':{'text':'原文\n后续'}}]})
            raw = json.dumps(record,ensure_ascii=False)+'\n'
            source.write_bytes(raw.encode()+b'{"partial":')
            db = sqlite3.connect(p/'memory.db'); initialize(db)
            self.assertEqual(sync_once(db,source),1)
            self.assertEqual(db.execute('SELECT raw_json FROM observations').fetchone()[0],raw[:-1])
            self.assertEqual(sync_once(db,source),0)
            source.write_bytes((raw+raw).encode())
            self.assertEqual(sync_once(db,source),1)  # Same text at different source offset is preserved.
            db.close(); db=sqlite3.connect(p/'memory.db')
            self.assertEqual(sync_once(db,source),0)
            for sql in ('UPDATE observations SET sender_id=3','DELETE FROM observations'):
                with self.assertRaises(sqlite3.IntegrityError): db.execute(sql)
                db.rollback()
            source.write_bytes((raw+raw+'invalid\n').encode())
            with self.assertRaises(json.JSONDecodeError): sync_once(db,source)
            self.assertEqual(db.execute('SELECT count(*) FROM observations').fetchone()[0],2)
            source.write_bytes(b'')
            with self.assertRaises(RuntimeError):sync_once(db,source)
            db.close()


if __name__ == '__main__': unittest.main()
