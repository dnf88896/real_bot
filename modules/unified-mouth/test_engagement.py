"""Attention on receipts and bounded autonomous opportunities, no real sending."""

import importlib
import json
import sqlite3
import sys
import tempfile
import time
import types
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import relay
from store import Store

package = types.ModuleType("engagement_test_package")
package.__path__ = [str(Path(__file__).parent)]
sys.modules[package.__name__] = package
Attention = importlib.import_module(package.__name__ + ".attention_support").Attention
Coordinator = importlib.import_module(package.__name__ + ".coordinator").Coordinator
proactive = importlib.import_module(package.__name__ + ".proactive")


class Fixture:
    def setup(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root)
        self.scene = {
            "platform": "qq",
            "account": "100000002",
            "chat_type": "private",
            "conversation": "100000001",
        }
        self.cfg = {
            "attention": {"enabled": True},
            "proactive": {
                "enabled": True,
                "targets": [self.scene],
                "min_interval_minutes": 1,
                "max_interval_minutes": 1,
                "start_hour": 9,
                "end_hour": 23,
                "max_unanswered": 1,
                "max_per_day": 2,
            },
        }
        self.write_config()
        with closing(sqlite3.connect(self.store.path)) as db:
            relay.initialize(db)

    def write_config(self):
        (self.root / "engagement.json").write_text(json.dumps(self.cfg))

    def packet(self, mid, group=None, sender="100000001"):
        return self.store.capture(
            platform="qq",
            account="100000002",
            kind="group" if group else "private",
            conversation=group or sender,
            sender=sender,
            message_id=str(mid),
            raw={"message": "测试", "sender": {"nickname": "测试用户"}},
        )[1]

    def sent(self, packet, suffix="0", status="sent", dest=None):
        scene = packet["scene"]
        dest = dest or {
            "platform": scene["platform"],
            "account": scene["account"],
            "chat_type": scene["chat_type"],
            "id": scene["conversation"],
        }
        record = {
            "platform": dest["platform"],
            "account_id": dest["account"],
            "chat_type": dest["chat_type"],
            "conversation_id": dest["id"],
        }
        with closing(sqlite3.connect(self.store.path)) as db, db:
            db.execute(
                "INSERT INTO relay_deliveries(id,batch_id,ordinal,destination,parts,status,record_json) VALUES(?,?,?,?,?,?,?)",
                (
                    packet["batch_id"] + ":" + suffix,
                    packet["batch_id"],
                    int(suffix),
                    json.dumps(dest),
                    "[]",
                    status,
                    json.dumps(record),
                ),
            )


