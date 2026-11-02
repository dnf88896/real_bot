"""Stateless OpenAI Chat Completions adapter; web history is never included."""
import hashlib
import hmac
import json
import math
import os
import threading
import time
import uuid
import urllib.request
import urllib.error
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PUBLIC_MODEL = 'kobato-flash'
SLOTS = threading.BoundedSemaphore(2)
RATE_LOCK = threading.Lock()
REQUESTS = deque()


def persona():
    return '\n'.join((ROOT / p).read_text(encoding='utf-8') for p in ('persona.md', 'character/soul.md', 'character/limit.md'))


def payload_for(body):
    if not isinstance(body, dict):
        raise ValueError('Request body must be a JSON object.')
    if body.get('model') not in (PUBLIC_MODEL, 'deepseek-flash'):
        raise ValueError('Model must be kobato-flash.')
    messages = body.get('messages')
    if not isinstance(messages, list) or not 1 <= len(messages) <= 100:
        raise ValueError('messages must contain 1 to 100 messages.')
    clean = []
    for message in messages:
        if not isinstance(message, dict) or message.get('role') not in ('system', 'developer', 'user', 'assistant'):
            raise ValueError('Only system, developer, user and assistant text messages are supported.')
        content = message.get('content')
        if isinstance(content, list):
            if not all(isinstance(p, dict) and p.get('type') == 'text' and isinstance(p.get('text'), str) for p in content):
                raise ValueError('Only text content parts are supported.')
            content = '\n'.join(p['text'] for p in content)
        if not isinstance(content, str) or message.get('tool_calls'):
            raise ValueError('Message content must be text; tool calls are not supported.')
        clean.append({'role': 'system' if message['role'] == 'developer' else message['role'], 'content': content})
    if sum(len(m['content']) for m in clean) > 64000:
        raise ValueError('Combined message content exceeds 64000 characters.')
    if type(body.get('n', 1)) is not int or body.get('n', 1) != 1 or body.get('tools') or body.get('functions'):
        raise ValueError('Only n=1 and text chat are supported.')
    stream = body.get('stream', False)
    if not isinstance(stream, bool):
        raise ValueError('stream must be a boolean.')
    tokens = body.get('max_tokens')
    if tokens is None:
        tokens = body.get('max_completion_tokens')
    if tokens is None:
        tokens = 700
    if type(tokens) is not int or not 1 <= tokens <= 4096:
        raise ValueError('max_tokens must be an integer from 1 to 4096.')
    payload = {'model': os.environ.get('DEEPSEEK_MODEL', 'deepseek-flash'), 'messages': [{'role': 'system', 'content': persona()}] + clean, 'thinking': {'type': 'disabled'}, 'max_tokens': tokens, 'stream': stream}
    for param, lower, upper in (('temperature', 0, 2), ('top_p', 0, 1)):
        value = body.get(param)
        if value is not None:
            if type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper or (param == 'top_p' and value == 0):
                raise ValueError(f'{param} is outside the supported range.')
            payload[param] = value
    stop = body.get('stop')
    if stop is not None:
        if not (isinstance(stop, str) and stop or isinstance(stop, list) and 1 <= len(stop) <= 16 and all(isinstance(s, str) and s for s in stop)):
            raise ValueError('stop must be a nonempty string or an array of 1 to 16 nonempty strings.')
        payload['stop'] = stop
    if 'user' in body and (not isinstance(body['user'], str) or len(body['user']) > 256):
        raise ValueError('user must be a string of at most 256 characters.')
    options = body.get('stream_options')
    if options is not None:
        if not stream or not isinstance(options, dict) or type(options.get('include_usage', False)) is not bool:
            raise ValueError('stream_options requires stream=true and a boolean include_usage.')
        payload['stream_options'] = {'include_usage': options.get('include_usage', False)}
    return payload


