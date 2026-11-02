"""Heybox own posts and observed-target comments. One create request, no retry."""
import base64,hashlib,io,json,re,sqlite3,time,urllib.request,urllib.error
if __package__:
    from . import heybox_refs as refs,heybox_context as context
else:
    import heybox_refs as refs,heybox_context as context
from pathlib import Path
from contextlib import closing
from PIL import Image

BASE='http://127.0.0.1:8010'
TITLE='zsbd'
ROOT=Path('/opt/astrbot-mouth')

class Rejected(ValueError):definite_failure=True
class Uncertain(RuntimeError):pass

def config(root=ROOT):
    p=Path(root)/'heybox-account.json'
    return {'enabled':False,'posting_enabled':False,'comments_enabled':False,'account_id':'','nickname':''} | (json.loads(p.read_text('utf8')) if p.exists() else {})

def allowed(destination,root=ROOT):
    cfg=config(root);account=str(cfg['account_id'])
    if not cfg['enabled'] or not account.isascii() or not account.isdecimal() or int(account)<=0:raise Rejected('Heybox account is disabled')
    if destination.get('platform')!='heybox' or str(destination.get('account'))!=account:raise Rejected('Heybox account mismatch')
    if destination.get('chat_type')=='post':
        if not cfg['posting_enabled'] or str(destination.get('id'))!=account:raise Rejected('Heybox own posting is disabled')
    elif destination.get('chat_type')=='comment':
        if not cfg['comments_enabled']:raise Rejected('Heybox comments are disabled')
        try:context.require(root,destination['id'])
        except ValueError as exc:raise Rejected(str(exc)) from None
    else:raise Rejected('Unsupported Heybox operation')
    return account

def targets(root=ROOT):
    cfg=config(root)
    try:account=allowed({'platform':'heybox','account':str(cfg['account_id']),'chat_type':'post','id':str(cfg['account_id'])},root)
    except Rejected:return []
    return [{'platform':'heybox','account':account,'chat_type':'post','id':account,
             'name':'小黑盒本人发帖','remark':(cfg.get('nickname') or '')+'；标题固定zsbd，不指定分区'}]

def request(path,body=None):
    if path not in ('/api/profile?limit=1','/api/posts') and not re.fullmatch(r'/api/posts/[1-9][0-9]*/comments',path):raise Rejected('Unsupported Heybox bridge operation')
    req=urllib.request.Request(BASE+path,data=json.dumps(body,ensure_ascii=False).encode('utf8') if body is not None else None,
                               headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=180 if body is not None else 35) as response:
            raw=response.read(8*1024*1024+1)
            if len(raw)>8*1024*1024:raise Uncertain('Heybox bridge response too large')
            value=json.loads(raw)
    except urllib.error.HTTPError as exc:
        # Explicit parameter/authentication rejections did not create a post.
        reason=exc.read(1500).decode('utf8','replace')
        if exc.code in (400,401,403,404,422):raise Rejected('Heybox rejected request HTTP '+str(exc.code)+': '+reason[:600]) from None
        raise Uncertain('Heybox result unknown HTTP '+str(exc.code)) from None
    except (urllib.error.URLError,TimeoutError,OSError,ValueError) as exc:
        raise Uncertain('Heybox bridge result unavailable: '+type(exc).__name__) from None
    if not isinstance(value,dict) or value.get('ok') is not True or not isinstance(value.get('data'),dict):
        raise Uncertain('Invalid Heybox bridge response envelope')
    return value['data']

def profile(root=ROOT,call=request):
    cfg=config(root);data=call('/api/profile?limit=1');user=data.get('user') or {}
    account=str(user.get('userid') or '')
    if not account or account!=str(cfg['account_id']):raise Rejected('Heybox logged-in identity differs from configured account')
    return account,str(user.get('username') or user.get('nickname') or user.get('name') or '')

def content(message,root):
    texts=[];images=[]
    for part in message:
        if part.get('type')=='text':texts.append(part['data']['text'])
        elif part.get('type')=='image':
            value=part['data'].get('file','')
            if not value.startswith('base64://'):raise Rejected('Heybox images require validated local bytes')
            try:
                blob=base64.b64decode(value[9:],validate=True)
                if not 0<len(blob)<=4*1024*1024:raise ValueError('size')
                with Image.open(io.BytesIO(blob)) as image:
                    fmt=image.format;image.verify()
                suffix={'PNG':'.png','JPEG':'.jpg','GIF':'.gif','WEBP':'.webp'}[fmt]
            except (ValueError,KeyError,OSError,Image.DecompressionBombError):raise Rejected('Invalid Heybox image') from None
            cache=Path(root)/'heybox-outgoing';cache.mkdir(mode=0o750,parents=True,exist_ok=True)
            path=cache/(hashlib.sha256(blob).hexdigest()+suffix)
            if not path.exists():
                with path.open('xb') as f:f.write(blob)
                path.chmod(0o600)
            elif path.read_bytes()!=blob:raise Rejected('Heybox image cache is inconsistent')
            images.append(str(path))
        else:raise Rejected('Unsupported Heybox post component')
    body=''.join(texts)
    if not body.strip() and not images:raise Rejected('Empty Heybox post')
    if len(images)>9:raise Rejected('At most nine post images')
    return {'title':TITLE,'text':body,'images':images,'post_kind':'image_text',
            'communities':[],'hashtags':[],'draft':False,'dry_run':False}

