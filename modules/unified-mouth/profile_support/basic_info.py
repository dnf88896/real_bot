"""Objective QQ identities and relationships, separate from conversation memory."""

import json
import sqlite3
import time
from pathlib import Path

from message_identity import clean_name

DB = Path("/opt/astrbot-mouth/basic.sqlite3")
ARCHIVE = Path("/opt/astrbot-mouth/identity-source.sqlite3")


def initialize(db):
    db.executescript("""PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS contacts(account TEXT,user_id TEXT,nickname TEXT,remark TEXT,preferred_name TEXT,preferred_name_source TEXT,first_contact TEXT,updated REAL,source TEXT,PRIMARY KEY(account,user_id));
    CREATE TABLE IF NOT EXISTS groups(account TEXT,group_id TEXT,name TEXT,bot_identity TEXT,reply_allowed INTEGER DEFAULT 0,updated REAL,source TEXT,PRIMARY KEY(account,group_id));
    CREATE TABLE IF NOT EXISTS members(account TEXT,group_id TEXT,user_id TEXT,nickname TEXT,card TEXT,role TEXT,active INTEGER DEFAULT 1,updated REAL,source TEXT,PRIMARY KEY(account,group_id,user_id));
    CREATE TABLE IF NOT EXISTS relationships(id INTEGER PRIMARY KEY,account TEXT,subject_id TEXT,object_id TEXT,relation TEXT,status TEXT CHECK(status IN ('reported','confirmed','disputed')),source_actor TEXT,source_ref TEXT,observed_at TEXT,updated REAL);
    CREATE INDEX IF NOT EXISTS relationship_scope ON relationships(account,subject_id,object_id);
    CREATE TABLE IF NOT EXISTS ingestion(account TEXT PRIMARY KEY,last_observation INTEGER DEFAULT 0);
    """)


