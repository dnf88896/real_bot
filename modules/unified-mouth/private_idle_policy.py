"""Operator policy for idle-triggered private activity, independent of identity links."""
import json
from pathlib import Path

FLAG='other_private_idle_enabled'
QQ_ACCOUNT='100000002'

def enabled(cfg,scene,root):
    if scene.get('chat_type')!='private' or cfg.get(FLAG,True):return True
    path=Path(root)/'wakefulness-config.json'
    owner=str(json.loads(path.read_text('utf8')).get('owner','100000001')) if path.exists() else '100000001'
    return (scene.get('platform')=='qq' and str(scene.get('account'))==QQ_ACCOUNT
            and str(scene.get('conversation',scene.get('id','')))==owner)
