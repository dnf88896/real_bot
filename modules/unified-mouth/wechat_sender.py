"""Immutable WeChat component delivery with per-operation checkpoints."""
import base64
import hashlib
import json
import sqlite3
import subprocess
import time
from contextlib import closing
from datetime import datetime, UTC
from pathlib import Path
from wechat_client import Client, Rejected, Uncertain, config, ROOT
from wechat_bridge import db as directory_db


def allowed(destination, root=ROOT):
    cfg = config(root)
    if not cfg['enabled'] or not cfg['send_enabled']:
        raise Rejected('WeChat sending disabled')
    if destination.get('platform') != 'wechat' or destination.get('account') != cfg['account_id']:
        raise Rejected('WeChat account mismatch')
    kind = destination.get('chat_type'); uid = destination.get('id')
    if kind not in ('private', 'group') or not isinstance(uid, str) or not uid or uid == cfg['account_id']:
        raise Rejected('Invalid WeChat destination')
    if not cfg.get('allow_all_' + kind, False) and uid not in cfg['allow_private' if kind == 'private' else 'allow_groups']:
        raise Rejected('WeChat recipient not enabled')
    with closing(directory_db(root)) as c:
        if not c.execute('SELECT 1 FROM directory WHERE account=? AND kind=? AND id=?', (destination['account'], kind, uid)).fetchone():
            raise Rejected('Unknown WeChat recipient')


def operations(destination, message, root=ROOT):
    """No routing API here: exact text/media already came from the relay.

    The mouth protocol is unchanged. Native mentions use private transport
    markers produced from existing components. Native quoting was deferred by
    the user; reply components are ignored so ordinary text still sends.
    """
    uid = destination['id']; operations = []; text = ''; shown = ''; reply = False
    def flush():
        nonlocal text, shown, reply
        if not text:
            return
        operations.append(('/Msg/SendTxt', {'ToWxid': uid, 'Content': text, 'Type': 1, **({'ReplyTo': reply} if reply else {})}, shown))
        text = ''; shown = ''; reply = False
    for part in message:
        kind = part.get('type'); data = part.get('data', {})
        if kind == 'reply':
            # User deferred native WeChat quoting; ordinary text still sends.
            continue
        elif kind == 'text':
            if any(c in data['text'] for c in ('\x01','\x02','\x03')):
                raise Rejected('Reserved transport characters in message body')
            text += data['text']; shown += data['text']
        elif kind == 'at':
            if destination['chat_type'] != 'group' or data.get('platform') != 'wechat':
                raise Rejected('Invalid WeChat mention')
            target = str(data['qq'])
            with closing(directory_db(root)) as c:
                row = c.execute('SELECT name FROM members WHERE account=? AND conversation=? AND id=?', (destination['account'], uid, target)).fetchone()
            if not row:
                raise Rejected('Unknown WeChat group member')
            name = (row[0] or target).strip()
            if not name or '\x01' in name or '\x02' in name:
                raise Rejected('Invalid WeChat mention name')
            with closing(directory_db(root)) as c:
                if c.execute('SELECT count(*) FROM members WHERE account=? AND conversation=? AND name=?',(destination['account'],uid,name)).fetchone()[0]!=1:
                    raise Rejected('WeChat mention name is ambiguous')
            text += '\x01' + name + '\x01'; shown += data.get('text') or '@' + name
        elif kind == 'image':
            flush(); value = data.get('file', '')
            if not value.startswith('base64://'):
                raise Rejected('WeChat image requires relay-validated bytes')
            blob = base64.b64decode(value[9:], validate=True)
            if not 0 < len(blob) <= 4 * 1024 * 1024:
                raise Rejected('Invalid WeChat image size')
            operations.append(('/Msg/UploadImg', {'ToWxid': uid, 'Base64': value[9:]}, '[图片]'))
        elif kind == 'voice':
            flush(); path = Path(data['file']).resolve()
            if path.parent != (Path(root) / 'outgoing-voice').resolve() or path.suffix != '.mp3' or not path.is_file():
                raise Rejected('Invalid WeChat voice path')
            if not 100 <= path.stat().st_size <= 8 * 1024 * 1024:
                raise Rejected('Invalid WeChat voice size')
            try:
                result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', str(path)],
                    capture_output=True, text=True, check=True, timeout=10)
                duration = int(float(result.stdout.strip()) * 1000)
            except Exception:
                raise Rejected('Unable to determine voice duration') from None
            if not 100 <= duration <= 60000:
                raise Rejected('WeChat voice must be at most 60 seconds')
            operations.append(('/Msg/SendVoice', {'ToWxid': uid, 'Base64': base64.b64encode(path.read_bytes()).decode(), 'Type': 2, 'VoiceTime': duration}, '【语音】' + data['text']))
        else:
            raise Rejected('WeChat component not implemented')
    flush()
    if not operations:
        raise Rejected('Empty WeChat message')
    return operations


