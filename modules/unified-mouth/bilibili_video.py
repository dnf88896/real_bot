"""Reusable authenticated Bilibili reads; comment samples stay in a temporary cache."""
import asyncio,json,sqlite3,time
from contextlib import closing
from datetime import datetime,UTC
from pathlib import Path
from urllib.parse import urlsplit,parse_qs,urlencode
if __package__:
    from . import bilibili_refs as refs
else:
    import bilibili_refs as refs


def connect(root):
    db=sqlite3.connect(Path(root)/'bilibili-video.sqlite3',timeout=20)
    db.row_factory=sqlite3.Row
    db.executescript('''CREATE TABLE IF NOT EXISTS videos(bvid TEXT PRIMARY KEY,info TEXT,updated REAL);
        CREATE TABLE IF NOT EXISTS comments(bvid TEXT,rpid TEXT,info TEXT,updated REAL,PRIMARY KEY(bvid,rpid));''')
    return db


def account_config(root):
    cfg=json.loads((Path(root)/'bilibili-account.json').read_text('utf8'))
    if not cfg.get('enabled'):raise ValueError('Bilibili account disabled')
    return cfg


def client_for(root):
    if __package__:
        # The transport deliberately remains a standalone module.
        import sys
        sys.path.insert(0,str(root))
    import bilibili_sender
    cfg=account_config(root)
    client=bilibili_sender.client_class()(sessdata=cfg['SESSDATA'],bili_jct=cfg['bili_jct'],device_id=cfg['device_id'],user_agent=cfg['user_agent'],buvid3=cfg.get('buvid3',''),buvid4=cfg.get('buvid4',''))
    client._self_uid=int(cfg['account_id'])
    return client,cfg


async def get(client,path,params):
    session=await client._get_session()
    await client._ensure_buvid()
    if '/wbi/' in path:
        params=await client._sign_wbi_params(params)
        if params is None:raise ValueError('Bilibili signing unavailable')
    async with session.get('https://api.bilibili.com'+path,params=params,timeout=20) as response:
        payload=await client._safe_json_from_response(response)
        if response.status!=200 or not isinstance(payload,dict) or payload.get('code')!=0:
            raise ValueError('Bilibili read failed: '+str(payload.get('code') if isinstance(payload,dict) else response.status))
        return payload.get('data') or {}


async def fetch_info(client,bvid):
    try:return await get(client,'/x/web-interface/view',{'bvid':bvid})
    except ValueError:return await get(client,'/x/web-interface/wbi/view',{'bvid':bvid})


async def resolve(client,value):
    if urlsplit(value).hostname=='b23.tv':
        url=value
        session=await client._get_session()
        for _ in range(5):
            parsed=urlsplit(url)
            if parsed.scheme not in ('http','https') or parsed.hostname not in ('b23.tv','www.bilibili.com','bilibili.com','m.bilibili.com') or parsed.username or parsed.password:
                raise ValueError('Unexpected Bilibili short-link destination')
            if parsed.hostname!='b23.tv':return refs.target(url)
            async with session.get(url,allow_redirects=False,timeout=15) as response:
                if response.status not in (301,302,303,307,308):raise ValueError('Short link did not redirect to video')
                from urllib.parse import urljoin
                url=urljoin(url,response.headers.get('Location',''))
        raise ValueError('Too many short-link redirects')
    parsed=urlsplit(value)
    import re
    match=re.fullmatch(r'/video/av([0-9]+)/?',parsed.path)
    if parsed.hostname in ('www.bilibili.com','bilibili.com','m.bilibili.com') and match:
        info=await get(client,'/x/web-interface/view',{'aid':match[1]})
        return refs.target('https://www.bilibili.com/video/'+info['bvid']+('?' + parsed.query if parsed.query else ''))
    return refs.target(value)


def video_info(data):
    bvid=data.get('bvid','')
    refs.target(bvid)
    return {'BV号':bvid,'AV号':str(data['aid']),'标题':str(data.get('title','')),
        'UP主':{'UID':str(data.get('owner',{}).get('mid','')),'名称':str(data.get('owner',{}).get('name',''))},
        '发布时间':datetime.fromtimestamp(data.get('pubdate') or 0,UTC).isoformat(),'简介':str(data.get('desc','')),
        '时长秒':data.get('duration'),'分P':[{'序号':p.get('page'),'标题':p.get('part'),'时长秒':p.get('duration')} for p in data.get('pages',[])],
        '链接':'https://www.bilibili.com/video/'+bvid}


