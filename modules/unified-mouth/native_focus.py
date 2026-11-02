"""Use AstrBot's persistent platform history for the complete current group."""

import asyncio
import json
from contextlib import closing

from .memory_engine import normalize
from .message_semantics import display_text


class NativeFocus:
    """Record received and actually delivered messages with stable source keys."""

    def __init__(self, context, memory, coordinator, profiles):
        self.context = context
        self.memory = memory
        self.coordinator = coordinator
        self.profiles = profiles
        self.lock = asyncio.Lock()
        self.seeded = False

    async def record(self, raw, direction, fallback):
        event = normalize(raw, direction, fallback)
        if not event or event["kind"] != "group":
            return
        async with self.lock:
            with closing(self.coordinator.connect()) as db:
                if db.execute(
                    "SELECT 1 FROM focus_sources WHERE key=?", (event["key"],)
                ).fetchone():
                    return
            native = await self.context.message_history_manager.insert(
                platform_id={"qq":"qq-unified","wechat":"wechat-unified","bilibili":"bilibili-unified"}[event["platform"]],
                user_id={"qq":"qq-unified","wechat":"wechat-unified","bilibili":"bilibili-unified"}[event["platform"]] + ":GroupMessage:" + event["conversation"],
                content={
                    "type": "bot" if direction == "outgoing" else "user",
                    "message": [{"type": "plain", "text": event["text"]}],
                    "source_key": event["key"],
                    "mentions_self": event['mentions_self'],
                    "received_at": raw.get("received_at", ""),
                    "source_label": self.profiles.display(raw)["source_label"],
                    "delivery_mode": raw.get("delivery_mode"),
                },
                sender_id=event["account"]
                if direction == "outgoing"
                else str(raw.get("sender_id", "")),
                sender_name="自己"
                if direction == "outgoing"
                else self.profiles.display(raw)["nickname"],
                max_messages=400,
            )
            with closing(self.coordinator.connect()) as db, db:
                db.execute(
                    "INSERT OR IGNORE INTO focus_sources VALUES(?,?)",
                    (event["key"], native.id),
                )

    async def maintain(self):
        """Seed old group context once, then append confirmed output receipts."""
        with closing(self.memory.connect()) as db:
            if not self.seeded:
                groups = [
                    r[0]
                    for r in db.execute(
                        "SELECT DISTINCT conversation FROM events WHERE kind='group'"
                    )
                ]
                rows = [
                    dict(r)
                    for group in groups
                    for r in reversed(
                        db.execute(
                            "SELECT * FROM events WHERE kind='group' AND conversation=? ORDER BY time DESC,key DESC LIMIT 400",
                            (group,),
                        ).fetchall()
                    )
                ]
            else:
                db.execute(
                    "ATTACH DATABASE ? AS social",
                    (str(self.coordinator.path.resolve()),),
                )
                rows = [
                    dict(r)
                    for r in db.execute(
                        "SELECT e.* FROM events e WHERE kind='group' AND direction='outgoing' "
                        "AND NOT EXISTS(SELECT 1 FROM social.focus_sources s WHERE s.key=e.key) ORDER BY time,key"
                    )
                ]
        for row in rows:
            await self.record(json.loads(row["raw"]), row["direction"], row["key"])
        self.seeded = True

    async def load(self, event, packet):
        """Return native group history and its parallel source-identity positions."""
        if packet["scene"]["chat_type"] != "group":
            return [], []
        history = await self.context.message_history_manager.get(
            platform_id=event.get_platform_id(),
            user_id=event.unified_msg_origin,
            page=1,
            page_size=400,
        )
        current = {
            normalize(item["observation"], "incoming", str(item["id"]))["key"]
            for item in packet["new_messages"]
            if not item["observation"].get("internal_event")
        }
        contexts, refs = [], []
        flags = self.memory.mention_flags([r.content.get('source_key') for r in history if 'mentions_self' not in r.content])
        for row in history:
            data = row.content
            key = data.get("source_key")
            if key in current:
                continue
            text = "".join(part.get("text", "") for part in data.get("message", []))
            text = display_text(text, data.get('mentions_self', flags.get(key, False)))
            preview = (
                "[拟发群消息，实际仅检验者收到] "
                if data.get("delivery_mode") == "group_preview"
                else ""
            )
            contexts.append(
                {
                    "role": "assistant" if data.get("type") == "bot" else "user",
                    "content": preview
                    + data.get("received_at", "")
                    + " "
                    + data.get("source_label", row.sender_name or "")
                    + " (QQ:"
                    + str(row.sender_id or "")
                    + ") "
                    + text,
                }
            )
            refs.append([key] if key else [])
        return contexts, refs
