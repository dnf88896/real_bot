"""Read-only contact directory for expression and recipient validation."""
import sqlite3
from pathlib import Path
DB=Path('/opt/astrbot-mouth/basic.sqlite3')

def directory(account):
    if not DB.exists():return []
    db=sqlite3.connect(DB.resolve().as_uri()+'?mode=ro',uri=True)
    try:
        people=[{'platform':'qq','account':str(account),'chat_type':'private','id':uid,'name':name or 'QQ '+uid,'remark':remark or ''} for uid,name,remark in db.execute('SELECT user_id,nickname,remark FROM contacts WHERE account=? AND user_id!=? ORDER BY user_id',(str(account),str(account)))]
        groups=[{'platform':'qq','account':str(account),'chat_type':'group','id':gid,'name':name or '群 '+gid} for gid,name in db.execute('SELECT group_id,name FROM groups WHERE account=? ORDER BY group_id',(str(account),))]
        return people+groups
    finally:db.close()

def with_current(packet,include_comment_targets=False):
    scene=packet['scene'];result=directory('100000002' if scene['platform']!='qq' else scene['account'])
    from platform_bridge import contacts,post_targets
    result += contacts()+post_targets()
    from wechat_bridge import directory as wechat_directory
    result += wechat_directory()
    from heybox_sender import targets as heybox_targets
    result += heybox_targets()
    if include_comment_targets:
        import bilibili_video
        from bilibili_refs import target
        wanted=set()
        for scope in packet.get('output_scopes',[]):
            if scope['platform']=='bilibili' and scope['chat_type']=='comment':
                try:bv,rpid=target(scope['target'])
                except ValueError:continue
                wanted.add(bv+(':'+rpid if rpid else ''))
        result += bilibili_video.targets(Path('/opt/astrbot-mouth'),wanted)
        from heybox_context import targets as heybox_comment_targets
        wanted_heybox={scope['target'] for scope in packet.get('output_scopes',[]) if scope['platform']=='heybox' and scope['chat_type']=='comment'}
        result += heybox_comment_targets(Path('/opt/astrbot-mouth'),wanted_heybox)
    if scene.get('chat_type')=='autonomous':return result
    item={'platform':scene['platform'],'account':str(scene['account']),'chat_type':scene['chat_type'],'id':str(scene['conversation']),'name':'当前对话'}
    if not any(all(str(r.get(k))==str(item[k]) for k in ('platform','account','chat_type','id')) for r in result):result.append(item)
    return result
