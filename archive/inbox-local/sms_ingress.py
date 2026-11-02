"""Authenticated, receive-only SMS webhook and heartbeat. No model or send API."""
import hashlib
import hmac
import json
import os
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from receiver import ROOT

PREFIX = '/bot-phone/'


def authorize(path, config):
    fields = path.split('/')
    if len(fields) != 4 or fields[1] != 'bot-phone' or fields[2] not in ('sms', 'heartbeat'):
        return None
    kind = fields[2]
    digest = hashlib.sha256(fields[3].encode()).hexdigest()
    return kind if hmac.compare_digest(digest, config.get(kind+'_hash', '')) else None


def database(root):
    db = sqlite3.connect(root/'phone.sqlite3', timeout=15)
    db.executescript('''PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS sms(id TEXT PRIMARY KEY,received REAL,record_json TEXT,is_test INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS heartbeat(device TEXT PRIMARY KEY,last_seen REAL,count INTEGER);''')
    return db


def save_sms(root, payload):
    if not isinstance(payload, dict): raise ValueError('Expected object')
    sender, text = payload.get('from'), payload.get('text')
    if not isinstance(sender, str) or not sender.strip() or len(sender) > 256:
        raise ValueError('Missing sender')
    if not isinstance(text, str) or len(text) > 12000:
        raise ValueError('Missing message text')
    sim = payload.get('sim', '')
    stamp = payload.get('sentStamp') or payload.get('receivedStamp') or ''
    identity = [sender, text, str(stamp), str(sim)]
    key = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
    record = {'platform': 'sms', 'sender': sender, 'text': text, 'sim': sim,
              'sent_at_device': payload.get('sentStamp'), 'received_at_device': payload.get('receivedStamp'),
              'received_at_server': time.time(), 'raw': payload}
    db = database(root)
    try:
        with db:
            result = db.execute('INSERT OR IGNORE INTO sms VALUES(?,?,?,?)',
                (key, time.time(), json.dumps(record, ensure_ascii=False), int(payload.get('_server_self_test') is True)))
        return result.rowcount == 1
    finally: db.close()


def save_heartbeat(root):
    db = database(root)
    try:
        with db:
            db.execute('INSERT INTO heartbeat VALUES(?,?,1) ON CONFLICT(device) DO UPDATE SET last_seen=excluded.last_seen,count=count+1',
                       ('owner-phone', time.time()))
    finally: db.close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    def setup(self):
        super().setup(); self.connection.settimeout(10)

    def reply(self, status, value):
        blob = json.dumps(value).encode()
        self.send_response(status); self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store'); self.send_header('Content-Length', str(len(blob)))
        self.end_headers(); self.wfile.write(blob)

    def kind(self):
        config = json.loads((self.server.root/'sms-routes.json').read_text())
        return authorize(self.path, config)

    def do_GET(self):
        if self.kind() != 'heartbeat': return self.reply(404, {})
        save_heartbeat(self.server.root); self.reply(200, {'ok': True})

    def do_POST(self):
        kind = self.kind()
        if not kind: return self.reply(404, {})
        try: size = int(self.headers.get('Content-Length', '0'))
        except ValueError: return self.reply(400, {})
        if self.headers.get('Transfer-Encoding') or not 0 <= size <= 65536:
            return self.reply(413, {})
        try:
            body = self.rfile.read(size)
            if len(body) != size: return self.reply(400, {})
            if kind == 'heartbeat':
                save_heartbeat(self.server.root)
                return self.reply(200, {'ok': True})
            payload = json.loads(body)
            created = save_sms(self.server.root, payload)
            self.reply(200, {'ok': True, 'duplicate': not created})
        except (ValueError, UnicodeError): self.reply(422, {'ok': False})
        except Exception: self.reply(503, {'ok': False})


if __name__ == '__main__':
    os.umask(0o077)
    server = ThreadingHTTPServer(('127.0.0.1', 8769), Handler)
    server.root = ROOT
    server.serve_forever()
