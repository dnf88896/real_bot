"""Prepare remaining migrations in the new namespace only."""

import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

ROOT = Path("/opt/astrbot-mouth")


def main():
    basic_backup = ROOT / "basic-before-social.sqlite3"
    if not basic_backup.exists():
        with closing(sqlite3.connect((ROOT / "basic.sqlite3").resolve().as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(basic_backup)) as destination:
                source.backup(destination)
    archive = ROOT / "identity-source.sqlite3"
    if not archive.exists():
        with closing(
            sqlite3.connect(
                "file:/opt/qq-brain-memory/data/observations.sqlite3?mode=ro", uri=True
            )
        ) as src:
            with closing(sqlite3.connect(archive)) as dst:
                src.backup(dst)
    # Original observation IDs in the copied profiles refer to this archive.
    with closing(sqlite3.connect(archive)) as db, db:
        db.execute(
            "CREATE INDEX IF NOT EXISTS identity_message ON observations(account_id,chat_type,conversation_id,message_id)"
        )
    cfg = ROOT / "social-config.json"
    if not cfg.exists():
        cfg.write_text(
            json.dumps(
                {
                    "allow_all_private": True,
                    "group_preview": True,
                    "owner": "100000001",
                    "private_wait": 1.0,
                    "private_quiet": 0.7,
                    "private_cap": 3.0,
                    "group_wait": 8.0,
                    "group_quiet": 2.0,
                    "group_cap": 12.0,
                    "private_batch": 12,
                    "group_batch": 80,
                    "private_chars": 12000,
                    "group_chars": 24000,
                    "global_concurrency": 3,
                    "group_fairness_seconds": 45,
                    "private_streak": 3,
                    "wake_enabled": True,
                },
                indent=2,
            )
        )
    native_path = Path("/opt/astrbot-runtime/data/cmd_config.json")
    backup = ROOT / "native-before-social.json"
    if not backup.exists():
        shutil.copy2(native_path, backup)
    native = json.loads(native_path.read_text(encoding="utf-8-sig"))
    native["platform_settings"]["enable_id_white_list"] = False
    native["platform_settings"]["unique_session"] = False
    native["platform_settings"]["ignore_bot_self_message"] = True
    native["disable_builtin_commands"] = True
    ltm = native.setdefault("provider_ltm_settings", {})
    ltm["group_icl_enable"] = False
    ltm["group_message_history_enable"] = False
    ltm.setdefault("active_reply", {})["enable"] = False
    native_path.write_text(
        json.dumps(native, ensure_ascii=False, indent=2), encoding="utf8"
    )
    delivery_path = ROOT / "delivery.json"
    if not (ROOT / "delivery-before-social.json").exists():
        shutil.copy2(delivery_path, ROOT / "delivery-before-social.json")
    delivery = json.loads(delivery_path.read_text("utf8"))
    delivery.update(
        allow_all_private=True,
        allow_groups=[],
        group_delivery_mode="owner_preview",
        group_preview_recipient="100000001",
        preview_all_groups=True,
    )
    delivery_path.write_text(json.dumps(delivery, indent=2))
    prompt_path = ROOT / "persona.txt"
    if not (ROOT / "persona-before-social.txt").exists():
        shutil.copy2(prompt_path, ROOT / "persona-before-social.txt")
    prompt = prompt_path.read_text("utf8")
    if "唤醒意愿：" not in prompt:
        prompt += "\n你可以在回复之外单独写出“唤醒意愿：”，自然说明自己想在某个会话连续多久没有新消息后，再重新考虑什么事情；对象未另指时是当前会话。没有意愿就不写。这段是留给自己的内部意愿，不发给对方；不要声称已经成功安排，执行是否成功以实际反馈为准。内部唤醒时可以继续说话或只输出【保持沉默】，但不再安排连续自唤醒。\n"
        prompt_path.write_text(prompt, encoding="utf8")
    for item in [
        archive,
        cfg,
        ROOT / "profile_support",
        *(ROOT / "profile_support").rglob("*"),
    ]:
        shutil.chown(item, user="qq-social", group="qq-social")
        item.chmod(0o750 if item.is_dir() else 0o600)
    for item in (native_path, backup, delivery_path, prompt_path):
        shutil.chown(item, user="qq-social", group="qq-social")
        item.chmod(0o600)


if __name__ == "__main__":
    main()
