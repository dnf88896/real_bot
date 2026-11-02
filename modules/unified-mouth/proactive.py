"""Persist proactive opportunities; reuse upstream timing, keep our only mouth."""

import json
import sqlite3
import time
import uuid
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo


def config(root):
    path = Path(root) / "engagement.json"
    return (json.loads(path.read_text("utf8")) if path.exists() else {}).get(
        "proactive", {}
    )


def activity(root, scene, exclude=None):
    """Same logical conversation only; a preview still belongs to its group."""
    root = Path(root)
    with closing(sqlite3.connect(root / "observations.sqlite3")) as db:
        incoming = db.execute(
            "SELECT coalesce(max(id),0) FROM observations WHERE account_id=? AND chat_type=? AND conversation_id=?",
            (scene["account"], scene["chat_type"], scene["conversation"]),
        ).fetchone()[0]
    outgoing = []
    with closing(sqlite3.connect(root / "mouth.sqlite3")) as db:
        if db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='relay_deliveries'"
        ).fetchone():
            rows = db.execute(
                "SELECT batch_id,id,record_json FROM relay_deliveries WHERE status='sent' AND record_json IS NOT NULL"
            )
            for batch, did, raw in rows:
                if batch == exclude:
                    continue
                record = json.loads(raw)
                if (
                    str(record.get("platform")),
                    str(record.get("account_id")),
                    str(record.get("chat_type")),
                    str(record.get("conversation_id")),
                ) == (
                    scene["platform"],
                    scene["account"],
                    scene["chat_type"],
                    scene["conversation"],
                ):
                    outgoing.append(did)
    return [incoming, sorted(outgoing)]


def target_allowed(cfg, scene):
    return cfg.get("enabled", False) and scene in cfg.get("targets", [])


def in_hours(now, cfg):
    hour = datetime.fromtimestamp(now, ZoneInfo("Asia/Shanghai")).hour
    start, end = cfg.get("start_hour", 9), cfg.get("end_hour", 23)
    return start <= hour < end if start < end else hour >= start or hour < end


def valid(root, wid, now=None):
    """Recheck policy/activity before generation and before every actual send."""
    root = Path(root)
    now = time.time() if now is None else now
    cfg = config(root)
    with closing(sqlite3.connect(root / "proactive.sqlite3")) as db:
        row = db.execute(
            "SELECT scene,baseline,status,batch,created FROM opportunities WHERE id=?",
            (wid,),
        ).fetchone()
    if not row or row[2] not in ("queued", "completed"):
        return False
    scene = json.loads(row[0])
    return (
        target_allowed(cfg, scene)
        and in_hours(now, cfg)
        and now - row[4] <= cfg.get("max_pending_seconds", 600)
        and activity(root, scene, row[3]) == json.loads(row[1])
    )


def finish(root, wid):
    with closing(sqlite3.connect(Path(root) / "proactive.sqlite3")) as db, db:
        db.execute(
            "UPDATE opportunities SET status='completed' WHERE id=? AND status='queued'",
            (wid,),
        )


