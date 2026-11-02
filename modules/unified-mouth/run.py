"""Start native AstrBot without exposing a new administrative dashboard."""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("ASTRBOT_ROOT", "/opt/astrbot-runtime"))

# Sync before AstrBot imports its process-cached provider and search settings.
from bot_credentials import synchronize
synchronize()

from usage_meter import install as install_usage_meter
install_usage_meter()

from astrbot.core import LogBroker, db_helper
from astrbot.core.core_lifecycle import AstrBotCoreLifecycle


async def main():
    """Initialize the shared persona, then run AstrBot's native lifecycle."""
    await db_helper.initialize()
    prompt = Path("/opt/astrbot-mouth/persona.txt").read_text("utf8")
    if await db_helper.get_persona_by_id("default"):
        await db_helper.update_persona(
            "default", system_prompt=prompt, tools=[], skills=[]
        )
    else:
        await db_helper.insert_persona("default", prompt, tools=[], skills=[])
    core = AstrBotCoreLifecycle(LogBroker(), db_helper)
    await core.initialize()
    try:
        await core.start()
    finally:
        await core.stop()


if __name__ == "__main__":
    asyncio.run(main())
