"""One maintained robot key; AstrBot settings are generated credential copies.

Never import user DSH settings or inspect unrelated providers. No secret logging.
"""
import argparse
import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

ROOT = Path('/opt/astrbot-mouth')
CONFIG = Path('/opt/astrbot-runtime/data/cmd_config.json')


def validate_key(value):
    if not isinstance(value, str):
        raise ValueError('API key must be text')
    value = value.strip()
    if not value.startswith('sk-') or not 16 <= len(value) <= 512 or any(c.isspace() for c in value):
        raise ValueError('Invalid API key format')
    return value


def load(path):
    try:
        value = json.loads(path.read_text('utf8'))
    except (OSError, ValueError):
        raise ValueError('Cannot read credential configuration: ' + str(path)) from None
    if not isinstance(value, dict):
        raise ValueError('Expected configuration object: ' + str(path))
    return value


def planned(root, config_path, key=None):
    api_path = root / 'api.json'
    api = load(api_path)
    key = validate_key(api.get('api_key') if key is None else key)
    api['api_key'] = key
    cfg = load(config_path)
    providers = [p for p in cfg.get('provider', []) if p.get('id') == 'unified-flash']
    if len(providers) != 1:
        raise ValueError('Expected exactly one unified-flash provider')
    source_id = providers[0].get('provider_source_id')
    sources = [p for p in cfg.get('provider_sources', []) if source_id and p.get('id') == source_id]
    if len(sources) != 1:
        raise ValueError('Cannot resolve robot provider source')
    # Do not accidentally rotate a provider also used by an unrelated model.
    if any(p.get('id') != 'unified-flash' and p.get('provider_source_id') == source_id
           for p in cfg.get('provider', [])):
        raise ValueError('Robot source is shared with another provider; resolve explicitly')
    sources[0]['key'] = [key]
    sources[0].pop('api_key', None)
    providers[0].pop('key', None)
    providers[0].pop('api_key', None)
    cfg.setdefault('provider_settings', {})['websearch_deepseek_key'] = [key]
    vision_path = root / 'image-vision-config.json'
    vision = load(vision_path) if vision_path.exists() else {}
    vision.pop('api_key', None)
    vision['key_source'] = str(api_path)
    return {api_path: api, config_path: cfg, vision_path: vision}


def atomic_write(path, data):
    existing = path.stat() if path.exists() else None
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        if existing and os.geteuid() == 0:
            os.fchown(fd, existing.st_uid, existing.st_gid)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def locked(root):
    with (root / '.credential-sync.lock').open('a') as stream:
        os.chmod(stream.name, 0o600)
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def synchronize(root=ROOT, config_path=CONFIG, *, key=None, check=False):
    root, config_path = Path(root), Path(config_path)
    with locked(root):
        plan = planned(root, config_path, key)
        changed = [p for p, value in plan.items() if not p.exists() or load(p) != value]
        if check:
            if changed:
                raise ValueError('Robot credential copies are out of sync: ' + ', '.join(map(str, changed)))
            return []
        original = {p: p.read_bytes() if p.exists() else None for p in changed}
        done = []
        try:
            for path in changed:
                atomic_write(path, (json.dumps(plan[path], ensure_ascii=False, indent=2) + '\n').encode('utf8'))
                done.append(path)
        except BaseException:
            for path in reversed(done):
                if original[path] is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write(path, original[path])
            raise
        return [str(p) for p in changed]


def main():
    parser = argparse.ArgumentParser(description='Synchronize robot credentials without printing secrets')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--key-file', type=Path, help='Restricted file containing only the new key')
    args = parser.parse_args()
    if args.check and args.key_file:
        parser.error('--check and --key-file cannot be combined')
    key = validate_key(args.key_file.read_text('utf-8-sig')) if args.key_file else None
    changed = synchronize(key=key, check=args.check)
    print('Robot credentials verified' if args.check else 'Robot credential files synchronized: ' + str(len(changed)))


if __name__ == '__main__':
    main()
