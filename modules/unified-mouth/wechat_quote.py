"""Locate the newest message bubble in the open WeChat chat.

The sender program cannot guess a bubble position (bubbles move with content and
scroll), so this module screenshots the conversation area, asks the project OCR
for text boxes, and returns the centre of the lowest line - preferring the left
side, which is where incoming messages sit.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

import wechat_title

ROOT = Path('/opt/astrbot-mouth')
OCR_PYTHON = '/opt/astrbot-ocr-venv/bin/python'
BOXES = Path('/opt/astrbot-mouth/wechat_ocr_boxes.py')


def _clean(main_geometry,deadline=None):
    """True when only the conversation window is on screen.

    A modal (logout confirmation, tip, built-in browser) overlaps the chat area
    and would both pollute the OCR text and eat the right-click, so in that case
    we report "no point" and the caller falls back to plain text.
    """
    out = subprocess.run(
        ['docker', 'exec', wechat_title.CONTAINER, 'bash', '-lc',
         'export DISPLAY=:1; for id in $(xdotool search --onlyvisible --class wechat 2>/dev/null); do '
         'printf "%s\\n" "$(xdotool getwindowgeometry --shell "$id" | sed -n \'s/^WIDTH=//p\')"; done'],
        capture_output=True, text=True, timeout=wechat_title.remaining(deadline,5))
    widths = [line.strip() for line in out.stdout.splitlines() if line.strip().isdigit()]
    return widths == [str(main_geometry[2])]


def point(root=ROOT, expected=None, side=None, deadline=None):
    """Return {'x','y','side','text'} in main-window coordinates, or None."""
    if not expected or side not in ('left','right'):return None
    x, y, width, height = wechat_title._geometry(deadline)
    if not _clean((x, y, width, height),deadline):
        return None
    image = wechat_title._frame(timeout=wechat_title.remaining(deadline,12))
    left = x + 300
    top = y + 96
    right = x + width - 8
    bottom = y + height - 150
    if right - left < 50 or bottom - top < 50:
        return None
    crop = image.crop((left, top, right, bottom))
    folder = Path(root) / 'incoming-images'
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary=tempfile.TemporaryDirectory(prefix='wechat-quote-',dir=folder)
    target = Path(temporary.name) / 'quote.png'
    crop.save(target)
    try:
        result = subprocess.run([OCR_PYTHON, str(BOXES), str(target), '0.35'],
                                capture_output=True, text=True, timeout=wechat_title.remaining(deadline,15))
        if result.returncode != 0:
            return None
        lines = (json.loads(result.stdout) or {}).get('lines') or []
    except Exception:
        return None
    finally:
        temporary.cleanup()
    if not lines:
        return None
    middle = crop.width // 2
    selected=[line for line in lines if ('left' if (line['x1']+line['x2'])//2<middle else 'right')==side]
    selected.sort(key=lambda line:(line['y1'],line['x1']))
    want=wechat_title.normal(expected);candidates={}
    for i in range(len(selected)):
        text='';group=[]
        for line in selected[i:i+8]:
            if group and line['y1']-group[-1]['y2']>25:break
            text+=wechat_title.normal(line['text']);group.append(line)
            if text==want:
                box={'x1':min(q['x1'] for q in group),'y1':min(q['y1'] for q in group),
                     'x2':max(q['x2'] for q in group),'y2':max(q['y2'] for q in group),'text':expected}
                candidates[tuple(box[k] for k in ('x1','y1','x2','y2'))]=box
            if len(text)>=len(want):break
    if len(candidates)!=1:return None
    chosen=next(iter(candidates.values()))
    # Keep the click inside the bubble, not on its edge.
    centre_x = min(max((chosen['x1'] + chosen['x2']) // 2, 20), crop.width - 20)
    centre_y = min(max((chosen['y1'] + chosen['y2']) // 2, 20), crop.height - 20)
    return {'x': int(300 + centre_x), 'y': int(96 + centre_y), 'side': side,
            'text': chosen['text'][:60], 'width': width, 'height': height}


if __name__ == '__main__':
    print(json.dumps(point(), ensure_ascii=False))
