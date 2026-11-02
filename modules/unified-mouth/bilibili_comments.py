"""One comment POST, immutable text, confirmed receipt and narrow memory metadata."""
import re,json,math
from urllib.parse import urlsplit
if __package__:
    from . import bilibili_video as video
    from . import bilibili_refs as refs
else:
    import bilibili_video as video
    import bilibili_refs as refs


def destination(value):
    match=re.fullmatch('('+refs.BV+r')(?::([0-9]+))?',value)
    if not match:raise ValueError('Invalid video/comment destination')
    if match[2] and int(match[2])<=0:raise ValueError('Invalid reply identifier')
    return match[1],match[2] or ''


async def publish(root,client,dest,message,csrf):
    from bilibili_sender import Rejected
    from bilibili_posts import content
    bvid,rpid=destination(dest['id'])
    try:text,images=content(message)
    except (ValueError,KeyError,TypeError) as exc:raise Rejected('Invalid comment text/image components') from exc
    if len(images)>2:raise Rejected('At most two comment images')
    if (not text.strip() and not images) or len(text)>1000:raise Rejected('Comment must contain text or images; text limit 1000 characters')
    info,parent=video.cached(root,bvid,rpid)
    if not info or (rpid and not parent):raise Rejected('Read video and target comment before publishing')
    # Validate the actual video, not an untrusted share-card title or stale target.
    current=video.video_info(await video.fetch_info(client,bvid))
    if current['BV号']!=bvid or current['AV号']!=info['AV号']:raise Rejected('Video identity changed')
    info=current
    pictures=[]
    for blob in images:
        uploaded=await client.upload_image(blob)
        if not isinstance(uploaded,dict):raise Rejected('Comment image upload failed; no comment created')
        url=uploaded.get('image_url');parsed=urlsplit(url) if isinstance(url,str) else None
        if not parsed or parsed.scheme not in ('http','https') or not (parsed.hostname or '').endswith('.hdslb.com'):
            raise Rejected('Invalid comment image upload URL')
        width=uploaded.get('image_width');height=uploaded.get('image_height')
        if any(isinstance(n,bool) or not isinstance(n,(int,float)) or not math.isfinite(n) or n<=0 for n in (width,height)):
            raise Rejected('Comment image upload dimensions missing')
        size=uploaded.get('img_size',len(blob)/1024)
        if isinstance(size,bool) or not isinstance(size,(int,float)) or not math.isfinite(size) or size<=0:
            raise Rejected('Comment image upload size invalid')
        pictures.append({'img_src':url,'img_width':width,'img_height':height,'img_size':size})
    body={'type':1,'oid':info['AV号'],'message':text,'plat':1,'csrf':csrf}
    if pictures:body['pictures']=json.dumps(pictures,ensure_ascii=False)
    if parent:
        body.update(root=parent['根评论ID'] if parent['根评论ID']!='0' else rpid,parent=rpid)
    session=await client._get_session()
    await client._ensure_buvid()
    async with session.post('https://api.bilibili.com/x/v2/reply/add',data=body,timeout=30,
                            headers={'Referer':info['链接'],'Origin':'https://www.bilibili.com'}) as response:
        payload=await client._safe_json_from_response(response)
        if response.status>=500:raise RuntimeError('Comment publish result uncertain')
        if not isinstance(payload,dict):raise RuntimeError('Missing comment acknowledgement')
        if response.status!=200 or payload.get('code')!=0:raise Rejected('Bilibili comment rejected: '+str(payload.get('code',response.status)))
        data=payload.get('data') or {}
        mid=str(data.get('rpid_str') or data.get('rpid') or '')
        if data.get('need_captcha') or not mid.isascii() or not mid.isdecimal() or int(mid)<=0:
            raise RuntimeError('No usable comment acceptance receipt')
        result={'accepted':True,'message_id':mid,'comment_url':info['链接']+'#reply'+mid,
                'video_info':info,'platform_receipt':{'rpid':mid,'root':str(data.get('root_str') or data.get('root') or '0')}}
        if parent:result['replied_comment']=parent
        return result