def observe(db, account, archive=ARCHIVE):
    if not Path(archive).exists():
        return
    last = db.execute(
        "SELECT last_observation FROM ingestion WHERE account=?", (account,)
    ).fetchone()
    src = sqlite3.connect(Path(archive).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        rows = src.execute(
            "SELECT id,sender_id,received_at,chat_type,conversation_id,raw_json FROM observations WHERE account_id=? AND id>? ORDER BY id LIMIT 1000",
            (account, last[0] if last else 0),
        ).fetchall()
    finally:
        src.close()
    with db:
        for oid, uid, stamp, kind, conv, raw in rows:
            event = json.loads(raw).get("raw_event", {})
            sender = event.get("sender", {}) or {}
            uid = str(uid)
            if kind == "group":
                db.execute(
                    "INSERT OR IGNORE INTO groups(account,group_id,name,updated,source) VALUES(?,?,?,?,?)",
                    (
                        account,
                        str(conv),
                        clean_name(event.get("group_name", "")),
                        time.time(),
                        "observation:" + str(oid),
                    ),
                )
                continue
            if kind != "private":
                continue
            db.execute(
                """INSERT INTO contacts(account,user_id,nickname,first_contact,updated,source) VALUES(?,?,?,?,?,?)
             ON CONFLICT(account,user_id) DO UPDATE SET nickname=CASE WHEN excluded.nickname!='' THEN excluded.nickname ELSE nickname END,first_contact=CASE WHEN first_contact IS NULL OR excluded.first_contact<first_contact THEN excluded.first_contact ELSE first_contact END""",
                (
                    account,
                    uid,
                    clean_name(sender.get("nickname", "")),
                    stamp,
                    time.time(),
                    "observation:" + str(oid),
                ),
            )
        if rows:
            db.execute(
                "INSERT INTO ingestion VALUES(?,?) ON CONFLICT(account) DO UPDATE SET last_observation=excluded.last_observation",
                (account, rows[-1][0]),
            )


def sync(db, account, friends, groups, own, allowed_groups=()):
    now = time.time()
    with db:
        for x in friends + [own]:
            uid = str(x["user_id"])
            db.execute(
                """INSERT INTO contacts(account,user_id,nickname,remark,updated,source) VALUES(?,?,?,?,?,?) ON CONFLICT(account,user_id) DO UPDATE SET nickname=excluded.nickname,remark=excluded.remark,updated=excluded.updated,source=excluded.source""",
                (
                    account,
                    uid,
                    clean_name(x.get("nickname", "")),
                    clean_name(x.get("remark", "")),
                    now,
                    "qq_contact_api",
                ),
            )
        for x in groups:
            db.execute(
                """INSERT INTO groups(account,group_id,name,reply_allowed,updated,source) VALUES(?,?,?,?,?,?) ON CONFLICT(account,group_id) DO UPDATE SET name=excluded.name,reply_allowed=excluded.reply_allowed,updated=excluded.updated,source=excluded.source""",
                (
                    account,
                    str(x["group_id"]),
                    clean_name(x.get("group_name", "")),
                    int(str(x["group_id"]) in {str(g) for g in allowed_groups}),
                    now,
                    "qq_group_api;delivery_config",
                ),
            )


def record_relationship(
    db,
    *,
    account,
    subject,
    object_id,
    relation,
    status,
    source_actor,
    source_ref,
    observed_at,
):
    if not all(
        [account, subject, object_id, relation, source_actor, source_ref, observed_at]
    ):
        raise ValueError("Relationship requires identities and attribution")
    if status not in ("reported", "confirmed", "disputed"):
        raise ValueError("Invalid confirmation status")
    db.execute(
        "INSERT INTO relationships(account,subject_id,object_id,relation,status,source_actor,source_ref,observed_at,updated) VALUES(?,?,?,?,?,?,?,?,?)",
        (
            account,
            subject,
            object_id,
            relation,
            status,
            source_actor,
            source_ref,
            observed_at,
            time.time(),
        ),
    )


def load_for_packet(packet):
    if not DB.exists():
        return {}
    scene = packet["scene"]
    if scene.get("platform") != "qq":
        return {}
    account = str(scene["account"])
    kind = scene["chat_type"]
    conv = str(scene["conversation"])
    people = {
        str(x["observation"].get("sender_id", ""))
        for x in packet.get("new_messages", [])
    }
    current = {
        str(x["observation"].get("sender_id", "")): x["observation"]
        for x in packet.get("new_messages", [])
    }
    people.discard("")
    if kind == "private":
        people.add(conv)
    db = sqlite3.connect(DB.resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row

    def contact(uid):
        row = db.execute(
            "SELECT user_id,nickname,remark,preferred_name,preferred_name_source,first_contact,source FROM contacts WHERE account=? AND user_id=?",
            (account, uid),
        ).fetchone()
        result = dict(row) if row else {"user_id": uid, "nickname": None}
        supplied = current.get(uid, {}).get("raw_event", {}).get("sender", {}) or {}
        if not result.get("nickname") and supplied.get("nickname"):
            result["nickname"] = clean_name(supplied["nickname"])
            result["source"] = "current_message"
        if db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='profile_heads'"
        ).fetchone():
            cutoff = max(
                (x.get("id", 0) for x in packet.get("new_messages", [])), default=0
            )
            profile = db.execute(
                "SELECT v.content,j.end_id FROM profile_versions v JOIN profile_jobs j ON j.id=v.job_id WHERE v.account=? AND v.user_id=? AND j.end_id<=? ORDER BY v.id DESC LIMIT 1",
                (account, uid, cutoff),
            ).fetchone()
            if profile:
                result["交流资料"] = {
                    **json.loads(profile[0]),
                    "processed_through_observation": profile[1],
                }
        return result

    try:
        db.execute("BEGIN")
        if kind == "group":
            row = db.execute(
                "SELECT group_id,name,bot_identity,reply_allowed,source FROM groups WHERE account=? AND group_id=?",
                (account, conv),
            ).fetchone()
            result = {
                "聊天类型": kind,
                "群资料": dict(row) if row else {"group_id": conv, "name": None},
            }
            if db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='group_style_heads'"
            ).fetchone():
                cutoff = max(
                    (x.get("id", 0) for x in packet.get("new_messages", [])), default=0
                )
                style = db.execute(
                    "SELECT v.content,j.end_id FROM group_style_versions v JOIN group_style_jobs j ON j.id=v.job_id WHERE v.account=? AND v.group_id=? AND j.end_id<=? ORDER BY v.id DESC LIMIT 1",
                    (account, conv, cutoff),
                ).fetchone()
                if style:
                    result["群资料"]["交流资料"] = {
                        **json.loads(style[0]),
                        "processed_through_observation": style[1],
                    }
            return result
        result = {
            "聊天类型": kind,
            "人物资料": [contact(uid) for uid in sorted(people)],
        }
        relations = []
        for uid in sorted(people):
            for row in db.execute(
                "SELECT * FROM relationships WHERE account=? AND (subject_id=? OR object_id=?) ORDER BY id DESC LIMIT 20",
                (account, uid, uid),
            ):
                if not any(x["id"] == row["id"] for x in relations):
                    relations.append(dict(row))
        result["人物关系"] = relations
        # Related people are included only when a stored relationship explains the link.
        linked = {x[k] for x in relations for k in ("subject_id", "object_id")} - people
        if linked:
            result["关系涉及人物"] = [contact(uid) for uid in sorted(linked)]
        return result
    finally:
        db.close()
