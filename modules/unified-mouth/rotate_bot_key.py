"""Authenticated key rotation with service-state preservation and rollback."""
import argparse
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from bot_credentials import ROOT, CONFIG, synchronize, validate_key

SERVICES = ('astrbot-unified.service', 'astrbot-relay.service')
BACKUPS = Path('/var/backups/robot-api-key')


def systemctl(*args, check=True):
    return subprocess.run(['systemctl', *args], check=check, capture_output=True)


def authenticate(key):
    req = urllib.request.Request('https://api.deepseek.com/user/balance',
                                 headers={'Authorization': 'Bearer ' + key})
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            data = json.load(response)
        if not isinstance(data, dict) or 'is_available' not in data:
            raise ValueError('Unexpected authentication response')
        if not data['is_available']:
            raise ValueError('New key account has no available balance')
    except urllib.error.HTTPError as exc:
        raise ValueError('New key authentication failed, HTTP ' + str(exc.code)) from None
    except urllib.error.URLError:
        raise ValueError('Cannot reach DeepSeek; configuration unchanged') from None


def rotate(key):
    if os.geteuid() != 0:
        raise ValueError('Run rotation as the server administrator')
    key = validate_key(key)
    authenticate(key)  # Before stopping any service or changing any credential.
    paths = (ROOT / 'api.json', CONFIG, ROOT / 'image-vision-config.json')
    backup = BACKUPS / (time.strftime('%Y%m%d-%H%M%S') + '-' + str(time.time_ns()))
    backup.mkdir(parents=True, mode=0o700, exist_ok=False)
    os.chmod(backup.parent, 0o700)
    for index, path in enumerate(paths):
        if path.exists():
            shutil.copy2(path, backup / str(index))
            os.chmod(backup / str(index), 0o600)
    active = [s for s in SERVICES if systemctl('is-active', '--quiet', s, check=False).returncode == 0]
    try:
        # Only the unified process caches credentials. Stop relay too, to avoid
        # requests straddling rotation; UI sending/receiving stay independent.
        for service in reversed(active):
            systemctl('stop', service)
        synchronize(ROOT, CONFIG, key=key)
        for service in active:
            systemctl('start', service)
        time.sleep(2)
        for service in active:
            systemctl('is-active', '--quiet', service)
        synchronize(ROOT, CONFIG, check=True)
    except BaseException:
        from bot_credentials import atomic_write
        for service in reversed(active):
            systemctl('stop', service, check=False)
        for index, path in enumerate(paths):
            saved = backup / str(index)
            if saved.exists():
                atomic_write(path, saved.read_bytes())
            else:
                path.unlink(missing_ok=True)
        for service in active:
            systemctl('start', service, check=False)
        raise
    print('Robot key rotated and verified. Previously stopped services remain stopped.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--key-file', required=True, type=Path)
    args = parser.parse_args()
    try:
        rotate(args.key_file.read_text('utf-8-sig'))
    except Exception as exc:
        # Arbitrary libraries can include credentials in exception messages.
        print('Rotation failed (' + type(exc).__name__ + '); inspect configuration/service state without exposing the key.')
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
