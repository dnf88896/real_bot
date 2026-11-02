"""Privacy-preserving request ledger. Meter failures never stop the bot.

Usage is recorded before response validation, so invalid-format retries still count.
No request/response text, URLs, credentials or reasoning are stored.
"""
import contextlib
import contextvars
import functools
import hashlib
import inspect
import json
import logging
import sqlite3
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo
from datetime import datetime

ROOT = Path('/opt/astrbot-mouth')
SCOPE = contextvars.ContextVar('robot_usage_scope', default=None)
_installed = False
_warning_at = 0
SCHEMA = '''CREATE TABLE IF NOT EXISTS requests(
 id TEXT PRIMARY KEY, started REAL NOT NULL, ended REAL, category TEXT NOT NULL,
 batch TEXT, backend TEXT, model TEXT, key_id TEXT, status TEXT,
 input_miss INTEGER, input_hit INTEGER, output_tokens INTEGER, reasoning_tokens INTEGER,
 usage_known INTEGER NOT NULL DEFAULT 0, estimated_yuan REAL, rate_peak INTEGER,
 http_status INTEGER, error_type TEXT, request_id TEXT, response_bytes INTEGER,
 repair_attempt INTEGER DEFAULT 0);
 CREATE INDEX IF NOT EXISTS requests_time ON requests(started);
 CREATE INDEX IF NOT EXISTS requests_category ON requests(category,started);
 CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);'''


def connect():
    db = sqlite3.connect(ROOT / 'api-usage.sqlite3', timeout=2)
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript(SCHEMA)
    db.execute('INSERT OR IGNORE INTO settings VALUES(?,?)', ('started', str(time.time())))
    db.commit()
    return db


def warn():
    global _warning_at
    if time.time() - _warning_at > 60:
        _warning_at = time.time()
        logging.getLogger('robot.usage').warning('Usage ledger unavailable; bot execution continues')


def store(row):
    try:
        with contextlib.closing(connect()) as db, db:
            fields = list(row)
            db.execute('INSERT INTO requests (' + ','.join(fields) + ') VALUES (' +
                       ','.join('?' for _ in fields) + ') ON CONFLICT(id) DO UPDATE SET ' +
                       ','.join(f'{f}=excluded.{f}' for f in fields if f != 'id'),
                       [row[f] for f in fields])
    except Exception:
        warn()


def key_id(key):
    return hashlib.sha256(str(key).encode()).hexdigest()[:16] if key else None


def normalize(usage):
    if hasattr(usage, 'model_dump'):
        usage = usage.model_dump()
    if not isinstance(usage, dict):
        return None
    if 'inputTokens' in usage:  # DSH inputTokens is uncached, not total input.
        miss = int(usage['inputTokens']) + int(usage.get('cacheWriteTokens') or 0)
        hit = int(usage.get('cacheReadTokens') or 0)
        output = int(usage.get('outputTokens') or 0)
    elif 'prompt_tokens' in usage and 'completion_tokens' in usage:
        hit = int(usage.get('prompt_cache_hit_tokens') or
                  (usage.get('prompt_tokens_details') or {}).get('cached_tokens') or 0)
        miss_value = usage.get('prompt_cache_miss_tokens')
        miss = int(miss_value if miss_value is not None else max(0, int(usage['prompt_tokens']) - hit))
        output = int(usage['completion_tokens'])
    else:
        return None
    reasoning = int((usage.get('completion_tokens_details') or {}).get('reasoning_tokens') or 0)
    if min(miss, hit, output, reasoning) < 0:
        return None
    return miss, hit, output, reasoning


def price(model, stamp, tokens):
    """Current Flash rate snapshot; holiday overrides are explicitly configurable."""
    local = datetime.fromtimestamp(stamp, ZoneInfo('Asia/Shanghai'))
    cfg = {}
    config = ROOT / 'usage-pricing.json'
    if config.exists():
        cfg = json.loads(config.read_text('utf8'))
    holidays = cfg.get('holidays', [])
    peak = local.weekday() < 5 and local.date().isoformat() not in holidays and (
        9 <= local.hour < 12 or 14 <= local.hour < 18)
    if model not in ('deepseek-flash', 'deepseek-v4-flash', 'deepseek-v4-flash-vision-exp'):
        return None, int(peak)
    miss, hit, output, _ = tokens
    rates = cfg.get('off_peak_per_million', {'input_miss': 1, 'input_hit': .02, 'output': 4})
    return round((miss * rates['input_miss'] + hit * rates['input_hit'] + output * rates['output']) *
                 (cfg.get('peak_multiplier', 2) if peak else 1) / 1_000_000, 9), int(peak)


