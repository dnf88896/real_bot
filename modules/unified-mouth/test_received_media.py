"""Received paths survive turns; resources cannot disappear into omit spans."""

import hashlib
import importlib
import io
import json
import sqlite3
import sys
import tempfile
import types
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import image_library
import incoming_images
import relay
from store import Store

package = types.ModuleType("media_test_package")
package.__path__ = [str(Path(__file__).parent)]
sys.modules[package.__name__] = package
ReceivedMedia = importlib.import_module(
    package.__name__ + ".received_media"
).ReceivedMedia
normalize = importlib.import_module(package.__name__ + ".memory_engine").normalize


class ReceivedTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root)
        self.images = self.root / "incoming"
        self.images.mkdir()
        self.path = self.images / "original.jpg"
        self.path.write_bytes(b"\xff\xd8\xfftest-original")
        self.patches = [
            patch.object(image_library, "INCOMING", self.images),
            patch.object(incoming_images, "INCOMING", self.images),
        ]
        for p in self.patches:
            p.start()
        self.media = ReceivedMedia(self.root)
        self.dest = {
            "platform": "qq",
            "account": "100000002",
            "chat_type": "private",
            "id": "100000001",
        }

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def packet(self, mid, image=False, uid="100000001"):
        parts = (
            [
                {
                    "type": "image",
                    "data": {
                        "url": "https://qpic.cn/original",
                        "summary": "平台图片摘要",
                    },
                }
            ]
            if image
            else [{"type": "text", "data": {"text": "把刚才那张原图发回来"}}]
        )
        return self.store.capture(
            platform="qq",
            account="100000002",
            kind="private",
            conversation=uid,
            sender=uid,
            message_id=str(mid),
            raw={
                "post_type": "message",
                "message": parts,
                "sender": {"nickname": "原图发送人"},
            },
        )[1]

    def test_next_turn_and_restart_keep_path_without_refetch(self):
        first = self.packet(1, image=True)
        with patch.object(
            incoming_images, "fetch", return_value=str(self.path)
        ) as fetch:
            offered = self.media.offer(first, [])
        self.assertEqual(offered[0]["path"], str(self.path))
        self.assertIn("100000001", offered[0]["description"])
        self.assertEqual(fetch.call_count, 1)
        source = normalize(first["new_messages"][0]["observation"], "incoming", "1")[
            "key"
        ]
        with patch.object(
            incoming_images, "fetch", side_effect=AssertionError("Path must be durable")
        ):
            second = self.packet(2)
            self.assertEqual(
                ReceivedMedia(self.root).offer(second, [source])[0]["path"],
                str(self.path),
            )
            unrelated = self.packet(3, uid="400000005")
            self.assertEqual(self.media.offer(unrelated, []), [])
            self.assertEqual(
                self.media.offer(unrelated, [source])[0]["path"], str(self.path)
            )

    def test_existing_downloaded_images_are_reindexed_without_network(self):
        first = self.packet(1, image=True)
        url = "https://qpic.cn/original"
        cached = self.images / (hashlib.sha256(url.encode()).hexdigest() + ".jpg")
        cached.write_bytes(self.path.read_bytes())
        key = normalize(first["new_messages"][0]["observation"], "incoming", "1")["key"]
        with patch.object(
            incoming_images, "fetch", side_effect=AssertionError("Use old cache")
        ):
            result = self.media.offer(self.packet(2), [key])
        self.assertEqual(result[0]["path"], str(cached))

    def test_image_content_cannot_be_discarded_or_classified_as_text(self):
        text = "[图片路径:" + str(self.path) + "]"
        options = [{"kind": "incoming", "path": str(self.path), "description": "原图"}]
        for kind in ("omit", "text"):
            plan = {
                "segments": [
                    {
                        "kind": kind,
                        "start": 0,
                        "end": len(text),
                        "destination": None if kind == "omit" else self.dest,
                    }
                ]
            }
            with self.assertRaisesRegex(ValueError, "valid content"):
                relay.validate_resources(plan, text, options)
        plan = {
            "segments": [
                {
                    "kind": "image",
                    "start": 0,
                    "end": len(text),
                    "destination": self.dest,
                }
            ]
        }
        relay.validate_resources(plan, text, options)
        message, spoken, paths = relay.compile_parts(text, plan["segments"], options)
        self.assertEqual(message[0]["type"], "image")
        self.assertEqual(spoken, [])
        self.assertEqual(paths, [str(self.path)])
        with self.assertRaises(ValueError):
            relay.offered_image_path("不要发这张 " + str(self.path), options)

    def test_omitted_only_image_triggers_corrective_api_retry(self):
        packet = self.packet(1)
        packet["image_options"] = [
            {"kind": "incoming", "path": str(self.path), "description": "原图"}
        ]
        text = str(self.path)
        requests = []

        def fake(request, timeout):
            body = json.loads(request.data)
            requests.append(body)
            kind = "omit" if len(requests) == 1 else "image"
            plan = {
                "segments": [
                    {
                        "kind": kind,
                        "start": 0,
                        "end": len(text),
                        "destination": None if kind == "omit" else self.dest,
                    }
                ]
            }
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

        (self.root / "api.json").write_text('{"api_key":"test-only"}')
        with (
            patch.object(relay, "ROOT", self.root),
            patch.object(relay.urllib.request, "urlopen", side_effect=fake),
        ):
            plan = relay.invoke(text, packet, [self.dest])
        self.assertEqual(len(requests), 2)
        self.assertEqual(plan["segments"][0]["kind"], "image")
        self.assertIn("valid content", requests[-1]["messages"][-1]["content"])

    def test_failed_media_plan_has_no_partial_deliveries(self):
        packet = self.packet(1)
        packet["image_options"] = [{"kind": "incoming", "path": str(self.path)}]
        self.store.prepare(packet)
        text = "你好\n" + str(self.path)
        self.store.complete(packet, text, {}, {})
        with closing(sqlite3.connect(self.store.path)) as db, db:
            relay.initialize(db)
            db.execute(
                "INSERT INTO relay_jobs(batch_id,status,source_hash,directory) VALUES(?,?,?,?)",
                (
                    packet["batch_id"],
                    "pending",
                    relay.digest(text),
                    json.dumps([self.dest]),
                ),
            )
            db.commit()
            relay.plan_one(
                db,
                lambda *args: {
                    "segments": [
                        {
                            "kind": "text",
                            "start": 0,
                            "end": 2,
                            "destination": self.dest,
                        },
                        {
                            "kind": "omit",
                            "start": 3,
                            "end": len(text),
                            "destination": None,
                        },
                    ]
                },
            )
            self.assertEqual(
                db.execute("SELECT count(*) FROM relay_deliveries").fetchone()[0], 0
            )
            self.assertEqual(
                db.execute("SELECT status FROM relay_jobs").fetchone()[0], "pending"
            )

    def test_explicit_headers_scope_and_body_mentions_are_not_recipients(self):
        current = self.dest | {"name": "青蛙"}
        other = self.dest | {"id": "400000005", "name": "果冻"}
        scene = {
            "platform": "qq",
            "account": "100000002",
            "chat_type": "private",
            "conversation": "100000001",
        }
        text = "你直接发给果冻，他要是不信让他来问我。"
        plan = {
            "segments": [
                {
                    "kind": "text",
                    "start": 0,
                    "end": len(text),
                    "destination": {k: other[k] for k in self.dest},
                }
            ]
        }
        with self.assertRaisesRegex(ValueError, "scope"):
            relay.validate_routes(plan, text, [current, other], scene)
        plan["segments"][0]["destination"] = self.dest
        relay.validate_routes(plan, text, [current, other], scene)
        text = "给果冻：\n你好\n再见"
        plan = {
            "segments": [
                {"kind": "omit", "start": 0, "end": 5, "destination": None},
                {
                    "kind": "text",
                    "start": 5,
                    "end": len(text),
                    "destination": {k: other[k] for k in self.dest},
                },
            ]
        }
        relay.validate_routes(plan, text, [current, other], scene)
        plan["segments"][1]["destination"] = self.dest
        with self.assertRaisesRegex(ValueError, "scope"):
            relay.validate_routes(plan, text, [current, other], scene)


if __name__ == "__main__":
    unittest.main()
