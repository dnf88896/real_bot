"""Receive QQ mail without sending, marking it read, or invoking any model."""
import argparse
import email.policy
import hashlib
import imaplib
import json
import os
import re
import sqlite3
import ssl
import time
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path

ACCOUNT = os.environ.get('BOT_INBOX_ACCOUNT', '')
ROOT = Path(os.environ.get('BOT_INBOX_ROOT', '/opt/bot-inbox'))
MAX_BYTES = 25 * 1024 * 1024


class PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []; self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'): self.hidden += 1
        if tag in ('br', 'p', 'div', 'li'): self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'): self.hidden = max(0, self.hidden - 1)

    def handle_data(self, text):
        if not self.hidden: self.parts.append(text)


def private_write(path, data):
    path = Path(path)
    fd = os.open(path.with_suffix(path.suffix + '.tmp'), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'wb') as f: f.write(data)
    path.with_suffix(path.suffix + '.tmp').replace(path)


def connect(code):
    client = imaplib.IMAP4_SSL('imap.qq.com', 993, ssl_context=ssl.create_default_context(), timeout=30)
    try:
        client.login(ACCOUNT, code)
        status, _ = client.select('INBOX', readonly=True)
        if status != 'OK': raise RuntimeError('Mailbox unavailable')
        return client
    except Exception:
        try: client.logout()
        except Exception: pass
        raise


def parse_mail(blob):
    message = BytesParser(policy=email.policy.default).parsebytes(blob)
    plain, html, attachments = [], [], []
    for part in message.walk():
        if part.is_multipart(): continue
        payload = part.get_payload(decode=True) or b''
        filename = part.get_filename()
        if filename or part.get_content_disposition() == 'attachment' or part.get_content_maintype() != 'text':
            attachments.append((filename or 'attachment', part.get_content_type(), payload))
        elif part.get_content_type() in ('text/plain', 'text/html'):
            try: text = payload.decode(part.get_content_charset() or 'utf-8', errors='replace')
            except LookupError: text = payload.decode('utf-8', errors='replace')
            (plain if part.get_content_type() == 'text/plain' else html).append(text)
    if not any(text.strip() for text in plain) and html:
        parser = PlainHTML(); parser.feed('\n'.join(html)); plain = [''.join(parser.parts)]
    return {'from': str(message.get('From', '')), 'to': str(message.get('To', '')),
            'subject': str(message.get('Subject', '')), 'date': str(message.get('Date', '')),
            'message_id': str(message.get('Message-ID', '')), 'text': '\n'.join(plain)}, attachments


def database(root):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(root/'inbox.sqlite3', timeout=20)
    db.executescript('''PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS checkpoints(account TEXT PRIMARY KEY, validity TEXT, last_uid INTEGER);
        CREATE TABLE IF NOT EXISTS messages(account TEXT,validity TEXT,uid INTEGER,received REAL,
            status TEXT,record_json TEXT,PRIMARY KEY(account,validity,uid));''')
    return db


def poll(root=ROOT, factory=connect):
    cfg = root/'mail-secret.json'
    if not cfg.exists(): return {'status': 'awaiting_authorization'}
    code = json.loads(cfg.read_text())['authorization_code']
    client = factory(code)
    db = database(root)
    try:
        validity = client.response('UIDVALIDITY')[1][0].decode()
        status, data = client.uid('search', None, 'ALL')
        if status != 'OK': raise RuntimeError('Mailbox search failed')
        uids = sorted(int(x) for x in (data[0] or b'').split())
        old = db.execute('SELECT validity,last_uid FROM checkpoints WHERE account=?', (ACCOUNT,)).fetchone()
        # First activation starts from new mail; existing mail remains untouched.
        if old is None:
            with db: db.execute('INSERT INTO checkpoints VALUES(?,?,?)', (ACCOUNT, validity, max(uids, default=0)))
            return {'status': 'initialized_new_mail_only', 'received': 0}
        # A server UID namespace reset requires rescan; event identity includes validity.
        last = old[1] if old[0] == validity else 0
        count = 0
        for uid in [x for x in uids if x > last][:50]:
            status, size_data = client.uid('fetch', str(uid), '(RFC822.SIZE)')
            sizes = [x[0] if isinstance(x, tuple) else x for x in size_data if x]
            match = re.search(rb'RFC822.SIZE\s+(\d+)', b' '.join(sizes))
            if status != 'OK' or not match: raise RuntimeError('Mailbox size unavailable')
            oversized = int(match[1]) > MAX_BYTES
            status, data = client.uid('fetch', str(uid), '(BODY.PEEK[HEADER])' if oversized else '(BODY.PEEK[])')
            blobs = [x[1] for x in data if isinstance(x, tuple) and isinstance(x[1], bytes)]
            if status != 'OK' or not blobs: raise RuntimeError('Mailbox download failed')
            blob = b''.join(blobs)
            record, parts = parse_mail(blob)
            key = hashlib.sha256(f'{ACCOUNT}:{validity}:{uid}'.encode()).hexdigest()
            folder = root/'messages'/key
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            private_write(folder/('headers.eml' if oversized else 'original.eml'), blob)
            record['attachments'] = []
            for i, (name, mime, payload) in enumerate(parts):
                target = folder/f'attachment-{i:03}.bin'
                private_write(target, payload)
                record['attachments'].append({'name': name, 'type': mime, 'path': str(target), 'bytes': len(payload)})
            record.update(platform='email', account=ACCOUNT, downloaded=not oversized)
            with db:
                db.execute('INSERT OR IGNORE INTO messages VALUES(?,?,?,?,?,?)',
                    (ACCOUNT, validity, uid, time.time(), 'oversize_headers_only' if oversized else 'received', json.dumps(record, ensure_ascii=False)))
                db.execute('UPDATE checkpoints SET validity=?,last_uid=? WHERE account=?', (validity, uid, ACCOUNT))
            count += 1
        return {'status': 'ok', 'received': count}
    finally:
        db.close()
        try: client.logout()
        except Exception: pass


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--once', action='store_true'); args = parser.parse_args()
    os.umask(0o077)
    while True:
        try: print(json.dumps(poll()), flush=True)
        except Exception as exc:
            # Authentication responses and message bodies never enter service logs.
            print(json.dumps({'status': 'error', 'type': type(exc).__name__}), flush=True)
            if args.once: raise SystemExit(1)
        if args.once: return
        time.sleep(30)


if __name__ == '__main__': main()