class Proactive:
    def __init__(self, root, coordinator):
        self.root = Path(root)
        self.coordinator = coordinator
        self.path = self.root / "proactive.sqlite3"
        self.enqueued = set()
        from .vendor.proactive_schedule import SchedulerService

        self.scheduler = SchedulerService(
            SimpleNamespace(timezone=ZoneInfo("Asia/Shanghai"))
        )
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript("""PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions(scope TEXT PRIMARY KEY,scene TEXT,baseline TEXT,next_due REAL,attempts INTEGER);
                CREATE TABLE IF NOT EXISTS opportunities(id TEXT PRIMARY KEY,scope TEXT,scene TEXT,baseline TEXT,batch TEXT UNIQUE,packet TEXT,status TEXT,created REAL);
                CREATE INDEX IF NOT EXISTS opportunities_time ON opportunities(created);
            """)

    def next_due(self, now, cfg):
        low, high = (
            cfg.get("min_interval_minutes", 30),
            cfg.get("max_interval_minutes", 90),
        )
        if (
            type(low) is not int
            or type(high) is not int
            or not 1 <= low <= high <= 10080
        ):
            raise ValueError("Invalid proactive minute interval")
        schedule = self.scheduler.compute_next_trigger(
            {"min_interval_minutes": low, "max_interval_minutes": high}
        )
        due = now + schedule[4]
        if not in_hours(due, cfg):
            local = datetime.fromtimestamp(due, ZoneInfo("Asia/Shanghai"))
            start = local.replace(
                hour=cfg.get("start_hour", 9), minute=0, second=0, microsecond=0
            )
            if start.timestamp() <= due:
                start += timedelta(days=1)
            due = start.timestamp()
        return due

    def due(self, now=None):
        now = time.time() if now is None else now
        cfg = config(self.root)
        if not cfg.get("enabled", False):
            return []
        local = datetime.fromtimestamp(now, ZoneInfo("Asia/Shanghai"))
        day_start = local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        packets = []
        with closing(sqlite3.connect(self.path)) as db, db:
            # Durable opportunities can survive a restart, but never regenerate
            # an already committed output or replay a stale conversation.
            for wid, batch, raw in db.execute(
                "SELECT id,batch,packet FROM opportunities WHERE status='queued'"
            ).fetchall():
                with closing(sqlite3.connect(self.root / "mouth.sqlite3")) as mouth:
                    generated = mouth.execute(
                        "SELECT 1 FROM outputs WHERE batch_id=?", (batch,)
                    ).fetchone()
                if generated:
                    db.execute(
                        "UPDATE opportunities SET status='completed' WHERE id=?", (wid,)
                    )
                elif not valid(self.root, wid, now):
                    db.execute(
                        "UPDATE opportunities SET status='cancelled' WHERE id=?", (wid,)
                    )
                elif wid not in self.enqueued:
                    packets.append(json.loads(raw))
                    self.enqueued.add(wid)
            for scene in cfg.get("targets", []):
                if not self.coordinator.permitted({"scene": scene}):
                    continue
                scope = json.dumps(scene, sort_keys=True)
                current = activity(self.root, scene)
                row = db.execute(
                    "SELECT baseline,next_due,attempts FROM sessions WHERE scope=?",
                    (scope,),
                ).fetchone()
                if row is None:
                    db.execute(
                        "INSERT INTO sessions VALUES(?,?,?,?,0)",
                        (
                            scope,
                            json.dumps(scene),
                            json.dumps(current),
                            self.next_due(now, cfg),
                        ),
                    )
                    continue
                previous, deadline, attempts = json.loads(row[0]), row[1], row[2]
                if current != previous:
                    # Only a new incoming message restores permission to try
                    # again; our own output must not start an infinite loop.
                    if current[0] != previous[0]:
                        attempts = 0
                    db.execute(
                        "UPDATE sessions SET baseline=?,next_due=?,attempts=? WHERE scope=?",
                        (json.dumps(current), self.next_due(now, cfg), attempts, scope),
                    )
                    continue
                if (
                    now < deadline
                    or not in_hours(now, cfg)
                    or attempts >= cfg.get("max_unanswered", 1)
                    or scope in self.coordinator.busy
                ):
                    continue
                if db.execute(
                    "SELECT count(*) FROM opportunities WHERE scope=? AND created>=?",
                    (scope, day_start),
                ).fetchone()[0] >= cfg.get("max_per_day", 2):
                    continue
                if db.execute(
                    "SELECT 1 FROM opportunities WHERE scope=? AND status='queued'",
                    (scope,),
                ).fetchone():
                    continue
                # Explicit self-wake tasks take precedence; do not send a second
                # autonomous opportunity while the mouth is already waiting.
                with closing(sqlite3.connect(self.root / "mouth.sqlite3")) as mouth:
                    if mouth.execute(
                        "SELECT 1 FROM sqlite_master WHERE name='intent_wakeups'"
                    ).fetchone():
                        pending = mouth.execute(
                            "SELECT origin_json FROM intent_wakeups WHERE status IN ('awaiting_delivery','waiting','queued')"
                        ).fetchall()
                        if any(
                            json.loads(r[0])
                            == {
                                "platform": scene["platform"],
                                "account": scene["account"],
                                "chat_type": scene["chat_type"],
                                "id": scene["conversation"],
                            }
                            for r in pending
                        ):
                            continue
                wid = "proactive:" + uuid.uuid4().hex
                batch = "wake:" + wid
                with closing(
                    sqlite3.connect(self.root / "observations.sqlite3")
                ) as source:
                    head = source.execute(
                        "SELECT coalesce(max(id),0) FROM observations"
                    ).fetchone()[0]
                intention = "当前会话已有一段时间没有新消息。这是一次自主联系的机会，不是对方来信；依据当前记忆决定是否有想说的话。没有合适内容时只输出【保持沉默】，不必为完成任务强行问候或催促，也不安排连续自唤醒。"
                packet = {
                    "batch_id": batch,
                    "scene": scene,
                    "wake_depth": 1,
                    "internal_wake": {
                        "kind": "proactive",
                        "wake_id": wid,
                        "intent": intention,
                        "origin": {
                            "platform": scene["platform"],
                            "account": scene["account"],
                            "chat_type": scene["chat_type"],
                            "id": scene["conversation"],
                        },
                        "due": deadline,
                    },
                    "new_messages": [
                        {
                            "id": head,
                            "observation": {
                                "internal_event": True,
                                "platform": scene["platform"],
                                "account_id": scene["account"],
                                "chat_type": scene["chat_type"],
                                "conversation_id": scene["conversation"],
                                "sender_id": scene["account"],
                                "received_at": datetime.fromtimestamp(
                                    now, UTC
                                ).isoformat(),
                                "raw_event": {"message": []},
                            },
                        }
                    ],
                    "image_options": [],
                    "engine": "astrbot",
                }
                db.execute(
                    "INSERT INTO opportunities VALUES(?,?,?,?,?,?,?,?)",
                    (
                        wid,
                        scope,
                        json.dumps(scene),
                        json.dumps(current),
                        batch,
                        json.dumps(packet, ensure_ascii=False),
                        "queued",
                        now,
                    ),
                )
                db.execute(
                    "UPDATE sessions SET attempts=attempts+1,next_due=? WHERE scope=?",
                    (self.next_due(now, cfg), scope),
                )
                packets.append(packet)
                self.enqueued.add(wid)
        return packets