def send(destination, message, root=ROOT, delivery_id=None, client=None):
    allowed(destination, root)
    if config(root).get('backend')=='webox' and client is None:
        from wechat_ui_transport import send as ui_send
        return ui_send(destination,message,root,delivery_id)
    plan = operations(destination, message, root)
    if not delivery_id:
        raise Rejected('WeChat delivery requires a stable delivery ID')
    client = client or Client(root)
    if not client.online():
        raise Rejected('WeChat account is not online')
    journal = Path(root) / 'wechat-outbox.sqlite3'
    confirmed = []
    with closing(sqlite3.connect(journal, timeout=20)) as c:
        c.execute('CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY,hash TEXT,status TEXT,receipt TEXT)')
        for index, (endpoint, body, logical) in enumerate(plan):
            key = str(delivery_id) + ':' + str(index)
            sha = hashlib.sha256(json.dumps([destination, endpoint, body], sort_keys=True).encode()).hexdigest()
            row = c.execute('SELECT hash,status,receipt FROM operations WHERE id=?', (key,)).fetchone()
            if row:
                if row[0] != sha:
                    raise Rejected('WeChat delivery changed after checkpoint')
                if row[1] == 'sent':
                    confirmed.append(json.loads(row[2])); continue
                raise Uncertain('WeChat delivery needs manual reconciliation')
            with c:
                c.execute("INSERT INTO operations VALUES(?,?,'sending',NULL)", (key, sha))
            try:
                receipt = client.sent(endpoint, body)
            except Exception as exc:
                with c:
                    c.execute('UPDATE operations SET status=? WHERE id=?', ('failed' if getattr(exc, 'definite_failure', False) else 'uncertain', key))
                # Preserve confirmed partial text in objective memory; do not claim unsent parts.
                if confirmed:
                    from output_memory import record_output, initialize
                    folder = Path(root) / 'output-memory'; folder.mkdir(mode=0o700, exist_ok=True)
                    with closing(sqlite3.connect(folder / 'output.sqlite3')) as audit, audit:
                        initialize(audit)
                        for n, previous in enumerate(confirmed):
                            record_output(audit, event_key='wechat-partial:' + str(delivery_id) + ':' + str(n),
                                timestamp=time.time(), platform='wechat', account=destination['account'],
                                chat_type=destination['chat_type'], conversation=destination['id'], phase='sent',
                                text=plan[n][2], receipt=previous)
                    raise Uncertain('WeChat bubble partially delivered; confirmed parts retained') from None
                raise
            with c:
                c.execute("UPDATE operations SET status='sent',receipt=? WHERE id=?", (json.dumps(receipt), key))
            confirmed.append(receipt)
    return {'message_id': confirmed[-1]['message_id'], 'platform_receipt': {'parts': confirmed}, 'reply_to':confirmed[0].get('reply_to')}
