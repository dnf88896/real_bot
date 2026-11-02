"""Late, platform-aware link presentation. Never asks a chat model to select a card."""
import asyncio,copy,ipaddress,json,re,sqlite3,time
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT={'enabled':False,'qq_enabled':True,'max_cards':4,'metadata_timeout_seconds':5,'native_ttl_seconds':604800,
         'generate_new':False,'generation_timeout_seconds':12,'generation_retry_seconds':60}
URL=re.compile(r'https?://[^\s<>"\x27`，。；！？（）【】]+',re.I)
MARKDOWN=re.compile(r'\[([^\]\n]+)\]\((https?://[^\s<>]+)\)')

def config(root):
    p=Path(root)/'share-cards-config.json'
    return DEFAULT | (json.loads(p.read_text('utf8')) if p.exists() else {})

def public_url(value):
    try:
        p=urlsplit(value)
        if p.scheme not in ('http','https') or not p.hostname or p.username or p.password or p.port not in (None,80,443):return False
        host=p.hostname.lower()
        if host=='localhost' or host.endswith(('.localhost','.local')):return False
        try:return ipaddress.ip_address(host).is_global
        except ValueError:return '.' in host
    except ValueError:return False

def references(source,start,end):
    excluded=[(m.start(),m.end()) for m in re.finditer(r'```[\s\S]*?(?:```|$)|`[^`\n]*(?:`|$)',source)]
    result=[];covered=[]
    for m in MARKDOWN.finditer(source,start,end):
        if public_url(m[2]) and not any(a<=m.start()<b for a,b in excluded):
            result.append((m.start(),m.end()));covered.append((m.start(),m.end()))
    for m in URL.finditer(source,start,end):
        a,b=m.span();value=m.group().rstrip('.,;:!?）)')
        b=a+len(value)
        if public_url(value) and not any(x<=a<y for x,y in excluded+covered):result.append((a,b))
    return sorted(result)

def expand(root,source,deliveries):
    cfg=config(root)
    if not cfg['enabled'] or not cfg['qq_enabled']:return deliveries
    result=[];count=0;attempts=0
    for delivery in deliveries:
        dest=delivery['destination']
        if dest.get('platform')!='qq' or dest.get('chat_type') not in ('private','group'):
            result.append(delivery);continue
        current=[]
        def flush():
            if current:result.append({'destination':dest,'parts':list(current)});current.clear()
        for part in delivery['parts']:
            matches=references(source,part['start'],part['end']) if part['kind']=='text' else []
            offset=part['start']
            for a,b in matches:
                if attempts>=cfg['max_cards']:break
                attempts+=1
                value=source[a:b];markdown=MARKDOWN.fullmatch(value);url=markdown[2] if markdown else value
                try:available=native_for(root,url)
                except Exception:available=None
                if available is None:continue
                if source[offset:a].strip():current.append({**part,'start':offset,'end':a})
                flush();result.append({'destination':dest,'parts':[{'kind':'share_card','start':a,'end':b}]})
                count+=1;offset=b
            if offset==part['start']:current.append(dict(part))
            elif source[offset:part['end']].strip():current.append({**part,'start':offset})
        flush()
    return result

async def video_metadata(root,url):
    # Only Bilibili's fixed API hosts are fetched; arbitrary websites are not read.
    import bilibili_video as video
    parsed=urlsplit(url)
    if parsed.hostname not in ('b23.tv','bilibili.com','www.bilibili.com','m.bilibili.com'):return {}
    client,_=video.client_for(root)
    try:
        bvid,_=await video.resolve(client,url)
        info=await video.fetch_info(client,bvid)
        result={'title':str(info.get('title',''))[:200],'description':'UP主：'+str(info.get('owner',{}).get('name',''))[:120]}
        cover=info.get('pic','');p=urlsplit(cover)
        if p.scheme in ('http','https') and (p.hostname or '').endswith('.hdslb.com'):result['image']=cover
        return result
    finally:await client.close()

def metadata(root,url):
    path=Path(root)/'share-cards.sqlite3'
    with closing(sqlite3.connect(path,timeout=10)) as db,db:
        db.execute('CREATE TABLE IF NOT EXISTS metadata(url TEXT PRIMARY KEY,value TEXT,expires REAL)')
        row=db.execute('SELECT value FROM metadata WHERE url=? AND expires>?',(url,time.time())).fetchone()
    if row:return json.loads(row[0])
    try:
        value=asyncio.run(asyncio.wait_for(video_metadata(root,url),config(root)['metadata_timeout_seconds']))
    except Exception:value={}
    with closing(sqlite3.connect(path,timeout=10)) as db,db:
        db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?,?)',(url,json.dumps(value,ensure_ascii=False),time.time()+(86400 if value else 600)))
    return value

