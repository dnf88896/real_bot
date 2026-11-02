"""Isolated admission, provenance and idle-wake regression tests."""

import asyncio
import importlib
import json
import sqlite3
import sys
import tempfile
import time
import types
import unittest
from contextlib import closing
from pathlib import Path

from store import Store

package = types.ModuleType("remaining_test_package")
package.__path__ = [str(Path(__file__).parent)]
sys.modules[package.__name__] = package
Coordinator = importlib.import_module(package.__name__ + ".coordinator").Coordinator
Profiles = importlib.import_module(package.__name__ + ".profiles").Profiles
IdleWake = importlib.import_module(package.__name__ + ".idle_wake").IdleWake
valid_wake = importlib.import_module(package.__name__ + ".idle_wake").valid


class AdmissionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root)
        (self.root / "social-config.json").write_text(
            json.dumps(
                {
                    "allow_all_private": True,
                    "group_preview": True,
                    "private_wait": 0.04,
                    "private_quiet": 0.02,
                    "private_cap": 0.08,
                    "group_wait": 0.04,
                    "group_quiet": 0.02,
                    "group_cap": 0.08,
                    "global_concurrency": 1,
                }
            )
        )
        self.coordinator = Coordinator(self.root)

    async def asyncTearDown(self):
        self.temp.cleanup()

    def packet(self, mid, user="100000001", kind="private", conv=None):
        _, packet, _ = self.store.capture(
            platform="qq",
            account="100000002",
            kind=kind,
            conversation=conv or user,
            sender=user,
            message_id=str(mid),
            raw={
                "message": [{"type": "text", "data": {"text": "消息" + str(mid)}}],
                "sender": {"nickname": "测试"},
            },
        )
        return packet

    async def test_multiple_messages_one_anchor_and_restart_recovery(self):
        one, two = self.packet(1), self.packet(2)
        first = asyncio.create_task(self.coordinator.admit(one))
        await asyncio.sleep(0.01)
        second = asyncio.create_task(self.coordinator.admit(two))
        a, b = await asyncio.gather(first, second)
        self.assertIsNone(b)
        self.assertEqual([i["id"] for i in a["new_messages"]], [1, 2])
        self.assertEqual(self.coordinator.active, 1)
        restarted = Coordinator(self.root)
        resumed = await restarted.admit(one)
        self.assertEqual([i["id"] for i in resumed["new_messages"]], [1, 2])
        restarted.finish(resumed, True)
        self.coordinator.finish(a, True)

    async def test_private_priority_but_no_same_conversation_overlap(self):
        first = await self.coordinator.admit(self.packet(1))
        group = self.packet(2, kind="group", conv="1234")
        private = self.packet(3, user="400000005")
        group_task = asyncio.create_task(self.coordinator.admit(group))
        private_task = asyncio.create_task(self.coordinator.admit(private))
        await asyncio.sleep(0.1)
        self.assertFalse(group_task.done())
        self.assertFalse(private_task.done())
        self.coordinator.finish(first, True)
        p = await asyncio.wait_for(private_task, 1)
        self.assertFalse(group_task.done())
        self.coordinator.finish(p, True)
        g = await asyncio.wait_for(group_task, 1)
        self.coordinator.finish(g, True)
        self.assertEqual(self.coordinator.active, 0)

    async def test_decision_cannot_override_owner_or_private(self):
        called = []

        def api(*a, **k):
            called.append(a)
            return {"reply": False}

        self.coordinator.api = api
        self.assertTrue(
            await self.coordinator.decide(self.packet(1, kind="group", conv="1234"), [])
        )
        self.assertTrue(
            await self.coordinator.decide(self.packet(2, user="400000005"), [])
        )
        self.assertEqual(called, [])
        self.assertFalse(
            await self.coordinator.decide(
                self.packet(3, user="400000005", kind="group", conv="1234"), []
            )
        )
        self.assertEqual(len(called), 1)

    async def test_group_idle_intention_gets_private_priority(self):
        first = await self.coordinator.admit(self.packet(1))
        group = self.packet(2, kind="group", conv="1234")
        intention = self.packet(3, kind="group", conv="1234")
        intention["internal_wake"] = {"intent": "继续聊聊"}
        ordinary = asyncio.create_task(self.coordinator.admit(group))
        wake = asyncio.create_task(self.coordinator.admit(intention))
        await asyncio.sleep(0.1)
        self.coordinator.finish(first, True)
        due = await asyncio.wait_for(wake, 1)
        self.assertIn("internal_wake", due)
        self.assertFalse(ordinary.done())
        self.coordinator.finish(due, True)
        remaining = await asyncio.wait_for(ordinary, 1)
        self.coordinator.finish(remaining, True)

    async def test_due_wake_bypasses_participation_veto_in_every_scene(self):
        def forbidden(*args, **kwargs):
            raise AssertionError("Due intention must reach the mouth")

        self.coordinator.api = forbidden
        for mid, kind in enumerate(("private", "group"), 10):
            packet = self.packet(mid, user="400000005", kind=kind, conv="1234")
            packet["internal_wake"] = {"intent": "继续聊聊"}
            self.assertTrue(await self.coordinator.decide(packet, []))
            with closing(self.coordinator.connect()) as db:
                row = db.execute(
                    "SELECT mandatory,reply,error FROM decisions WHERE batch=?",
                    (packet["batch_id"],),
                ).fetchone()
                self.assertEqual(tuple(row), (1, 1, None))