def comment_info(row):
    rpid=str(row.get('rpid_str') or row.get('rpid') or '')
    if not rpid.isascii() or not rpid.isdecimal() or int(rpid)<=0:raise ValueError('Invalid Bilibili comment data')
    member=row.get('member') or {}
    return {'评论ID':rpid,'根评论ID':str(row.get('root_str') or row.get('root') or '0'),
        '父评论ID':str(row.get('parent_str') or row.get('parent') or '0'),
        '作者':{'UID':str(member.get('mid','')),'名称':str(member.get('uname',''))},
        '正文':str((row.get('content') or {}).get('message','')),'时间':row.get('ctime'),
        '点赞':row.get('like',0)}


def remember_video(root,info):
    """Only basic video information enters global observations, never comment samples."""
    if __package__:
        from .store import Store
    else:
        from store import Store
    import hashlib
    encoded=json.dumps(info,ensure_ascii=False,sort_keys=True)
    with closing(connect(root)) as db,db:
        db.execute('INSERT OR REPLACE INTO videos VALUES(?,?,?)',(info['BV号'],encoded,time.time()))
    Store(root).capture(platform='bilibili',account=str(account_config(root)['account_id']),kind='video',conversation=info['BV号'],sender=info['UP主']['UID'],
        message_id=info['BV号']+':'+hashlib.sha256(encoded.encode()).hexdigest(),generate=False,
        raw={'post_type':'video_info','sender':{'nickname':info['UP主']['名称']},'message':[{'type':'text','data':{'text':encoded}}]})


def cache_comments(root,bvid,rows):
    with closing(connect(root)) as db,db:
        db.execute('DELETE FROM comments WHERE updated<?',(time.time()-86400,))
        for row in rows:db.execute('INSERT OR REPLACE INTO comments VALUES(?,?,?,?)',(bvid,row['评论ID'],json.dumps(refs.without_comment_numbers(row),ensure_ascii=False),time.time()))


async def read_with_client(root,client,value):
    bvid,rpid=await resolve(client,value)
    data=await fetch_info(client,bvid)
    info=video_info(data)
    if info['BV号']!=bvid:raise ValueError('Bilibili returned a different video')
    remember_video(root,info)
    query=parse_qs(urlsplit(value).query)
    root_id=(query.get('comment_root') or [''])[0]
    result={'视频资料':info,'读取范围':'标题、简介和评论；未观看画面或收听音频','评论区':[]}
    rows=[]
    try:
        if root_id:
            if not root_id.isascii() or not root_id.isdecimal() or int(root_id)<=0:raise ValueError('Invalid root comment')
            page=int((query.get('comment_page') or ['1'])[0])
            if not 1<=page<=100:raise ValueError('Invalid comment page')
            comments=await get(client,'/x/v2/reply/reply',{'type':1,'oid':info['AV号'],'root':root_id,'pn':page,'ps':20})
            raw=([comments['root']] if comments.get('root') else [])+(comments.get('replies') or [])
            rows=[comment_info(r) for r in raw]
            result['更多回复']=info['链接']+'?'+urlencode({'comment_root':root_id,'comment_page':page+1})
        else:
            requested=(query.get('comment_sort') or [''])[0]
            sorts=[requested] if requested in ('latest','hot') else ['hot','latest']
            page=int((query.get('comment_page') or ['1'])[0])
            if not 1<=page<=100:raise ValueError('Invalid comment page')
            for sort in sorts:
                try:
                    if query.get('comment_cursor'):raise ValueError('Continue WBI cursor')
                    comments=await get(client,'/x/v2/reply',{'type':1,'oid':info['AV号'],'sort':0 if sort=='latest' else 1,'pn':page,'ps':10})
                    more=info['链接']+'?'+urlencode({'comment_sort':sort,'comment_page':page+1})
                except ValueError:
                    cursor=(query.get('comment_cursor') or [''])[0]
                    if page!=1 and not cursor:raise
                    comments=await get(client,'/x/v2/reply/wbi/main',{'type':1,'oid':info['AV号'],'mode':2 if sort=='latest' else 3,'pagination_str':json.dumps({'offset':cursor})})
                    next_cursor=(comments.get('cursor',{}).get('pagination_reply') or {}).get('next_offset','')
                    more=info['链接']+'?'+urlencode({'comment_sort':sort,'comment_cursor':next_cursor}) if next_cursor else None
                raw=(comments.get('replies') or [])[:10]
                normalized=[comment_info(r) for r in raw]
                rows.extend(normalized)
                result['评论区'].append({'排序':sort,'评论':normalized,'继续读取':more})
        # Replies returned inline are useful temporarily and can be explicit targets.
        cache_comments(root,bvid,rows)
        if root_id:result['评论区']=[{'根评论':root_id,'评论':rows}]
        for row in rows:
            root=row['根评论ID'] if row['根评论ID']!='0' else row['评论ID']
            row['评论链接']=info['链接']+'?'+urlencode({'comment_root':root,'comment_id':row['评论ID']})
        if rpid and not any(r['评论ID']==rpid for r in rows):result['指定评论提示']='本次未读到该评论，请继续读取对应回复链；不能凭评论ID猜正文。'
    except (ValueError,TypeError,KeyError) as exc:
        result['评论读取错误']=str(exc)
    return refs.present_comments(result,bvid)