def native_db(root):
    db=sqlite3.connect(Path(root)/'share-cards.sqlite3',timeout=10)
    db.execute('CREATE TABLE IF NOT EXISTS native_cards(source TEXT PRIMARY KEY,url TEXT,card TEXT,bvid TEXT,created REAL)')
    db.execute('CREATE TABLE IF NOT EXISTS generated_cards(url TEXT PRIMARY KEY,bvid TEXT,card TEXT,created REAL,expires REAL,error TEXT,retry_after REAL)')
    return db

def unpack_ark(value):
    for _ in range(5):
        if isinstance(value,str):value=json.loads(value)
        if not isinstance(value,dict):break
        if value.get('app'):
            detail=value.get('meta',{}).get('detail_1',{})
            launch=str(detail.get('url',''))
            parsed=urlsplit(launch if '://' in launch else 'https://'+launch)
            if value['app']!='com.tencent.miniapp_01' or str(detail.get('appid'))!='400000004' or parsed.hostname!='m.q.qq.com':
                raise ValueError('Unexpected generated card template')
            return value
        if 'data' not in value:break
        value=value['data']
    raise ValueError('Missing native card in response')

def validate_generated(card,bvid):
    card=unpack_ark(card)
    detail=card['meta']['detail_1']
    if detail.get('qqdocurl')!='https://www.bilibili.com/video/'+bvid:
        raise ValueError('Generated card is missing the matching video web URL')
    return card

async def request_ark(body):
    import aiohttp
    gateway=json.loads(Path('/opt/snowluma/config/onebot_3654076639.json').read_text('utf8'))
    net=gateway['networks']['httpServers'][0]
    if net['host']!='127.0.0.1' or int(net['port'])!=13000:raise ValueError('Unexpected gateway endpoint')
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as session:
        async with session.post('http://127.0.0.1:13000/get_mini_app_ark',json=body,
            headers={'Authorization':'Bearer '+net['accessToken']}) as response:
            payload=await response.json(content_type=None)
            if response.status!=200 or payload.get('status')!='ok' or payload.get('retcode')!=0:
                raise ValueError('Card generation rejected: '+str(payload.get('retcode',response.status)))
            return unpack_ark(payload)

async def generate_card(root,url):
    import bilibili_video as video
    client,_=video.client_for(root)
    try:
        bvid,_=await video.resolve(client,url)
        info=await video.fetch_info(client,bvid)
        if info.get('bvid')!=bvid:raise ValueError('Video identity mismatch')
        cover=info.get('pic','');cover='https:'+cover if cover.startswith('//') else cover
        if urlsplit(cover).scheme not in ('https','http') or not (urlsplit(cover).hostname or '').endswith('.hdslb.com'):
            raise ValueError('Invalid video cover')
        body={'type':'bili','title':'哔哩哔哩','desc':str(info.get('title',''))[:200],
            'picUrl':cover,'jumpUrl':'pages/video/video?bvid='+bvid,
            'webUrl':'https://www.bilibili.com/video/'+bvid}
        card=validate_generated(await request_ark(body),bvid)
        return bvid,card
    finally:await client.close()

def generated_for(root,url):
    cfg=config(root)
    if not cfg['generate_new']:return None
    from bilibili_refs import target
    try:wanted=target(url)[0]
    except ValueError:wanted=None
    now=time.time()
    with closing(native_db(root)) as db:
        row=db.execute('SELECT card,bvid FROM generated_cards WHERE expires>? AND (url=? OR (? IS NOT NULL AND bvid=?)) AND card IS NOT NULL ORDER BY created DESC LIMIT 1',(now,url,wanted,wanted)).fetchone()
        failed=db.execute('SELECT retry_after FROM generated_cards WHERE url=? AND retry_after>?',(url,now)).fetchone()
    if row:
        try:return validate_generated(json.loads(row[0]),row[1])
        except (ValueError,TypeError,KeyError):pass
    if failed:return None
    try:
        bvid,card=asyncio.run(asyncio.wait_for(generate_card(root,url),cfg['generation_timeout_seconds']))
        error=None;encoded=json.dumps(card,ensure_ascii=False);expires=now+cfg['native_ttl_seconds'];retry=0
    except Exception as exc:
        bvid=wanted;card=None;encoded=None;error=type(exc).__name__+': '+str(exc)[:160];expires=0;retry=now+cfg['generation_retry_seconds']
    with closing(native_db(root)) as db,db:
        db.execute('INSERT OR REPLACE INTO generated_cards VALUES(?,?,?,?,?,?,?)',(url,bvid,encoded,time.time(),expires,error,retry))
    return card

