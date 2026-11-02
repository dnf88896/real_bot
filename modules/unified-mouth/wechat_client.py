"""Private WeChat transport backends. Credentials never enter prompts."""
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path('/opt/astrbot-mouth')
DEFAULT = {'enabled': False, 'receive_enabled': False, 'send_enabled': False,
           'account_id': '', 'base_url': 'http://127.0.0.1:18080',
           'credentials_file': '/opt/wechat-lab/business/credentials.json',
           'allow_private': [], 'allow_groups': [], 'poll_seconds': 3,
           'capabilities': ['text', 'image', 'mention', 'quote', 'voice']}


class Rejected(ValueError):
    definite_failure = True


class Uncertain(RuntimeError):
    """An operation may have reached WeChat. Never automatically resend it."""


def config(root=ROOT):
    path = Path(root) / 'wechat-config.json'
    return DEFAULT | (json.loads(path.read_text('utf8')) if path.exists() else {})


class Client:
    def __init__(self, root=ROOT, request=None):
        self.root = Path(root)
        self.cfg = config(root)
        self.request = request
        self.backend = None
        if self.cfg.get('backend') == 'webox':
            from wechat_webox import Webox
            self.backend = Webox(self.cfg, request)

    def call(self, path, body=None, method='POST', binary=False):
        if self.backend:
            return self.backend.call(path, body, method, binary)
        if not self.cfg['enabled']:
            raise Rejected('WeChat transport disabled')
        if self.request:
            value = self.request(path, body, method, binary)
        else:
            base = self.cfg['base_url'].rstrip('/')
            url = urllib.parse.urlsplit(base)
            if url.username or url.password or url.query or url.fragment or url.scheme not in ('http', 'https'):
                raise Rejected('Invalid WeChat API base URL')
            if url.scheme == 'http' and url.hostname not in ('localhost', '127.0.0.1', '::1'):
                raise Rejected('Nonlocal WeChat API requires HTTPS')
            credentials = json.loads(Path(self.cfg['credentials_file']).read_text('utf8'))
            headers = {'X-Access-Token': credentials['access_token'], 'Content-Type': 'application/json'}
            query = urllib.parse.urlencode({'authcode': credentials['authcode']})
            req = urllib.request.Request(base + path + '?' + query,
                data=json.dumps(body or {}, ensure_ascii=False).encode() if method == 'POST' else None,
                headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=35) as response:
                    content = response.read(16 * 1024 * 1024 + 1)
                    if len(content) > 16 * 1024 * 1024:
                        raise Uncertain('WeChat API response exceeds limit')
                    if binary and not response.headers.get('Content-Type', '').startswith('application/json'):
                        return content
                    value = json.loads(content)
            except urllib.error.HTTPError as exc:
                if exc.code in (400, 401, 403, 404, 413, 422):
                    raise Rejected('WeChat API rejected request') from None
                raise Uncertain('WeChat API HTTP failure') from None
            except (TimeoutError, OSError, ValueError):
                raise Uncertain('WeChat API result unavailable') from None
        if binary and isinstance(value, bytes):
            return value
        if not isinstance(value, dict) or type(value.get('Code')) is not int:
            raise Uncertain('Unrecognized WeChat response envelope')
        if value['Code'] != 0 or value.get('Success') is False:
            raise Rejected('WeChat API operation rejected (code %s)' % value['Code'])
        return value.get('Data')

    def online(self):
        data = self.call('/Login/GetLoginStatus', method='GET')
        return isinstance(data, dict) and data.get('loginState') == 'ready' and data.get('longLinkReady') is True

    def sync(self, cursor='initial'):
        return self.call('/Msg/Sync', {'Scene': 0, 'Synckey': cursor})

    def open_chat(self, uid):
        """Bring a conversation to the front without sending anything."""
        if self.backend:
            return self.backend.open_chat(uid)
        raise Rejected('WeChat chat control requires the webox backend')

    def sent(self, path, body):
        if self.backend:
            return self.backend.sent(path, body)
        data = self.call(path, body)
        # Outer Code=0 alone is insufficient: inspect the actual send result.
        if not isinstance(data, dict):
            raise Uncertain('WeChat send response has no receipt')
        for key in ('ret', 'Ret', 'retcode'):
            if key in data and data[key] != 0:
                raise Rejected('WeChat rejected message')
        for key in ('baseResp', 'BaseResponse', 'base_resp'):
            if isinstance(data.get(key), dict) and data[key].get('ret', data[key].get('Ret', 0)) != 0:
                raise Rejected('WeChat rejected message')
        mid = next((str(data[k]) for k in ('message_id', 'new_msg_id', 'NewMsgId', 'svr_id') if data.get(k)), '')
        if not mid or mid == '0' or data.get('status') in ('failed', 'pending', 'queued'):
            raise Uncertain('WeChat has not confirmed message delivery')
        return {'message_id': mid, 'platform_receipt': data}
