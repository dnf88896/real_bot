"""Read the title of the chat currently open in the WeChat container.

Used by the independent sender program, never by the mouth: it captures the VNC
framebuffer, crops the conversation header and runs the project's existing
RapidOCR worker on it.
"""
import json
import io
import os
import signal
import sys
import unicodedata
import tempfile
import time
import struct
import subprocess
from pathlib import Path

from PIL import Image

ROOT = Path('/opt/astrbot-mouth')
CONTAINER = 'wechat-webox-desktop-1'
OCR_PYTHON = '/opt/astrbot-ocr-venv/bin/python'
MIN_CONFIDENCE = 0.3
LIMIT = 200

_RFB = (
    "docker exec -i " + CONTAINER + " python3 -u -c '"
    "import socket,sys,select,os; s=socket.create_connection((\"127.0.0.1\",5900));\n"
    "while True:\n"
    " r,_,_=select.select([s,sys.stdin.buffer],[],[]);\n"
    " for f in r:\n"
    "  d=os.read(f.fileno(),65536);\n"
    "  if not d:sys.exit();\n"
    "  s.sendall(d) if f is sys.stdin.buffer else (sys.stdout.buffer.write(d),sys.stdout.buffer.flush())'"
)


def remaining(deadline,cap):
    if deadline is None:return cap
    value=min(cap,deadline-time.monotonic())
    if value<=0:raise TimeoutError('WeChat verification deadline exceeded')
    return value


def _geometry(deadline=None):
    out = subprocess.run(
        ['docker', 'exec', CONTAINER, 'bash', '-lc',
         'export DISPLAY=:1; for id in $(xdotool search --onlyvisible --class wechat); do '
         'g=$(xdotool getwindowgeometry --shell "$id"); w=$(printf "%s\\n" "$g" | sed -n "s/^WIDTH=//p"); '
         'if [ "${w:-0}" -ge 800 ] && [ "${w:-0}" -le 900 ]; then printf "%s\\n" "$g"; break; fi; done'],
        capture_output=True, text=True, timeout=remaining(deadline,5))
    values = {}
    for line in out.stdout.splitlines():
        if '=' in line:
            key, _, value = line.partition('=')
            values[key.strip()] = value.strip()
    if not values.get('WIDTH'):
        raise RuntimeError('WeChat main window geometry unavailable')
    return int(values['X']), int(values['Y']), int(values['WIDTH']), int(values['HEIGHT'])


def _frame_worker():
    process = subprocess.Popen(_RFB, shell=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def read(n):
        data = b''
        while len(data) < n:
            more = process.stdout.read(n - len(data))
            if not more:
                raise RuntimeError('framebuffer closed')
            data += more
        return data

    try:
        version = read(12)
        process.stdin.write(version)
        process.stdin.flush()
        count = read(1)[0]
        if 1 not in read(count):raise RuntimeError('Unauthenticated RFB unavailable')
        process.stdin.write(b'\1'); process.stdin.flush()
        if read(4)!=b'\0'*4:raise RuntimeError('RFB handshake rejected')
        process.stdin.write(b'\1'); process.stdin.flush()
        init = read(24)
        width, height = struct.unpack('>HH', init[:4])
        if not 0<width<=4096 or not 0<height<=4096 or width*height>8000000:raise RuntimeError('Invalid framebuffer size')
        name_size=struct.unpack('>I',init[20:])[0]
        if name_size>4096:raise RuntimeError('Invalid RFB name')
        read(name_size)
        process.stdin.write(b'\0\0\0\0' + struct.pack('>BBBBHHHBBBxxx', 32, 24, 0, 1, 255, 255, 255, 16, 8, 0))
        process.stdin.write(struct.pack('>BBHi', 2, 0, 1, 0))
        process.stdin.write(struct.pack('>BBHHHH', 3, 0, 0, 0, width, height))
        process.stdin.flush()
        read(1)
        head = read(3)
        rects = struct.unpack('>H', head[1:])[0]
        image = Image.new('RGB', (width, height))
        for _ in range(rects):
            x, y, rw, rh, enc = struct.unpack('>HHHHi', read(12))
            if x+rw>width or y+rh>height or rw*rh>8000000:raise RuntimeError('Invalid RFB rectangle')
            if enc != 0:
                raise RuntimeError('unexpected framebuffer encoding')
            image.paste(Image.frombytes('RGB', (rw, rh), read(rw * rh * 4), 'raw', 'BGRX'), (x, y))
        return image
    finally:
        if process.poll() is None:process.kill()
        process.wait(timeout=3)


def _frame(timeout=12):
    process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--frame-worker'],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=(os.name!='nt'))
    try:
        data,errors=process.communicate(timeout=timeout)
        if process.returncode or not data:raise RuntimeError('WeChat screenshot worker failed')
        if len(data)>32*1024*1024:raise RuntimeError('WeChat screenshot too large')
        with Image.open(io.BytesIO(data)) as image:return image.convert('RGB')
    except subprocess.TimeoutExpired:
        raise TimeoutError('WeChat screenshot timed out') from None
    finally:
        if os.name!='nt':
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
        elif process.poll() is None:process.kill()
        process.communicate(timeout=3)


def title(root=ROOT, deadline=None):
    """Return the OCR text of the open conversation's header."""
    x, y, width, height = _geometry(deadline)
    image = _frame(timeout=remaining(deadline,12))
    left, top = max(0, x + 290), max(0, y + 34)
    right, bottom = min(image.width, x + width - 200), min(image.height, y + min(height, 78))
    crop = image.crop((left, top, right, bottom))
    folder = Path(root) / 'incoming-images'
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    handle=tempfile.NamedTemporaryFile(prefix='wechat-title-',suffix='.png',dir=folder,delete=False)
    target=Path(handle.name);handle.close()
    try:
        crop.resize((crop.width*3,crop.height*3)).save(target)
        result=subprocess.run([OCR_PYTHON,str(Path(root)/'ocr_worker.py'),str(target),
            str(MIN_CONFIDENCE),str(LIMIT)],capture_output=True,text=True,timeout=remaining(deadline,15))
        if result.returncode:raise RuntimeError('WeChat title OCR failed')
        return (json.loads(result.stdout).get('text') or '').strip()
    finally:
        target.unlink(missing_ok=True)


def normal(value):
    # OCR may insert spacing or use full-width ASCII. Never accept substrings.
    return ''.join(unicodedata.normalize('NFKC',str(value)).split()).casefold()


def matches(expected, seen):
    want=normal(expected);got=normal(seen)
    return bool(want and got and want==got)


if __name__ == '__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--frame-worker':
        _frame_worker().save(sys.stdout.buffer,'PNG')
        raise SystemExit(0)
    text = title()
    print(json.dumps({'seen': text, 'match': matches(sys.argv[1], text) if len(sys.argv) > 1 else None},
                     ensure_ascii=False))
