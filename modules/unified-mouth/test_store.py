import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from store import Store


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def capture(self, **overrides):
        args = {
            "platform": "qq",
            "account": "100000002",
            "kind": "private",
            "conversation": "100000001",
            "sender": "100000001",
            "message_id": "1",
            "raw": {"message": [{"type": "text", "data": {"text": "hello"}}]},
        }
        return self.store.capture(**{**args, **overrides})

    def test_capture_is_idempotent_and_platform_scoped(self):
        first, packet, _ = self.capture()
        self.assertEqual(first, self.capture()[0])
        self.assertNotEqual(first, self.capture(platform="future")[0])
        with closing(sqlite3.connect(self.store.archive)) as db, db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM observations").fetchone()[0], 2
            )

    def test_original_unicode_and_whitespace_are_preserved(self):
        batch, packet, _ = self.capture()
        text = "  hello🙂\n第二条  "
        self.assertTrue(self.store.complete(packet, text, {}, {}))
        self.assertFalse(self.store.complete(packet, text, {}, {}))
        self.assertTrue(self.capture()[2])
        with closing(sqlite3.connect(self.store.path)) as db, db:
            self.assertEqual(db.execute("SELECT text FROM outputs").fetchone()[0], text)
            self.assertEqual(
                db.execute("SELECT count(*) FROM deliveries").fetchone()[0], 0
            )
            self.assertEqual(
                json.loads(db.execute("SELECT packet FROM jobs").fetchone()[0])[
                    "engine"
                ],
                "astrbot",
            )

    def test_no_empty_or_rewritten_final_response(self):
        _, packet, _ = self.capture()
        with self.assertRaises(ValueError):
            self.store.complete(packet, "\n ", {}, {})
        self.store.complete(packet, "original", {}, {})
        with self.assertRaises(ValueError):
            self.store.complete(packet, "rewritten", {}, {})


if __name__ == "__main__":
    unittest.main()
