"""Shared clock-based wakefulness and a logical-destination sleep gate."""
import json
import math
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT = {
    "enabled": False,
    "timezone": "Asia/Shanghai",
    "owner": "100000001",
    "periods": [
        [0, 480, "睡眠中"], [480, 540, "迷糊"],
        [540, 720, "清醒"], [720, 780, "迷糊"],
        [780, 1320, "清醒"], [1320, 1440, "迷糊"],
    ],
}

DESCRIPTIONS = {
    '清醒':'精神充足，注意力稳定，表达自然。',
    '迷糊':'有些困倦，注意力略松散，表达可以简短，但仍能理解当前事情。',
    '睡眠中':'正在睡眠；若因现有例外被唤醒，表现为想去睡觉、仍有困意。',
}

def description(root,now=None):
    label=state(root,now)
    return label+'：'+DESCRIPTIONS[label]


def config(root):
    path = Path(root) / "wakefulness-config.json"
    cfg = DEFAULT | (json.loads(path.read_text("utf8")) if path.exists() else {})
    end = 0
    for start, stop, label in cfg["periods"]:
        if start != end or not start < stop <= 1440 or label not in ("睡眠中", "迷糊", "清醒"):
            raise ValueError("Invalid wakefulness schedule")
        end = stop
    if end != 1440:
        raise ValueError("Incomplete wakefulness schedule")
    return cfg


def state(root, now=None):
    cfg = config(root)
    if not cfg["enabled"]:
        return "清醒"
    at = datetime.fromtimestamp(time.time() if now is None else now, ZoneInfo(cfg["timezone"]))
    minute = at.hour * 60 + at.minute
    return next(label for start, stop, label in cfg["periods"] if start <= minute < stop)


def allowed(root, scene, now=None):
    if state(root, now) != "睡眠中":
        return True
    if scene.get('platform')=='wechat' and scene.get('chat_type')=='private':
        from wechat_client import config as wechat_config
        owner=wechat_config(root).get('owner_id','')
        return bool(owner and str(scene.get('conversation',scene.get('id','')))==owner)
    # Preview routing must not turn a logical group reply into a sleep exception.
    return (scene.get("platform") == "qq" and scene.get("chat_type") == "private"
            and str(scene.get("conversation", scene.get("id", ""))) == str(config(root)["owner"]))


def sleep_started(root,now):
    cfg=config(root)
    if not cfg['enabled']:return None
    at=datetime.fromtimestamp(now,ZoneInfo(cfg['timezone']))
    minute=at.hour*60+at.minute
    for start,stop,label in cfg['periods']:
        if start<=minute<stop:
            return (at.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(minutes=start)).timestamp() if label=='睡眠中' else None


def after_awake(root,start,seconds):
    """Absolute deadline after a budget of non-sleep seconds; stable across restarts."""
    if not math.isfinite(start) or not math.isfinite(seconds) or seconds<0:raise ValueError('Invalid awake-time budget')
    if seconds==0:return start
    cfg=config(root)
    if not cfg['enabled']:return start+seconds
    periods=[(a,b) for a,b,label in cfg['periods'] if label!='睡眠中']
    if not periods:raise ValueError('Wakefulness schedule has no awake periods')
    day=datetime.fromtimestamp(start,ZoneInfo(cfg['timezone'])).replace(hour=0,minute=0,second=0,microsecond=0)
    remaining=seconds
    while True:
        for a,b in periods:
            left=max(start,(day+timedelta(minutes=a)).timestamp())
            right=(day+timedelta(minutes=b)).timestamp()
            if right<=left:continue
            if remaining<=right-left:return left+remaining
            remaining-=right-left
        day+=timedelta(days=1)