def current_scope():
    value = SCOPE.get()
    if value:
        return value
    # Both plugin-relative and root imports exist; use the active ContextVar only.
    for name, module in list(sys.modules.items()):
        if name == 'submission_tool' or name.endswith('.submission_tool'):
            try:
                tool = module.CURRENT.get()
                if tool:
                    packet = tool.event.get_extra('unified_mouth_packet') or {}
                    active = packet.get('active_type')
                    kind = ('interest_expression' if active == 'interest' else
                            'idle' if active == 'idle' else active or
                            (packet.get('scene') or {}).get('chat_type', 'other'))
                    return {'category': 'mouth.' + str(kind), 'batch': packet.get('batch_id')}
            except Exception:
                pass
    return {'category': 'mouth.other', 'batch': None}


def repair_attempt():
    for name, module in list(sys.modules.items()):
        if name == 'submission_tool' or name.endswith('.submission_tool'):
            try:
                tool = module.CURRENT.get()
                if tool:
                    value = tool.event.get_extra('unified_submission_repairs', 0)
                    return value if type(value) is int else 0
            except Exception:
                pass
    frame = inspect.currentframe()
    try:
        for _ in range(30):
            if frame is None:
                break
            if Path(frame.f_code.co_filename).name == 'submission_tool.py' and frame.f_code.co_name == 'boundary':
                value = frame.f_locals.get('attempt', 0)
                return value if type(value) is int else 0
            frame = frame.f_back
    finally:
        del frame
    return 0


class Call:
    def __init__(self, category, model='deepseek-flash', key=None, backend='llm', batch=None, stamp=None, identity=None):
        self.row = {'id': identity or uuid.uuid4().hex, 'started': stamp or time.time(),
                    'category': category, 'batch': batch, 'backend': backend, 'model': model,
                    'key_id': key_id(key), 'status': 'pending', 'usage_known': 0,
                    'repair_attempt': repair_attempt()}
        self.done = False
        store(self.row)

    def finish(self, usage=None, *, status='ok', http_status=None, error=None, request_id=None, response_bytes=None):
        if self.done:
            return
        self.done = True
        try:
            tokens = normalize(usage)
            self.row.update(ended=time.time(), status=status, http_status=http_status,
                            error_type=type(error).__name__ if error else None,
                            request_id=request_id, response_bytes=response_bytes)
            if tokens is not None:
                cost, peak = price(self.row['model'], self.row['started'], tokens)
                self.row.update(input_miss=tokens[0], input_hit=tokens[1], output_tokens=tokens[2],
                                reasoning_tokens=tokens[3], usage_known=1, estimated_yuan=cost, rate_peak=peak)
            store(self.row)
        except Exception:
            warn()


def memory_category(task, data):
    if isinstance(data, dict):
        if '候选' in data:
            return 'memory.recall_select'
        if '上轮检索' in data:
            return 'memory.recall_query'
        if '新增记录' in data or '待精简摘要' in data:
            return 'memory.mid_summary'
        if '本轮工具记录' in data or '待修正摘要' in data:
            return 'interest.summary'
    if '参与' in task or 'reply' in task:
        return 'participation'
    return 'memory.other'


def json_request(request, timeout, category, model='deepseek-flash'):
    auth = request.get_header('Authorization', '')
    call = Call(category, model, auth.removeprefix('Bearer '))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            result = json.loads(raw)
            status = getattr(response, 'status', 200)
        call.finish(result.get('usage'), http_status=status, request_id=result.get('id'), response_bytes=len(raw))
        return result
    except BaseException as exc:
        call.finish(status='error', error=exc, http_status=getattr(exc, 'code', None))
        raise


@contextlib.contextmanager
def scope(category, batch=None):
    token = SCOPE.set({'category': category, 'batch': batch})
    try:
        yield
    finally:
        SCOPE.reset(token)


async def tool_loop(function, **kwargs):
    event = kwargs.get('event')
    packet = event.get_extra('unified_mouth_packet') or {} if event else {}
    with scope('interest.explore', packet.get('batch_id')):
        return await function(**kwargs)


