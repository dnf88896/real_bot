"""Explicit native WeChat voice send using the independent virtual mic.

No passive listener or mouth integration. Entire UI transaction is serialized.
"""
import argparse
import fcntl
import json
import os
import sqlite3
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, '/opt/wechat-webox/session-selector')
sys.path.insert(0, '/opt/astrbot-mouth')
from session_selector import Desktop, Policy, OCR, Navigator, HOME, NavigationError
from text_input import Clipboard, Composer
from unified_sender import Observer
import virtual_mic as mic

ROOT = Path('/opt/wechat-webox/virtual-mic')


def recording(image):
    if image.size != (874, 640):
        return False
    # Green send arrow only appears in this location while recording.
    pixels = list(image.crop((822, 590, 850, 622)).convert('RGB').getdata())
    return sum(g > 125 and g > r+45 and g > b+15 for r, g, b in pixels) >= 150


def click(ui, geometry, x, y):
    if ui.geometry() != geometry:
        raise NavigationError('WeChat geometry changed')
    ui.run(['xdotool', 'mousemove', str(geometry[1]+x), str(geometry[2]+y), 'click', '1'])


def send(conversation, text):
    from voice_output import synthesize
    from wechat_client import config
    from output_memory import ROOT as OUTPUT, initialize, record_output
    key = 'voice-test-' + uuid.uuid4().hex
    evidence = ROOT / key
    evidence.mkdir(mode=0o700)
    audio = synthesize('/opt/astrbot-mouth', text)
    result = {'id': key, 'conversation': conversation, 'text': text, 'audio_path': audio,
              'state': 'preparing', 'created_at': time.time()}
    mic.write_json(evidence/'result.json', result)
    with (HOME/'ui.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        policy = Policy(**json.loads((HOME/'policy.json').read_text()))
        ui = Desktop(policy)
        ocr, clipboard = OCR(), Clipboard()
        nav = Navigator(ui, ocr, policy)
        observer = Observer()
        attempted = False
        try:
            observer.ready()
            target = observer.baseline(conversation)
            room = target['contact']['roomid']
            kind = 'group' if room.endswith('@chatroom') else 'private'
            # Finish navigation/return-to-top before consulting any later operation state.
            if not nav.verify(conversation, kind):
                nav.select(conversation, kind)
            composer = Composer(ui, nav, clipboard)
            image, geometry = ui.frame()
            composer.focus(geometry)
            if composer.read():
                raise NavigationError('Existing draft preserved')
            if not mic.status()['ready']:
                raise NavigationError('Virtual microphone unavailable')
            baseline = observer.baseline(conversation)['positions']
            if recording(image):
                raise NavigationError('Pre-existing recording must be handled manually')
            click(ui, geometry, 781, 609)
            time.sleep(0.35)
            image, _ = ui.frame()
            image.save(evidence/'recording.png')
            if not recording(image):
                raise NavigationError('Native voice recording UI not detected')
            sources = json.loads(mic.pulse(['-f','json','list','sources']).stdout)
            source = next(s['index'] for s in sources if s['name']==mic.SOURCE)
            streams = json.loads(mic.pulse(['-f','json','list','source-outputs']).stdout)
            if not any(s['source']==source for s in streams):
                raise NavigationError('WeChat is not recording from the virtual microphone')
            result['state']='recording'
            mic.write_json(evidence/'result.json', result)
            result['injection'] = mic.play(audio)
            if result['injection']['state'] != 'completed':
                raise NavigationError('Voice injection interrupted')
            time.sleep(0.25)
            if not nav.verify(conversation, kind) or not recording(ui.frame()[0]):
                raise NavigationError('Recipient or recording changed before send')
            result['state']='sending'
            mic.write_json(evidence/'result.json', result)
            attempted = True
            click(ui, geometry, 835, 605)
            deadline=time.monotonic()+25
            receipt=None
            while time.monotonic()<deadline:
                voices=[x for x in observer.items(room,baseline) if x.get('Kind')=='voice']
                if len(voices)==1:
                    receipt=voices[0]
                    break
                if len(voices)>1:
                    raise RuntimeError('Multiple new outgoing voices; attribution uncertain')
                time.sleep(0.75)
            if not receipt:
                raise RuntimeError('No confirmed outgoing voice receipt; do not automatically resend')
            stamp=time.time()
            result.update(state='sent', roomid=room, receipt=receipt, confirmed_at=stamp)
            mic.write_json(evidence/'result.json', result)
            ui.frame()[0].save(evidence/'sent.png')
            OUTPUT.mkdir(parents=True,exist_ok=True)
            with sqlite3.connect(OUTPUT/'output.sqlite3') as db:
                initialize(db)
                record_output(db,event_key='wechat-ui:'+key+':sent',timestamp=stamp,
                    platform='wechat', account=config()['account_id'],chat_type=kind,
                    conversation=room,phase='sent',text=text,receipt=receipt['MessageID'],
                    delivery_metadata={'voice_paths':[audio], 'voice_text':text,
                        'platform_receipt':receipt,'delivery_mode':'wechat_native_voice'})
            for file in OUTPUT.glob('output.sqlite3*'):
                os.chown(file,990,990)
            return result
        except Exception as error:
            if result['state']!='sent':
                result.update(state='uncertain' if attempted else 'failed',error=str(error))
                mic.write_json(evidence/'result.json',result)
            if not attempted and result['state']=='failed':
                try:
                    image, g=ui.frame()
                    if recording(image):
                        click(ui,g,633,605)
                except Exception:
                    pass
            raise
        finally:
            clipboard.close()
            ocr.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--conversation',required=True)
    parser.add_argument('--text',required=True)
    args=parser.parse_args()
    print(json.dumps(send(args.conversation,args.text),ensure_ascii=False))
