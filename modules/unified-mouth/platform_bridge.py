"""Platform identity and durable Bilibili ingress, without another reply model."""
import base64
import privacy_commands
import hashlib
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

ROOT = Path('/opt/astrbot-mouth')
PLATFORM_IDS = {'qq': 'qq-unified', 'bilibili': 'bilibili-unified', 'wechat': 'wechat-unified'}


def directory_db(root=ROOT):
    db = sqlite3.connect(Path(root)/'platform-directory.sqlite3', timeout=20)
    db.execute('CREATE TABLE IF NOT EXISTS contacts(platform TEXT,account TEXT,id TEXT,name TEXT,updated REAL,PRIMARY KEY(platform,account,id))')
    return db


def remember_contact(platform, account, uid, name, root=ROOT):
    with closing(directory_db(root)) as db, db:
        db.execute('INSERT INTO contacts VALUES(?,?,?,?,?) ON CONFLICT(platform,account,id) DO UPDATE SET name=excluded.name,updated=excluded.updated',
                   (platform,str(account),str(uid),name or platform+' UID '+str(uid),time.time()))


def contacts(root=ROOT):
    if not (Path(root)/'platform-directory.sqlite3').exists():
        return []
    with closing(directory_db(root)) as db:
        return [dict(platform=p,account=a,chat_type='private',id=uid,name=name,remark='')
                for p,a,uid,name in db.execute('SELECT platform,account,id,name FROM contacts')]


def post_targets(root=ROOT):
    path=Path(root)/'bilibili-account.json'
    if not path.exists():return []
    cfg=json.loads(path.read_text('utf8'))
    if not cfg.get('enabled') or not cfg.get('posting_enabled'):return []
    uid=str(cfg['account_id'])
    return [{'platform':'bilibili','account':uid,'chat_type':'post','id':uid,
             'name':'个人动态（发帖）','remark':'本人B站主页，公开发布文字或图文动态'}]


def canonical_bilibili(message):
    """Retain native payload; common fields describe observations, not QQ IDs."""
    raw = dict(message.raw_message)
    if '_unified_canonical' in raw:
        return raw['_unified_canonical']
    original = raw.copy()
    content = raw.get('content', {})
    if isinstance(content,str):
        content = json.loads(content or '{}')
    parts=[]
    for component in message.message:
        kind = getattr(component,'type','')
        kind = str(getattr(kind,'value',kind)).lower()
        if kind in ('plain','text'):
            parts.append({'type':'text','data':{'text':component.text}})
        elif kind=='image':
            data={'url':content.get('url'),'summary':'B站私信原图，内容未识别'}
            value=getattr(component,'file','') or ''
            if isinstance(value,str) and value.startswith('base64://'):
                from image_library import INCOMING,checked_image
                blob=base64.b64decode(value[9:],validate=True)
                if len(blob)>4*1024*1024:
                    data['summary']='原图超过当前4MB附件上限'
                else:
                    INCOMING.mkdir(parents=True,exist_ok=True)
                    path=INCOMING/(hashlib.sha256(blob).hexdigest()+'.jpg')
                    if not path.exists():
                        path.write_bytes(blob)
                    data['cached_path']=str(checked_image(str(path)))
            parts.append({'type':'image','data':data})
    uid=str(message.sender.user_id)
    name=message.sender.nickname or 'B站 UID '+uid
    canonical={'post_type':'message','message_type':'private','self_id':str(message.self_id),
               'user_id':uid,'message_id':str(message.message_id),'time':message.timestamp,
               'sender':{'user_id':uid,'nickname':name},'message':parts,'native_event':original}
    return canonical


