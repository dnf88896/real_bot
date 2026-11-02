"""Operator-controlled private no-history experiment, disabled by default."""
import json
from pathlib import Path


def enabled(root, packet):
    path=Path(root)/'context-test.json'
    cfg=json.loads(path.read_text('utf8')) if path.exists() else {}
    scene=packet.get('scene',{})
    return bool(cfg.get('enabled') and scene.get('platform')=='qq'
                and scene.get('chat_type')=='private'
                and str(scene.get('conversation'))==str(cfg.get('owner','100000001')))


def empty_memory():
    return {'近期记忆':[], '概括记忆':'', '相关记忆':[]}
