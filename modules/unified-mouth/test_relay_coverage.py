"""Full coverage, corrective annotation, and no partial execution."""

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import relay
from store import Store


class CoverageTest(unittest.TestCase):
    dest = {
        "platform": "qq",
        "account": relay.ACCOUNT,
        "chat_type": "private",
        "id": "11111",
    }
    scene = {
        "platform": "qq",
        "account": relay.ACCOUNT,
        "chat_type": "private",
        "conversation": "11111",
    }

    def segment(self, start, end, kind="text"):
        return {
            "start": start,
            "end": end,
            "kind": kind,
            "destination": None if kind == "omit" else self.dest,
        }

    def wake_plan(self, text):
        return {
            "segments": [],
            "wakeups": [
                {"start": text.index("唤醒意愿"), "end": len(text), "idle_minutes": 1}
            ],
        }

    def test_actual_failure_missing_reply_before_wake_is_rejected(self):
        text = "再来一遍🙂。\n\n唤醒意愿：当前私聊一分钟没消息再回来。"
        plan = self.wake_plan(text)
        with self.assertRaisesRegex(ValueError, "Unmarked.*0:6"):
            relay.validate(plan, text, [self.dest])
        plan["segments"] = [self.segment(0, text.index("\n"))]
        self.assertEqual(len(relay.validate(plan, text, [self.dest])), 1)

    def test_interior_suffix_punctuation_and_empty_plan_are_rejected(self):
        for text, segments in [
            ("甲🙂乙", [self.segment(0, 1), self.segment(2, 3)]),
            ("甲乙尾", [self.segment(0, 2)]),
            ("甲。", [self.segment(0, 1)]),
            ("甲", []),
        ]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                relay.validate({"segments": segments}, text, [self.dest])

    def test_structure_media_whitespace_and_multicast(self):
        text = "给用户：\n🙂\n【表情包7】\n"
        plan = {
            "segments": [
                self.segment(0, 4, "omit"),
                self.segment(5, 6),
                self.segment(7, 14, "sticker"),
            ]
        }
        relay.validate(plan, text, [self.dest])
        # Exact repeated spans for separate recipients remain valid.
        other = self.dest | {"id": "22222"}
        relay.validate(
            {
                "segments": [
                    self.segment(0, 1),
                    self.segment(0, 1) | {"destination": other},
                ]
            },
            "🙂",
            [self.dest, other],
        )
        only = " \n唤醒意愿：一分钟后回来。\n"
        relay.validate(self.wake_plan(only), only, [self.dest])

    def test_api_relabels_after_gap_feedback_and_is_bounded(self):
        text = "你好🙂\n唤醒意愿：一分钟后再说。"
        incomplete = self.wake_plan(text)
        complete = self.wake_plan(text)
        complete["segments"] = [self.segment(0, 3)]
        requests = []

        def reply(request, timeout):
            requests.append(json.loads(request.data))
            plan = incomplete if len(requests) == 1 else complete
            return io.BytesIO(
                json.dumps(
                    {
                        "choices": [
                            {
                                "finish_reason": "stop",
                                "message": {"content": json.dumps(plan)},
                            }
                        ]
                    }
                ).encode()
            )

        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "api.json").write_text('{"api_key":"test-only"}')
            with (
                patch.object(relay, "ROOT", Path(folder)),
                patch.object(relay.urllib.request, "urlopen", side_effect=reply),
            ):
                result = relay.invoke(text, {"scene": self.scene}, [self.dest])
                self.assertEqual(result, complete)
                self.assertIn("Unmarked", requests[1]["messages"][-1]["content"])
                requests.clear()

                def always_incomplete(request, timeout):
                    requests.append(request)
                    return io.BytesIO(
                        json.dumps(
                            {
                                "choices": [
                                    {
                                        "finish_reason": "stop",
                                        "message": {"content": json.dumps(incomplete)},
                                    }
                                ]
                            }
                        ).encode()
                    )

                with patch.object(
                    relay.urllib.request, "urlopen", side_effect=always_incomplete
                ):
                    with self.assertRaisesRegex(ValueError, "Unmarked"):
                        relay.invoke(text, {"scene": self.scene}, [self.dest])
                self.assertEqual(len(requests), 3)

    def test_gap_registers_neither_wake_nor_partial_delivery(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(folder)
            _, packet, _ = store.capture(
                platform="qq",
                account=relay.ACCOUNT,
                kind="private",
                conversation="11111",
                sender="11111",
                message_id="1",
                raw={"message": "测试"},
            )
            text = "漏掉这句\n唤醒意愿：一分钟后再说。"
            batch = packet["batch_id"]
            with closing(sqlite3.connect(store.path)) as db, db:
                db.execute(
                    "INSERT INTO outputs(batch_id,created,text) VALUES(?,?,?)",
                    (batch, 1, text),
                )
                relay.initialize(db)
                db.execute(
                    "INSERT INTO relay_jobs(batch_id,status,source_hash,directory) VALUES(?,?,?,?)",
                    (batch, "pending", relay.digest(text), json.dumps([self.dest])),
                )
                db.commit()
                relay.plan_one(db, lambda *args: self.wake_plan(text))
                self.assertEqual(
                    db.execute("SELECT count(*) FROM intent_wakeups").fetchone()[0], 0
                )
                self.assertEqual(
                    db.execute("SELECT count(*) FROM relay_deliveries").fetchone()[0], 0
                )
                self.assertEqual(
                    db.execute("SELECT status FROM relay_jobs").fetchone()[0], "pending"
                )


if __name__ == "__main__":
    unittest.main()
