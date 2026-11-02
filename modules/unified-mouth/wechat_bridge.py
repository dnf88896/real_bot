"""Canonical WeChat ingress and platform-isolated contact/member directory."""
import hashlib
import privacy_commands
import json
import sqlite3
import time
import base64
from contextlib import closing
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path('/opt/astrbot-mouth')


def db(root=ROOT):
    connection = sqlite3.connect(Path(root) / 'wechat.sqlite3', timeout=20)
    connection.row_factory = sqlite3.Row
    connection.executescript('''PRAGMA journal_mode=WAL;
      CREATE TABLE IF NOT EXISTS directory(account TEXT,kind TEXT,id TEXT,name TEXT,remark TEXT,updated REAL,PRIMARY KEY(account,kind,id));
      CREATE TABLE IF NOT EXISTS members(account TEXT,conversation TEXT,id TEXT,name TEXT,updated REAL,PRIMARY KEY(account,conversation,id));
      CREATE TABLE IF NOT EXISTS inbox(key TEXT PRIMARY KEY,account TEXT,payload TEXT,state TEXT,error TEXT,created REAL);
      CREATE TABLE IF NOT EXISTS messages(account TEXT,conversation TEXT,id TEXT,raw TEXT,PRIMARY KEY(account,conversation,id));
      CREATE TABLE IF NOT EXISTS checkpoints(account TEXT PRIMARY KEY,cursor TEXT);
    ''')
    return connection


def remember(account, kind, uid, name='', remark='', root=ROOT):
    if kind not in ('private', 'group') or not uid or not account:
        raise ValueError('Invalid WeChat directory identity')
    with closing(db(root)) as c, c:
        c.execute('INSERT INTO directory VALUES(?,?,?,?,?,?) ON CONFLICT(account,kind,id) DO UPDATE SET name=CASE WHEN excluded.name!=\'\' THEN excluded.name ELSE directory.name END,remark=CASE WHEN excluded.remark!=\'\' THEN excluded.remark ELSE directory.remark END,updated=excluded.updated',
                  (str(account), kind, str(uid), name, remark, time.time()))


def directory(root=ROOT):
    if not (Path(root) / 'wechat.sqlite3').exists():
        return []
    with closing(db(root)) as c:
        return [dict(platform='wechat', account=r['account'], chat_type=r['kind'], id=r['id'],
                     name=r['name'] or r['id'], remark=r['remark']) for r in c.execute('SELECT * FROM directory WHERE id!=account')]


def member(value, destination, root=ROOT):
    if not value.startswith('@') or destination.get('chat_type') != 'group':
        return {'type': 'text', 'data': {'text': value}}
    target = value[1:].strip()
    with closing(db(root)) as c:
        rows = c.execute('SELECT id FROM members WHERE account=? AND conversation=? AND (id=? OR name=?)',
                         (destination['account'], destination['id'], target, target)).fetchall()
    if len(rows) != 1:
        raise ValueError('WeChat mention member missing or ambiguous')
    return {'type': 'at', 'data': {'qq': rows[0]['id'], 'platform': 'wechat', 'text': value}}


def scalar(value):
    return str(value.get('string', '')) if isinstance(value, dict) else str(value or '')


