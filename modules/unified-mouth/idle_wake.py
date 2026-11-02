"""Native event adapter for persistent conversation-idle intentions."""

import importlib
import json
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path


def valid(root, wake_id):
    """Check watched activity while excluding this wake's own outgoing bubbles."""
    root = Path(root)
    if wake_id.startswith('active:'):
        module=importlib.import_module((__package__+'.active_system') if __package__ else 'active_system')
        return module.valid(root,wake_id)
    if wake_id.startswith('autonomous:'):
        module=importlib.import_module((__package__+'.autonomy') if __package__ else 'autonomy')
        return module.valid(root,wake_id)
    if wake_id.startswith('heybox-notify:'):
        module=importlib.import_module((__package__+'.heybox_notifications') if __package__ else 'heybox_notifications')
        return module.valid(root,wake_id)
    if wake_id.startswith('bili-notify:'):
        if __package__:
            from .bilibili_notifications import valid as notification_valid
        else:
            from bilibili_notifications import valid as notification_valid
        return notification_valid(root,wake_id)
    if wake_id.startswith(("post:","heybox-post:")):
        if __package__:
            from .post_activity import valid as posting_valid
        else:
            from post_activity import valid as posting_valid
        return posting_valid(root,wake_id)
    if wake_id.startswith("proactive:"):
        # Also importable by the standalone relay outside the plugin package.
        if __package__:
            from .proactive import valid as proactive_valid
        else:
            from proactive import valid as proactive_valid
        return proactive_valid(root, wake_id)
    wakeups = importlib.import_module("wakeups")
    with closing(sqlite3.connect(root / "mouth.sqlite3")) as db:
        row = db.execute(
            "SELECT status,watch_json,baseline_json FROM intent_wakeups WHERE id=?",
            (wake_id,),
        ).fetchone()
        if not row or row[0] not in ("waiting", "queued", "completed"):
            return False
        with closing(
            sqlite3.connect(
                (root / "observations.sqlite3").resolve().as_uri() + "?mode=ro",
                uri=True,
            )
        ) as source:
            return wakeups.activity(
                db, source, json.loads(row[1]), "wake:" + wake_id
            ) == json.loads(row[2])


