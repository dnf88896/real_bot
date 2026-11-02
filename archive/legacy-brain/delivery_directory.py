"""Read-only contact directory for expression and recipient validation."""
import sqlite3
from pathlib import Path
DB=Path('/opt/qq-identity/basic.sqlite3')

def directory(account):
    if not DB.exists():return []
    db=sqlite3.connect(DB.resolve().as_uri()+'?mode=ro',uri=True)
    try:
        people=[{'platform':'qq','account':str(account),'chat_type':'private','id':uid,'name':name or 'QQ '+uid,'remark':remark or ''} for uid,name,remark in db.execute('SELECT user_id,nickname,remark FROM contacts WHERE account=? AND user_id!=? ORDER BY user_id',(str(account),str(account)))]
        groups=[{'platform':'qq','account':str(account),'chat_type':'group','id':gid,'name':name or '群 '+gid} for gid,name in db.execute('SELECT group_id,name FROM groups WHERE account=? ORDER BY group_id',(str(account),))]
        return people+groups
    finally:db.close()

def with_current(packet):
    scene=packet['scene'];result=directory(scene['account'])
    item={'platform':scene['platform'],'account':str(scene['account']),'chat_type':scene['chat_type'],'id':str(scene['conversation']),'name':'当前对话'}
    if not any(all(str(r.get(k))==str(item[k]) for k in ('platform','account','chat_type','id')) for r in result):result.append(item)
    return result
