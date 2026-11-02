"""Bridge sourced private profiles and collective group styles into AstrBot."""

import hashlib
import private_chat_state
import importlib
import json
import sqlite3
import sys
import threading
import time
from contextlib import closing
from pathlib import Path


class Profiles:
    """Reuse the source-validated 30/300-message profile workers in a new root."""

    def __init__(self, root):
        self.root = Path(root)
        self.archive = self.root / "identity-source.sqlite3"
        self.path = self.root / "basic.sqlite3"
        support = self.root / "profile_support"
        if not support.exists():
            support = Path("/opt/astrbot-mouth/profile_support")
        self.support = support
        self.last_refresh = 0
        self.modules = None
        self.source_lock = threading.Lock()

    def load_modules(self):
        if self.modules is None:
            sys.path.insert(0, str(self.support))
            basic = importlib.import_module("basic_info")
            person = importlib.import_module("profile_worker")
            group = importlib.import_module("group_style_worker")
            identity = importlib.import_module("message_identity")
            basic.DB = self.path
            basic.ARCHIVE = self.archive
            person.ARCHIVE = group.ARCHIVE = self.archive
            person.ROOT = group.ROOT = self.root
            person.API_CONFIG = group.API_CONFIG = self.root / "api.json"
            identity.CACHE = self.root / "contacts.json"
            self.modules = basic, person, group, identity
        return self.modules

    def sync_source(self):
        with self.source_lock:
            self._sync_source()

    def _sync_source(self):
        """Append new source records after the legacy IDs, without ID collisions.

        Existing profile references and 30/300 cursors keep their original IDs;
        the new native observation IDs are tracked separately.
        """
        if not self.archive.exists():
            return
        with closing(sqlite3.connect(self.archive, timeout=20)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS native_cursor(id INTEGER PRIMARY KEY,last_id INTEGER)"
            )
            row = db.execute("SELECT last_id FROM native_cursor WHERE id=1").fetchone()
            last = row[0] if row else 0
            with closing(
                sqlite3.connect(
                    (self.root / "observations.sqlite3").resolve().as_uri()
                    + "?mode=ro",
                    uri=True,
                )
            ) as source:
                rows = source.execute(
                    "SELECT id,raw_json FROM observations WHERE id>? ORDER BY id",
                    (last,),
                ).fetchall()
            for oid, encoded in rows:
                raw = json.loads(encoded)
                if private_chat_state.private_scope(self.root,raw):
                    continue
                if raw.get('platform','qq') not in ('qq','wechat','bilibili'):
                    continue
                event = raw.get("raw_event", {})
                account, kind, conv = (
                    str(raw["account_id"]),
                    raw["chat_type"],
                    str(raw["conversation_id"]),
                )
                mid = str(raw.get("message_id") or event.get("message_id") or "")
                if not db.execute(
                    "SELECT 1 FROM observations WHERE account_id=? AND chat_type=? AND conversation_id=? AND message_id=?",
                    (account, kind, conv, mid),
                ).fetchone():
                    parts = event.get("message", [])
                    text = (
                        parts
                        if isinstance(parts, str)
                        else "".join(
                            p.get("data", {}).get("text", "")
                            for p in parts
                            if p.get("type") == "text"
                        )
                    )
                    db.execute(
                        "INSERT INTO observations(source_file,source_offset,sha256,modality,platform,account_id,chat_type,conversation_id,sender_id,received_at,event_time,message_id,text_content,raw_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            "astrbot-native",
                            oid,
                            hashlib.sha256(encoded.encode()).hexdigest(),
                            "message",
                            raw.get("platform", "qq"),
                            account,
                            kind,
                            conv,
                            str(raw["sender_id"]),
                            raw["received_at"],
                            event.get("time"),
                            mid,
                            text,
                            encoded,
                        ),
                    )
            if rows:
                db.execute(
                    "INSERT OR REPLACE INTO native_cursor VALUES(1,?)", (rows[-1][0],)
                )

    def maintain(self):
        """Update source, names and one recursive batch of each profile type."""
        if not self.archive.exists():
            return
        self.sync_source()
        basic, person, group, _ = self.load_modules()
        with closing(sqlite3.connect(self.path, timeout=20)) as db:
            person.initialize(db)
            group.initialize(db)
            basic.observe(db, "100000002", self.archive)
            from wechat_client import config as wechat_config
            cfg=wechat_config(self.root)
            if cfg['enabled'] and cfg['account_id']:
                from wechat_bridge import directory
                rows=directory(self.root)
                friends=[{'user_id':r['id'],'nickname':r['name'],'remark':r.get('remark','')} for r in rows if r['chat_type']=='private']
                groups=[{'group_id':r['id'],'group_name':r['name']} for r in rows if r['chat_type']=='group']
                basic.sync(db,cfg['account_id'],friends,groups,{'user_id':cfg['account_id'],'nickname':cfg.get('account_name') or cfg['account_id']})
                basic.observe(db,cfg['account_id'],self.archive)
            with closing(sqlite3.connect(self.archive)) as src:
                other_accounts=src.execute("SELECT DISTINCT account_id FROM observations WHERE platform='bilibili' AND chat_type='private'").fetchall()
            for (account,) in other_accounts:basic.observe(db,account,self.archive)
            person.plan(db, 30)
            group.plan(db, 300)
            person.process_one(db, target=500)
            group.process_one(db, target=500)

    def refresh(self, call):
        """Refresh names with read-only OneBot calls, retaining human overrides."""
        if not self.archive.exists() or time.time() - self.last_refresh < 30:
            return
        friends = call("get_friend_list", {})
        groups = call("get_group_list", {})
        own = call("get_login_info", {})
        if (
            not isinstance(friends, list)
            or not isinstance(groups, list)
            or str(own.get("user_id")) != "100000002"
        ):
            raise ValueError("Unexpected QQ identity response")
        basic, _, _, _ = self.load_modules()
        with closing(sqlite3.connect(self.path, timeout=20)) as db:
            basic.initialize(db)
            basic.sync(db, "100000002", friends, groups, own)
        cache = {
            "account_id": "100000002",
            "updated_at": time.time(),
            "users": {
                str(x["user_id"]): x.get("nickname", "") for x in friends + [own]
            },
            "groups": {str(x["group_id"]): x.get("group_name", "") for x in groups},
        }
        temporary = self.root / "contacts.json.tmp"
        temporary.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf8")
        temporary.replace(self.root / "contacts.json")
        self.last_refresh = time.time()

    def identity_snapshot(self, packet):
        from identity_links import snapshot
        scene=packet['scene']
        if scene['chat_type']=='private':users=[str(scene['conversation'])]
        else:users=list(dict.fromkeys(str(i['observation'].get('sender_id','')) for i in packet.get('new_messages',[]) if not i['observation'].get('internal_event')))[:12]
        return [value for user in users if user and user!=str(scene['account'])
                if (value:=snapshot(self.root,scene['platform'],scene['account'],user))]

    def snapshot(self, packet):
        """Load current source-validated profiles without another model call."""
        if packet['scene']['platform'] not in ('qq','wechat'):
            return {'平台':packet['scene']['platform'],'用户ID':str(packet['scene']['conversation'])}
        if not self.archive.exists():
            return {}
        basic, _, _, _ = self.load_modules()
        with closing(
            sqlite3.connect(self.archive.resolve().as_uri() + "?mode=ro", uri=True)
        ) as db:
            cutoff = db.execute(
                "SELECT coalesce(max(id),0) FROM observations"
            ).fetchone()[0]
        scoped = dict(packet)
        scoped["new_messages"] = [
            dict(item, id=cutoff) for item in packet["new_messages"]
        ]
        # Legacy helper uses QQ as its admission flag; all DB keys remain the
        # actual platform account and identity. Never merge names across sites.
        if packet['scene']['platform']=='wechat':scoped['scene']={**packet['scene'],'platform':'qq'}
        result = basic.load_for_packet(scoped)
        if packet['scene']['platform']=='wechat':result['平台']='wechat' 
        if "群资料" in result:
            result["群资料"].pop("reply_allowed", None)
        return result

    def display(self, observation):
        if observation.get('platform','qq') != 'qq':
            from .platform_bridge import display
            return display(observation)
        if self.support.exists():
            return self.load_modules()[3].identity(observation)
        raw = observation.get("raw_event", {})
        sender = raw.get("sender", {})
        name = (
            sender.get("card")
            or sender.get("nickname")
            or observation.get("sender_id", "")
        )
        kind = observation.get("chat_type", "private")
        label = (
            "私聊"
            if kind == "private"
            else "群 " + str(observation.get("conversation_id", ""))
        )
        return {"nickname": name, "source_label": f"[{label}]{name}："}