def canonical(item, account):
    """Accept MAX v2 fields and legacy protobuf fields, never invent a sender."""
    if not isinstance(item, dict):
        raise ValueError('Invalid WeChat message')
    account = str(account)
    mid = scalar(item.get('new_msg_id') or item.get('svr_id') or item.get('NewMsgId') or item.get('message_id'))
    sender = scalar(item.get('from_user_id') or item.get('from_user_name') or item.get('FromUserName'))
    recipient = scalar(item.get('to_user_id') or item.get('to_user_name') or item.get('ToUserName'))
    conversation = scalar(item.get('conversation_id') or item.get('chat_user_id') or item.get('chat_room_name'))
    if not conversation:
        conversation = sender if sender.endswith('@chatroom') else recipient if sender == account else sender
    kind = 'group' if conversation.endswith('@chatroom') or item.get('chat_type') == 'group' else 'private'
    content = scalar(item.get('text') or item.get('content') or item.get('Content'))
    if kind == 'group' and sender.endswith('@chatroom'):
        if ':\n' not in content:
            raise ValueError('Group message lacks member identity')
        sender, content = content.split(':\n', 1)
    if not account or not mid or not sender or not conversation:
        raise ValueError('WeChat source identity incomplete')
    if sender == account or item.get('is_self') is True:
        return None
    if sender in ('filehelper','fmessage','medianote','newsapp','notifymessage','weixin'):
        return None
    if kind == 'private' and recipient and recipient != account:
        raise ValueError('WeChat message belongs to another account')
    parts = []
    msg_type = int(item.get('type') or item.get('msg_type') or item.get('MsgType') or 1)
    if msg_type == 1 and content:
        parts.append({'type': 'text', 'data': {'text': content}})
    elif msg_type == 34:
        voice = item.get('voice') or {}
        transcription = voice.get('transcription') or voice.get('text') or ''
        parts.append({'type': 'text', 'data': {'text': '【语音转文字】' + (transcription or ('[转写暂不可用]' if voice.get('status') == 'unavailable' else '[未识别到文字]'))}})
    elif msg_type not in (1, 3, 47):
        parts.append({'type': 'text', 'data': {'text': '[微信消息类型:%s]' % msg_type}})
    for image in ([item.get('image')] if isinstance(item.get('image'), dict) else []):
        parts.append({'type': 'image', 'data': {'summary': '微信原图，内容未识别', 'download_context': image.get('download_context') or image}})
    if msg_type in (3, 47) and not parts:
        parts.append({'type': 'text', 'data': {'text': '[图片尚未下载]' if msg_type == 3 else '[微信表情尚未下载]'}})
    ats = item.get('at_user_list') or []
    source = scalar(item.get('MsgSource') or item.get('msg_source'))
    if not ats and source and len(source) < 100000:
        try:
            ats = (ET.fromstring(source).findtext('.//atuserlist') or '').split(',')
        except ET.ParseError:
            ats = []
    parts.extend({'type': 'at', 'data': {'qq': str(uid), 'platform': 'wechat'}} for uid in ats if uid)
    if not parts:
        raise ValueError('Empty WeChat message retained without generating a reply')
    timestamp = float(item.get('timestamp') or item.get('create_time') or item.get('CreateTime') or time.time())
    if timestamp > 1e12:
        timestamp /= 1000
    return {'post_type': 'message', 'message_type': kind, 'self_id': account, 'user_id': sender,
            'group_id': conversation if kind == 'group' else '', 'message_id': mid, 'time': timestamp,
            'group_name': item.get('group_name', ''), 'sender': {'user_id': sender, 'nickname': item.get('sender_name') or item.get('nickname') or sender},
            'message': parts, 'reply_context': item.get('reply_context'), 'native_event': item}


class Inbox:
    def __init__(self, root=ROOT):
        self.root = Path(root)

    def save(self, account, payload):
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        key = hashlib.sha256((str(account) + encoded).encode()).hexdigest()
        with closing(db(self.root)) as c, c:
            c.execute("INSERT OR IGNORE INTO inbox VALUES(?,?,?,'pending',NULL,?)", (key, str(account), encoded, time.time()))
        return key

    def pending(self):
        with closing(db(self.root)) as c:
            return [dict(r) for r in c.execute("SELECT * FROM inbox WHERE state='pending' ORDER BY created LIMIT 100")]

    def finish(self, key, error=None):
        with closing(db(self.root)) as c, c:
            c.execute('UPDATE inbox SET state=?,error=? WHERE key=?', ('quarantine' if error else 'captured', error, key))

    def capture(self, item, account, client=None):
        from store import Store
        raw = canonical(item, account)
        if raw is None:
            return None
        kind=raw['message_type']; conversation=raw['group_id'] or raw['user_id']
        if privacy_commands.operation(raw) is not None:
            # Directory metadata is needed for native UI addressing, not chat memory.
            if kind=='private':remember(account,kind,conversation,raw['sender']['nickname'],root=self.root)
            privacy_commands.handle(self.root,platform='wechat',account=account,kind=kind,
                conversation=conversation,sender=raw['user_id'],message_id=raw['message_id'],raw=raw)
            return None
        if client:
            hydrate_images(raw, client)
        from image_ocr import ImageOCR
        from image_vision import VisionEnricher
        raw=VisionEnricher(self.root,inner=ImageOCR(self.root)).enrich(raw)
        kind = raw['message_type']; conversation = raw['group_id'] or raw['user_id']
        remember(account, kind, conversation, raw.get('group_name') if kind == 'group' else raw['sender']['nickname'], root=self.root)
        with closing(db(self.root)) as c, c:
            c.execute('INSERT OR IGNORE INTO messages VALUES(?,?,?,?)', (account, conversation, raw['message_id'], json.dumps(raw, ensure_ascii=False)))
            if kind == 'group':
                c.execute('INSERT OR REPLACE INTO members VALUES(?,?,?,?,?)', (account, conversation, raw['user_id'], raw['sender']['nickname'], time.time()))
        # Both observation and recovery job precede dispatch; a core crash cannot lose the message.
        from wechat_client import config as wechat_config
        enabled = kind!='group' or wechat_config(self.root).get('group_input_enabled',False)
        batch, packet, duplicate = Store(self.root).capture(platform='wechat', account=account, kind=kind, conversation=conversation,
                                sender=raw['user_id'], message_id=raw['message_id'], raw=raw, generate=enabled)
        if enabled and not duplicate:
            with closing(sqlite3.connect(self.root / 'social.sqlite3', timeout=20)) as c, c:
                c.execute('CREATE TABLE IF NOT EXISTS queue(key TEXT PRIMARY KEY,scope TEXT,kind TEXT,arrival REAL,characters INTEGER,packet TEXT,status TEXT,anchor TEXT,error TEXT)')
                encoded = json.dumps(packet, ensure_ascii=False)
                c.execute("INSERT OR IGNORE INTO queue VALUES(?,?,?,?,?,?,'pending',NULL,NULL)",
                          (batch, json.dumps(packet['scene'], sort_keys=True), kind, time.time(), len(encoded), encoded))
        return raw


