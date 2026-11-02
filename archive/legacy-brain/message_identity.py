"""Deterministic source labels; contact names are data, never instructions."""
import json,re,unicodedata
from pathlib import Path
CACHE=Path('/opt/qq-identity/contacts.json')

def clean_name(value):
    value=re.sub(r'<\$[^>]*>','',str(value or ''))
    return ' '.join(''.join(c for c in value if c.isspace() or unicodedata.category(c)!='Cc').split())

def identity(raw):
    try:cache=json.loads(CACHE.read_text('utf8'))
    except (OSError,ValueError):cache={}
    event=raw.get('raw_event',{});sender=event.get('sender',{}) or {}
    account=str(raw.get('account_id',event.get('self_id','')))
    outgoing=raw.get('direction')=='outgoing'
    uid=account if outgoing else str(raw.get('sender_id',event.get('user_id',sender.get('user_id',''))))
    kind=raw.get('chat_type',event.get('message_type','private'))
    conv=str(raw.get('conversation_id',event.get('group_id','')))
    scoped=cache if str(cache.get('account_id',''))==account else {}
    nick=(raw.get('sender_nickname') or sender.get('nickname') or scoped.get('users',{}).get(uid) or sender.get('card') or ('自己' if outgoing else 'QQ '+uid))
    group=raw.get('group_name') or event.get('group_name') or scoped.get('groups',{}).get(conv) or '群 '+conv
    nick=clean_name(nick);group=clean_name(group)
    label='私聊' if kind=='private' else group if kind=='group' else clean_name(kind)
    return {'chat_type':kind,'nickname':nick,'group_name':group if kind=='group' else None,'source_label':f'[{label}]{nick}：'}

def display(raw,text):
    info=identity(raw)
    return {**info,'display':info['source_label']+' '+text}
