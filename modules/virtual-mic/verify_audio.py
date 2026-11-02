"""Real audio loopback verification; never opens or operates WeChat UI."""
import array
import json
import math
import os
import subprocess
import time
import wave
from pathlib import Path
import virtual_mic as mic


def capture(name, seconds):
    return subprocess.Popen(mic.docker(['ffmpeg', '-nostdin', '-v', 'error', '-y',
        '-f', 'pulse', '-i', mic.SOURCE, '-t', str(seconds), '-ar', '16000', '-ac', '1',
        mic.DATA + '/' + name]), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def measure(name):
    with wave.open(str(mic.HOST_DATA / name)) as wav:
        samples = array.array('h', wav.readframes(wav.getnframes()))
        duration = len(samples) / wav.getframerate()
    rms = math.sqrt(sum(x*x for x in samples) / len(samples)) / 32768
    audible = [i for i, value in enumerate(samples) if abs(value) > 500]
    span = (audible[-1] - audible[0] + 1) / 16000 if audible else 0
    return {'rms': rms, 'duration': duration, 'audible_span_seconds': span}


def wait(process):
    _, error = process.communicate(timeout=15)
    assert process.returncode == 0, error.decode()


tone = mic.ROOT / 'test-tone.wav'
with wave.open(str(tone), 'wb') as wav:
    wav.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
    wav.writeframes(array.array('h', (int(6553 * math.sin(2*math.pi*440*i/48000))
                                     for i in range(48000*2))).tobytes())
tone.chmod(0o644)
report = {}
record = capture('verify-idle.wav', 1)
wait(record)
report['idle'] = measure('verify-idle.wav')
assert report['idle']['rms'] < 0.0001, report

record = capture('verify-injection.wav', 4)
time.sleep(0.6)
report['play'] = mic.play(tone)
wait(record)
report['injection'] = measure('verify-injection.wav')
assert report['injection']['rms'] > 0.03, report
assert 1.95 <= report['injection']['audible_span_seconds'] <= 2.05, report

record = capture('verify-isolation.wav', 4)
time.sleep(0.6)
# Reuse only the public synthesized test tone; output must not reach the virtual mic.
mic.command(['docker', 'cp', str(tone), mic.CONTAINER + ':' + mic.DATA + '/test-speaker.wav'])
mic.command(['docker', 'exec', mic.CONTAINER, 'chmod', '644', mic.DATA + '/test-speaker.wav'])
mic.command(mic.docker(['paplay', '--device=' + mic.SPEAKER, mic.DATA + '/test-speaker.wav']))
wait(record)
report['speaker_isolation'] = measure('verify-isolation.wav')
assert report['speaker_isolation']['rms'] < 0.0001, report

process = subprocess.Popen(['/opt/astrbot-src/.venv/bin/python', str(mic.ROOT / 'virtual_mic.py'),
                            'play', str(tone)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
for _ in range(100):
    current = mic.status().get('current')
    if current and current['state'] == 'playing':
        break
    time.sleep(0.05)
else:
    raise AssertionError('Player did not start')
second = mic.command(['/opt/astrbot-src/.venv/bin/python', str(mic.ROOT / 'virtual_mic.py'),
                      'play', str(tone)], check=False)
assert second.returncode != 0 and b'busy' in second.stderr, second
report['concurrent_playback'] = 'rejected'
report['stop'] = mic.stop()
output, error = process.communicate(timeout=10)
assert process.returncode == 0, error
report['stopped_player'] = json.loads(output)
assert report['stopped_player']['state'] == 'stopped', report
assert mic.status()['current'] is None
print(json.dumps(report, ensure_ascii=False, indent=2))
(mic.ROOT / 'verification.json').write_text(json.dumps(report, indent=2))
