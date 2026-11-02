"""Administrative per-conversation privacy state; never a model/QQ command.

Intervals classify newly ingested records by receipt time. Memory persists the
classification on each source identity so closing a mode never releases old rows.
"""
import argparse
import fcntl
import json
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path('/opt/astrbot-mouth')
_cache = {}


def scope(scene):
    kind = scene.get('kind', scene.get('chat_type'))
    if kind != 'private':
        return ''
    platform = scene.get('platform', 'qq')
    account = scene.get('account', scene.get('account_id'))
    conversation = scene.get('conversation', scene.get('conversation_id'))
    if not all(isinstance(v, (str, int)) and str(v) for v in (platform,account,conversation)):
        return ''
    return json.dumps([str(platform), str(account), 'private', str(conversation)], separators=(',', ':'))


def load(root=ROOT):
    root = Path(root)
    path = root / 'private-chat-state.json'
    try:
        st = path.stat()
    except FileNotFoundError:
        if (root / '.private-chat-state.lock').exists():
            raise ValueError('Privacy state missing; refusing to assume public')
        return {'version':1, 'sessions':{}}
    signature = (st.st_ino, st.st_mtime_ns, st.st_size)
    cached = _cache.get(str(path))
    if cached and cached[0] == signature:
        return cached[1]
    try:
        data = json.loads(path.read_text('utf8'))
        if data.get('version') != 1 or not isinstance(data.get('sessions'),dict):
            raise ValueError()
        for key, row in data['sessions'].items():
            if scope(row) != key or type(row['enabled']) is not bool:
                raise ValueError()
            periods = row['intervals']
            if not isinstance(periods,list):
                raise ValueError()
            end = -float('inf')
            for start, stop in periods:
                if not isinstance(start,(int,float)) or start < end or (stop is not None and stop < start):
                    raise ValueError()
                end = float('inf') if stop is None else stop
            if bool(periods and periods[-1][1] is None) != row['enabled']:
                raise ValueError()
    except (OSError,ValueError,KeyError,TypeError):
        raise ValueError('Invalid privacy state; refusing to assume public') from None
    _cache[str(path)] = (signature,data)
    return data


def private_scope(root, record):
    key = scope(record)
    if not key:
        return ''
    row = load(root)['sessions'].get(key)
    if row is None:
        return ''
    stamp = record.get('time', record.get('received_at', record.get('sent_at')))
    try:
        at = float(stamp) if isinstance(stamp,(int,float)) else datetime.fromisoformat(stamp.replace('Z','+00:00')).timestamp()
    except (TypeError,ValueError,AttributeError):
        # Unknown source time must not be treated as permission to share.
        return key if row['intervals'] else ''
    return key if any(start <= at and (end is None or at < end) for start,end in row['intervals']) else ''


def set_state(root, scene, enabled, *, now=None):
    root = Path(root)
    key = scope(scene)
    if not key or type(enabled) is not bool:
        raise ValueError('Privacy state requires an exact private conversation identity')
    # Read before creating the first lock file; a missing established state is an error.
    initial = load(root)
    lock = root / '.private-chat-state.lock'
    with lock.open('a') as stream:
        os.chmod(lock,0o600)
        if os.geteuid() == 0:
            owner = root.stat()
            os.chown(lock,owner.st_uid,owner.st_gid)
        fcntl.flock(stream,fcntl.LOCK_EX)
        data = load(root) if (root/'private-chat-state.json').exists() else initial
        data = json.loads(json.dumps(data))
        at = time.time() if now is None else float(now)
        row = data['sessions'].get(key, {'platform':str(scene['platform']),
              'account':str(scene.get('account',scene.get('account_id'))),
              'chat_type':'private','conversation':str(scene.get('conversation',scene.get('conversation_id'))),
              'enabled':False,'changed_at':None,'intervals':[]})
        if row['enabled'] == enabled and key in data['sessions']:
            return row
        if row['changed_at'] is not None and at < row['changed_at']:
            raise ValueError('Privacy transitions must advance in time')
        if enabled:
            row['intervals'].append([at,None])
        elif row['intervals']:
            row['intervals'][-1][1] = at
        row.update(enabled=enabled,changed_at=at)
        data['sessions'][key] = row
        path = root/'private-chat-state.json'
        fd,tmp = tempfile.mkstemp(prefix='.private-state-',dir=root)
        try:
            os.fchmod(fd,0o600)
            if os.geteuid() == 0:
                owner = path.stat() if path.exists() else root.stat()
                os.fchown(fd,owner.st_uid,owner.st_gid)
            with os.fdopen(fd,'w',encoding='utf8') as target:
                json.dump(data,target,ensure_ascii=False,indent=2)
                target.flush(); os.fsync(target.fileno())
            os.replace(tmp,path)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
        _cache.pop(str(path),None)
        return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command',choices=['enable','disable','status'])
    parser.add_argument('--platform')
    parser.add_argument('--account')
    parser.add_argument('--conversation')
    parser.add_argument('--root',type=Path,default=ROOT)
    args = parser.parse_args()
    if args.command == 'status':
        result = load(args.root)
    else:
        if not all((args.platform,args.account,args.conversation)):
            parser.error('platform/account/conversation are required')
        result = set_state(args.root,dict(platform=args.platform,account=args.account,
                           chat_type='private',conversation=args.conversation),args.command=='enable')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == '__main__':main()
