"""Objective message metadata derived only from protocol segments."""
SELF_MENTION_TAG = '[此消息@了你]'


def mentions_self(observation, account=None):
    event = observation.get('raw_event', {})
    if observation.get('platform', 'qq') not in ('qq', 'wechat') or observation.get('internal_event') or event.get('post_type', 'message') != 'message':
        return False
    account = str(account if account is not None else observation.get('account_id', event.get('self_id', '')))
    parts = event.get('message', [])
    return bool(account) and isinstance(parts, list) and any(
        isinstance(p, dict) and p.get('type') == 'at'
        and str(p.get('data', {}).get('qq', '')) == account for p in parts)


def display_text(text, mentioned):
    return SELF_MENTION_TAG+' '+text if mentioned and not text.startswith(SELF_MENTION_TAG) else text
