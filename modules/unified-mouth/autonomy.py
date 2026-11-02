"""One-shot global activity, without a source conversation or hidden reply target."""
import json
import secrets
import sqlite3
import time
import uuid
from contextlib import closing
from datetime import datetime, UTC
from pathlib import Path

if __package__:
    from . import wakefulness
    from .affect import affinity_description
else:
    import wakefulness
    from affect import affinity_description

TASK = '你是一个拥有统一记忆、人格和情绪的仿生人。现在没有需要你立即回复的消息。请结合当前时间、自身状态和全局记忆，自主决定此刻想做什么。你可以联系熟悉的人、参与群聊、发布动态、查找感兴趣的资料，或者保持沉默。合理考虑不同对象，不必每次联系同一个人。需要表达时，通过提交工具完成。'
SCENE = {'platform':'global','account':'unified','chat_type':'autonomous','conversation':'self'}
POLICY = '本次没有当前会话或默认收件人。表达时明确选择目录中的平台、操作和目标；保持沉默可提交空动作数组。可以先读取B站视频资料和评论，再选具体评论目标编号进行回复；没有依据时不猜测视频内容。目录资料是已有观察的简述，缺失不代表陌生，同名不代表同一身份。'


def is_autonomous(packet):
    return packet.get('internal_wake',{}).get('kind') == 'autonomous'


def config(root):
    p=Path(root)/'autonomy-config.json'
    return {'enabled':False,'min_delay_seconds':300,'max_delay_seconds':600} | (json.loads(p.read_text('utf8')) if p.exists() else {})


