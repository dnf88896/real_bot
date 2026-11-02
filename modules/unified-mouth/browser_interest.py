"""Periodic DSH browser exploration, with durable routing and verified screenshots."""
import asyncio
import usage_meter
import hashlib
import json
import os
import signal
import time
from contextlib import closing
from pathlib import Path

if __package__:
    from .persona_source import personality_tail
else:
    from persona_source import personality_tail

BROWSER_TASK = ('你是一个拥有统一记忆、人格和情绪的仿生人。现在进行兴趣探索。'
    '像平时上网一样，寻找自己感兴趣的内容。可以浏览、搜索、打开网页并继续探索，结合已有兴趣，也尝试新方向。'
    '发现值得记录或分享的内容时，截取能体现其有趣之处的画面。不必为了截图而截图，没有合适的发现也如实说明。'
    '不要只停留在标题或搜索结果；了解具体内容，区分实际看到的事实与自己的推测。'
    '最后形成探索笔记：实际进行了什么操作、发现了什么、具体有趣在哪里、重要细节、对应来源链接、值得保留的截图路径，以及自己的感受或想法。')

DEFAULT = {'enabled': False, 'every': 10, 'timeout_seconds': 420}


def config(root):
    p = Path(root) / 'browser-interest-config.json'
    cfg = DEFAULT | (json.loads(p.read_text('utf8')) if p.exists() else {})
    if type(cfg['every']) is not int or cfg['every'] < 1 or type(cfg['timeout_seconds']) is not int or not 30 <= cfg['timeout_seconds'] <= 480:
        raise ValueError('Invalid browser exploration cadence or timeout')
    return cfg


def initialize(memory):
    with closing(memory.connect()) as db, db:
        db.executescript('''CREATE TABLE IF NOT EXISTS exploration_routes(
            batch TEXT PRIMARY KEY, ordinal INTEGER UNIQUE, mode TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS browser_explorations(
            batch TEXT PRIMARY KEY,status TEXT,media TEXT,error TEXT,updated REAL);''')


def route(memory, batch):
    cfg = config(memory.root)
    with closing(memory.connect()) as db, db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT mode FROM exploration_routes WHERE batch=?', (batch,)).fetchone()
        if row:
            return row[0] if cfg['enabled'] else 'normal'
        ordinal = db.execute('SELECT coalesce(max(ordinal),0)+1 FROM exploration_routes').fetchone()[0]
        mode = 'browser' if cfg['enabled'] and ordinal % cfg['every'] == 0 else 'normal'
        db.execute('INSERT INTO exploration_routes VALUES(?,?,?,?)', (batch, ordinal, mode, time.time()))
        return mode


def workspace(root, batch):
    return Path(root) / 'browser-explorations' / hashlib.sha256(batch.encode()).hexdigest()[:24]


def media(memory, batch):
    with closing(memory.connect()) as db:
        row = db.execute("SELECT media FROM browser_explorations WHERE batch=? AND status='explored'", (batch,)).fetchone()
    if not row:
        return []
    # This validator belongs to the shared mouth runtime, not the plugin package.
    # main.py places ROOT on sys.path and uses this same absolute module.
    from image_library import checked_image
    result = []
    for item in json.loads(row[0] or '[]'):
        try:
            checked_image(item['path'])
        except (OSError, ValueError):
            continue
        result.append(item)
    return result


