"""Proactive Chat SchedulerService excerpt, AGPL-3.0.
Source: Pancakes-Labs/astrbot_plugin_proactive_chat
Commit: 77a1d408ccd003a9d34ad4f51acfb8fc17bc35f5
Only compute_next_trigger retained; behavior unchanged. See vendor/LICENSE.
"""
import random
import time
from datetime import datetime


class SchedulerService:
    def __init__(self, plugin):
        self.plugin = plugin

    def compute_next_trigger(
        self, schedule_conf: dict
    ) -> tuple[datetime, float, int, int, int, float]:
        """计算下一次主动消息的触发时间。

        由“最小/最大间隔分钟数”推导随机间隔并生成执行时间。
        私聊调度与自动触发共用本方法，确保时间语义只在一处维护。

        Returns:
            (run_date, scheduled_at, min_interval, max_interval,
             random_interval, next_trigger_time)
        """
        plugin = self.plugin
        min_interval = int(schedule_conf.get("min_interval_minutes", 30)) * 60
        max_interval = max(
            min_interval,
            int(schedule_conf.get("max_interval_minutes", 900)) * 60,
        )
        random_interval = random.randint(min_interval, max_interval)
        scheduled_at = time.time()
        next_trigger_time = scheduled_at + random_interval
        run_date = datetime.fromtimestamp(next_trigger_time, tz=plugin.timezone)
        return (
            run_date,
            scheduled_at,
            min_interval,
            max_interval,
            random_interval,
            next_trigger_time,
        )
