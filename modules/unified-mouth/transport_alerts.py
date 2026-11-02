"""Out-of-band transport alerts. Never enter model or conversation memory."""
import hashlib,json,os,sqlite3,time
from contextlib import closing
from pathlib import Path


def connect(root):
    path=Path(root)/'transport-alerts.sqlite3'
    db=sqlite3.connect(path,timeout=10)
    db.execute('CREATE TABLE IF NOT EXISTS alerts(id TEXT PRIMARY KEY,text TEXT,state TEXT,message_id TEXT,created REAL,error TEXT)')
    db.commit();path.chmod(0o600)
    if os.geteuid()==0:os.chown(path,990,990)
    return db


def emit(root,notice_id,target):
    text='【程序故障】微信向「'+target+'」发送已暂停：找不到目标会话或顶部昵称核验失败。未发送内容已保留，后续气泡不再反复滚动寻找；处理后可恢复该目标任务。'
    with closing(connect(root)) as db:
        with db:
            inserted=db.execute("INSERT OR IGNORE INTO alerts VALUES(?,?,'sending',NULL,?,NULL)",(notice_id,text,time.time())).rowcount
        if not inserted:return
        try:
            from qq_sender import onebot
            receipt=onebot('send_private_msg',{'user_id':100000001,'message':[{'type':'text','data':{'text':text}}]})
            with db:db.execute("UPDATE alerts SET state='sent',message_id=? WHERE id=?",(str(receipt['message_id']),notice_id))
        except Exception as exc:
            with db:db.execute("UPDATE alerts SET state='uncertain',error=? WHERE id=?",(type(exc).__name__,notice_id))


def is_echo(root,raw):
    account=str(raw.get('self_id',''))
    own=raw.get('post_type')=='message_sent' or str(raw.get('user_id',''))==account or str(raw.get('sender',{}).get('user_id',''))==account
    if account!='100000002' or not own:return False
    path=Path(root)/'transport-alerts.sqlite3'
    if not path.exists():return False
    parts=raw.get('message',raw.get('raw_message',''))
    text=parts if isinstance(parts,str) else ''.join(p.get('data',{}).get('text','') for p in parts if p.get('type')=='text')
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        return db.execute('SELECT 1 FROM alerts WHERE message_id=? OR text=?',(str(raw.get('message_id','')),text)).fetchone() is not None
