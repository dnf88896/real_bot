"""Program-only participation, cooldown, delivery pacing and bounded text variants."""
import json,time,math,secrets,sqlite3,hashlib,random,re
from pathlib import Path
CONFIG=Path('/opt/qq-mouth/behavior-config.json')
DEFAULT={'enabled':False,'base_probability':0.02,'boost_probability':0.35,'boost_seconds':120,'boost_half_life':45,'cooldown_seconds':12,'density_window':60,'max_batches':3,'typing_speed':12,'typing_min':0.4,'typing_max':3.5,'bubble_gap_min':0.8,'bubble_gap_max':2.0,'typo_enabled':True,'typo_probability':0.02}
def config():
    value=dict(DEFAULT)
    if CONFIG.exists():value.update(json.loads(CONFIG.read_text('utf8')))
    for k,v in value.items():
        if k not in DEFAULT:raise ValueError('Unknown behavior setting')
        if type(DEFAULT[k]) is bool:
            if type(v) is not bool:raise ValueError('Invalid behavior switch')
        elif type(v) not in (int,float) or not math.isfinite(v) or v<0:raise ValueError('Invalid behavior number')
    for k in ['base_probability','boost_probability','typo_probability']:
        if value[k]>1:raise ValueError('Invalid probability')
    if not value['typing_speed'] or not value['boost_half_life'] or value['max_batches']<1:raise ValueError('Invalid behavior limit')
    if value['typing_min']>value['typing_max'] or value['bubble_gap_min']>value['bubble_gap_max']:raise ValueError('Invalid delay range')
    return value

def reply_times(path,scene):
    if not Path(path).exists():return []
    db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True);batches={}
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='relay_deliveries'").fetchone():return []
        for raw, in db.execute("SELECT record_json FROM relay_deliveries WHERE status='sent' AND record_json IS NOT NULL ORDER BY updated DESC LIMIT 2000"):
            obj=json.loads(raw)
            if (str(obj.get('account_id')),obj.get('chat_type'),str(obj.get('conversation_id')))==(str(scene['account']),scene['chat_type'],str(scene['conversation'])):
                bid=obj.get('batch_id');batches[bid]=max(batches.get(bid,0),obj.get('sent_at',0))
    finally:db.close()
    return sorted(batches.values())

def participation(db,items,scene,batch,mouth_path,owner,now=None):
    cfg=config();now=time.time() if now is None else now
    if not cfg['enabled'] or scene['chat_type']!='group' or any(x['observation'].get('internal_event') for x in items):return {'allowed':True,'applicable':False}
    direct=any(str(x['observation'].get('sender_id',''))==owner or any(isinstance(p,dict) and p.get('type')=='at' and str(p.get('data',{}).get('qq'))==str(scene['account']) for p in (x['observation'].get('raw_event',{}).get('message',[]) if isinstance(x['observation'].get('raw_event',{}).get('message',[]),list) else [])) for x in items)
    times=reply_times(mouth_path,scene);age=max(0,now-times[-1]) if times else None
    count=sum(now-t<cfg['density_window'] for t in times)
    blocked=(age is not None and age<cfg['cooldown_seconds']) or count>=cfg['max_batches']
    p=cfg['base_probability']
    if age is not None and age<cfg['boost_seconds']:
        p=max(p,p+(cfg['boost_probability']-p)*math.exp(-math.log(2)*age/cfg['boost_half_life']))
    db.execute('CREATE TABLE IF NOT EXISTS behavior_draws(observation_id INTEGER PRIMARY KEY,batch_id TEXT,probability REAL,draw INTEGER,eligible INTEGER,created REAL)')
    selected=[]
    with db:
        db.execute('BEGIN IMMEDIATE')
        for x in items:
            oid=x['id'];r=db.execute('SELECT eligible FROM behavior_draws WHERE observation_id=?',(oid,)).fetchone()
            if r is None:
                draw=secrets.randbelow(1000000);hit=int(draw<p*1000000)
                db.execute('INSERT INTO behavior_draws VALUES(?,?,?,?,?,?)',(oid,batch,p,draw,hit,now));r=(hit,)
            if r[0]:selected.append(oid)
    return {'applicable':True,'allowed':bool(direct or (selected and not blocked)),'direct':direct,'eligible_ids':selected,'probability':p,'cooldown':blocked,'recent_reply_batches':count,'note':'筛选通过只表示可以考虑参与，仍须判断是否值得回复；不指导回复内容。'}

