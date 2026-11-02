"""Expiring HTTPS-fronted, one-use authorization form; listens on loopback only."""
import hashlib
import hmac
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from receiver import ACCOUNT, ROOT, connect, private_write

LOCK = threading.Lock()
BASE = '/bot-inbox-setup/'
PAGE = '''<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bot 邮箱接收配置</title><body style="max-width:36em;margin:3em auto;padding:1em;font-family:system-ui">
<h2>Bot 邮箱接收配置</h2><p>邮箱：bot@example.invalid</p>
<p>请先在 QQ 邮箱设置开启 IMAP/SMTP。此处只填写邮箱授权码，不填写 QQ 密码。授权码只提交到本服务器，配置成功后链接失效。</p>
<form><label>邮箱授权码 <input id="code" type="password" required autocomplete="off" maxlength="64"></label>
<p><button>验证并启用收件</button></p></form><p id="result"></p>
<p>首次启用只接收之后的新邮件，不导入旧邮件。不发送邮件，不自动回复，不调用 AI。</p>
<script nonce="NONCE">let token=location.hash.slice(1);history.replaceState(null,'',location.pathname);
document.querySelector('form').onsubmit=async e=>{e.preventDefault();let b=document.querySelector('button');b.disabled=true;
let code=document.querySelector('#code');try{let r=await fetch(location.pathname,{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+token},body:JSON.stringify({authorization_code:code.value})});
let v=await r.json();document.querySelector('#result').textContent=v.message;if(r.ok){code.value='';token='';}else{b.disabled=false;}}
catch(e){document.querySelector('#result').textContent='连接失败，请稍后重试';b.disabled=false;}};</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    def respond(self, status, data, mime='application/json; charset=utf-8', nonce=None):
        blob = data.encode() if isinstance(data, str) else json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status); self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(blob))); self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer'); self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-%s'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'" % (nonce or 'none'))
        self.end_headers(); self.wfile.write(blob)

    def do_GET(self):
        if self.path != BASE: return self.respond(404, {})
        nonce = secrets.token_urlsafe(20)
        self.respond(200, PAGE.replace('NONCE', nonce), 'text/html; charset=utf-8', nonce)

    def do_POST(self):
        if self.path != BASE: return self.respond(404, {})
        if self.headers.get('Origin') != 'https://bot.example.invalid': return self.respond(403, {'message': '来源不符'})
        try: size = int(self.headers.get('Content-Length', '0'))
        except ValueError: return self.respond(400, {})
        if not 0 < size <= 2048: return self.respond(413, {})
        if not LOCK.acquire(blocking=False): return self.respond(429, {'message': '正在验证，请稍候'})
        try:
            ticket = ROOT/'setup-ticket.json'
            if not ticket.exists(): return self.respond(403, {'message': '链接已失效'})
            state = json.loads(ticket.read_text())
            token = self.headers.get('Authorization', '').removeprefix('Bearer ')
            digest = hashlib.sha256(token.encode()).hexdigest()
            if not hmac.compare_digest(digest, state['hash']) or time.time() > state['expires']:
                return self.respond(403, {'message': '链接无效或已过期'})
            if time.time() < state.get('retry_after', 0): return self.respond(429, {'message': '请一分钟后再试'})
            state['retry_after'] = time.time() + 60
            private_write(ticket, json.dumps(state).encode())
            data = json.loads(self.rfile.read(size))
            code = ''.join(str(data.get('authorization_code', '')).split())
            if not 8 <= len(code) <= 64: return self.respond(400, {'message': '请填写有效授权码'})
            try:
                client = connect(code); client.logout()
            except Exception:
                return self.respond(400, {'message': '邮箱验证失败，请确认 IMAP 已开启并填写邮箱授权码；一分钟后可重试'})
            private_write(ROOT/'mail-secret.json', json.dumps({'account': ACCOUNT, 'authorization_code': code}).encode())
            ticket.unlink()
            self.respond(200, {'message': '验证成功，服务器已启用自动收件。请一分钟后给此邮箱发一封新邮件测试。'})
        except Exception:
            self.respond(500, {'message': '配置未完成，请稍后重试'})
        finally: LOCK.release()


if __name__ == '__main__':
    ThreadingHTTPServer(('127.0.0.1', 8768), Handler).serve_forever()
