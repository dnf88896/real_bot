"""Install the memory configuration and import legacy data by read-only backup."""

import hashlib
import json
import shutil
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from memory_engine import DEFAULTS, Memory

ROOT = Path("/opt/astrbot-mouth")
PLUGIN = Path("/opt/astrbot-runtime/data/plugins/astrbot_plugin_unified_mouth")


def main():
    memory = Memory(ROOT)
    backups = memory.directory / "legacy-snapshots"
    backups.mkdir(exist_ok=True)
    fingerprints = {}
    for source, name in [
        (Path("/opt/qq-short-memory/short.sqlite3"), "short.sqlite3"),
        (Path("/opt/qq-middle-memory/middle.sqlite3"), "middle.sqlite3"),
    ]:
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        target = backups / name
        if not target.exists():
            with closing(
                sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
            ) as src:
                with closing(sqlite3.connect(target)) as dst:
                    src.backup(dst)
        after = hashlib.sha256(source.read_bytes()).hexdigest()
        if before != after:
            raise RuntimeError("Legacy database changed during read-only import")
        fingerprints[str(source)] = after
    (backups / "source-sha256.json").write_text(json.dumps(fingerprints, indent=2))
    memory.import_legacy(backups / "short.sqlite3")
    memory.sync()
    (memory.directory / "config.json").write_text(json.dumps(DEFAULTS, indent=2))

    path = Path("/opt/astrbot-runtime/data/cmd_config.json")
    shutil.copy2(
        path, memory.directory / f"astrbot-config-before-{int(time.time())}.json"
    )
    config = json.loads(path.read_text(encoding="utf-8-sig"))
    for provider in config.get("provider_sources", []) + config.get("provider", []):
        if provider.get("id") in ("unified-flash", "unified-flash_source"):
            # The model supports 1M. This is an emergency guard, not a history
            # retention policy. Native automatic LLM summaries are disabled.
            provider["max_context_tokens"] = 1000000
    config["agent_runner"]["config"]["compression"] = {
        "max_turns": -1,
        "trim_turns": 1,
        "overflow_strategy": "truncate_by_turns",
        "fallback_max_tokens": 1000000,
    }
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf8")
    for directory in (memory.directory, PLUGIN):
        for item in [directory, *directory.rglob("*")]:
            shutil.chown(item, user="qq-social", group="qq-social")
            item.chmod(0o750 if item.is_dir() else 0o600)
    shutil.chown(path, user="qq-social", group="qq-social")
    path.chmod(0o600)
    with closing(memory.connect()) as db:
        print(
            json.dumps(
                {
                    "layers": dict(
                        db.execute("SELECT layer,count(*) FROM events GROUP BY layer")
                    ),
                    "evictions": db.execute(
                        "SELECT count(*) FROM evictions"
                    ).fetchone()[0],
                    "configuration": DEFAULTS,
                    "legacy_unchanged": True,
                }
            )
        )


if __name__ == "__main__":
    main()
