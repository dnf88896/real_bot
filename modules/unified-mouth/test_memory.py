"""Exercise global partitioning, source-aware deduplication and recursive recall."""

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from memory_engine import Memory, history_window, normalize


def incoming(mid, text, conversation="alice"):
    return {
        "platform": "qq",
        "account_id": "bot",
        "chat_type": "private",
        "conversation_id": conversation,
        "sender_id": conversation,
        "message_id": str(mid),
        "received_at": datetime.fromtimestamp(mid, UTC).isoformat(),
        "raw_event": {
            "message": [{"type": "text", "data": {"text": text}}],
            "sender": {"nickname": conversation},
        },
    }


class MemoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.calls = []

        def api(task, data, json_output=False):
            self.calls.append(data)
            if json_output:
                if "候选" in data:
                    limit = 24 if data["查询"].get("deeper") else 12
                    return {"keys": [r["来源标识"] for r in data["候选"][:limit]]}
                return {
                    "queries": ["雨天踢球"],
                    "deeper": "再找" in data["当前消息"].get("text", ""),
                }
            return "旧概括与新增事件的递归摘要。"

        self.memory = Memory(self.root, api)

    def tearDown(self):
        self.temp.cleanup()

    def insert(self, n=240):
        keys = []
        with closing(self.memory.connect()) as db, db:
            for i in range(1, n + 1):
                raw = incoming(
                    i, "雨天踢球后来感冒，细节" + str(i), "alice" if i % 2 else "bob"
                )
                keys.append(self.memory.add(db, raw, "incoming", "test:" + str(i)))
            self.memory.partition(db)
        return keys

    def test_partition_has_no_holes_and_eviction_is_once(self):
        self.insert(450)
        with closing(self.memory.connect()) as db, db:
            self.assertEqual(
                dict(db.execute("SELECT layer,count(*) FROM events GROUP BY layer")),
                {"short": 200, "long": 250},
            )
            self.memory.partition(db)
            self.assertEqual(
                db.execute("SELECT count(*) FROM evictions").fetchone()[0], 250
            )
            self.memory.config["short_records"] = 220
            self.memory.partition(db)
            self.memory.config["short_records"] = 200
            self.memory.partition(db)
            self.assertEqual(
                db.execute("SELECT count(*) FROM evictions").fetchone()[0], 250
            )

    def test_current_and_long_dedup_by_identity_not_text(self):
        keys = self.insert()
        # One identical-text event from another conversation must survive.
        with closing(self.memory.connect()) as db, db:
            raw = incoming(241, "完全相同", "alice")
            a = self.memory.add(db, raw, "incoming", "a")
            b = self.memory.add(db, incoming(242, "完全相同", "bob"), "incoming", "b")
            self.memory.partition(db)
        result = self.memory.snapshot(
            "cid", [], a, {"text": "雨天踢球"}, [[keys[0], a]]
        )
        recent = {r["来源标识"] for r in result["近期记忆"]}
        related = {r["来源标识"] for r in result["相关记忆"]}
        self.assertNotIn(a, recent)
        self.assertIn(b, recent)
        self.assertNotIn(keys[0], related)
        self.assertTrue(related)
        self.assertEqual(len(recent), 199)

    def test_recursive_batches_and_failure_do_not_advance(self):
        self.insert(650)
        self.assertTrue(self.memory.consolidate())
        self.assertEqual(self.calls[-1]["已有摘要"], "")
        self.assertTrue(self.memory.consolidate())
        self.assertEqual(self.calls[-1]["已有摘要"], "旧概括与新增事件的递归摘要。")
        self.assertFalse(self.memory.consolidate())
        with closing(self.memory.connect()) as db:
            self.assertEqual(
                db.execute("SELECT max(end_seq) FROM summaries").fetchone()[0], 400
            )
        self.insert(850)
        self.memory.api = lambda *a, **k: "x" * 701
        with self.assertRaises(ValueError):
            self.memory.consolidate()
        with closing(self.memory.connect()) as db:
            self.assertEqual(
                db.execute("SELECT max(end_seq) FROM summaries").fetchone()[0], 400
            )

    def test_history_source_positions_and_trim(self):
        keys = self.insert(2)
        messages = [
            {"role": "user", "content": "相同"},
            {"role": "assistant", "content": "回复"},
        ]
        self.memory.bind_history("cid", messages, [], {"batch_id": "batch1"}, keys[0])
        self.assertEqual(
            self.memory.history_refs("cid", messages), [[keys[0]], ["batch:batch1"]]
        )
        # A reset with similar text is not sufficient to deduplicate.
        self.assertEqual(self.memory.history_refs("other", messages), [[], []])
        history = []
        for i in range(300):
            history += [
                {"role": "user", "content": str(i)},
                {
                    "role": "assistant",
                    "content": "查询",
                    "tool_calls": [{"id": str(i)}],
                },
                {"role": "tool", "content": "资料", "tool_call_id": str(i)},
                {"role": "assistant", "content": "答复"},
            ]
        limited, _ = history_window(history)
        count = sum(
            m["role"] in ("user", "assistant") and not m.get("tool_calls")
            for m in limited
        )
        self.assertLessEqual(count + 1, 400)
        self.assertEqual(limited[0]["role"], "user")
        self.assertEqual(len(limited) % 4, 0)

    def test_followup_uses_previous_recall_and_expands(self):
        self.insert()
        first = self.memory.snapshot("cid", [], "current", {"text": "以前踢球"}, [])
        second = self.memory.snapshot("cid", [], "current", {"text": "再找旧事"}, [])
        self.assertTrue(
            [c for c in self.calls if "上轮检索" in c][-1]["上轮检索"]["records"]
        )
        self.assertEqual(len(first["相关记忆"]), 12)
        self.assertEqual(len(second["相关记忆"]), 24)
        self.assertTrue(
            {r["来源标识"] for r in second["相关记忆"]}
            - {r["来源标识"] for r in first["相关记忆"]}
        )

    def test_only_confirmed_output_is_a_chat_event(self):
        raw = {
            "platform": "qq",
            "account_id": "bot",
            "chat_type": "private",
            "conversation_id": "alice",
            "received_at": datetime.now(UTC).isoformat(),
            "phase": "generated",
            "text": "草稿",
        }
        self.assertIsNone(normalize(raw, "outgoing", "draft"))
        raw.update(phase="sent", receipt="123", text="实际错字🙂")
        event = normalize(raw, "outgoing", "sent")
        self.assertEqual(event["text"], "实际错字🙂")

    def test_recall_includes_followup_without_repeating_keywords(self):
        self.memory.config["short_records"] = 2
        with closing(self.memory.connect()) as db, db:
            first = self.memory.add(db, incoming(1, "我冒雨踢球了"), "incoming", "rain")
            later = self.memory.add(
                db, incoming(2, "后来回家换了衣服，已经没事了"), "incoming", "later"
            )
            for i in range(3, 7):
                self.memory.add(db, incoming(i, "其他消息"), "incoming", str(i))
            self.memory.partition(db)
        result = self.memory.snapshot("cid", [], "current", {"text": "那次后来呢"}, [])
        found = {r["来源标识"] for r in result["相关记忆"]}
        self.assertIn(first, found)
        self.assertIn(later, found)

    def test_sent_receipt_dedup_is_platform_and_conversation_scoped(self):
        outgoing = {
            "platform": "qq",
            "account_id": "bot",
            "chat_type": "private",
            "conversation_id": "alice",
            "received_at": datetime.now(UTC).isoformat(),
            "phase": "sent",
            "receipt": "123",
            "text": "发出的原话",
        }
        with closing(self.memory.connect()) as db, db:
            same = self.memory.add(db, outgoing, "outgoing", "out1")
            other = self.memory.add(
                db, outgoing | {"platform": "future"}, "outgoing", "out2"
            )
            self.memory.partition(db)
        with closing(sqlite3.connect(self.root / "mouth.sqlite3")) as db, db:
            db.execute(
                "CREATE TABLE relay_deliveries(batch_id,status,message_id,destination)"
            )
            db.execute(
                "INSERT INTO relay_deliveries VALUES(?,?,?,?)",
                (
                    "b",
                    "sent",
                    "123",
                    json.dumps(
                        {
                            "platform": "qq",
                            "account": "bot",
                            "chat_type": "private",
                            "id": "alice",
                        }
                    ),
                ),
            )
        result = self.memory.snapshot(
            "cid", [], "current", {"text": "你好"}, [["batch:b"]]
        )
        recent = {r["来源标识"] for r in result["近期记忆"]}
        self.assertNotIn(same, recent)
        self.assertIn(other, recent)

    def test_import_is_readonly_and_idempotent(self):
        source = self.root / "old.sqlite3"
        with closing(sqlite3.connect(source)) as db, db:
            for table in ("entries", "long_entries"):
                db.execute(
                    f"CREATE TABLE {table}(event_key,event_time,account_id,chat_type,conversation_id,direction,raw_json)"
                )
            db.execute(
                "INSERT INTO entries VALUES(?,?,?,?,?,?,?)",
                (
                    "input:1",
                    "t",
                    "bot",
                    "private",
                    "alice",
                    "incoming",
                    json.dumps(incoming(1, "旧事件")),
                ),
            )
        before = source.read_bytes()
        self.memory.import_legacy(source)
        self.memory.import_legacy(source)
        self.assertEqual(before, source.read_bytes())
        with closing(self.memory.connect()) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM events").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