async def lookup(root,text):
    found=refs.references(text)
    if not found:return None
    # Prefer the full URL, retaining explicit pagination and comment identifiers.
    urls=[r for r in found if r.startswith('http')]
    candidates=urls+[r for r in found if not r.startswith('http') and not any(r in u for u in urls)]
    if len(candidates)>1:
        ids=set()
        for ref in candidates:
            try:ids.add(refs.target(ref)[0])
            except ValueError:ids.add(ref)
        if len(ids)>1:return json.dumps({'提示':'发现多个视频，请逐个使用具体BV号或链接读取。','视频':candidates},ensure_ascii=False)
    try:client,cfg=client_for(root)
    except (OSError,ValueError):return json.dumps({'B站读取失败':'当前B站账号不可用'},ensure_ascii=False)
    try:
        return json.dumps(await asyncio.wait_for(read_with_client(root,client,candidates[0]),65),ensure_ascii=False)
    except (ValueError,asyncio.TimeoutError) as exc:
        return json.dumps({'B站读取失败':str(exc) or '请求超时'},ensure_ascii=False)
    except Exception as exc:
        return json.dumps({'B站读取失败':type(exc).__name__},ensure_ascii=False)
    finally:await client.close()


def targets(root,requested=None):
    """Relay-only directory: queried videos and temporary explicit comment IDs."""
    root=Path(root)
    if not (root/'bilibili-video.sqlite3').exists():return []
    try:cfg=account_config(root)
    except (OSError,ValueError):return []
    account=str(cfg['account_id'])
    result=[]
    with closing(connect(root)) as db:
        if requested is not None:
            wanted=list({x.split(':',1)[0] for x in requested})
            if not wanted:return []
            videos=db.execute('SELECT * FROM videos WHERE bvid IN ('+','.join('?' for _ in wanted)+')',wanted).fetchall()
        else:
            videos=db.execute('SELECT * FROM videos ORDER BY updated DESC LIMIT 100').fetchall()
        for row in videos:
            info=json.loads(row['info']);base={'platform':'bilibili','account':account,'chat_type':'comment'}
            result.append({**base,'id':row['bvid'],'name':info['标题']})
            for comment in db.execute('SELECT rpid FROM comments WHERE bvid=? AND updated>? ORDER BY updated DESC LIMIT 40',(row['bvid'],time.time()-86400)):
                result.append({**base,'id':row['bvid']+':'+comment[0],'name':'评论'+comment[0]})
    return [r for r in result if requested is None or r['id'] in requested]


def cached(root,bvid,rpid=''):
    with closing(connect(root)) as db:
        row=db.execute('SELECT info FROM videos WHERE bvid=?',(bvid,)).fetchone()
        video=json.loads(row[0]) if row else None
        row=db.execute('SELECT info FROM comments WHERE bvid=? AND rpid=? AND updated>?',(bvid,rpid,time.time()-86400)).fetchone() if rpid else None
    return video,refs.without_comment_numbers(json.loads(row[0])) if row else None