class IdleWake:
    """Re-enter the native event flow with fresh context, never send directly."""

    def __init__(self, root, context, coordinator):
        self.root = Path(root)
        self.context = context
        self.coordinator = coordinator
        self.enqueued = set()
        self.wakeups = importlib.import_module("wakeups")
        self.wakeups.MOUTH = self.root / "mouth.sqlite3"
        self.wakeups.ARCHIVE = self.root / "observations.sqlite3"

    def due(self):
        if not self.coordinator.config["wake_enabled"]:
            return []
        with closing(sqlite3.connect(self.root / "mouth.sqlite3", timeout=20)) as db:
            # The relay creates these tables. Isolated generation tests have no relay.
            if not db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='relay_jobs'"
            ).fetchone():
                return []
            with closing(
                sqlite3.connect(
                    (self.root / "observations.sqlite3").resolve().as_uri()
                    + "?mode=ro",
                    uri=True,
                )
            ) as source:
                rows = self.wakeups.tick(db, source)
                head = source.execute(
                    "SELECT coalesce(max(id),0) FROM observations"
                ).fetchone()[0]
            packets = []
            for row in rows:
                batch = "wake:" + row["id"]
                if row["id"] in self.enqueued:
                    module=importlib.import_module((__package__+'.active_gate') if __package__ else 'active_gate')
                    if not module.ready(self.root,batch,default=False):continue
                if db.execute(
                    "SELECT 1 FROM outputs WHERE batch_id=?", (batch,)
                ).fetchone():
                    with db:
                        db.execute(
                            "UPDATE intent_wakeups SET status='completed' WHERE id=?",
                            (row["id"],),
                        )
                    continue
                origin = json.loads(row["origin_json"])
                event = {
                    "wake_id": row["id"],
                    "intent": row["intent"],
                    "watch": json.loads(row["watch_json"]),
                    "origin": origin,
                    "idle_minutes": row["minutes"],
                    "due": row["due"],
                }
                packet = {
                    "batch_id": batch,
                    "scene": {
                        "platform": origin["platform"],
                        "account": origin["account"],
                        "chat_type": origin["chat_type"],
                        "conversation": origin["id"],
                    },
                    "wake_depth": 1,
                    "internal_wake": event,
                    "new_messages": [
                        {
                            "id": head,
                            "observation": {
                                "internal_event": True,
                                "platform": origin["platform"],
                                "account_id": origin["account"],
                                "chat_type": origin["chat_type"],
                                "conversation_id": origin["id"],
                                "sender_id": origin["account"],
                                "received_at": datetime.now(UTC).isoformat(),
                                "raw_event": {"message": []},
                            },
                        }
                    ],
                    "image_options": [],
                    "engine": "astrbot",
                }
                module=importlib.import_module((__package__+'.wakefulness') if __package__ else 'wakefulness')
                if not module.allowed(self.root,packet['scene']):continue
                if not self.coordinator.permitted(packet):
                    continue
                with db:
                    db.execute(
                        "UPDATE intent_wakeups SET status='queued',brain_batch_id=? WHERE id=?",
                        (batch, row["id"]),
                    )
                self.enqueued.add(row["id"])
                packets.append(packet)
            return packets

    async def emit(self, packet):
        """Create a native QQ event; internal metadata prevents fake input storage."""
        if packet['scene']['chat_type']=='autonomous':
            from .autonomy import emit
            return await emit(self.context,packet)
        if packet['scene']['platform']=='wechat':
            adapter=self.context.get_platform_inst('wechat-unified')
            return await adapter.emit_packet(packet) if adapter is not None else False
        if packet['scene']['platform']=='heybox':
            from .platform_bridge import emit_heybox
            return await emit_heybox(self.context,packet)
        if packet['scene']['platform']=='bilibili':
            from .platform_bridge import emit_bilibili
            return await emit_bilibili(self.context,packet)
        from aiocqhttp import Event
        from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
            AiocqhttpMessageEvent,
        )

        adapter = self.context.get_platform_inst("qq-unified")
        if adapter is None:
            return False
        scene = packet["scene"]
        if packet.get("internal_wake"):
            raw = {
                "post_type": "message",
                "message_type": scene["chat_type"],
                "sub_type": "normal" if scene["chat_type"] == "group" else "friend",
                "time": int(time.time()),
                "self_id": int(scene["account"]),
                "user_id": int(scene["conversation"]),
                "message_id": packet["batch_id"],
                "message": [
                    {
                        "type": "text",
                        "data": {
                            "text": "当前唤醒意愿：" + packet["internal_wake"]["intent"]
                        },
                    }
                ],
                "sender": {
                    "user_id": int(scene["conversation"]),
                    "nickname": "当前会话",
                },
                "font": 0,
            }
            if scene["chat_type"] == "group":
                raw["group_id"] = int(scene["conversation"])
        else:
            raw = packet["new_messages"][0]["observation"]["raw_event"]
        message = await adapter.convert_message(Event(raw))
        event = AiocqhttpMessageEvent(
            message.message_str,
            message,
            adapter.meta(),
            scene["conversation"],
            adapter.bot,
        )
        event.set_extra("unified_recovery_packet", packet)
        if packet.get("internal_wake"):
            event.set_extra("unified_internal_wake", True)
        await self.context.get_event_queue().put(event)
        return True

    def finish(self, packet):
        internal = packet.get("internal_wake")
        if internal and str(internal.get("wake_id","")).startswith("active:"):return
        if internal and internal.get("kind") in ("post_activity", "bili_notification", "heybox_notification", "autonomous"):
            return
        if internal and internal.get("kind") == "proactive":
            if __package__:
                from .proactive import finish
            else:
                from proactive import finish
            finish(self.root, internal["wake_id"])
            return
        if internal:
            with (
                closing(sqlite3.connect(self.root / "mouth.sqlite3", timeout=20)) as db,
                db,
            ):
                db.execute(
                    "UPDATE intent_wakeups SET status='completed' WHERE id=? AND status='queued'",
                    (internal["wake_id"],),
                )