def hydrate_images(raw, client):
    """Download only through the authenticated documented endpoint, not arbitrary URLs."""
    from image_library import INCOMING, checked_image
    for part in raw['message']:
        if part['type'] != 'image':
            continue
        data = part['data']; context = data.get('download_context')
        try:
            if not isinstance(context, dict):
                raise ValueError('Missing image download context')
            context = dict(context)
            endpoint = context.pop('endpoint', '/Tools/CdnDownloadImage')
            if endpoint not in ('/Tools/CdnDownloadImage', '/Tools/DownloadImg'):
                raise ValueError('Unsupported image download endpoint')
            response = client.call(endpoint, context, binary=True)
            if isinstance(response, dict):
                value = response.get('Base64') or response.get('base64')
                if not isinstance(value, str):
                    raise ValueError('Image response has no bytes')
                response = base64.b64decode(value.split(',', 1)[-1], validate=True)
            if not isinstance(response, bytes) or not 0 < len(response) <= 4 * 1024 * 1024:
                raise ValueError('Invalid image size')
            extension = '.png' if response.startswith(b'\x89PNG') else '.gif' if response.startswith(b'GIF8') else '.webp' if response.startswith(b'RIFF') else '.jpg'
            INCOMING.mkdir(mode=0o700, parents=True, exist_ok=True)
            path = INCOMING / (hashlib.sha256(response).hexdigest() + extension)
            if not path.exists():
                path.write_bytes(response); path.chmod(0o600)
            data['cached_path'] = str(checked_image(str(path)))
        except Exception as exc:
            data['download_status'] = 'unavailable:' + type(exc).__name__


def refresh_directory(client, account, root=ROOT):
    """Bounded pagination; only explicit returned IDs enter the directory."""
    cursor = {'currentChatRoomContactSeq': 0, 'currentWxcontactSeq': 0}; seen = set(); count = 0
    for _ in range(100):
        data = client.call('/Friend/GetContractList', cursor)
        if not isinstance(data, dict):
            raise ValueError('Unknown WeChat directory envelope')
        rows = data.get('contacts') or data.get('ContactList') or data.get('items') or []
        ids = []
        for item in rows:
            uid = scalar(item.get('userName') or item.get('UserName') or item.get('wxid') or item.get('id')) if isinstance(item, dict) else scalar(item)
            if uid:
                ids.append(uid)
        for start in range(0, len(ids), 20):
            detail = client.call('/Friend/GetContractDetail', {'userName': ','.join(ids[start:start + 20])})
            details = detail if isinstance(detail, list) else detail.get('contacts', detail.get('ContactList', detail.get('items', []))) if isinstance(detail, dict) else []
            for row in details:
                uid = scalar(row.get('userName') or row.get('UserName') or row.get('wxid') or row.get('id'))
                if uid not in ids:
                    continue
                remember(account, 'group' if uid.endswith('@chatroom') else 'private', uid,
                         scalar(row.get('nickName') or row.get('NickName') or row.get('name')),
                         scalar(row.get('remark') or row.get('Remark')), root)
                count += 1
        next_cursor = {key: data.get(key, cursor[key]) for key in cursor}
        signature = json.dumps(next_cursor, sort_keys=True)
        if next_cursor == cursor or signature in seen or data.get('has_more') is False:
            break
        seen.add(signature); cursor = next_cursor
    return count


def items(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ('items', 'messages', 'AddMsgs', 'AddMsgList'):
            if isinstance(data.get(key), list):
                return data[key]
        if any(k in data for k in ('new_msg_id', 'NewMsgId')):
            return [data]
        if data.get('count') == 0:
            return []
    raise ValueError('Unknown WeChat sync payload; retained for inspection')


def lookup_message(account,room,mid,root=ROOT):
    """Read an exact observed native message, including own transport echoes."""
    with closing(db(root)) as c:
        row=c.execute('SELECT raw FROM messages WHERE account=? AND conversation=? AND id=?',(account,room,mid)).fetchone()
        if row:
            raw=json.loads(row[0]);text=''.join(p['data'].get('text','') for p in raw.get('message',[]) if p['type']=='text')
            return {'text':text,'sender':raw['user_id'],'id':mid}
        for payload, in c.execute('SELECT payload FROM inbox WHERE account=? ORDER BY created DESC LIMIT 200',(account,)):
            for item in json.loads(payload).get('items',[]):
                if str(item.get('conversation_id'))==room and str(item.get('new_msg_id'))==mid:
                    return {'text':item.get('text') or '', 'sender':item.get('from_user_id'),'id':mid}
    return None
