"""Isolated CPU ASR worker; accepts an already exported Tencent SILK file."""
import io
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault('OMP_NUM_THREADS', '2')


def recognize(path):
    import numpy as np
    import pysilk
    import sherpa_onnx
    started = time.monotonic()
    raw = Path(path).read_bytes()
    if not raw.startswith(b'\x02#!SILK_V3') or len(raw) > 4 * 1024 * 1024:
        raise ValueError('Invalid Tencent SILK file')
    pcm = io.BytesIO()
    pysilk.decode(io.BytesIO(raw), pcm, 16000)
    samples = np.frombuffer(pcm.getvalue(), dtype='<i2').astype(np.float32) / 32768
    duration = len(samples) / 16000
    if not 0 < duration <= 125:
        raise ValueError('Voice duration outside supported range')
    # Avoid asking ASR to invent speech from digital silence.
    if float(np.max(np.abs(samples))) < 0.0001:
        return {'text': '', 'duration_seconds': duration, 'status': 'no_speech'}
    recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model='/opt/wechat-asr/models/model.int8.onnx',
        tokens='/opt/wechat-asr/models/tokens.txt',
        num_threads=2, language='auto', use_itn=True, provider='cpu')
    texts = []
    # Bounded segments prevent long voice messages from producing huge attention matrices.
    for offset in range(0, len(samples), 25 * 16000):
        stream = recognizer.create_stream()
        stream.accept_waveform(16000, samples[offset:offset + 25 * 16000])
        recognizer.decode_stream(stream)
        texts.append(stream.result.text.strip())
    text = ''.join(texts).strip()
    return {'text': text, 'duration_seconds': round(duration, 3),
            'elapsed_seconds': round(time.monotonic() - started, 3),
            'status': 'ok' if text else 'no_speech',
            'engine': 'sherpa-onnx/SenseVoiceSmall-int8'}


if __name__ == '__main__':
    print(json.dumps(recognize(sys.argv[1]), ensure_ascii=False))
