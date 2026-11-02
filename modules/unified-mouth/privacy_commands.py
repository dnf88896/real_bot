"""Private-chat control plane. Commands and receipts never enter chat memory."""
import contextlib
import fcntl
import hashlib
import json
import logging
import os
import sqlite3
import time
from pathlib import Path

import private_chat_state as privacy

COMMANDS={'/隐私':'toggle','/隐私 开':'enable','/隐私 关':'disable','/隐私 状态':'status'}
SUPPORTED={'qq','wechat','bilibili'}


def text(raw):
    parts=raw.get('message')
    if isinstance(parts,str):return parts.strip()
    if not isinstance(parts,list) or not parts:return None
    if any(not isinstance(p,dict) or p.get('type')!='text' or
           not isinstance((p.get('data') or {}).get('text'),str) for p in parts):return None
    return ''.join(p['data']['text'] for p in parts).strip()


def operation(raw):
    return COMMANDS.get(text(raw))


def connect(root):
    path=Path(root)/'privacy-commands.sqlite3'
    db=sqlite3.connect(path,timeout=20);db.row_factory=sqlite3.Row
    os.chmod(path,0o600)
    db.executescript('''PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS commands(
          id TEXT PRIMARY KEY,scope TEXT,platform TEXT,account TEXT,conversation TEXT,
          source_id TEXT,operation TEXT,target INTEGER,state_status TEXT,
          reply TEXT,delivery_status TEXT,receipt_id TEXT,error_type TEXT,created REAL,updated REAL);
        CREATE INDEX IF NOT EXISTS command_scope ON commands(scope,created);
        CREATE INDEX IF NOT EXISTS command_receipt ON commands(platform,account,receipt_id);''')
    return db


def is_echo(root, platform, account, raw):
    """Drop the gateway's own control receipts, including the pre-HTTP-return race."""
    sender=raw.get('sender') or {}
    own=(raw.get('post_type')=='message_sent' or raw.get('is_self') is True or
         str(raw.get('user_id',''))==str(account) or str(sender.get('user_id',''))==str(account))
    if not own or not (Path(root)/'privacy-commands.sqlite3').exists():return False
    value=text(raw)
    if value is None and isinstance(raw.get('raw_message'),str):value=raw['raw_message'].strip()
    mid=str(raw.get('message_id',''))
    with contextlib.closing(connect(root)) as db:
        return db.execute("""SELECT 1 FROM commands WHERE platform=? AND account=?
            AND delivery_status IN ('sending','sent','uncertain')
            AND ((receipt_id!='' AND receipt_id=?) OR reply=?) LIMIT 1""",
            (platform,str(account),mid,value)).fetchone() is not None


def notify(root, scene, reply, identity):
    """Reuse platform transport only, bypassing model/relay/output-memory paths."""
    parts=[{'type':'text','data':{'text':reply}}]
    destination={'platform':scene['platform'],'account':str(scene['account']),
                 'chat_type':'private','id':str(scene['conversation'])}
    if scene['platform']=='qq':
        from qq_sender import onebot,ACCOUNT
        if str(scene['account'])!=ACCOUNT:raise ValueError('QQ control account mismatch')
        return onebot('send_private_msg',{'user_id':int(scene['conversation']),'message':parts})
    if scene['platform']=='wechat':
        from wechat_sender import send
        return send(destination,parts,Path(root),delivery_id='privacy-control:'+identity)
    if scene['platform']=='bilibili':
        from bilibili_sender import send
        return send(destination,parts)
    raise ValueError('Unsupported control platform')


def handle(root, *, platform, account, kind, conversation, sender, message_id, raw, send=None):
    """True means consumed, even on an error: never fall through to a model.

    The journal stores only command metadata and fixed receipts, not chat text.
    A persisted target makes a replayed toggle idempotent across a process crash.
    """
    root=Path(root)
    if is_echo(root,platform,account,raw):return True
    op=operation(raw)
    if op is None:return False
    # This setting belongs to private conversations. Do not expose control
    # functionality in groups or let a self-echo configure another conversation.
    if (kind!='private' or platform not in SUPPORTED or str(sender)!=str(conversation)
            or str(sender)==str(account) or raw.get('post_type')=='message_sent' or raw.get('is_self') is True
            or str((raw.get('sender') or {}).get('user_id',''))==str(account)
            or message_id is None or not str(message_id)):
        return True
    scene=dict(platform=platform,account=str(account),chat_type='private',conversation=str(conversation))
    scope=privacy.scope(scene)
    identity=hashlib.sha256(json.dumps([scope,str(message_id)]).encode()).hexdigest()
    folder=root/'privacy-command-locks';folder.mkdir(mode=0o700,exist_ok=True)
    lock=folder/(hashlib.sha256(scope.encode()).hexdigest()+'.lock')
    with lock.open('a') as stream:
        os.chmod(lock,0o600);fcntl.flock(stream,fcntl.LOCK_EX)
        with contextlib.closing(connect(root)) as db:
            row=db.execute('SELECT * FROM commands WHERE id=?',(identity,)).fetchone()
            if row and row['delivery_status'] in ('sent','uncertain'):return True
            if row and row['delivery_status']=='sending':
                # A previous send may have succeeded. No blind resend.
                with db:db.execute("UPDATE commands SET delivery_status='uncertain',updated=? WHERE id=?",(time.time(),identity))
                return True
            if row is None:
                current=privacy.load(root)['sessions'].get(scope,{}).get('enabled',False)
                target=None if op=='status' else not current if op=='toggle' else op=='enable'
                now=time.time()
                # Commit before changing the state file, so replay uses this
                # exact desired target rather than toggling it a second time.
                with db:db.execute('INSERT INTO commands VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (identity,scope,platform,str(account),str(conversation),str(message_id),op,target,
                     'pending',None,'pending',None,None,now,now))
                row=db.execute('SELECT * FROM commands WHERE id=?',(identity,)).fetchone()
            if row['state_status']=='pending':
                try:
                    if row['operation']!='status':privacy.set_state(root,scene,bool(row['target']))
                    after=privacy.load(root)['sessions'].get(scope,{}).get('enabled',False)
                    if row['operation']!='status' and after!=bool(row['target']):
                        raise ValueError('Privacy state verification failed')
                    label='隐私' if after else '普通'
                    reply=('当前为' if row['operation']=='status' else '已转换为')+'（'+label+'）状态'
                    status='confirmed'
                    error=None
                except Exception as exc:
                    status='failed';error=type(exc).__name__
                    reply='隐私状态操作失败，未确认变更，请稍后查询。'
                with db:db.execute('UPDATE commands SET state_status=?,reply=?,error_type=?,updated=? WHERE id=?',
                    (status,reply,error,time.time(),identity))
            row=db.execute('SELECT * FROM commands WHERE id=?',(identity,)).fetchone()
            with db:db.execute("UPDATE commands SET delivery_status='sending',updated=? WHERE id=?",(time.time(),identity))
            try:
                receipt=(send or notify)(root,scene,row['reply'],identity)
                mid=str((receipt or {}).get('message_id',''))
                if not mid:raise ValueError('Control receipt missing confirmation')
                with db:db.execute("UPDATE commands SET delivery_status='sent',receipt_id=?,updated=? WHERE id=?",(mid,time.time(),identity))
            except Exception as exc:
                with db:db.execute("UPDATE commands SET delivery_status='uncertain',error_type=?,updated=? WHERE id=?",(type(exc).__name__,time.time(),identity))
                logging.getLogger(__name__).warning('Privacy control receipt outcome uncertain; no automatic resend')
    return True
