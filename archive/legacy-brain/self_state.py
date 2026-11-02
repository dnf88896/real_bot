"""Global, declarative self state; no dialogue-driven transitions yet."""
import json
from pathlib import Path
STATE=Path('/opt/qq-mouth/self-state.json')
DEFAULT={'emotion':{'value':'开心'},'wakefulness':{'value':'清醒'}}
def load():
    obj=json.loads(STATE.read_text('utf8')) if STATE.exists() else DEFAULT
    return {'情绪':obj['emotion']['value'],'清醒程度':obj['wakefulness']['value']}