class MeteredStream:
    def __init__(self, inner, call):
        self.inner, self.call = inner, call

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def __aiter__(self):
        try:
            async for chunk in self.inner:
                if getattr(chunk, 'usage', None) is not None:
                    self.call.finish(chunk.usage, request_id=getattr(chunk, 'id', None))
                yield chunk
        except BaseException as exc:
            self.call.finish(status='error', error=exc)
            raise
        finally:
            self.call.finish(status='missing_usage')

    async def close(self):
        try:
            return await self.inner.close()
        finally:
            self.call.finish(status='missing_usage')

    async def __aenter__(self):
        await self.inner.__aenter__()
        return self

    async def __aexit__(self, *args):
        try:
            return await self.inner.__aexit__(*args)
        finally:
            self.call.finish(status='missing_usage')


def install():
    """Wrap the actual SDK request, below native tool loops and format repair."""
    global _installed
    if _installed:
        return
    from openai.resources.chat.completions import AsyncCompletions
    original = AsyncCompletions.create

    @functools.wraps(original)
    async def create(resource, *args, **kwargs):
        # Do not meter another provider accidentally added to AstrBot.
        if 'api.deepseek.com' not in str(resource._client.base_url):
            return await original(resource, *args, **kwargs)
        info = current_scope()
        call = Call(info['category'], kwargs.get('model', 'unknown'),
                    getattr(resource._client, 'api_key', None), batch=info.get('batch'))
        try:
            result = await original(resource, *args, **kwargs)
            if kwargs.get('stream') is True:
                return MeteredStream(result, call)
            call.finish(getattr(result, 'usage', None), request_id=getattr(result, 'id', None))
            return result
        except BaseException as exc:
            call.finish(status='error', error=exc, http_status=getattr(exc, 'status_code', None))
            raise

    AsyncCompletions.create = create
    _installed = True
    try:
        with contextlib.closing(connect()):
            pass
    except Exception:
        warn()


def web_begin(name, key):
    return Call('web.' + name, model='search-fetch', key=key, backend='web')


def web_finish(call, data, status):
    try:
        nonempty = bool(data.get('results') if call.row['category'] == 'web.search' else data.get('content'))
        cfg_path = ROOT / 'usage-pricing.json'
        cfg = json.loads(cfg_path.read_text('utf8')) if cfg_path.exists() else {}
        call.row.update(ended=time.time(), status='ok' if status == 200 else 'error', http_status=status,
                        usage_known=1, estimated_yuan=cfg.get('search_fetch_per_success', .005) if status == 200 and nonempty else 0.)
        call.done = True
        store(call.row)
    except Exception:
        warn()


async def web_request(session, action, payload, headers):
    auth = headers.get('Authorization', '').removeprefix('Bearer ')
    call = web_begin(action, auth)
    try:
        async with session.post('https://api.deepseek.com/v1/tools/' + action,
                                json=payload, headers=headers) as response:
            if response.status != 200:
                reason = await response.text()
                web_finish(call, {}, response.status)
                raise RuntimeError(f'DeepSeek {action} failed: {reason}, status: {response.status}')
            data = await response.json()
            web_finish(call, data, response.status)
            return data
    except BaseException as exc:
        call.finish(status='error', error=exc)
        raise


def import_dsh():
    """Import only special-browser sessions since ledger activation, once/step.

    DSH stores actual per-request usage in compressed session events. Failed
    browser runs are imported too; historical/manual DSH sessions are excluded.
    """
    try:
        with contextlib.closing(connect()) as db:
            since = float(db.execute("SELECT value FROM settings WHERE key='started'").fetchone()[0])
            seen = {r[0] for r in db.execute("SELECT id FROM requests WHERE backend='dsh'")}
        sessions = ROOT / 'browser-dsh/sessions'
        key = json.loads((ROOT / 'api.json').read_text('utf8'))['api_key']
        for path in sessions.rglob('session.v4.jsonl.zstd'):
            if path.stat().st_mtime < since:
                continue
            proc = subprocess.Popen(['zstd', '-q', '-d', '-c', str(path)],
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            try:
                for raw in proc.stdout:
                    try:
                        row = json.loads(raw)
                    except (ValueError, UnicodeError):
                        continue
                    stamp = (row.get('time') or 0) / 1000
                    if row.get('type') != 'assistant/message' or stamp < since:
                        continue
                    data = row.get('data') or {}
                    usage = data.get('usage')
                    if normalize(usage) is None:
                        continue
                    identity = 'dsh:' + hashlib.sha256((str(path.parent) + ':' + str(row.get('seq'))).encode()).hexdigest()
                    if identity in seen:
                        continue
                    call = Call('interest.special_browser', key=key, backend='dsh', stamp=stamp, identity=identity)
                    call.finish(usage)
                    seen.add(identity)
            finally:
                proc.stdout.close()
                proc.wait(timeout=10)
    except Exception:
        warn()
