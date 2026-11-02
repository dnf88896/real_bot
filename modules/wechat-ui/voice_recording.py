"""Native recording inside an already-owned UI transaction."""
import json,sys,time,uuid
sys.path.insert(0,'/opt/wechat-webox/virtual-mic')
import virtual_mic as mic
from voice_sender import recording,click

def send(ui,nav,name,kind,geometry,path):
    if not mic.status()['ready']:raise RuntimeError('Virtual microphone unavailable')
    if recording(ui.frame()[0]):raise RuntimeError('Pre-existing recording preserved')
    token='preflight-'+uuid.uuid4().hex
    try:
        _,duration=mic.prepare(__import__('pathlib').Path(path),token)
        if duration>50:raise RuntimeError('Voice exceeds 50 seconds; native recording needs headroom')
    finally:
        for suffix in ('.input','.wav'):(mic.HOST_DATA/(token+suffix)).unlink(missing_ok=True)
    attempted=False
    try:
        click(ui,geometry,781,609);time.sleep(0.35)
        if not recording(ui.frame()[0]):raise RuntimeError('Voice recording UI unavailable')
        sources=json.loads(mic.pulse(['-f','json','list','sources']).stdout)
        source=next(s['index'] for s in sources if s['name']==mic.SOURCE)
        streams=json.loads(mic.pulse(['-f','json','list','source-outputs']).stdout)
        if not any(s['source']==source for s in streams):raise RuntimeError('Wrong recording source')
        result=mic.play(path)
        if result['state']!='completed':raise RuntimeError('Audio injection did not complete')
        time.sleep(0.25)
        if not nav.verify(name,kind) or not recording(ui.frame()[0]):raise RuntimeError('Recipient or recorder changed')
        attempted=True;click(ui,geometry,835,605)
    except Exception:
        if not attempted:
            try:
                image,g=ui.frame()
                if recording(image):click(ui,g,633,605)
            except Exception:pass
        raise
