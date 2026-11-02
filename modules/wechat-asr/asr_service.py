"""Private Unix socket: exact message lookup, isolated decoding, immutable ASR cache.

The privileged part only reads dedicated WeChat databases. Recognition runs as
qq-social in a bounded child and releases the model after each voice message.
"""
import hashlib
import json
import logging
import os
import re
import socketserver
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, '/opt/wechat-webox/voice-extraction')
from extract_voice import extract, silk_packets

ROOT = Path('/opt/astrbot-mouth/incoming-wechat-voice')
SOCKET = '/run/wechat-asr/asr.sock'
LOCK = threading.Lock()
LOG = logging.getLogger('wechat-asr')


def validate(request):
    mid, room = request.get('message_id'), request.get('conversation_id')
    if not isinstance(mid, str) or not re.fullmatch(r'[1-9][0-9]{0,19}', mid):
        raise ValueError('Invalid message ID')
    if not isinstance(room, str) or not re.fullmatch(r'[A-Za-z0-9_@.-]{1,128}', room):
        raise ValueError('Invalid conversation ID')
    return mid, room


def transcription(request):
    mid, room = validate(request)
    if not LOCK.acquire(timeout=8):
        raise TimeoutError('Voice recognition busy')
    try:
        metadata = ROOT / (mid + '.json')
        if metadata.exists():
            info = json.loads(metadata.read_text())
        else:
            info = extract(mid)
        if info['conversation_id'] != room or str(info['message_id']) != mid:
            raise ValueError('Voice conversation mismatch')
        audio = ROOT / (mid + '.silk')
        raw = audio.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != info['sha256']:
            raise ValueError('Voice integrity mismatch')
        silk_packets(raw)
        cached = ROOT / (mid + '.asr.json')
        if cached.exists():
            result = json.loads(cached.read_text())
            if result.get('sha256') == digest and result.get('conversation_id') == room:
                return result | {'cached': True}
        process = subprocess.run(
            ['/opt/wechat-asr/venv/bin/python', '/opt/wechat-asr/recognize.py', str(audio)],
            capture_output=True, timeout=40, text=True, user=990, group=990,
            extra_groups=[], env={'PATH': '/usr/bin:/bin', 'OMP_NUM_THREADS': '2',
                                  'OPENBLAS_NUM_THREADS': '1', 'PYTHONUTF8': '1'})
        if process.returncode:
            # Do not log private audio, transcripts, database paths or key material.
            raise RuntimeError('Recognition process failed: exit ' + str(process.returncode))
        result = json.loads(process.stdout)
        if result.get('status') not in ('ok', 'no_speech') or not isinstance(result.get('text'), str):
            raise ValueError('Invalid recognition output')
        result.update(message_id=mid, conversation_id=room, sha256=digest,
                      audio_path=str(audio), cached=False)
        part = cached.with_suffix('.tmp')
        part.write_text(json.dumps(result, ensure_ascii=False), encoding='utf8')
        part.chmod(0o600)
        os.chown(part, 990, 990)
        part.replace(cached)
        LOG.info('completed message_id=%s status=%s duration=%s elapsed=%s',
                 mid, result['status'], result.get('duration_seconds'), result.get('elapsed_seconds'))
        return result
    finally:
        LOCK.release()


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(55)
        try:
            data = self.rfile.readline(4097)
            if len(data) > 4096 or not data.endswith(b'\n'):
                raise ValueError('Invalid request size')
            request = json.loads(data)
            if not isinstance(request, dict):
                raise ValueError('Invalid request')
            result = {'ok': True, **transcription(request)}
        except Exception as error:
            # Specific safe codes prevent "not yet downloaded" being mistaken for silence.
            code = ('not_found' if isinstance(error, LookupError) else
                    'timeout' if isinstance(error, (TimeoutError, subprocess.TimeoutExpired)) else
                    'unavailable')
            LOG.warning('request failed type=%s code=%s', type(error).__name__, code)
            result = {'ok': False, 'error': code}
        try:
            self.wfile.write(json.dumps(result, ensure_ascii=False).encode() + b'\n')
        except (BrokenPipeError, ConnectionResetError):
            LOG.info('caller disconnected; completed operation retained')


if __name__ == '__main__':
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    Path(SOCKET).unlink(missing_ok=True)
    with socketserver.ThreadingUnixStreamServer(SOCKET, Handler) as server:
        os.chown(SOCKET, 0, 990)
        os.chmod(SOCKET, 0o660)
        server.serve_forever()
