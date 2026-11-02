"""Configurable synthesis cache and QQ record conversion, without an extra LLM."""
import base64,hashlib,json,os,signal,subprocess,uuid
from pathlib import Path

DEFAULT={'enabled':False,'voice':'zh-CN-XiaoxiaoNeural','rate':'+0%','volume':'+0%',
         'pitch':'+0Hz','max_chars':1000,'timeout_seconds':45,
         'python':'/opt/astrbot-tts-venv/bin/python','backend':'edge'}

def config(root):
    path=Path(root)/'voice-output-config.json'
    return DEFAULT | (json.loads(path.read_text('utf8')) if path.exists() else {})

def synthesize(root,text):
    root=Path(root);cfg=config(root)
    if not cfg['enabled']:raise ValueError('Voice output disabled')
    if not isinstance(text,str) or not text.strip() or len(text)>cfg['max_chars']:
        raise ValueError('Voice text empty or exceeds configured character limit')
    backend=cfg.get('backend','edge')
    if backend not in ('edge','gpt_sovits'):raise ValueError('Unknown voice backend')
    identity=[text,backend,*[cfg[k] for k in ('voice','rate','volume','pitch')],cfg.get('model_revision','')]
    key=hashlib.sha256(json.dumps(identity,ensure_ascii=False).encode()).hexdigest()
    folder=root/'outgoing-voice';folder.mkdir(mode=0o700,exist_ok=True)
    path=folder/(key+'.mp3')
    if not path.exists():
        temp=folder/(key+'.'+uuid.uuid4().hex+'.mp3')
        job={k:cfg[k] for k in ('voice','rate','volume','pitch')}
        job.update(text=text,output=str(temp))
        try:
            worker=cfg.get('worker') if backend=='gpt_sovits' else str(root/'tts_worker.py')
            if not worker:raise ValueError('Voice worker not configured')
            if backend=='gpt_sovits':
                # Kill frontend descendants too if synthesis times out, before releasing its lock.
                with subprocess.Popen([cfg['python'],worker],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE,text=True,encoding='utf8',start_new_session=True) as proc:
                    try:stdout,stderr=proc.communicate(json.dumps(job,ensure_ascii=False),timeout=cfg['timeout_seconds'])
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid,signal.SIGKILL);proc.communicate();raise
                    result=subprocess.CompletedProcess(proc.args,proc.returncode,stdout,stderr)
            else:
                result=subprocess.run([cfg['python'],worker],input=json.dumps(job,ensure_ascii=False),
                                      capture_output=True,text=True,encoding='utf8',timeout=cfg['timeout_seconds'])
            if result.returncode:
                detail=(getattr(result,'stderr','') or getattr(result,'stdout','') or '')[-1200:]
                raise ValueError('Speech synthesis failed: '+detail)
            if not temp.is_file() or not 100<=temp.stat().st_size<=8*1024*1024:raise ValueError('Invalid synthesized audio')
            os.chmod(temp,0o600);os.replace(temp,path)
        finally:temp.unlink(missing_ok=True)
    if not 100<=path.stat().st_size<=8*1024*1024:raise ValueError('Invalid cached audio')
    return str(path)

def component(root,text,destination):
    if not destination or destination.get('platform') not in ('qq','wechat') or destination.get('chat_type') not in ('private','group'):
        raise ValueError('Voice supports enabled QQ/WeChat private/group destinations')
    return {'type':'voice','data':{'file':synthesize(root,text),'text':text}}

def wire(root,message):
    result=[];folder=(Path(root)/'outgoing-voice').resolve()
    for part in message:
        if part.get('type')!='voice':result.append(part);continue
        path=Path(part['data']['file']).resolve()
        if path.parent!=folder or path.suffix!='.mp3' or not path.is_file() or not 100<=path.stat().st_size<=8*1024*1024:
            raise ValueError('Invalid outgoing voice path')
        result.append({'type':'record','data':{'file':'base64://'+base64.b64encode(path.read_bytes()).decode('ascii')}})
    return result
