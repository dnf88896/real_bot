"""Standalone virtual microphone. No WeChat UI, messages, models or network API.

Host CLI controls a user-scoped PulseAudio server inside the existing desktop.
All subprocess arguments are arrays, and arbitrary PulseAudio commands are not exposed.
"""
import argparse
import contextlib
import fcntl
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

CONTAINER = 'wechat-webox-desktop-1'
ROOT = Path('/opt/wechat-webox/virtual-mic')
HOST_DATA = Path('/opt/wechat-webox/state/virtual-mic')
DATA = '/webox/state/virtual-mic'
RUNTIME = '/tmp/bot-virtual-mic'
SOURCE = 'bot_virtual_mic'
BUS = 'bot_voice_bus'
SPEAKER = 'bot_speaker'
CLIENT = 'BotVoiceInjector'

PULSE = '''.fail
load-module module-native-protocol-unix socket=/tmp/bot-virtual-mic/native auth-anonymous=0
load-module module-null-sink sink_name=bot_voice_bus rate=48000 channels=1 channel_map=mono sink_properties=device.description=BotVoiceInjectionBus
load-module module-remap-source master=bot_voice_bus.monitor source_name=bot_virtual_mic channels=1 channel_map=mono source_properties=device.description=BotVirtualMicrophone
load-module module-null-sink sink_name=bot_speaker rate=48000 channels=2 sink_properties=device.description=BotPlaybackIsolated
set-default-sink bot_speaker
set-default-source bot_virtual_mic
'''


def command(args, timeout=10, check=True, **kwargs):
    result = subprocess.run(args, capture_output=True, timeout=timeout, **kwargs)
    if check and result.returncode:
        raise RuntimeError('Command failed: ' + str(args[0]) + ': ' + result.stderr.decode(errors='replace')[-1000:])
    return result


def docker(args):
    return ['docker', 'exec', '-u', '1000:1000', '-e', 'HOME=/webox/state/home',
            '-e', 'XDG_RUNTIME_DIR=' + RUNTIME, '-e', 'PULSE_SERVER=unix:' + RUNTIME + '/native',
            CONTAINER, *args]


def pulse(args, **kwargs):
    return command(docker(['pactl', *args]), **kwargs)


def write_json(path, value):
    temporary = path.with_suffix('.new')
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf8')
    temporary.chmod(0o600)
    temporary.replace(path)


def status():
    result = pulse(['-f', 'json', 'info'], check=False)
    if result.returncode:
        return {'ready': False, 'error': 'audio_server_unavailable'}
    info = json.loads(result.stdout)
    sources = json.loads(pulse(['-f', 'json', 'list', 'sources']).stdout)
    sinks = json.loads(pulse(['-f', 'json', 'list', 'sinks']).stdout)
    names = {s['name'] for s in sources}
    sink_names = {s['name'] for s in sinks}
    ready = (SOURCE in names and {BUS, SPEAKER}.issubset(sink_names)
             and info['default_source_name'] == SOURCE and info['default_sink_name'] == SPEAKER)
    current = None
    try:
        current = json.loads((ROOT / 'current.json').read_text())
    except FileNotFoundError:
        pass
    return {'ready': ready, 'source': SOURCE, 'default_source': info['default_source_name'],
            'default_output': info['default_sink_name'], 'current': current}


def setup():
    ROOT.mkdir(parents=True, exist_ok=True)
    HOST_DATA.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chown(HOST_DATA, 1000, 1000)
    config = HOST_DATA / 'pulse.pa'
    config.write_text(PULSE)
    config.chmod(0o600)
    os.chown(config, 1000, 1000)
    command(['docker', 'exec', CONTAINER, 'install', '-d', '-m', '700', '-o', '1000', '-g', '1000', RUNTIME])
    if not status()['ready']:
        # This dedicated socket must be free; never kill/replace an unrelated audio server.
        if pulse(['info'], check=False).returncode == 0:
            raise RuntimeError('Existing dedicated audio server configuration differs')
        command(docker(['pulseaudio', '-n', '--file=' + DATA + '/pulse.pa', '--daemonize=yes',
                        '--exit-idle-time=-1', '--disallow-exit=yes', '--disable-shm=yes',
                        '--log-target=file:' + RUNTIME + '/pulse.log']), timeout=15)
        for _ in range(30):
            if status()['ready']:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError('Virtual microphone failed to become ready')
    # Native desktop applications discover the same server, without relaunching WeChat.
    client = Path('/opt/wechat-webox/state/home/.config/pulse/client.conf')
    client.parent.mkdir(parents=True, exist_ok=True)
    desired = 'default-server = unix:/tmp/bot-virtual-mic/native\nautospawn = no\n'
    backup = ROOT / 'client.conf.before'
    if client.exists() and client.read_text() != desired and not backup.exists():
        backup.write_bytes(client.read_bytes())
        backup.chmod(0o600)
    client.write_text(desired)
    client.chmod(0o600)
    os.chown(client, 1000, 1000)
    return status()


