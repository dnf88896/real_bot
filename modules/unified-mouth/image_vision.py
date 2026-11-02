"""Describe a received image and hand the result to the mouth as plain text.

This program is independent of the mouth: it adds no tool, no interface and no
request field. Its output is an ordinary text part next to 【图片文字识别】, so the
mouth cannot tell whether OCR or a vision model produced it.
"""

import usage_meter
import base64,hashlib,json,sqlite3,time
from contextlib import closing
from pathlib import Path
from urllib.request import Request,urlopen

MARKER='【图片内容】'
MIME={'.jpg':'image/jpeg','.jpeg':'image/jpeg','.png':'image/png','.gif':'image/gif','.webp':'image/webp'}
DEFAULT={'enabled':True,'api_base':'https://api.deepseek.com','model':'deepseek-flash',
    'key_source':'/opt/astrbot-mouth/api.json','api_key':'','detail':'low',
    'timeout_seconds':60,'max_images':4,'max_chars':400,'max_bytes':8*1024*1024,
    'prompt':('用一两句话说明这张图片：画面里的主体、场景，以及它在表达什么。'
              '如果是表情包或梗图，说明它的情绪和常见用法。只描述看得见的内容，'
              '不猜测真实身份，不编造画面之外的信息，不超过200字。')}


def config(root=Path('/opt/astrbot-mouth')):
    p=Path(root)/'image-vision-config.json'
    try:return DEFAULT | (json.loads(p.read_text('utf8')) if p.exists() else {})
    except (OSError,ValueError):return dict(DEFAULT)


def api_key(cfg):
    if cfg.get('api_key'):return str(cfg['api_key'])
    # Supports canonical api.json; old provider-source fixtures remain readable.
    try:
        data=json.loads(Path(cfg['key_source']).read_text('utf8'))
        # Production points at the same canonical file as memory, relay and DSH.
        if isinstance(data.get('api_key'),str) and data['api_key'].strip():
            return data['api_key'].strip()
        for source in data.get('provider_sources') or []:
            keys=source.get('key') or source.get('api_key') or []
            if isinstance(keys,str):keys=[keys]
            if keys:return str(keys[0])
    except (OSError,ValueError,TypeError,KeyError):
        pass
    raise ValueError('Vision credentials unavailable')


def data_url(path,limit):
    p=Path(path);size=p.stat().st_size
    if size<=0 or size>limit:raise ValueError('Image size out of range')
    mime=MIME.get(p.suffix.lower()) or 'image/jpeg'
    return 'data:'+mime+';base64,'+base64.b64encode(p.read_bytes()).decode('ascii')


def parse(reply):
    choices=((reply or {}).get('choices') or [])
    text=((choices[0] or {}).get('message') or {}).get('content') if choices else ''
    if not isinstance(text,str) or not text.strip():raise ValueError('Empty vision reply')
    return ' '.join(text.split())


def clip(text,limit):
    text=' '.join(str(text or '').split())
    return text if len(text)<=limit else text[:max(1,limit-1)].rstrip('，,、；; ')+'…'


class ImageVision:
    def __init__(self,root,request=None):
        self.root=Path(root);self._request=request
        self.path=self.root/'image-vision.sqlite3'
        try:
            with closing(self.connect()) as db,db:
                db.execute('CREATE TABLE IF NOT EXISTS contents(key TEXT PRIMARY KEY,result TEXT,created REAL)')
        except sqlite3.Error:
            pass  # An unusable cache only costs repeat calls, never a message.

    def connect(self):return sqlite3.connect(self.path,timeout=30)

    def cached(self,key):
        with closing(self.connect()) as db:
            row=db.execute('SELECT result FROM contents WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self,key,result):
        with closing(self.connect()) as db,db:
            db.execute('INSERT OR IGNORE INTO contents VALUES(?,?,?)',(key,json.dumps(result,ensure_ascii=False),time.time()))
        return result

    def call(self,cfg,url):
        """Non-thinking and no max_tokens: the model answers in one short pass."""
        key=api_key(cfg)
        body=json.dumps({'model':cfg['model'],'thinking':{'type':'disabled'},
            'messages':[{'role':'user','content':[
            {'type':'text','text':cfg['prompt']},
            {'type':'image_url','image_url':{'url':url,'detail':cfg.get('detail','low')}}]}]},
            ensure_ascii=False).encode('utf8')
        req=Request(cfg['api_base'].rstrip('/')+'/chat/completions',data=body,
            headers={'Content-Type':'application/json','Authorization':'Bearer '+key})
        return usage_meter.json_request(req, cfg['timeout_seconds'], 'vision.image', model=cfg['model'])

    def describe(self,path):
        """Never raises: a lost caption must not cost the message itself."""
        cfg=config(self.root)
        try:digest=hashlib.sha256(Path(path).read_bytes()).hexdigest()
        except OSError:digest=None
        if digest:
            try:found=self.cached(digest)
            except sqlite3.Error:found=None
            if found is not None:return found
        try:
            reply=(self._request or self.call)(cfg,data_url(path,cfg['max_bytes']))
            result={'status':'ok','caption':clip(parse(reply),cfg['max_chars'])}
        except Exception as exc:
            result={'status':'failed','caption':'','error':type(exc).__name__}
        # A failure is retried next time: only a real caption is worth caching.
        if digest and result['status']=='ok':
            try:self.save(digest,result)
            except sqlite3.Error:pass
        return result


class VisionEnricher:
    """Runs after OCR: every image that already has a local path gets one caption."""
    def __init__(self,root,inner=None,vision=None):
        self.root=Path(root);self.inner=inner;self.vision=vision or ImageVision(self.root)

    def enrich(self,raw):
        cfg=config(self.root)
        if self.inner is not None:raw=self.inner.enrich(raw)
        parts=raw.get('message')
        if not cfg['enabled'] or raw.get('_image_vision') or raw.get('post_type')!='message' or not isinstance(parts,list):
            return raw
        images=[p for p in parts if isinstance(p,dict) and p.get('type')=='image']
        if not images:return raw
        described=[]
        for part in images:
            data=part.get('data') if isinstance(part.get('data'),dict) else {}
            path=data.get('cached_path')
            try:usable=bool(path) and Path(path).is_file()
            except OSError:usable=False
            if not usable:continue
            if len(described)>=cfg['max_images']:break
            try:result=self.vision.describe(path)
            except Exception as exc:result={'status':'failed','caption':'','error':type(exc).__name__}
            described.append((str(path),result))
        if not described:return raw
        prepared=json.loads(json.dumps(raw,ensure_ascii=False))
        targets=[p for p in prepared['message'] if isinstance(p,dict) and p.get('type')=='image'
                 and (p.get('data') or {}).get('cached_path')]
        for part,(_,result) in zip(targets,described):
            part.setdefault('data',{})['vision']={k:v for k,v in result.items() if k!='path'}
        for _,result in described:
            text=(MARKER+result['caption']) if result['status']=='ok' else (MARKER+'（未能识别图片内容）')
            prepared['message'].append({'type':'text','data':{'text':text}})
        prepared['_image_vision']=True
        prepared['raw_message']=''.join(p.get('data',{}).get('text','') for p in prepared['message']
                                        if isinstance(p,dict) and p.get('type')=='text')
        return prepared