def capture(root,raw,source,created=None):
    parts=raw.get('message')
    if not isinstance(parts,list):return 0
    candidates=[]
    for i,part in enumerate(parts):
        if not isinstance(part,dict) or part.get('type')!='json':continue
        try:
            card=part.get('data',{}).get('data');card=json.loads(card) if isinstance(card,str) else card
            if not isinstance(card,dict) or card.get('app')!='com.tencent.miniapp_01':continue
            detail=card.get('meta',{}).get('detail_1',{})
            if str(detail.get('appid'))!='400000004':continue
            url=detail.get('qqdocurl','')
            if not public_url(url) or urlsplit(url).hostname not in ('b23.tv','www.bilibili.com','bilibili.com','m.bilibili.com'):continue
            launch=detail.get('url','');launch=urlsplit(launch if '://' in launch else 'https://'+launch)
            if launch.hostname!='m.q.qq.com':continue
            from bilibili_refs import target
            try:bvid=target(url)[0]
            except ValueError:bvid=None
            candidates.append((str(source)+':'+str(i),url,json.dumps(card,ensure_ascii=False),bvid,time.time() if created is None else created))
        except (ValueError,TypeError,AttributeError):continue
    if candidates:
        with closing(native_db(root)) as db,db:db.executemany('INSERT OR IGNORE INTO native_cards VALUES(?,?,?,?,?)',candidates)
    return len(candidates)

def seed(root,limit=1000):
    from datetime import datetime
    with closing(sqlite3.connect(Path(root)/'observations.sqlite3')) as db:
        rows=db.execute("SELECT id,received_at,raw_json FROM observations WHERE raw_json LIKE '%miniapp_01%' AND coalesce(json_extract(raw_json,'$.platform'),'qq')='qq' ORDER BY id DESC LIMIT ?",(limit,)).fetchall()
    count=0
    for oid,stamp,encoded in rows:
        raw=json.loads(encoded)
        count+=capture(root,raw.get('raw_event',{}),'observation:'+str(oid),datetime.fromisoformat(stamp.replace('Z','+00:00')).timestamp())
    return count

async def resolve_native(root,url):
    import bilibili_video as video
    from bilibili_refs import target
    try:wanted=target(url)[0]
    except ValueError:wanted=None
    client,_=video.client_for(root)
    try:
        if wanted is None:wanted=(await video.resolve(client,url))[0]
        with closing(native_db(root)) as db:
            rows=db.execute('SELECT source,url FROM native_cards WHERE bvid IS NULL AND created>? ORDER BY created DESC LIMIT 8',(time.time()-config(root)['native_ttl_seconds'],)).fetchall()
        # One authenticated read at a time; the caller bounds the entire lookup.
        for source,link in rows:
            try:resolved=(await video.resolve(client,link))[0]
            except Exception:continue
            with closing(native_db(root)) as db,db:db.execute('UPDATE native_cards SET bvid=? WHERE source=?',(resolved,source))
            if resolved==wanted:break
        return wanted
    finally:await client.close()

def native_for(root,url):
    if urlsplit(url).hostname not in ('b23.tv','www.bilibili.com','bilibili.com','m.bilibili.com'):return None
    from bilibili_refs import target
    try:wanted=target(url)[0]
    except ValueError:wanted=None
    cutoff=time.time()-config(root)['native_ttl_seconds']
    def lookup():
        with closing(native_db(root)) as db:
            return db.execute('SELECT card FROM native_cards WHERE created>? AND (url=? OR (? IS NOT NULL AND bvid=?)) ORDER BY created DESC LIMIT 1',(cutoff,url,wanted,wanted)).fetchone()
    row=lookup()
    if not row and config(root)['generate_new']:
        return generated_for(root,url)
    if not row:
        try:wanted=asyncio.run(asyncio.wait_for(resolve_native(root,url),config(root)['metadata_timeout_seconds']))
        except Exception:pass
        row=lookup()
    return json.loads(row[0]) if row else None

def component(root,source):
    markdown=MARKDOWN.fullmatch(source);url=markdown[2] if markdown else source
    if not public_url(url):raise ValueError('Invalid share URL')
    try:card=native_for(root,url)
    except Exception:card=None
    if card is None:return {'type':'text','data':{'text':source}}
    return {'type':'share_card','data':{'source':source,'url':url,'native_card':card}}

def wire(message,account='100000002'):
    result=[]
    for segment in message:
        if segment.get('type')!='share_card':result.append(copy.deepcopy(segment));continue
        card=segment['data']['native_card']
        result.append({'type':'json','data':{'data':json.dumps(card,ensure_ascii=False)}})
    return result

def literal(message):
    return [({'type':'text','data':{'text':p['data']['source']}} if p.get('type')=='share_card' else copy.deepcopy(p)) for p in message]

def send_card(message,destination,call):
    cards=[p for p in message if p.get('type')=='share_card']
    if len(cards)!=1 or any(p.get('type') not in ('share_card','text') for p in message):
        raise ValueError('Native share delivery must contain exactly one card')
    target='group_id' if destination['chat_type']=='group' else 'user_id'
    action='send_group_msg' if destination['chat_type']=='group' else 'send_private_msg'
    if any(p.get('type')=='text' for p in message):
        # Owner-preview labels must stay visible. Mixed text+Ark rendering has
        # not been verified; preserve the labeled link instead of risking loss.
        receipt=call(action,{target:int(destination['id']),'message':literal(message)})
        receipt['share_card_fallback']=True
        return receipt
    return call(action,{target:int(destination['id']),'message':wire(message,destination['account'])})
