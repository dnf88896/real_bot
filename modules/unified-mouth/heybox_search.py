"""小黑盒 keyword-routed reads: the five hottest posts, each with its first three floors.

Reads only, with a local observed-target cache; no platform writes; the local REST bridge
(xiaoheihe-cli, loopback 8010) is the only data source.
"""
import asyncio,json,re
from urllib.parse import quote
import aiohttp
if __package__:
    from . import heybox_refs as refs,heybox_context as context
else:
    import heybox_refs as refs,heybox_context as context

BASE='http://127.0.0.1:8010'
KEYWORDS=('小黑盒','小黒盒','xiaoheihe','heybox')
POSTS=5
FLOORS=3
REPLIES=5
TEXT_LIMIT=120


def mentions(text):
    value=str(text or '')
    lower=value.lower()
    return any((k in value) if not k.isascii() else (k in lower) for k in KEYWORDS)


def keyword(text):
    """Drop the platform word; whatever is left is what the user wants to search."""
    value=str(text or '')
    for word in KEYWORDS:
        value=re.sub(re.escape(word),' ',value,flags=re.I)
    return ' '.join(value.split()).strip(' \t\r\n，,。.:：;；!！?？、')


def comment_number(post_id,comment_id):return refs.number(post_id,comment_id)


def number_target(value):
    if not str(value or '').strip().startswith('H评:'):return None
    try:return refs.target(value)
    except ValueError:return None


def clip(value):
    text=' '.join(str(value or '').split())
    return text if len(text)<=TEXT_LIMIT else text[:TEXT_LIMIT]+'…'


def heat(info):
    return int(info.get('link_award_num') or 0)+int(info.get('comment_num') or 0)


def rank(items,limit=POSTS):
    """Only post rows; hottest first, measured by likes plus comment count."""
    posts=[row.get('info') or {} for row in items or [] if row.get('type')=='link']
    posts=[post for post in posts if str(post.get('linkid') or '').isdecimal()]
    return sorted(posts,key=lambda post:(heat(post),int(post.get('comment_num') or 0)),reverse=True)[:limit]


def reply_entry(post_id,comment):
    return {'作者':(comment.get('author') or {}).get('name',''),'正文':clip(comment.get('content')),
        '点赞':int(comment.get('likes') or 0),'评论目标编号':comment_number(post_id,comment.get('comment_id'))}


def floor_entry(post_id,comment,extra=None):
    entry={'楼层':comment.get('floor_num'),'作者':(comment.get('author') or {}).get('name',''),
        '正文':clip(comment.get('content')),'点赞':int(comment.get('likes') or 0),
        '时间':comment.get('time'),'评论目标编号':comment_number(post_id,comment.get('comment_id'))}
    if comment.get('is_top'):entry['置顶']=True
    replies=[reply_entry(post_id,row) for row in (comment.get('child_comments') or [])[:REPLIES] if str(row.get('comment_id') or '').isdecimal()]
    entry['回复']=replies
    more=max(0,(extra if extra is not None else int(comment.get('child_num') or 0))-len(replies))
    if more:entry['更多回复']=more
    return entry


def post_entry(info,detail=None,comments=None):
    post_id=str(info.get('linkid'))
    detail=detail or {}
    content=detail.get('title') or info.get('title') or ''
    return {'帖子ID':post_id,'标题':clip(content) if content else '',
        '作者':((detail.get('author') or {}).get('name') or (info.get('user') or {}).get('name') or ''),
        '链接':detail.get('url') or 'https://www.xiaoheihe.cn/app/bbs/link/'+post_id,
        '点赞':int(info.get('link_award_num') or 0),'评论数':int(info.get('comment_num') or 0),
        '摘要':clip(info.get('description') or detail.get('content')),
        '前三楼':[floor_entry(post_id,row) for row in (comments or [])[:FLOORS] if str(row.get('comment_id') or '').isdecimal()]}


def payload(query,posts,error=''):
    value={'平台':'小黑盒','关键词':query,'排序':'热度（点赞+评论数）','帖子':posts}
    if error:value['搜索失败']=error
    return json.dumps(value,ensure_ascii=False)


