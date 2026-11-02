"""Adapt Chat Plus attention to confirmed relay receipts and our admission gate."""

import asyncio
import copy
import importlib.util
import json
import sqlite3
import sys
import uuid
from contextlib import closing, contextmanager
from pathlib import Path
from .affect import Affect


class Attention:
    def __init__(self, root):
        self.root = Path(root)
        self.affect = Affect(root)
        config = self.root / "engagement.json"
        self.config = (
            json.loads(config.read_text("utf8")) if config.exists() else {}
        ).get("attention", {})
        self.enabled = self.config.get("enabled", False)
        self.manager = None
        self.lock = asyncio.Lock()
        self.path = self.root / "attention.sqlite3"
        if not self.enabled:
            return
        # Upstream uses a module-global class. Isolate it per runtime/fixture.
        module_name = __package__ + ".vendor.attention_" + uuid.uuid4().hex
        spec = importlib.util.spec_from_file_location(
            module_name, Path(__file__).parent / "vendor/attention_manager.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        self.manager = module.AttentionManager
        self.manager._initialized = True
        self.manager.ATTENTION_DECAY_HALFLIFE = float(
            self.config.get("half_life_seconds", 300)
        )
        if self.manager.ATTENTION_DECAY_HALFLIFE <= 0:
            raise ValueError("Invalid attention half life")
        self.manager.ENABLE_EMOTION_DETECTION = False
        self.manager.ENABLE_CONVERSATION_FATIGUE = False
        self.manager.ENABLE_SPILLOVER = True
        # SQLite state and processed receipt identities commit together. Disable
        # upstream file saves; no new emotion or second cooldown system is enabled.
        self.manager._storage_path = None
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript("""PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS state(id INTEGER PRIMARY KEY, data TEXT);
                CREATE TABLE IF NOT EXISTS receipts(identity TEXT PRIMARY KEY);
            """)
            row = db.execute("SELECT data FROM state WHERE id=1").fetchone()
        if row:
            saved = json.loads(row[0])
            self.manager._attention_map = saved["users"]
            self.manager._conversation_activity_map = saved["activity"]
        else:
            # Enabling the feature must not treat old replies as new interactions.
            with closing(sqlite3.connect(self.root / "mouth.sqlite3")) as source:
                if source.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='relay_deliveries'"
                ).fetchone():
                    old = source.execute(
                        "SELECT batch_id,destination FROM relay_deliveries WHERE status='sent' GROUP BY batch_id,destination"
                    ).fetchall()
                else:
                    old = []
            with closing(sqlite3.connect(self.path)) as db, db:
                for batch, destination in old:
                    db.execute(
                        "INSERT OR IGNORE INTO receipts VALUES(?)",
                        (json.dumps([batch, json.loads(destination)], sort_keys=True),),
                    )
                self.save(db)

    def save(self, db):
        db.execute(
            "INSERT OR REPLACE INTO state VALUES(1,?)",
            (
                json.dumps(
                    {
                        "users": self.manager._attention_map,
                        "activity": self.manager._conversation_activity_map,
                    },
                    ensure_ascii=False,
                ),
            ),
        )

    async def probability(self, packet, base):
        if not self.enabled:
            return base
        async with self.lock:
            scene = packet["scene"]
            raw = packet["new_messages"][0]["observation"]
            value = await self.manager.get_adjusted_probability(
                platform_name=scene["platform"] + ":" + scene["account"],
                is_private=scene["chat_type"] == "private",
                chat_id=scene["conversation"],
                current_user_id=str(raw.get("sender_id", "")),
                current_user_name=str(
                    raw.get("raw_event", {}).get("sender", {}).get("nickname", "")
                ),
                current_probability=base,
                attention_increased_probability=float(
                    self.config.get("max_probability", 0.9)
                ),
                attention_decreased_probability=float(
                    self.config.get("min_probability", 0.1)
                ),
                attention_duration=float(self.config.get("duration_seconds", 120)),
                enabled=True,
            )
            with closing(sqlite3.connect(self.path)) as db, db:
                self.save(db)
            if self.affect.cfg['enabled']:
                affinity = self.affect.value(str(raw.get('sender_id','')),platform=scene['platform'])
                value = max(0,min(.98,value*(1+self.affect.cfg['affinity']['probability_factor']*affinity)))
            return value

    async def sync(self):
        """Credit each logical delivered batch once, never generated-only output."""
        await asyncio.to_thread(self.affect.sync_affinity)
        if not self.enabled:
            return
        with closing(sqlite3.connect(self.root / "mouth.sqlite3")) as source:
            if not source.execute(
                "SELECT 1 FROM sqlite_master WHERE name='relay_deliveries'"
            ).fetchone():
                return
            rows = source.execute("""SELECT d.batch_id,d.destination,j.packet
                FROM relay_deliveries d JOIN jobs j ON j.batch_id=d.batch_id
                WHERE d.status='sent' GROUP BY d.batch_id,d.destination
                ORDER BY min(d.rowid)""").fetchall()
        async with self.lock:
            with self.rollback_memory(), closing(sqlite3.connect(self.path)) as db, db:
                for batch, destination, encoded in rows:
                    dest = json.loads(destination)
                    identity = json.dumps([batch, dest], sort_keys=True)
                    if db.execute(
                        "SELECT 1 FROM receipts WHERE identity=?", (identity,)
                    ).fetchone():
                        continue
                    packet = json.loads(encoded)
                    scene = packet["scene"]
                    if all(
                        str(scene[a]) == str(dest[b])
                        for a, b in (
                            ("platform", "platform"),
                            ("account", "account"),
                            ("chat_type", "chat_type"),
                            ("conversation", "id"),
                        )
                    ):
                        users = {}
                        for item in packet["new_messages"]:
                            raw = item["observation"]
                            if not raw.get("internal_event"):
                                users[str(raw["sender_id"])] = (
                                    raw.get("raw_event", {})
                                    .get("sender", {})
                                    .get("nickname", "")
                                )
                        for uid, name in users.items():
                            await self.manager.record_replied_user(
                                scene["platform"] + ":" + scene["account"],
                                scene["chat_type"] == "private",
                                scene["conversation"],
                                uid,
                                name,
                                attention_boost_step=0.4 / len(users),
                                attention_decrease_step=0.1 / len(users),
                                emotion_boost_step=0,
                            )
                    db.execute("INSERT INTO receipts VALUES(?)", (identity,))
                self.save(db)

    @contextmanager
    def rollback_memory(self):
        before_users = copy.deepcopy(self.manager._attention_map)
        before_activity = copy.deepcopy(self.manager._conversation_activity_map)
        try:
            yield
        except BaseException:
            self.manager._attention_map = before_users
            self.manager._conversation_activity_map = before_activity
            raise

    def close(self):
        if self.manager:
            sys.modules.pop(self.manager.__module__, None)
