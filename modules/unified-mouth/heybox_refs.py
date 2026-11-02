"""Platform-scoped Heybox post/comment identifiers. No network requests."""
import re
from urllib.parse import urlsplit,parse_qs

ID=r'[1-9][0-9]*'
HOSTS={'www.xiaoheihe.cn','xiaoheihe.cn','api.xiaoheihe.cn'}

def target(value):
    value=str(value or '').strip()
    match=re.fullmatch(r'(?:H评:)?('+ID+r')(?::('+ID+r'))?',value)
    if match:
        if value.startswith('H评:') and not match[2]:raise ValueError('Comment number must include both IDs')
        return match[1],match[2] or ''
    url=urlsplit(value)
    if url.scheme not in ('http','https') or url.hostname not in HOSTS or url.username or url.password or url.port not in (None,80,443):raise ValueError('Invalid Heybox target')
    match=re.fullmatch(r'/app/bbs/link/('+ID+r')/?',url.path)
    query=parse_qs(url.query)
    if match:pid=match[1]
    elif url.path in ('/v3/bbs/app/api/web/share','/bbs/app/api/web/share') and len(query.get('link_id',[]))==1:pid=query['link_id'][0]
    else:raise ValueError('Unsupported Heybox post link')
    cid=query.get('comment_id',[''])[0]
    if not re.fullmatch(ID,pid) or (cid and not re.fullmatch(ID,cid)):raise ValueError('Invalid Heybox native ID')
    return pid,cid

def canonical(value):
    pid,cid=target(value);return pid+(':'+cid if cid else '')

def post_url(pid):
    if not re.fullmatch(ID,str(pid)):raise ValueError('Invalid Heybox post ID')
    return 'https://www.xiaoheihe.cn/app/bbs/link/'+str(pid)

def number(pid,cid):
    if not re.fullmatch(ID,str(pid)) or not re.fullmatch(ID,str(cid)):raise ValueError('Invalid Heybox comment ID')
    return 'H评:'+str(pid)+':'+str(cid)

def reference(text):
    text=str(text or '')
    match=re.search(r'H评:'+ID+r':'+ID,text)
    if match:return (*target(match[0]),1)
    for found in re.finditer(r'https?://[^\s<>"\x27]+',text):
        value=found[0].rstrip('。，,；;！!？?）)]}')
        try:pid,cid=target(value)
        except ValueError:continue
        page=int(parse_qs(urlsplit(value).query).get('page',['1'])[0])
        if not 1<=page<=100:raise ValueError('Heybox page out of range')
        return pid,cid,page
    match=re.fullmatch(r'\s*(?:小黑盒|小黒盒|heybox|xiaoheihe)\s*('+ID+r')\s*',text,re.I)
    return (match[1],'',1) if match else None
