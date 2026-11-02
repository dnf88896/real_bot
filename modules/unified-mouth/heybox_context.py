"""Observed Heybox targets and their root-floor relationships, separate from memory."""
import json,sqlite3,time,re
from pathlib import Path
from contextlib import closing
if __package__:from . import heybox_refs as refs
else:import heybox_refs as refs
TTL=24*3600

def connect(root):
    db=sqlite3.connect(Path(root)/'heybox-context.sqlite3',timeout=20);db.row_factory=sqlite3.Row
    db.executescript('''CREATE TABLE IF NOT EXISTS posts(id TEXT PRIMARY KEY,data TEXT,seen REAL);
    CREATE TABLE IF NOT EXISTS comments(post TEXT,id TEXT,root TEXT,data TEXT,seen REAL,PRIMARY KEY(post,id));''');db.commit();return db

def remember(root,pid,detail,comments,now=None):
    pid=refs.target(pid)[0];now=time.time() if now is None else now
    if detail.get('post_id') and str(detail['post_id'])!=pid:raise ValueError('Heybox post identity mismatch')
    post={k:v for k,v in detail.items() if k!='comments'}
    with closing(connect(root)) as db,db:
        db.execute('INSERT OR REPLACE INTO posts VALUES(?,?,?)',(pid,json.dumps(post,ensure_ascii=False),now))
        def walk(items,root_id=None,depth=0):
            if depth>8:return
            for item in items:
                cid=str(item.get('comment_id') or '')
                if not re.fullmatch(refs.ID,cid):continue
                parent=root_id or cid
                value={k:v for k,v in item.items() if k not in ('child_comments','评论目标编号','目标评论编号')}
                db.execute('INSERT OR REPLACE INTO comments VALUES(?,?,?,?,?)',(pid,cid,parent,json.dumps(value,ensure_ascii=False),now))
                walk(item.get('child_comments') or [],parent,depth+1)
        walk(comments)
        db.execute('DELETE FROM comments WHERE seen<?',(now-7*TTL,));db.execute('DELETE FROM posts WHERE seen<?',(now-7*TTL,))

def cached(root,pid,cid='',now=None):
    path=Path(root)/'heybox-context.sqlite3';now=time.time() if now is None else now
    if not path.exists():return None,None
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=20)) as db:
        row=db.execute('SELECT data FROM posts WHERE id=? AND seen>=?',(pid,now-TTL)).fetchone()
        if not row:return None,None
        post=json.loads(row[0]);comment=None
        if cid:
            c=db.execute('SELECT root,data FROM comments WHERE post=? AND id=? AND seen>=?',(pid,cid,now-TTL)).fetchone()
            if c and db.execute('SELECT 1 FROM comments WHERE post=? AND id=? AND seen>=?',(pid,c[0],now-TTL)).fetchone():comment={**json.loads(c[1]),'root_comment_id':c[0]}
        return post,comment

def require(root,value):
    pid,cid=refs.target(value);post,comment=cached(root,pid,cid)
    if not post or (cid and not comment):raise ValueError('请先读取小黑盒帖子及目标评论；资料不存在或已过期时重新读取对应页面')
    return pid,cid,post,comment

def brief(pid,post):
    author=post.get('author') or {}
    return {'帖子ID':pid,'标题':post.get('title',''),'作者':author.get('name') or author.get('username') or '', '作者UID':str(author.get('user_id') or author.get('userid') or ''), '链接':refs.post_url(pid)}

def targets(root,wanted):
    if not wanted:return []
    path=Path(root)/'heybox-account.json'
    if not path.exists():return []
    cfg=json.loads(path.read_text('utf8'));account=str(cfg.get('account_id',''))
    if not cfg.get('enabled') or not cfg.get('comments_enabled') or not re.fullmatch(refs.ID,account):return []
    result=[]
    for value in sorted(set(wanted)):
        try:pid,cid,post,comment=require(root,value)
        except ValueError:continue
        result.append({'platform':'heybox','account':account,'chat_type':'comment','id':refs.canonical(value),
                       'name':('小黑盒评论回复' if cid else '小黑盒帖子评论')+' '+str(post.get('title',''))[:80],
                       'remark':str((comment or {}).get('content',''))[:120]})
    return result