async def request(session,path,params=None):
    async with session.get(BASE+path,params=params,timeout=aiohttp.ClientTimeout(total=20)) as response:
        if response.status!=200:raise ValueError('Heybox read failed: '+str(response.status))
        value=await response.json(content_type=None)
    if not isinstance(value,dict) or value.get('success') is False:raise ValueError('Heybox read failed: '+str(value.get('message') if isinstance(value,dict) else ''))
    return value.get('data') or {}


async def collect(query,fetch=None,limit=POSTS,root=None):
    """search -> rank -> per post: detail + first floors. Read-only and bounded."""
    if fetch is None:
        async with aiohttp.ClientSession() as session:
            async def bound(path,params=None):return await request(session,path,params)
            return await collect(query,bound,limit,root)
    found=await fetch('/api/search',{'keyword':query,'kind':'post','limit':25})
    chosen=rank((found.get('result') or {}).get('items'),limit)
    semaphore=asyncio.Semaphore(4)
    async def one(info):
        post_id=str(info.get('linkid'))
        async with semaphore:
            try:
                detail,comments=await asyncio.gather(
                    fetch('/api/post/'+post_id),
                    fetch('/api/post/'+post_id+'/comments',{'page':1,'page_size':FLOORS}))
            except (ValueError,KeyError,TypeError,aiohttp.ClientError) as exc:
                entry=post_entry(info)
                entry['读取失败']=type(exc).__name__
                return entry
        if root is not None:context.remember(root,post_id,detail,comments.get('comments') or [])
        return post_entry(info,detail,comments.get('comments') or [])
    if not chosen:return []
    return list(await asyncio.gather(*[one(info) for info in chosen]))


async def read_post(root,pid,cid='',page=1,fetch=None):
    if fetch is None:
        async with aiohttp.ClientSession() as session:
            async def bound(path,params=None):return await request(session,path,params)
            return await read_post(root,pid,cid,page,bound)
    detail,comments=await asyncio.gather(fetch('/api/post/'+pid),fetch('/api/post/'+pid+'/comments',{'page':page,'page_size':10}))
    rows=comments.get('comments') or [];context.remember(root,pid,detail,rows)
    result={'平台':'小黑盒','帖子信息':context.brief(pid,detail),'正文':str(detail.get('content') or '')[:12000],
            '正文是否截断':len(str(detail.get('content') or ''))>12000,'评论页':page,
            '评论':[floor_entry(pid,row) for row in rows if str(row.get('comment_id') or '').isdecimal()],
            '说明':'同一楼层下的回复属于该根楼层；评论目标编号用于回复，不能放进正文。帖子正文或评论可能只是部分记录。'}
    if len(rows)>=10 and page<100:result['下一页URL']=refs.post_url(pid)+'?page='+str(page+1)
    if cid:
        _,target=context.cached(root,pid,cid)
        if target:
            result['目标评论']={'作者':(target.get('author') or {}).get('name',''),'正文':target.get('content',''),'评论目标编号':refs.number(pid,cid)}
        else:result['目标评论状态']='此评论尚未读取或缓存已过期，请继续读取对应评论分页；不能猜测楼层关系。'
    return json.dumps(result,ensure_ascii=False)


async def lookup(root,text,reader=None):
    """Keep generic/Bilibili tools unless a Heybox query or resource is explicit."""
    ref=refs.reference(text)
    if ref:
        try:return await asyncio.wait_for(read_post(root,*ref),40)
        except (ValueError,aiohttp.ClientError,OSError,asyncio.TimeoutError) as exc:return payload(str(text),[],str(exc) or type(exc).__name__)
    if not mentions(text):return None
    query=keyword(text)
    if not query:return payload('',[])
    try:
        posts=await asyncio.wait_for(reader(query) if reader is not None else collect(query,root=root),40)
    except asyncio.TimeoutError:return payload(query,[],'请求超时')
    except (ValueError,aiohttp.ClientError,OSError) as exc:return payload(query,[],str(exc) or type(exc).__name__)
    return payload(query,posts)
