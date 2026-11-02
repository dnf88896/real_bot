"""Cache received QQ image references without vision/model calls."""
import hashlib,ipaddress,json,socket,urllib.request,urllib.parse
from pathlib import Path
from image_library import INCOMING,checked_image

def validate_url(url):
    p=urllib.parse.urlsplit(url)
    host=(p.hostname or '').lower()
    if p.scheme not in ('http','https') or p.username or p.password or p.port not in (None,80,443):raise ValueError('Invalid media URL')
    if not any(host==d or host.endswith('.'+d) for d in ('qpic.cn','qq.com','qq.com.cn')):raise ValueError('Only QQ media hosts accepted')
    for info in socket.getaddrinfo(host,p.port or (443 if p.scheme=='https' else 80),type=socket.SOCK_STREAM):
        if not ipaddress.ip_address(info[4][0]).is_global:raise ValueError('Non-public media host')

class Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        validate_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def fetch(url):
    INCOMING.mkdir(mode=0o700,parents=True,exist_ok=True)
    key=hashlib.sha256(url.encode()).hexdigest()
    file=INCOMING/(key+'.jpg')
    if file.exists():return str(checked_image(str(file)))
    validate_url(url)
    req=urllib.request.Request(url,headers={'User-Agent':'QQ-image-cache/1.0'})
    with urllib.request.build_opener(Redirects()).open(req,timeout=5) as r:data=r.read(4*1024*1024+1)
    if len(data)>4*1024*1024:raise ValueError('Image too large')
    tmp=INCOMING/(key+'.tmp');tmp.write_bytes(data);tmp.chmod(0o600);tmp.replace(file)
    try:return str(checked_image(str(file)))
    except Exception:file.unlink(missing_ok=True);raise

def references(records):
    result=[];downloads=0
    for record in records:
        event=record['observation'].get('raw_event',{})
        parts=event.get('message',[])
        if not isinstance(parts,list):continue
        for part in parts:
            if not isinstance(part,dict) or part.get('type')!='image':continue
            meta={'observation_id':record['id'],'kind':'incoming','description':'收到的图片，未识别内容'}
            if downloads>=4:
                meta['status']='deferred';result.append(meta);continue
            downloads+=1
            try:
                url=part.get('data',{}).get('url')
                if not isinstance(url,str):raise ValueError('No downloadable image URL')
                meta['path']=fetch(url);meta['status']='available'
            except Exception:meta['status']='unavailable'
            result.append(meta)
    return result