def prepare(destination,message,root):
    payload=content(message,root)
    if destination['chat_type']=='post':return '/api/posts',payload,None
    pid,cid,post,comment=context.require(root,destination['id'])
    if len(payload['images'])>2:raise Rejected('At most two comment images')
    if re.search(r'H评:[0-9]+:[0-9]+',payload['text']):raise Rejected('Internal comment number is not comment body text')
    body={'text':payload['text'],'images':payload['images'],'dry_run':False,
          'reply_id':cid if cid else '-1','root_id':comment['root_comment_id'] if cid else '-1'}
    return '/api/posts/'+pid+'/comments',body,(pid,cid,post,comment)

def preflight(destination,message,root=ROOT,call=request):
    allowed(destination,root);path,payload,info=prepare(destination,message,root);profile(root,call)
    return call(path,{**payload,'dry_run':True})

def connect(root):
    db=sqlite3.connect(Path(root)/'heybox-posts.sqlite3',timeout=20);db.row_factory=sqlite3.Row
    db.execute('''CREATE TABLE IF NOT EXISTS deliveries(id TEXT PRIMARY KEY,status TEXT,request_json TEXT,receipt_json TEXT,error TEXT,created REAL,updated REAL)''');db.commit()
    return db

def send(destination,message,root=ROOT,delivery_id=None,call=request):
    account=allowed(destination,root);path,payload,info=prepare(destination,message,root)
    if not isinstance(delivery_id,str) or not delivery_id:raise Rejected('Persistent delivery identity required')
    identity_data={'account':account,'payload':payload}
    if info is not None:identity_data['path']=path
    identity=json.dumps(identity_data,ensure_ascii=False,sort_keys=True)
    with closing(connect(root)) as db,db:
        db.execute('BEGIN IMMEDIATE');old=db.execute('SELECT * FROM deliveries WHERE id=?',(delivery_id,)).fetchone()
        if old:
            if old['request_json']!=identity:raise Rejected('Heybox delivery identity reused with different content')
            if old['status']=='verified':return json.loads(old['receipt_json'])
            if old['status']=='rejected':raise Rejected('Heybox delivery was rejected; no automatic retry')
            raise Uncertain('Heybox delivery already attempted; read back before any further action')
        db.execute("INSERT INTO deliveries VALUES(?,'reserved',?,NULL,NULL,?,?)",(delivery_id,identity,time.time(),time.time()))
    try:
        try:profile(root,call)
        except Exception as exc:raise Rejected('Heybox pre-send identity check failed: '+str(exc)) from None
        result=call(path,payload)
        native_id=str(result.get('comment_id' if info is not None else 'link_id') or '')
        if result.get('accepted') is not True:raise Uncertain('Heybox create response did not confirm acceptance')
        if not native_id.isascii() or not native_id.isdecimal() or int(native_id)<=0:raise Uncertain('Heybox accepted without usable ID; do not repeat')
        with closing(connect(root)) as db,db:
            db.execute("UPDATE deliveries SET status='accepted_unverified',receipt_json=?,updated=? WHERE id=?",(json.dumps(result,ensure_ascii=False),time.time(),delivery_id))
        if result.get('verified') is not True or (info is None and result.get('is_draft') is not False):
            raise Uncertain('Heybox accepted but readback unverified; native_id='+native_id)
        if info is None:
            receipt={'accepted':True,'verified':True,'message_id':native_id,'post_url':refs.post_url(native_id),
                     'post_title':TITLE,'post_kind':'image_text','platform_receipt':{k:result[k] for k in ('accepted','verified','link_id','is_draft') if k in result}}
        else:
            pid,cid,post,comment=info
            receipt={'accepted':True,'verified':True,'message_id':native_id,'comment_id':native_id,'post_url':refs.post_url(pid),
                     'post_info':context.brief(pid,post),'platform_receipt':{'accepted':True,'verified':True,'comment_id':native_id}}
            if isinstance(result.get('readback_text'),str):receipt['delivered_text']=result['readback_text']
            if cid:receipt['replied_comment']={'评论ID':cid,'根评论ID':comment['root_comment_id'],'作者':(comment.get('author') or {}).get('name',''),'作者UID':str((comment.get('author') or {}).get('user_id') or ''),'正文':comment.get('content','')}
            if cid and comment.get('images'):receipt['replied_comment']['图片']=[x.get('url') for x in comment['images'] if isinstance(x,dict) and x.get('url')]
        with closing(connect(root)) as db,db:db.execute("UPDATE deliveries SET status='verified',receipt_json=?,error=NULL,updated=? WHERE id=?",(json.dumps(receipt,ensure_ascii=False),time.time(),delivery_id))
        return receipt
    except Exception as exc:
        with closing(connect(root)) as db,db:
            db.execute("UPDATE deliveries SET status=CASE WHEN status='accepted_unverified' THEN status ELSE ? END,error=?,updated=? WHERE id=?",('rejected' if getattr(exc,'definite_failure',False) else 'uncertain',type(exc).__name__+': '+str(exc)[:700],time.time(),delivery_id))
        raise