class AttentionTest(Fixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.setup()
        self.att = Attention(self.root)

    async def asyncTearDown(self):
        self.att.close()
        self.temp.cleanup()

    async def test_receipts_not_generation_or_failure_bubbles_count_once(self):
        packet = self.packet(1, group="1234")
        self.store.complete(packet, "生成了但未发送", {}, {})
        self.sent(packet, status="failed")
        await self.att.sync()
        self.assertEqual(self.att.manager._attention_map, {})
        self.sent(packet, suffix="1")
        self.sent(packet, suffix="2")
        await self.att.sync()
        await self.att.sync()
        key = "qq:3654076639_group_1234"
        profile = self.att.manager._attention_map[key]["100000001"]
        self.assertAlmostEqual(profile["attention_score"], 0.4)
        self.assertEqual(profile["interaction_count"], 1)
        self.assertEqual(profile["emotion"], 0)
        focused = await self.att.probability(packet, 0.02)
        stranger = self.packet(2, group="1234", sender="400000005")
        other_group = self.packet(3, group="6789", sender="400000005")
        self.assertGreater(focused, await self.att.probability(stranger, 0.02))
        self.assertEqual(await self.att.probability(other_group, 0.02), 0.02)
        restarted = Attention(self.root)
        try:
            await restarted.sync()
            self.assertEqual(
                restarted.manager._attention_map[key]["100000001"][
                    "interaction_count"
                ],
                1,
            )
        finally:
            restarted.close()

    async def test_decay_does_not_repeat_when_probability_is_read(self):
        now = time.time()
        profile = {"attention_score": 1.0, "emotion": 0, "last_interaction": now}
        await self.att.manager._apply_attention_decay(profile, now + 150)
        await self.att.manager._apply_attention_decay(profile, now + 300)
        self.assertAlmostEqual(profile["attention_score"], 0.5)
        await self.att.manager._apply_attention_decay(profile, now + 300)
        self.assertAlmostEqual(profile["attention_score"], 0.5)

    async def test_receipt_state_rolls_back_if_database_commit_fails(self):
        packet = self.packet(1, group="1234")
        self.sent(packet)
        with patch.object(
            self.att, "save", side_effect=sqlite3.OperationalError("test failure")
        ):
            with self.assertRaises(sqlite3.OperationalError):
                await self.att.sync()
        self.assertEqual(self.att.manager._attention_map, {})
        await self.att.sync()
        profile = self.att.manager._attention_map["qq:3654076639_group_1234"][
            "100000001"
        ]
        self.assertAlmostEqual(profile["attention_score"], 0.4)

    async def test_enable_does_not_credit_old_sent_messages(self):
        packet = self.packet(1, group="1234")
        self.sent(packet)
        # A separate fresh feature database represents first deployment.
        fresh = self.root / "fresh"
        fresh.mkdir()
        import shutil

        for name in ("mouth.sqlite3", "engagement.json"):
            shutil.copy2(self.root / name, fresh / name)
        att = Attention(fresh)
        try:
            await att.sync()
            self.assertEqual(att.manager._attention_map, {})
        finally:
            att.close()


class ProactiveTest(Fixture, unittest.TestCase):
    def setUp(self):
        self.setup()
        self.coordinator = Coordinator(self.root)
        self.engine = proactive.Proactive(self.root, self.coordinator)
        self.now = datetime(
            2026, 10, 22, 12, tzinfo=ZoneInfo("Asia/Shanghai")
        ).timestamp()
        self.packet(1)

    def tearDown(self):
        self.coordinator.attention.close()
        self.temp.cleanup()

    def first(self):
        self.assertEqual(self.engine.due(self.now), [])
        return self.engine.due(self.now + 61)[0]

    def test_restart_once_fresh_input_cancels_old_packet(self):
        packet = self.first()
        wid = packet["internal_wake"]["wake_id"]
        self.assertTrue(proactive.valid(self.root, wid, self.now + 62))
        self.assertEqual(self.engine.due(self.now + 62), [])
        restarted = proactive.Proactive(self.root, self.coordinator)
        self.assertEqual(
            restarted.due(self.now + 62)[0]["batch_id"], packet["batch_id"]
        )
        self.packet(2)
        self.assertFalse(proactive.valid(self.root, wid, self.now + 63))
        self.assertEqual(restarted.due(self.now + 63), [])

    def test_own_multibubble_output_valid_and_no_unanswered_chasing(self):
        packet = self.first()
        wid = packet["internal_wake"]["wake_id"]
        self.store.prepare(packet)
        self.store.complete(packet, "你好", {}, {})
        self.sent(packet)
        self.sent(packet, suffix="1")
        proactive.finish(self.root, wid)
        self.assertTrue(proactive.valid(self.root, wid, self.now + 62))
        self.assertEqual(self.engine.due(self.now + 200), [])
        self.assertEqual(self.engine.due(self.now + 2000), [])
        with closing(sqlite3.connect(self.store.archive)) as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM observations").fetchone()[0], 1
            )

    def test_daily_budget_and_target_and_quiet_hours(self):
        one = self.first()
        proactive.finish(self.root, one["internal_wake"]["wake_id"])
        self.packet(2)
        self.engine.due(self.now + 70)
        two = self.engine.due(self.now + 131)[0]
        proactive.finish(self.root, two["internal_wake"]["wake_id"])
        self.packet(3)
        self.engine.due(self.now + 140)
        self.assertEqual(self.engine.due(self.now + 201), [])
        self.cfg["proactive"]["enabled"] = False
        self.write_config()
        self.assertFalse(
            proactive.valid(self.root, two["internal_wake"]["wake_id"], self.now + 132)
        )
        night = datetime(
            2026, 10, 22, 23, 30, tzinfo=ZoneInfo("Asia/Shanghai")
        ).timestamp()
        self.assertFalse(proactive.in_hours(night, self.cfg["proactive"]))
        self.assertGreater(
            self.engine.next_due(night, self.cfg["proactive"]), night + 60
        )

    def test_explicit_intention_takes_precedence(self):
        self.engine.due(self.now)
        with closing(sqlite3.connect(self.store.path)) as db, db:
            origin = {
                "platform": "qq",
                "account": "100000002",
                "chat_type": "private",
                "id": "100000001",
            }
            db.execute(
                "INSERT INTO intent_wakeups(id,status,origin_json) VALUES('test','waiting',?)",
                (json.dumps(origin),),
            )
        self.assertEqual(self.engine.due(self.now + 61), [])

    def test_pending_expiry_and_busy_session(self):
        self.engine.due(self.now)
        self.coordinator.busy.add(json.dumps(self.scene, sort_keys=True))
        self.assertEqual(self.engine.due(self.now + 61), [])
        self.coordinator.busy.clear()
        packet = self.engine.due(self.now + 62)[0]
        self.assertFalse(
            proactive.valid(
                self.root, packet["internal_wake"]["wake_id"], self.now + 700
            )
        )

    def test_send_worker_cancels_new_activity_and_rejects_other_recipient(self):
        packet = self.first()
        self.store.prepare(packet)
        self.store.complete(packet, "你好", {}, {})
        directory = {
            "platform": "qq",
            "account": "100000002",
            "chat_type": "private",
            "id": "100000001",
        }
        with closing(sqlite3.connect(self.store.path)) as db, db:
            db.execute(
                "INSERT INTO relay_jobs(batch_id,status,source_hash,directory) VALUES(?,?,?,?)",
                (
                    packet["batch_id"],
                    "pending",
                    relay.digest("你好"),
                    json.dumps([directory]),
                ),
            )
            db.commit()
            relay.plan_one(
                db,
                lambda *args: {
                    "segments": [
                        {"kind": "text", "destination": directory, "start": 0, "end": 2}
                    ]
                },
            )
            db.execute(
                "UPDATE relay_deliveries SET destination=?",
                (json.dumps(directory | {"id": "400000005"}),),
            )
            db.commit()
            calls = []
            with (
                patch.object(relay, "ROOT", self.root),
                patch.object(relay, "ARCHIVE", self.store.archive),
                patch.object(time, "time", return_value=self.now + 62),
            ):
                relay.send_one(
                    db, {"allow_all_private": True}, lambda *args: calls.append(args)
                )
                self.assertEqual(calls, [])
                self.assertIn(
                    "configured conversation",
                    db.execute("SELECT error FROM relay_deliveries").fetchone()[0],
                )
                db.execute(
                    "UPDATE relay_deliveries SET status='pending',destination=?",
                    (json.dumps(directory),),
                )
                db.commit()
                self.packet(2)
                relay.send_one(
                    db, {"allow_all_private": True}, lambda *args: calls.append(args)
                )
                self.assertEqual(calls, [])
                self.assertIn(
                    "cancelled",
                    db.execute("SELECT error FROM relay_deliveries").fetchone()[0],
                )


if __name__ == "__main__":
    unittest.main()
