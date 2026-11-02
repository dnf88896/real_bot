"""Decode a small expression envelope; never obtain body text from the relay."""
import json

OPERATIONS = {'私信': 'private', '群聊': 'group', '发帖': 'post', '评论': 'comment'}
SUPPORTED = {('qq', 'private'), ('qq', 'group'), ('wechat', 'private'), ('wechat', 'group'), ('bilibili', 'private'), ('bilibili', 'post'), ('bilibili', 'comment'), ('heybox', 'post'), ('heybox', 'comment')}


def decode(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate output field')
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=unique)
    rows = value if isinstance(value, list) else [value]
    if not 1 <= len(rows) <= 20:
        raise ValueError('Expected 1..20 output actions')
    text = ''; scopes = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'平台', '操作', '目标', '内容'}:
            raise ValueError('Expected exactly 平台/操作/目标/内容')
        if any(not isinstance(v, str) or not v.strip() for v in row.values()):
            raise ValueError('All output fields must be nonempty strings')
        platform = {'微信': 'wechat','小黑盒':'heybox'}.get(row['平台'], row['平台']); kind = OPERATIONS.get(row['操作'])
        if (platform, kind) not in SUPPORTED:
            raise ValueError('Unsupported platform/operation')
        if text:
            text += '\n'
        start = len(text); text += row['内容']
        scopes.append({'start': start, 'end': len(text), 'platform': platform,
                       'chat_type': kind, 'target': row['目标']})
    if len(text) > 100000:
        raise ValueError('Expression too large')
    return text, scopes


def destinations(scope, contacts, scene):
    """Resolve exact identities before unique nickname keywords, within a platform."""
    choices = [r for r in contacts if r['platform'] == scope['platform']
               and r['chat_type'] == scope['chat_type']]
    target = scope['target'].strip()
    identity = lambda r: (r['platform'], str(r['account']), r['chat_type'], str(r['id']))
    if scope['chat_type']=='comment':
        if scope['platform']=='heybox':
            if __package__:from .heybox_refs import canonical
            else:from heybox_refs import canonical
            target=canonical(target)
        else:
            if __package__:from .bilibili_refs import target as video_target
            else:from bilibili_refs import target as video_target
            bv,rpid=video_target(target)
            target=bv+(':'+rpid if rpid else '')
    if target in ('当前会话', '当前对话', '对方'):
        matches = {identity(r) for r in choices if str(r['account']) == str(scene['account'])
                   and str(r['id']) == str(scene['conversation']) and r['platform'] == scene['platform']
                   and r['chat_type'] == scene['chat_type']}
    elif scope['chat_type'] == 'post' and target == '自己':
        matches = {identity(r) for r in choices if str(r['id']) == str(r['account'])}
    else:
        matches = {identity(r) for r in choices if any(target == str(r.get(k) or '')
                   for k in ('id', 'name', 'remark'))}
        if not matches:
            matches = {identity(r) for r in choices if any(target in str(r.get(k) or '')
                       for k in ('name', 'remark'))}
    if len(matches) != 1:
        raise ValueError('JSON target missing or ambiguous: ' + target)
    return matches


def validate_routes(plan, text, contacts, scene, scopes):
    for scope in scopes:
        destinations(scope, contacts, scene)
    for part in plan['segments']:
        if part['kind'] == 'omit':
            continue
        owners = [s for s in scopes if s['start'] <= part['start'] < part['end'] <= s['end']]
        dest = part['destination']
        if len(owners) != 1 or tuple(dest[k] for k in ('platform', 'account', 'chat_type', 'id')) not in destinations(owners[0], contacts, scene):
            raise ValueError('Segment contradicts JSON destination or crosses action boundary')
    for wake in plan.get('wakeups', []):
        if not any(s['start'] <= wake['start'] < wake['end'] <= s['end'] for s in scopes):
            raise ValueError('Wake intention crosses action boundary')


def separate(deliveries, scopes):
    """Two posts to the same feed are still two independently requested actions."""
    result = []
    for delivery in deliveries:
        previous = None
        for part in delivery['parts']:
            index = next(i for i, s in enumerate(scopes) if s['start'] <= part['start'] < part['end'] <= s['end'])
            if previous != index:
                result.append({'destination': delivery['destination'], 'parts': []})
            result[-1]['parts'].append(part)
            previous = index
    return result