def usage_of(result):
    source = result.get('usage') or {}
    return {k: source.get(k, 0) for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')}


class OpenAIAPI:
    def api_error(self, status, message, code=None):
        kind = 'authentication_error' if status == 401 else 'rate_limit_error' if status == 429 else 'invalid_request_error' if status < 500 else 'server_error'
        return self.send(status, {'error': {'message': message, 'type': kind, 'param': None, 'code': code or kind}})

    def api_authorized(self):
        expected = os.environ.get('KOBATO_API_KEY_SHA256', '')
        auth = self.headers.get('Authorization', '')
        scheme, _, token = auth.partition(' ')
        actual = hashlib.sha256(token.encode()).hexdigest()
        if not expected or scheme.lower() != 'bearer' or not token or not hmac.compare_digest(actual, expected):
            self.api_error(401, 'Invalid or missing API key.', 'invalid_api_key')
            return False
        return True

    def api_get(self):
        if not self.api_authorized():
            return
        if self.path != '/v1/models':
            return self.api_error(404, 'Unknown API endpoint.')
        return self.send(200, {'object': 'list', 'data': [{'id': PUBLIC_MODEL, 'object': 'model', 'created': 1792425600, 'owned_by': 'kobato'}]})

    def sse(self, value):
        data = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        self.wfile.write(('data: ' + data + '\n\n').encode())
        self.wfile.flush()

    def api_post(self):
        if not self.api_authorized():
            return
        if self.path != '/v1/chat/completions':
            return self.api_error(404, 'Unknown API endpoint.')
        try:
            if not self.headers.get('Content-Type', '').startswith('application/json'):
                raise ValueError('Content-Type must be application/json.')
            size = int(self.headers.get('Content-Length', '0'))
            if not 1 <= size <= 262144:
                raise ValueError('Request body must be between 1 and 262144 bytes.')
            payload = payload_for(json.loads(self.rfile.read(size)))
        except (ValueError, TypeError):
            return self.api_error(400, 'Invalid JSON or unsupported parameters; use text messages, max_tokens 1-4096, temperature 0-2, top_p >0 and <=1.')
        if not os.environ.get('DEEPSEEK_API_KEY'):
            return self.api_error(503, 'Upstream API is not configured.')
        with RATE_LOCK:
            current = time.monotonic()
            while REQUESTS and REQUESTS[0] <= current - 60:
                REQUESTS.popleft()
            if len(REQUESTS) >= 20:
                return self.api_error(429, 'API limit is 20 chat requests per minute.')
            REQUESTS.append(current)
        if not SLOTS.acquire(blocking=False):
            return self.api_error(429, 'At most two concurrent API requests are allowed.')
        started = False
        try:
            request = urllib.request.Request('https://api.deepseek.com/chat/completions', data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + os.environ['DEEPSEEK_API_KEY']})
            completion_id = 'chatcmpl-' + uuid.uuid4().hex
            created = int(time.time())
            with urllib.request.urlopen(request, timeout=120) as upstream:
                if not payload['stream']:
                    result = json.load(upstream)
                    choice = result['choices'][0]
                    content = choice['message'].get('content')
                    if not isinstance(content, str):
                        raise ValueError('Invalid upstream content')
                    return self.send(200, {'id': completion_id, 'object': 'chat.completion', 'created': created, 'model': PUBLIC_MODEL, 'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': content}, 'finish_reason': choice.get('finish_reason', 'stop'), 'logprobs': None}], 'usage': usage_of(result)})
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
                self.send_header('Cache-Control', 'no-cache, no-store')
                self.send_header('X-Accel-Buffering', 'no')
                self.send_header('Connection', 'close')
                self.end_headers()
                self.close_connection = True
                started = True
                include_usage = payload.get('stream_options', {}).get('include_usage', False)
                final_usage = None
                for raw in upstream:
                    line = raw.decode('utf-8').strip()
                    if not line.startswith('data:'):
                        continue
                    data = line[5:].strip()
                    if data == '[DONE]':
                        if include_usage and final_usage is not None:
                            self.sse({'id': completion_id, 'object': 'chat.completion.chunk', 'created': created, 'model': PUBLIC_MODEL, 'choices': [], 'usage': final_usage})
                        self.sse('[DONE]')
                        return
                    part = json.loads(data)
                    if 'error' in part:
                        raise ValueError('Upstream stream error')
                    choices = []
                    for choice in part.get('choices', []):
                        delta = choice.get('delta') or {}
                        safe_delta = {k: delta[k] for k in ('role', 'content') if delta.get(k) is not None}
                        choices.append({'index': choice.get('index', 0), 'delta': safe_delta, 'finish_reason': choice.get('finish_reason'), 'logprobs': None})
                    chunk = {'id': completion_id, 'object': 'chat.completion.chunk', 'created': created, 'model': PUBLIC_MODEL, 'choices': choices}
                    if include_usage:
                        chunk['usage'] = None
                        if part.get('usage'):
                            final_usage = usage_of(part)
                    self.sse(chunk)
                raise ValueError('Upstream stream ended without DONE')
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as error:
            status = 429 if isinstance(error, urllib.error.HTTPError) and error.code == 429 else 502
            if started:
                try:
                    self.sse({'error': {'message': 'Upstream stream interrupted. Please retry.', 'type': 'server_error', 'code': 'upstream_error'}})
                    self.sse('[DONE]')
                except OSError:
                    pass
            else:
                self.api_error(status, 'Upstream request failed. Please retry.', 'upstream_error')
        finally:
            SLOTS.release()