async def explore(memory, packet, public, memories, persona, external_rule):
    root = Path(memory.root)
    batch = packet['batch_id']
    work = workspace(root, batch)
    work.mkdir(parents=True, exist_ok=True, mode=0o700)
    # A failed/restarted attempt must never borrow the previous browser's evidence.
    for name in ('manifest.json', 'run.jsonl', 'diagnostics.log'):
        old = work / name
        if old.exists():
            old.replace(work / (name + '.previous'))
    # Preserve exactly the normal exploration's two background blocks.
    task = json.dumps({'自身信息与兴趣': public, '全局记忆': memories}, ensure_ascii=False)+'\n\n'+personality_tail(persona,root)
    system = external_rule + '\n' + BROWSER_TASK + '\n【人格资料】\n' + persona
    (work / 'input.json').write_text(json.dumps({'system': system, 'prompt': task}, ensure_ascii=False), 'utf8')
    api = json.loads((root / 'api.json').read_text('utf8'))
    env = {**os.environ, 'HOME': str(root / 'browser-dsh'), 'DSH_HOME': str(root / 'browser-dsh'),
        'PATH': '/opt/node22/bin:/usr/bin:/bin', 'DEEPSEEK_API_KEY': api['api_key'],
        'DSH_BROWSER_SYSTEM': system, 'BROWSER_EXPLORATION_WORK': str(work),
        'PLAYWRIGHT_BROWSERS_PATH': '/opt/interest-browser-engine', 'DSH_PERMISSION_MODE': 'danger-full-access'}
    with closing(memory.connect()) as db, db:
        db.execute('INSERT OR REPLACE INTO browser_explorations VALUES(?,\'running\',NULL,NULL,?)', (batch, time.time()))
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec('/opt/dsh/bin/dsh', '--profile', 'interest-browser', '--json', '-',
            cwd=work, env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, start_new_session=True)
        stdout, stderr = await asyncio.wait_for(proc.communicate(task.encode()), config(root)['timeout_seconds'])
        (work / 'run.jsonl').write_bytes(stdout)
        (work / 'diagnostics.log').write_bytes(stderr[-24000:])
        events = []
        for line in stdout.decode('utf8', 'replace').splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
        finals = [e for e in events if e.get('type') == 'final']
        final = finals[-1] if finals else {}
        report = final.get('text') or final.get('content') or final.get('answer') or ''
        if proc.returncode or not isinstance(report, str) or not report.strip():
            raise ValueError('DSH browser run did not complete; inspect isolated diagnostics')
        manifest = json.loads((work / 'manifest.json').read_text('utf8'))
        if not manifest.get('events'):
            raise ValueError('No actual browser activity recorded')
        incoming = root / 'incoming-images'
        incoming.mkdir(exist_ok=True, mode=0o700)
        images = []
        for item in manifest.get('screenshots', [])[:5]:
            source = Path(item['path']).resolve()
            if source.parent != work.resolve() or not source.name.startswith('shot-') or source.suffix != '.jpg':
                raise ValueError('Screenshot path not generated by this browser run')
            data = source.read_bytes()
            if not data.startswith(b'\xff\xd8\xff') or not 0 < len(data) <= 4 * 1024 * 1024:
                raise ValueError('Invalid screenshot bytes or size')
            target = incoming / ('browse-' + work.name + '-' + source.name)
            target.write_bytes(data)
            report = report.replace(str(source), str(target))
            image = {'kind': 'incoming', 'path': str(target),
                'description': '本轮实际浏览网页的截图：' + item.get('title', '')[:120] + '；' + item.get('note', '')[:300],
                'source_url': item['url']}
            images.append(image)
        appendix = '\n\n【程序核验的本轮截图】\n' + json.dumps(images, ensure_ascii=False) if images else ''
        memory.record(packet, 'dsh_browser_exploration', {'pages': [e.get('url') for e in manifest['events']], 'screenshots': images}, report + appendix)
        with closing(memory.connect()) as db, db:
            db.execute("UPDATE browser_explorations SET status='explored',media=?,updated=? WHERE batch=?",
                (json.dumps(images, ensure_ascii=False), time.time(), batch))
        return report + appendix
    except BaseException as exc:
        if proc and proc.returncode is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except asyncio.TimeoutError:
                os.killpg(proc.pid, signal.SIGKILL)
                await proc.wait()
        with closing(memory.connect()) as db, db:
            db.execute("UPDATE browser_explorations SET status='failed',error=?,updated=? WHERE batch=?", (type(exc).__name__ + ': ' + str(exc)[:300], time.time(), batch))
        raise

    finally:
        await asyncio.to_thread(usage_meter.import_dsh)