class ProvenanceTest(unittest.TestCase):
    def test_profile_source_ids_continue_past_legacy_cursor(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "identity-source.sqlite3"
            with closing(sqlite3.connect(source)) as db, db:
                db.execute(
                    "CREATE TABLE observations(id INTEGER PRIMARY KEY,source_file,source_offset,sha256,modality,platform,account_id,chat_type,conversation_id,sender_id,received_at,event_time,message_id,text_content,raw_json)"
                )
                for i in range(1, 31):
                    raw = {"raw_event": {"message": "喜欢天文"}}
                    db.execute(
                        "INSERT INTO observations(id,account_id,chat_type,conversation_id,sender_id,received_at,message_id,raw_json) VALUES(?,?,?,?,?,?,?,?)",
                        (
                            i,
                            "100000002",
                            "private",
                            "100000001",
                            "100000001",
                            "2026-10-22",
                            "old" + str(i),
                            json.dumps(raw),
                        ),
                    )
            profiles = Profiles(root)
            _, worker, _, _ = profiles.load_modules()

            def fake(old, events, basic, target):
                return {
                    "summary": "喜欢天文",
                    "facts": old["facts"]
                    + [
                        {
                            "kind": "preference",
                            "text": "自述喜欢天文",
                            "source_ids": [events[0]["id"]],
                        }
                    ],
                }

            with closing(sqlite3.connect(profiles.path)) as db:
                worker.initialize(db)
                worker.plan(db)
                worker.process_one(db, api=fake)
            store = Store(root)
            for i in range(1, 31):
                store.capture(
                    platform="qq",
                    account="100000002",
                    kind="private",
                    conversation="100000001",
                    sender="100000001",
                    message_id="new" + str(i),
                    raw={"message": "继续聊星空"},
                )
            profiles.sync_source()
            profiles.sync_source()
            with closing(sqlite3.connect(source)) as db:
                self.assertEqual(
                    db.execute("SELECT count(*) FROM observations").fetchone()[0], 60
                )
            with closing(sqlite3.connect(profiles.path)) as db:
                worker.plan(db)
                worker.process_one(db, api=fake)
                self.assertEqual(
                    db.execute("SELECT last_observation FROM profile_heads").fetchone()[
                        0
                    ],
                    60,
                )
                facts = json.loads(
                    db.execute(
                        "SELECT content FROM profile_versions ORDER BY id DESC LIMIT 1"
                    ).fetchone()[0]
                )["facts"]
                self.assertEqual([f["source_ids"][0] for f in facts], [1, 31])
                self.assertEqual(
                    db.execute("SELECT count(*) FROM members").fetchone()[0], 0
                )

    def test_wake_uses_origin_and_new_activity_cancels(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            Store(root)
            import wakeups

            with closing(sqlite3.connect(root / "mouth.sqlite3")) as db, db:
                wakeups.initialize(db)
                db.execute("CREATE TABLE relay_jobs(batch_id,status)")
                db.execute(
                    "CREATE TABLE relay_deliveries(batch_id,status,updated,record_json)"
                )
                origin = {
                    "platform": "qq",
                    "account": "100000002",
                    "chat_type": "private",
                    "id": "100000001",
                }
                watch = origin | {"chat_type": "group", "id": "1234"}
                old = time.time() - 100
                with closing(sqlite3.connect(root / "observations.sqlite3")) as source:
                    items = [
                        {
                            "start": 0,
                            "end": 3,
                            "intent": "提醒我",
                            "minutes": 1 / 60,
                            "watch": watch,
                            "origin": origin,
                        }
                    ]
                    db.execute("INSERT INTO relay_jobs VALUES('source','control_only')")
                    wakeups.register(db, "source", items, {}, source, now=old)
            coordinator = Coordinator(root)
            wake = IdleWake(root, None, coordinator)
            packet = wake.due()[0]
            self.assertEqual(packet["scene"]["conversation"], origin["id"])
            self.assertEqual(packet["internal_wake"]["watch"]["id"], watch["id"])
            self.assertTrue(valid_wake(root, packet["internal_wake"]["wake_id"]))
            Store(root).capture(
                platform="qq",
                account="100000002",
                kind="group",
                conversation="1234",
                sender="other",
                message_id="new",
                raw={"message": "新消息"},
            )
            self.assertFalse(valid_wake(root, packet["internal_wake"]["wake_id"]))


if __name__ == "__main__":
    unittest.main()
