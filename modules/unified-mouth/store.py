"""Durable handoff from AstrBot final responses to the independent relay."""

import hashlib
import json
import os
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
if __package__:
    from .message_semantics import mentions_self
else:
    from message_semantics import mentions_self

ROOT = Path(os.environ.get("ASTRBOT_MOUTH_ROOT", "/opt/astrbot-mouth"))


class Store:
    """Store immutable input and generated output in the new runtime only."""

    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.archive = self.root / "observations.sqlite3"
        self.path = self.root / "mouth.sqlite3"
        with closing(sqlite3.connect(self.archive)) as db, db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS observations(
                  id INTEGER PRIMARY KEY, event_key TEXT UNIQUE, account_id TEXT,
                  chat_type TEXT, conversation_id TEXT, sender_id TEXT,
                  received_at TEXT, raw_json TEXT);
            """)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS jobs(batch_id TEXT PRIMARY KEY,packet TEXT,
                  status TEXT,attempts INTEGER DEFAULT 0,next_retry REAL DEFAULT 0,error TEXT);
                CREATE TABLE IF NOT EXISTS outputs(batch_id TEXT PRIMARY KEY,created REAL,
                  text TEXT,persona_name TEXT,persona_hash TEXT,model TEXT,
                  request_json TEXT,usage_json TEXT,sent_to_qq INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS media_outputs(batch_id TEXT PRIMARY KEY,paths_json TEXT);
                CREATE TABLE IF NOT EXISTS deliveries(batch_id TEXT,status TEXT,updated REAL,
                  message_id TEXT,record_json TEXT);
            """)

    def notice(self, raw):
        """Audit protocol notifications without creating a chat observation or job."""
        encoded = json.dumps(raw, sort_keys=True, ensure_ascii=False)
        with closing(sqlite3.connect(self.archive)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS platform_notices(event_key TEXT PRIMARY KEY,received_at TEXT,raw_json TEXT)"
            )
            db.execute(
                "INSERT OR IGNORE INTO platform_notices VALUES(?,?,?)",
                (
                    hashlib.sha256(encoded.encode()).hexdigest(),
                    datetime.now(UTC).isoformat(),
                    encoded,
                ),
            )

    def capture(
        self,
        *,
        platform,
        account,
        kind,
        conversation,
        sender,
        message_id,
        raw,
        generate=True,
    ):
        """Capture an event once and prepare its handoff envelope.

        Args:
            platform: Normalized source platform.
            account: Bot account ID.
            kind: Private or group conversation type.
            conversation: Stable destination identifier.
            sender: Source sender ID.
            message_id: Stable platform message identifier.
            raw: Original source event dictionary.
            generate: Whether to create a model job, or only record an observation.

        Returns:
            A batch ID, handoff packet and duplicate-generation flag.
        """
        identity = json.dumps([platform, account, kind, conversation, message_id])
        batch = "astrbot:" + hashlib.sha256(identity.encode()).hexdigest()
        if platform=='qq':
            try:
                if __package__:
                    from . import share_cards
                else:
                    import share_cards
                share_cards.capture(self.root,raw,batch)
            except Exception:
                pass

        observation = {
            "platform": platform,
            "account_id": str(account),
            "chat_type": kind,
            "conversation_id": str(conversation),
            "sender_id": str(sender),
            "message_id": str(message_id),
            "received_at": datetime.now(UTC).isoformat(),
            "raw_event": raw,
        }
        observation['mentions_self'] = mentions_self(observation, account)
        with closing(sqlite3.connect(self.archive)) as db, db:
            db.execute(
                "INSERT OR IGNORE INTO observations(event_key,account_id,chat_type,"
                "conversation_id,sender_id,received_at,raw_json) VALUES(?,?,?,?,?,?,?)",
                (
                    batch,
                    str(account),
                    kind,
                    str(conversation),
                    str(sender),
                    observation["received_at"],
                    json.dumps(observation, ensure_ascii=False),
                ),
            )
            oid, encoded = db.execute(
                "SELECT id,raw_json FROM observations WHERE event_key=?", (batch,)
            ).fetchone()
        packet = {
            "batch_id": batch,
            "scene": {
                "platform": platform,
                "account": str(account),
                "chat_type": kind,
                "conversation": str(conversation),
            },
            "new_messages": [{"id": oid, "observation": json.loads(encoded)}],
            "image_options": [],
            "engine": "astrbot",
        }
        if not generate:
            return batch, packet, False
        with closing(sqlite3.connect(self.path)) as db, db:
            old = db.execute(
                "SELECT 1 FROM outputs WHERE batch_id=?", (batch,)
            ).fetchone()
            db.execute(
                "INSERT OR IGNORE INTO jobs(batch_id,packet,status) VALUES(?,?,'collecting')",
                (batch, json.dumps(packet, ensure_ascii=False)),
            )
        return batch, packet, bool(old)

    def prepare(self, packet):
        """Persist an admitted native batch, including merged source messages."""
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "INSERT OR IGNORE INTO jobs(batch_id,packet,status) VALUES(?,?,'collecting')",
                (packet["batch_id"], json.dumps(packet, ensure_ascii=False)),
            )
            db.execute(
                "UPDATE jobs SET packet=? WHERE batch_id=? AND status='collecting'",
                (json.dumps(packet, ensure_ascii=False), packet["batch_id"]),
            )

    def complete(self, packet, text, request, usage):
        """Persist the final original text without stripping or rewriting.

        Args:
            packet: Captured envelope with resource references.
            text: Final model content, excluding reasoning and intermediate steps.
            request: Redacted structural request audit without credentials.
            usage: Final response usage supplied by AstrBot.

        Returns:
            Whether a new generated output was committed.

        Raises:
            ValueError: If content is empty or an existing output would be changed.
        """
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Empty final model response")
        encoded = json.dumps(packet, ensure_ascii=False)
        with closing(sqlite3.connect(self.path)) as db, db:
            old = db.execute(
                "SELECT text FROM outputs WHERE batch_id=?", (packet["batch_id"],)
            ).fetchone()
            if old:
                if old[0] != text:
                    raise ValueError(
                        "Final output already committed with different text"
                    )
                return False
            db.execute(
                "INSERT INTO outputs VALUES(?,?,?,?,?,?,?,?,0)",
                (
                    packet["batch_id"],
                    time.time(),
                    text,
                    "AstrBot unified persona",
                    "",
                    request.get("model", "deepseek-flash"),
                    json.dumps(request, ensure_ascii=False, default=str),
                    json.dumps(usage, ensure_ascii=False, default=str),
                ),
            )
            db.execute(
                "UPDATE jobs SET packet=?,status='generated' WHERE batch_id=?",
                (encoded, packet["batch_id"]),
            )
            db.execute(
                "INSERT INTO media_outputs VALUES(?,?)", (packet["batch_id"], "[]")
            )
        return True