def persist_bilibili(message, root=ROOT, generate=True):
    """Called before adapter ACK/enqueue; restart can recover a captured message."""
    from store import Store
    raw=canonical_bilibili(message)
    if privacy_commands.handle(root,platform='bilibili',account=str(message.self_id),kind='private',
            conversation=str(message.sender.user_id),sender=str(message.sender.user_id),
            message_id=str(message.message_id),raw=raw):return True
    from image_ocr import ImageOCR
    from image_vision import VisionEnricher
    raw=VisionEnricher(root,inner=ImageOCR(root)).enrich(raw)
    remember_contact('bilibili',message.self_id,message.sender.user_id,message.sender.nickname,root)
    batch,packet,duplicate=Store(root).capture(platform='bilibili',account=str(message.self_id),kind='private',
                        conversation=str(message.session_id),sender=str(message.sender.user_id),
                        message_id=str(message.message_id),raw=raw,generate=generate)
    if generate and not duplicate:
        with closing(sqlite3.connect(Path(root)/'social.sqlite3',timeout=20)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS queue(key TEXT PRIMARY KEY,scope TEXT,kind TEXT,arrival REAL,characters INTEGER,packet TEXT,status TEXT,anchor TEXT,error TEXT)')
            encoded=json.dumps(packet,ensure_ascii=False)
            db.execute("INSERT OR IGNORE INTO queue VALUES(?,?,?,?,?,?,'pending',NULL,NULL)",
                       (batch,json.dumps(packet['scene'],sort_keys=True),'private',time.time(),len(encoded),encoded))
    message.raw_message={**dict(message.raw_message),'_unified_canonical':raw}


async def emit_bilibili(context, packet):
    from astrbot.api.platform import AstrBotMessage,MessageMember,MessageType
    from astrbot.api.message_components import Plain
    adapter=context.get_platform_inst(PLATFORM_IDS['bilibili'])
    if adapter is None or not adapter.client:
        return False
    # Obtain the event class from the actual installed adapter package.
    import importlib
    event_class=importlib.import_module(adapter.__class__.__module__).BilibiliPlatformEvent
    scene=packet['scene'];message=AstrBotMessage()
    message.type=MessageType.FRIEND_MESSAGE;message.self_id=str(scene['account'])
    session=('activity:post:'+('heybox:' if scene['platform']=='heybox' else '')+str(scene['account'])) if scene['chat_type']=='post' else str(scene['conversation'])
    if scene['chat_type']=='comment':session='activity:comment:'+str(scene['account'])+':'+str(scene['conversation'])
    message.session_id=session;message.message_id=packet['batch_id']
    message.timestamp=int(time.time());message.message_str='内部重新处理'
    message.message=[Plain(message.message_str)]
    message.sender=MessageMember(user_id=('activity-post-'+str(scene['account'])) if scene['chat_type']=='post' else str(scene['conversation']),nickname='自己的发帖活动' if scene['chat_type']=='post' else '当前会话')
    if packet.get('notification_focus'):
        author=packet['notification_focus']['对应回复链'][-1]['作者']
        message.sender=MessageMember(user_id=author['UID'],nickname=author['名称'])
    if packet.get('internal_wake'):
        message.raw_message={'_unified_canonical':{'post_type':'message','message':[]}}
    else:
        raw=packet['new_messages'][0]['observation']['raw_event']
        message.raw_message={'_unified_canonical':raw}
    event=event_class(message.message_str,message,adapter.meta(),session,adapter.client)
    event.set_extra('unified_recovery_packet',packet)
    await context.get_event_queue().put(event)
    return True


async def emit_heybox(context,packet):
    """Internal event carrier; all actual writes remain in the Heybox relay."""
    from astrbot.api.platform import AstrBotMessage,MessageMember,MessageType
    from astrbot.api.message_components import Plain
    from astrbot.api.event import AstrMessageEvent
    from astrbot.core.platform.platform_metadata import PlatformMetadata
    scene=packet['scene'];message=AstrBotMessage()
    message.type=MessageType.FRIEND_MESSAGE;message.self_id=str(scene['account'])
    session='activity:'+scene['chat_type']+':heybox:'+str(scene['account'])+':'+str(scene['conversation'])
    message.session_id=session;message.message_id=packet['batch_id'];message.timestamp=int(time.time())
    message.message_str=packet['internal_wake']['intent'];message.message=[Plain(message.message_str)]
    message.sender=MessageMember(user_id=str(scene['account']),nickname='自己的小黑盒活动')
    if packet.get('heybox_notification_focus'):
        focus=packet['heybox_notification_focus'];actor=focus['触发者']
        message.sender=MessageMember(user_id=actor['UID'],nickname=actor['名称'])
    message.raw_message={'post_type':'message','message':[]}
    event=AstrMessageEvent(message.message_str,message,PlatformMetadata('heybox','小黑盒内部事件','heybox-unified'),session)
    event.set_extra('unified_recovery_packet',packet);event.set_extra('unified_internal_wake',True)
    await context.get_event_queue().put(event)
    return True


def source_platform(event):
    # Synthetic posting events use the existing native event carrier, but their
    # logical identity is always the persisted packet, not the transport adapter.
    packet=event.get_extra('unified_recovery_packet')
    if packet and packet.get('internal_wake',{}).get('kind')=='post_activity':
        return packet['scene']['platform']
    if event.get_platform_name()=='heybox':return 'heybox'
    if event.get_platform_name() == 'wechat_unified': return 'wechat'
    return 'bilibili' if event.get_platform_name()=='bilibili' else 'qq'


def display(observation):
    raw=observation.get('raw_event',{});sender=raw.get('sender',{})
    name=sender.get('nickname') or observation.get('sender_id','')
    platform=observation.get('platform','qq')
    label = (raw.get('group_name') or '群聊' + str(observation.get('conversation_id', ''))) if observation.get('chat_type') == 'group' else '私聊'
    return {'nickname':name,'source_label':f'[{platform}][{label}]{name}：'}