class Autonomy:
    def __init__(self,root):
        self.root=Path(root);self.path=self.root/'autonomy.sqlite3'
        with closing(self.connect()) as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS activities(id TEXT PRIMARY KEY,due REAL,status TEXT,created REAL,updated REAL,error TEXT)')

    def connect(self):
        db=sqlite3.connect(self.path,timeout=20);db.row_factory=sqlite3.Row;return db

    def schedule(self,now=None):
        """Explicit one-shot scheduling; never reschedule merely because it finished."""
        now=time.time() if now is None else now;cfg=config(self.root)
        low,high=cfg['min_delay_seconds'],cfg['max_delay_seconds']
        if type(low) is not int or type(high) is not int or not 1<=low<=high<=604800:
            raise ValueError('Invalid autonomous delay')
        with closing(self.connect()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute("SELECT id,due FROM activities WHERE status IN ('scheduled','queued') ORDER BY created LIMIT 1").fetchone()
            if old:return dict(old)
            wid='autonomous:'+uuid.uuid4().hex;due=now+low+secrets.randbelow(high-low+1)
            db.execute('INSERT INTO activities VALUES(?,?,?,?,?,NULL)',(wid,due,'scheduled',now,now))
        return {'id':wid,'due':due}

    def packet(self,wid):
        return {'batch_id':wid,'scene':dict(SCENE),'wake_depth':1,
                'internal_wake':{'kind':'autonomous','wake_id':wid,'intent':TASK},
                'new_messages':[{'id':0,'observation':{'internal_event':True,'platform':'global',
                    'account_id':'unified','chat_type':'autonomous','conversation_id':'self','sender_id':'self',
                    'received_at':datetime.now(UTC).isoformat(),'raw_event':{'message':[]}}}],
                'image_options':[],'engine':'astrbot'}

    def due(self,now=None):
        now=time.time() if now is None else now
        if not config(self.root)['enabled'] or not wakefulness.allowed(self.root,SCENE,now):return []
        packets=[]
        with closing(self.connect()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            for row in db.execute("SELECT * FROM activities WHERE (status='scheduled' AND due<=?) OR (status='queued' AND updated<?)",(now,now-90)).fetchall():
                # A crash after committing output must never generate another output.
                mouth=self.root/'mouth.sqlite3'
                if mouth.exists():
                    with closing(sqlite3.connect(mouth)) as out:
                        if out.execute("SELECT 1 FROM sqlite_master WHERE name='outputs'").fetchone() and out.execute('SELECT 1 FROM outputs WHERE batch_id=?',(row['id'],)).fetchone():
                            db.execute("UPDATE activities SET status='completed',updated=? WHERE id=?",(now,row['id']));continue
                db.execute("UPDATE activities SET status='queued',updated=? WHERE id=?",(now,row['id']))
                packets.append(self.packet(row['id']))
        return packets


def valid(root,wid,now=None):
    path=Path(root)/'autonomy.sqlite3'
    if not path.exists() or not config(root)['enabled'] or not wakefulness.allowed(root,SCENE,now):return False
    with closing(sqlite3.connect(path)) as db:
        row=db.execute('SELECT status FROM activities WHERE id=?',(wid,)).fetchone()
    return bool(row and row[0] in ('queued','completed'))


def finish(root,packet,committed,error=None):
    if not is_autonomous(packet) or packet["internal_wake"]["wake_id"].startswith("active:"):return
    with closing(Autonomy(root).connect()) as db,db:
        db.execute("UPDATE activities SET status=?,updated=?,error=? WHERE id=? AND status='queued'",
                   ('completed' if committed else 'failed',time.time(),str(error)[:200] if error else None,packet['internal_wake']['wake_id']))


def validate_expression(packet,scopes):
    if not is_autonomous(packet):return
    for scope in scopes:
        if scope['target'].strip() in ('当前会话','当前对话','对方'):raise ValueError('主动活动没有默认会话，请明确选择目录目标')


def directory(root,rows,affect):
    """Attach compact sourced profiles and platform-isolated affinity in pure code."""
    result=[];path=Path(root)/'basic.sqlite3'
    profiles={};styles={}
    if path.exists():
        with closing(sqlite3.connect(path)) as db:
            for heads,versions,column,dest in [('profile_heads','profile_versions','user_id',profiles),('group_style_heads','group_style_versions','group_id',styles)]:
                for account,uid,content in db.execute(f'SELECT h.account,h.{column},v.content FROM {heads} h JOIN {versions} v ON v.id=h.version_id'):
                    data=json.loads(content);summary=data.get('summary','')
                    if isinstance(summary,str) and summary:dest[(str(account),str(uid))]=summary[:300]+('…' if len(summary)>300 else '')
    for source in rows:
        row=dict(source);platform=row['platform'];kind=row['chat_type'];uid=str(row['id'])
        if kind=='private' and platform in ('qq','bilibili','wechat'):
            row.update(affect.identity_affinity(uid,platform,str(row['account'])))
            brief=profiles.get((str(row['account']),uid)) if platform=='qq' else None
            if brief:row['资料简述']=brief
        elif kind=='group':
            brief=styles.get((str(row['account']),uid))
            if brief:row['群聊风格简述']=brief
        result.append(row)
    return result


async def emit(context,packet):
    from astrbot.api.platform import AstrBotMessage, MessageMember, MessageType
    from astrbot.api.message_components import Plain
    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
    adapter=context.get_platform_inst('qq-unified')
    if adapter is None:return False
    message=AstrBotMessage();message.type=MessageType.FRIEND_MESSAGE
    message.self_id='100000002';message.session_id='activity:autonomous';message.message_id=packet['batch_id']
    message.timestamp=int(time.time());message.message_str=TASK;message.message=[Plain(TASK)]
    message.sender=MessageMember(user_id='activity-autonomous',nickname='自主活动')
    message.raw_message={'post_type':'message','message':[]}
    event=AiocqhttpMessageEvent(TASK,message,adapter.meta(),message.session_id,adapter.bot)
    event.set_extra('unified_recovery_packet',packet);event.set_extra('unified_internal_wake',True)
    await context.get_event_queue().put(event)
    return True


if __name__=='__main__':
    import argparse
    from zoneinfo import ZoneInfo
    parser=argparse.ArgumentParser(description='Schedule one global autonomous activity; never loop implicitly.')
    parser.add_argument('command',choices=['schedule','status'])
    parser.add_argument('--root',default='/opt/astrbot-mouth')
    args=parser.parse_args();scheduler=Autonomy(args.root)
    if args.command=='schedule':
        row=scheduler.schedule()
        print(json.dumps({**row,'北京时间':datetime.fromtimestamp(row['due'],ZoneInfo('Asia/Shanghai')).isoformat()},ensure_ascii=False))
    else:
        with closing(scheduler.connect()) as db:
            print(json.dumps({'enabled':config(args.root)['enabled'],'tasks':[dict(r) for r in db.execute('SELECT * FROM activities ORDER BY created DESC LIMIT 10')]},ensure_ascii=False))
