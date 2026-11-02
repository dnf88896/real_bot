"""Bilibili own-feed publishing. Immutable body, one create request, no retry."""
import base64

CREATE_URL='https://api.bilibili.com/x/dynamic/feed/create/dyn'


def content(message):
    text=[];images=[]
    for item in message:
        if item['type']=='text':text.append(item['data']['text'])
        elif item['type']=='image':
            value=item['data']['file']
            if not value.startswith('base64://'):raise ValueError('Post image must be validated local bytes')
            blob=base64.b64decode(value[9:],validate=True)
            if not 0<len(blob)<=4*1024*1024:raise ValueError('Post image size invalid')
            images.append(blob)
        else:raise ValueError('Unsupported post component')
    if len(images)>9:raise ValueError('At most nine post images')
    body=''.join(text)
    if not body.strip() and not images:raise ValueError('Empty post')
    return body,images


def payload(body,pictures):
    # Newlines remain part of raw_text, never separate create requests.
    return {'dyn_req':{'content':{'contents':[{'raw_text':body,'type':1,'biz_id':''}]},
                       'scene':2 if pictures else 1,'pics':pictures}}


async def publish(client,message,csrf):
    from bilibili_sender import Rejected
    body,images=content(message)
    pictures=[]
    for blob in images:
        uploaded=await client.upload_image(blob)
        if not uploaded:raise Rejected('Post image upload failed; no post created')
        if not all(uploaded.get(k) for k in ('image_url','image_width','image_height')):
            raise Rejected('Post image upload receipt incomplete')
        pictures.append({'img_src':uploaded['image_url'],'img_width':uploaded['image_width'],
                         'img_height':uploaded['image_height'],'img_size':len(blob)})
    session=await client._get_session()
    await client._ensure_buvid()
    async with session.post(CREATE_URL,params={'csrf':csrf},json=payload(body,pictures),
                            headers={'Referer':'https://t.bilibili.com/','Origin':'https://t.bilibili.com'}) as response:
        result=await client._safe_json_from_response(response)
        if not isinstance(result,dict):raise RuntimeError('Post result unknown: malformed response')
        if response.status>=500:raise RuntimeError('Post result unknown: server error')
        if response.status!=200 or result.get('code')!=0:
            raise Rejected('Post rejected, code '+str(result.get('code',response.status)))
        data=result.get('data') or {}
        dynamic_id=str(data.get('dyn_id_str') or data.get('dynamic_id_str') or data.get('dynamic_id') or '')
        if not dynamic_id.isascii() or not dynamic_id.isdecimal() or int(dynamic_id)<=0:
            raise RuntimeError('Post accepted without usable ID; do not repeat creation')
        return {'accepted':True,'message_id':dynamic_id,'post_url':'https://t.bilibili.com/'+dynamic_id,
                'platform_receipt':data}
