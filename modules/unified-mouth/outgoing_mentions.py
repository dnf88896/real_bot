"""Translate marked original @ spans into QQ components, never invent targets."""
import re


def component(value, destination, call):
    if destination and destination.get('platform')=='wechat':
        from wechat_bridge import member
        return member(value,destination)
    literal = {'type':'text','data':{'text':value}}
    if not destination or destination.get('platform') != 'qq' or destination.get('chat_type') != 'group':
        return literal
    if not value.startswith('@') or value != value.strip():
        return literal
    target = value[1:]
    if re.fullmatch(r'[1-9][0-9]{4,11}', target):
        return {'type':'at','data':{'qq':target}}
    if not target or target.lower() in ('all','全体成员','所有人'):
        return literal
    try:
        members = call('get_group_member_list', {'group_id':int(destination['id'])})
        ids = {str(m['user_id']) for m in members
               if target in (m.get('nickname'),m.get('card')) and str(m.get('user_id','')).isdigit()}
    except Exception:
        return literal
    return {'type':'at','data':{'qq':ids.pop()}} if len(ids)==1 else literal


def preview(message):
    return [({'type':'text','data':{'text':(p['data'].get('text') or '@'+str(p['data']['qq'])) if p['data'].get('platform')=='wechat' else '[拟@QQ '+str(p['data']['qq'])+']'}}
             if p.get('type')=='at' else p) for p in message]


def spoken(message):
    return ['【语音】'+p['data']['text'] if p['type']=='voice' else p['data']['text'] if p['type']=='text' else p['data']['source'] if p['type']=='share_card' else p['data'].get('text') or '@'+str(p['data']['qq'])
            for p in message if p['type'] in ('text','at','share_card','voice')]