# Conservative variants of utterance particles, not arbitrary character replacement.
VARIANTS={'嗯':'恩','哦':'噢','呀':'吖'}
def variant(text,names,rng,probability):
    if rng.random()>=probability:return text,[]
    protected=[]
    for pattern in [r'https?://\S+',r'`[^`]*`',r'```[\s\S]*?```',r'\[[^\]]*\]',r'[@＠]\S+',r'[0-9A-Za-z_./:\\-]+']:
        protected.extend((m.start(),m.end()) for m in re.finditer(pattern,text))
    for name in names:
        if name:
            protected.extend((m.start(),m.end()) for m in re.finditer(re.escape(name),text))
    candidates=[]
    for i,c in enumerate(text):
        if c not in VARIANTS or any(a<=i<b for a,b in protected):continue
        # Only interjections at a boundary, or a terminal particle immediately before punctuation.
        before=i==0 or text[i-1].isspace() or text[i-1] in '，。！？!?：:'
        after=i+1==len(text) or text[i+1].isspace() or text[i+1] in '，。！？!?～~'
        if after and (before or c=='呀'):candidates.append(i)
    if not candidates:return text,[]
    i=rng.choice(candidates);replacement=VARIANTS[text[i]]
    return text[:i]+replacement+text[i+1:],[{'index':i,'original':text[i],'sent':replacement}]

def rendering(db,did,message,texts,packet,now=None):
    now=time.time() if now is None else now;cfg=config()
    db.execute('CREATE TABLE IF NOT EXISTS delivery_renderings(delivery_id TEXT PRIMARY KEY,message TEXT,texts TEXT,changes TEXT,not_before REAL)');db.commit()
    row=db.execute('SELECT message,texts,changes,not_before FROM delivery_renderings WHERE delivery_id=?',(did,)).fetchone()
    if row:return json.loads(row[0]),json.loads(row[1]),json.loads(row[2]),row[3]
    rng=random.Random(int(hashlib.sha256(did.encode()).hexdigest(),16));changes=[];spoken=[];rendered=[]
    names=[str(x.get(k) or '') for x in packet.get('delivery_names',[]) for k in ('name','remark')]
    for part in message:
        part=json.loads(json.dumps(part))
        if part['type']=='text':
            value=part['data']['text']
            if cfg['enabled'] and cfg['typo_enabled'] and not changes:
                value,changed=variant(value,names,rng,cfg['typo_probability']);changes.extend(changed)
            part['data']['text']=value;spoken.append(value)
        rendered.append(part)
    delay=0
    if cfg['enabled']:
        delay=min(cfg['typing_max'],max(cfg['typing_min'],len(''.join(spoken))/cfg['typing_speed']))*rng.uniform(0.85,1.15)
        previous=db.execute("SELECT 1 FROM relay_deliveries WHERE batch_id=(SELECT batch_id FROM relay_deliveries WHERE id=?) AND ordinal<(SELECT ordinal FROM relay_deliveries WHERE id=?) AND status='sent'",(did,did)).fetchone()
        if previous:delay+=rng.uniform(cfg['bubble_gap_min'],cfg['bubble_gap_max'])
    not_before=now+delay
    with db:db.execute('INSERT OR IGNORE INTO delivery_renderings VALUES(?,?,?,?,?)',(did,json.dumps(rendered,ensure_ascii=False),json.dumps(spoken,ensure_ascii=False),json.dumps(changes,ensure_ascii=False),not_before))
    return rendered,spoken,changes,not_before
