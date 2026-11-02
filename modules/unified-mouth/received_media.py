"""Persist provenance and reusable paths for received images, without vision."""

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from .memory_engine import normalize


class ReceivedMedia:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / "received-media.sqlite3"
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript("""CREATE TABLE IF NOT EXISTS images(
                source TEXT,ordinal INTEGER,account TEXT,kind TEXT,conversation TEXT,
                sender TEXT,nickname TEXT,time TEXT,path TEXT,status TEXT,description TEXT,
                PRIMARY KEY(source,ordinal));
                CREATE INDEX IF NOT EXISTS images_scope ON images(account,kind,conversation);
            """)

    def capture(self, records, download=True):
        import incoming_images
        from image_library import INCOMING, checked_image

        downloads = 0
        with closing(sqlite3.connect(self.path)) as db, db:
            for record in records:
                raw = record["observation"]
                event = raw.get("raw_event", {})
                item = normalize(raw, "incoming", str(record["id"]))
                if not item:
                    continue
                parts = event.get("message", [])
                if not isinstance(parts, list):
                    continue
                ordinal = 0
                for part in parts:
                    if not isinstance(part, dict) or part.get("type") != "image":
                        continue
                    index = ordinal
                    ordinal += 1
                    old = db.execute(
                        "SELECT path,status FROM images WHERE source=? AND ordinal=?",
                        (item["key"], index),
                    ).fetchone()
                    if old and old[1] == "available" and Path(old[0]).is_file():
                        continue
                    data = part.get("data", {})
                    url = data.get("url")
                    path, status = None, "unavailable"
                    if data.get('cached_path'):
                        try:
                            path=str(checked_image(data['cached_path']))
                            status='available'
                        except (ValueError,OSError):
                            pass
                    if isinstance(url, str):
                        cached = INCOMING / (
                            hashlib.sha256(url.encode()).hexdigest() + ".jpg"
                        )
                        try:
                            if cached.exists():
                                path = str(checked_image(str(cached)))
                            elif not path and download and downloads < 4:
                                downloads += 1
                                path = incoming_images.fetch(url)
                            if path:
                                status = "available"
                        except Exception:
                            pass
                    if not download and not path:
                        continue
                    description = str(data.get("summary") or "收到的图片")[:150]
                    db.execute(
                        "INSERT OR REPLACE INTO images VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            item["key"],
                            index,
                            item["account"],
                            item["kind"],
                            item["conversation"],
                            str(raw.get("sender_id", "")),
                            event.get("sender", {}).get("nickname", ""),
                            raw.get("received_at", ""),
                            path,
                            status,
                            description,
                        ),
                    )

    def offer(self, packet, visible_keys):
        # Recover paths already downloaded by the prior pipeline, with no network.
        scene = packet["scene"]
        with closing(sqlite3.connect(self.root / "observations.sqlite3")) as db:
            recent = db.execute(
                "SELECT id,raw_json FROM observations WHERE account_id=? AND chat_type=? AND conversation_id=? ORDER BY id DESC LIMIT 400",
                (
                    scene["account"],
                    scene["chat_type"],
                    scene["conversation"],
                ),
            ).fetchall()
        self.capture(
            [{"id": oid, "observation": json.loads(raw)} for oid, raw in recent],
            download=False,
        )
        self.capture(packet["new_messages"])
        keys = set(visible_keys)
        keys.update(
            normalize(x["observation"], "incoming", str(x["id"]))["key"]
            for x in packet["new_messages"]
            if not x["observation"].get("internal_event")
        )
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT * FROM images WHERE status='available' ORDER BY time,ordinal"
            ).fetchall()
        result = []
        seen = set()
        for row in rows:
            if (
                row["source"] not in keys
                or row["path"] in seen
                or not Path(row["path"]).is_file()
            ):
                continue
            seen.add(row["path"])
            result.append(
                {
                    "kind": "incoming",
                    "path": row["path"],
                    "description": "收到的原图，可原样转发；附带的图片文字识别只描述文字，不代表已理解画面；来源"
                    + (row["nickname"] or row["sender"])
                    + "(用户ID:"
                    + row["sender"]
                    + ") "
                    + row["time"]
                    + "；平台附带摘要："
                    + row["description"],
                }
            )
        return result
