"""Conservative group quoting from immutable source events, without an API."""
import re
if __package__:
    from .message_semantics import mentions_self
else:
    from message_semantics import mentions_self


def candidate(packet, destination):
    scene = packet['scene']
    if packet.get('internal_wake') or scene.get('platform') != 'qq' or scene.get('chat_type') != 'group':
        return None
    if any(str(scene.get(a)) != str(destination.get(b)) for a,b in (
        ('platform','platform'),('account','account'),('chat_type','chat_type'),('conversation','id'))):
        return None
    sources = {}
    for item in packet.get('new_messages', []):
        raw = item['observation']; event = raw.get('raw_event', {})
        if (raw.get('internal_event') or event.get('post_type', 'message') != 'message'
                or raw.get('chat_type') != 'group'
                or str(raw.get('account_id')) != str(scene['account'])
                or str(raw.get('conversation_id')) != str(scene['conversation'])):
            return None
        mid = str(raw.get('message_id') or event.get('message_id') or '')
        if not re.fullmatch(r'-?\d+', mid) or int(mid) == 0:
            return None
        sources[mid] = item
    at = [i for i in sources.values() if mentions_self(i['observation'], scene['account'])]
    chosen = at[0] if len(at) == 1 else next(iter(sources.values())) if len(sources) == 1 else None
    if chosen is None:
        return None
    raw = chosen['observation']; event = raw.get('raw_event', {})
    parts = event.get('message', [])
    text = parts if isinstance(parts, str) else ''.join(
        p.get('data', {}).get('text', '') for p in parts if isinstance(p, dict) and p.get('type') == 'text')
    return {'message_id': str(raw.get('message_id') or event['message_id']),
            'sender_id': str(raw.get('sender_id', '')), 'source_id': chosen['id'],
            'rule': 'sole_at' if at else 'single_message', 'preview': text[:120] or '[非文字消息]'}


def first_text(db, batch, ordinal, destination, parts, original):
    if not any(p['kind'] == 'text' and original[p['start']:p['end']].strip() for p in parts):
        return False
    import json
    for raw_dest, raw_parts in db.execute(
            'SELECT destination,parts FROM relay_deliveries WHERE batch_id=? AND ordinal<? ORDER BY ordinal',
            (batch, ordinal)):
        if json.loads(raw_dest) == destination and any(
                p['kind'] == 'text' and original[p['start']:p['end']].strip() for p in json.loads(raw_parts)):
            return False
    return True


def attach(db, batch, ordinal, packet, destination, parts, original, message, preview=False):
    # SnowLuma may add the quoted author when reply and at coexist. Preserve
    # the explicit @ and omit automatic quoting to avoid an extra recipient.
    if any(p.get('type')=='at' for p in message):
        return message, None
    target = candidate(packet, destination)
    if not target or not first_text(db, batch, ordinal, destination, parts, original):
        return message, None
    if preview:
        # A group message ID is invalid in the owner's private conversation.
        prefix = {'type': 'text', 'data': {'text':
            '[拟引用群消息 ' + target['message_id'] + (' · 微信ID ' if destination['platform']=='wechat' else ' · QQ ') + target['sender_id'] + '] '
            + target['preview'] + '\n'}}
    else:
        # Only a reply segment: never synthesize an at segment for its author.
        prefix = {'type': 'reply', 'data': {'id': target['message_id']}}
    return [prefix] + message, target
