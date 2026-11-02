"""Automatic OCR before capture: immutable per-message results, no chat-model tool."""
import asyncio,copy,hashlib,json,sqlite3,subprocess,time
from contextlib import closing
from pathlib import Path

MARKER='【图片文字识别】'
DEFAULT={'enabled':False,'python':'/opt/astrbot-ocr-venv/bin/python','timeout_seconds':25,
         'max_images':8,'max_text_chars':12000,'min_confidence':0.5}

class ImageOCR:
    def __init__(self,root,recognize=None,fetch=None):
        self.root=Path(root);self.recognize=recognize;self.fetch=fetch
        self.path=self.root/'image-ocr.sqlite3'
        with closing(self.connect()) as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS events(key TEXT PRIMARY KEY,result TEXT,created REAL)')
            db.execute('CREATE TABLE IF NOT EXISTS contents(key TEXT PRIMARY KEY,result TEXT,created REAL)')

    def connect(self):return sqlite3.connect(self.path,timeout=30)

    def config(self):
        p=self.root/'image-ocr-config.json'
        return DEFAULT | (json.loads(p.read_text('utf8')) if p.exists() else {})

    def read(self,table,key):
        with closing(self.connect()) as db:
            row=db.execute('SELECT result FROM '+table+' WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self,table,key,result):
        with closing(self.connect()) as db,db:
            db.execute('INSERT OR IGNORE INTO '+table+' VALUES(?,?,?)',(key,json.dumps(result,ensure_ascii=False),time.time()))
        return self.read(table,key)

    def image(self,data,cfg):
        from image_library import checked_image
        path=data.get('cached_path')
        if path:path=str(checked_image(path))
        elif isinstance(data.get('url'),str):
            if self.fetch:path=self.fetch(data['url'])
            else:
                from incoming_images import fetch
                path=fetch(data['url'])
            path=str(checked_image(path))
        else:raise ValueError('No available image')
        digest=hashlib.sha256(Path(path).read_bytes()).hexdigest()
        result=self.read('contents',digest)
        if result is None:
            if self.recognize:result=self.recognize(path)
            else:
                completed=subprocess.run([cfg['python'],str(self.root/'ocr_worker.py'),path,
                    str(cfg['min_confidence']),str(cfg['max_text_chars'])],capture_output=True,
                    text=True,encoding='utf8',timeout=cfg['timeout_seconds'],check=True,
                    env=None)
                result=json.loads(completed.stdout)
            if result.get('status') not in ('ok','empty') or not isinstance(result.get('text'),str):
                raise ValueError('Invalid OCR result')
            result=self.save('contents',digest,result)
        return {**result,'path':path}

    def enrich(self,raw):
        cfg=self.config();parts=raw.get('message')
        if not cfg['enabled'] or raw.get('_image_ocr') or raw.get('post_type')!='message' or not isinstance(parts,list):return raw
        if not any(isinstance(p,dict) and p.get('type')=='image' for p in parts):return raw
        identity=[raw.get('self_id'),raw.get('message_type'),raw.get('group_id'),raw.get('user_id'),raw.get('message_id'),parts]
        key=hashlib.sha256(json.dumps(identity,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        results=self.read('events',key)
        if results is None:
            results=[]
            for part in parts:
                if not isinstance(part,dict) or part.get('type')!='image':continue
                try:
                    if len(results)>=cfg['max_images']:raise OverflowError('Image limit')
                    result=self.image(part.get('data',{}),cfg)
                except Exception as exc:
                    result={'status':'failed','text':'','error':type(exc).__name__}
                results.append(result)
            results=self.save('events',key,results)
        prepared=copy.deepcopy(raw);prepared['_image_ocr']={'engine':'rapidocr_onnxruntime','results':results}
        prepared['message']=[];index=0
        for part in parts:
            segment=copy.deepcopy(part);prepared['message'].append(segment)
            if not isinstance(part,dict) or part.get('type')!='image':continue
            result=results[index];index+=1
            segment.setdefault('data',{})['ocr']={k:v for k,v in result.items() if k!='path'}
            if result.get('path'):segment['data']['cached_path']=result['path']
            if result['status']=='ok':
                text=MARKER+'（仅为图中文字，可能有识别错误）\n'+result['text']
                if result.get('truncated'):text+='\n【识别文字过长，已截断】'
            elif result['status']=='empty':text=MARKER+'（未检测到可识别文字，不表示图片没有其他内容）'
            else:text=MARKER+'（识别失败，未获得图中文字）'
            prepared['message'].append({'type':'text','data':{'text':text}})
        prepared['raw_message']=''.join(p.get('data',{}).get('text','') for p in prepared['message'] if isinstance(p,dict) and p.get('type')=='text')
        return prepared

class IncomingEnricher:
    def __init__(self,voice,ocr):self.voice=voice;self.ocr=ocr
    async def enrich(self,raw):
        raw=await self.voice.enrich(raw)
        if isinstance(raw.get('message'),list) and any(isinstance(p,dict) and p.get('type')=='image' for p in raw['message']):
            return await asyncio.to_thread(self.ocr.enrich,raw)
        return raw