def validate_audio(path):
    source = Path(path).resolve(strict=True)
    if not source.is_file() or source.suffix.lower() not in ('.wav', '.mp3', '.ogg', '.flac', '.m4a', '.pcm'):
        raise ValueError('Supported input: WAV/MP3/OGG/FLAC/M4A')
    if source.stat().st_size > 64 * 1024 * 1024:
        raise ValueError('Audio exceeds 64MiB')
    if source.suffix.lower() == '.pcm':
        raise ValueError('Raw PCM requires explicit metadata; use a WAV container')
    return source


def prepare(source, target):
    # Copy first: no attacker-controlled paths are interpreted in the container.
    command(['docker', 'cp', str(source), CONTAINER + ':' + DATA + '/' + target + '.input'], timeout=30)
    command(['docker', 'exec', CONTAINER, 'chown', '1000:1000', DATA + '/' + target + '.input'])
    name = DATA + '/' + target + '.wav'
    # Audio only; reject durations over 120s, instead of quietly truncating the recording.
    duration = command(docker(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-show_entries', 'format=duration',
                              '-of', 'default=nw=1:nk=1', DATA + '/' + target + '.input']))
    seconds = float(duration.stdout)
    if not 0 < seconds <= 120:
        raise ValueError('Audio must be between 0 and 120 seconds')
    command(docker(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-protocol_whitelist', 'file,pipe', '-i', DATA + '/' + target + '.input',
                    '-vn', '-ac', '1', '-ar', '48000', '-c:a', 'pcm_s16le', name]), timeout=30)
    return name, seconds


def play(path):
    source = validate_audio(path)
    ROOT.mkdir(parents=True, exist_ok=True)
    with (ROOT / 'play.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Virtual microphone busy; concurrent playback rejected')
        if not status()['ready']:
            raise RuntimeError('Virtual microphone is not ready; run setup')
        token = 'voice-' + uuid.uuid4().hex
        state = ROOT / 'current.json'
        cancelled = ROOT / (token + '.cancel')
        write_json(state, {'id': token, 'state': 'preparing', 'started_at': time.time()})
        started = time.monotonic()
        try:
            name, duration = prepare(source, token)
            if cancelled.exists():
                return {'id': token, 'state': 'stopped'}
            write_json(state, {'id': token, 'state': 'playing', 'duration_seconds': duration,
                               'started_at': time.time()})
            # Real-time playback, not a file transfer. Source is silent before/after this stream.
            result = command(docker(['paplay', '--device=' + BUS, '--client-name=' + CLIENT,
                                      '--stream-name=' + token, '--latency-msec=60', name]),  # gitleaks:allow -- generated audio routing tag, not a credential
                             timeout=duration + 15, check=False)
            if cancelled.exists():
                return {'id': token, 'state': 'stopped'}
            if result.returncode:
                raise RuntimeError('Audio playback did not complete')
            return {'id': token, 'state': 'completed', 'duration_seconds': duration,
                    'elapsed_seconds': round(time.monotonic() - started, 3)}
        finally:
            # A timed-out/disconnected player must never keep feeding the microphone.
            stop_stream(token)
            state.unlink(missing_ok=True)
            cancelled.unlink(missing_ok=True)
            for suffix in ('.input', '.wav'):
                (HOST_DATA / (token + suffix)).unlink(missing_ok=True)


def stop_stream(token):
    result = pulse(['-f', 'json', 'list', 'sink-inputs'], check=False)
    if result.returncode:
        return
    for stream in json.loads(result.stdout):
        properties = stream.get('properties', {})
        if properties.get('application.name') == CLIENT and properties.get('media.name') == token:
            pulse(['kill-sink-input', str(stream['index'])], check=False)


def stop():
    try:
        current = json.loads((ROOT / 'current.json').read_text())
    except FileNotFoundError:
        return {'state': 'idle'}
    token = current['id']
    if not token.startswith('voice-') or len(token) != 38:
        raise RuntimeError('Invalid playback state')
    (ROOT / (token + '.cancel')).touch(mode=0o600)
    # Covers a stop that races with native stream creation after preparation.
    for _ in range(30):
        stop_stream(token)
        if not (ROOT / 'current.json').exists():
            return {'state': 'stopped', 'id': token}
        time.sleep(0.1)
    return {'state': 'stop_requested', 'id': token}


def maintain():
    while True:
        try:
            if not status()['ready']:
                setup()
        except Exception as error:
            print('audio server check failed: ' + str(error), file=sys.stderr, flush=True)
        time.sleep(5)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    for action in ('setup', 'status', 'stop', 'maintain'):
        sub.add_parser(action)
    sub.add_parser('play').add_argument('audio_file')
    args = parser.parse_args()
    try:
        result = play(args.audio_file) if args.action == 'play' else globals()[args.action]()
        print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
