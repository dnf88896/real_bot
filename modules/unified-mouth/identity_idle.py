"""Identity-aware idle clocks. No model calls or changes to interest/post periods."""
import hashlib,json,sqlite3,time
from pathlib import Path
from contextlib import closing
from identity_links import snapshot
import private_idle_policy,active_gate

PRIORITY={'qq':0,'wechat':1,'bilibili':2,'heybox':3}

def scope(scene):return json.dumps(scene,sort_keys=True)

def confirmed(root,scene):
    if not scene or scene.get('chat_type')!='private':return [scene] if scene else []
    info=snapshot(root,scene['platform'],scene['account'],scene['conversation'])
    if info.get('存在冲突'):return [scene]
    members={scope(scene):scene}
    for n in info.get('同一人的已关联账号',[]):
        value={'platform':n['平台'],'account':n['机器人账号'],'chat_type':'private','conversation':n['用户ID']}
        members[scope(value)]=value
    return [members[k] for k in sorted(members)]

def cooldown_scenes(root,scene):
    members=confirmed(root,scene);result={scope(s):s for s in members}
    if not scene or scene.get('chat_type')!='private':return members
    # Suspected edges are not an equivalence relation; do not follow chains of suspicion.
    for member in members:
        info=snapshot(root,member['platform'],member['account'],member['conversation'])
        if info.get('存在冲突'):continue
        for relation in info.get('身份关系',[]):
            if relation.get('状态')!='疑似同一人' or relation.get('生效状态')!='疑似同一人':continue
            n=relation['对方账号'];other={'platform':n['平台'],'account':n['机器人账号'],'chat_type':'private','conversation':n['用户ID']}
            for s in confirmed(root,other):result[scope(s)]=s
    return list(result.values())

def initialize(db):
    if 'identity_context' not in {r[1] for r in db.execute('PRAGMA table_info(active_tasks)')}:
        db.execute('ALTER TABLE active_tasks ADD COLUMN identity_context TEXT')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS identity_idle_clocks(key TEXT PRIMARY KEY,members TEXT,scene TEXT,level INTEGER,due REAL,signature TEXT,inflight TEXT,enabled INTEGER);
    CREATE TABLE IF NOT EXISTS identity_idle_membership(scope TEXT PRIMARY KEY,clock TEXT);
    ''')

def group_key(members):return hashlib.sha256(json.dumps(sorted(scope(s) for s in members)).encode()).hexdigest()

def baselines(root,members,exclude=None):
    from active_system import latest
    return [{'scene':s,'baseline':latest(root,s,exclude)} for s in members]

def task_valid(root,scene,context,batch):
    if scene.get('chat_type')!='private':return True
    members=confirmed(root,scene)
    if not context:return len(members)<=1
    if len(members)<=1 or group_key(members)!=context['key']:return False
    path=Path(root)/'active-system.sqlite3'
    with closing(sqlite3.connect(path)) as db:
        row=db.execute('SELECT enabled,scene FROM identity_idle_clocks WHERE key=?',(context['key'],)).fetchone()
    if not row or not row[0] or json.loads(row[1])!=scene:return False
    return baselines(root,members,batch)==context['baselines']

def sync(engine,now):
    """Keep original session rows; overlay one clock per confirmed person."""
    from active_system import interval
    from wakefulness import after_awake
    with closing(engine.gate.connect()) as db:
        sessions=[dict(r) for r in db.execute('SELECT * FROM idle_sessions')]
    existing={r['scope']:r for r in sessions};groups={}
    for row in sessions:
        scene=json.loads(row['scene']);members=confirmed(engine.root,scene)
        if len(members)>1:groups[group_key(members)]=members
    active_members={}
    with closing(engine.gate.connect()) as db,db:
        db.execute('UPDATE identity_idle_clocks SET enabled=0')
        for key,members in groups.items():
            active_members.update({scope(s):key for s in members})
            available=[s for s in members if engine.roster(s) is True and private_idle_policy.enabled(active_gate.config(engine.root),s,engine.root)]
            if not available:continue
            available.sort(key=lambda s:(PRIORITY.get(s['platform'],99),
                -existing.get(scope(s),{}).get('last_time',0),scope(s)))
            selected=available[0]
            old=db.execute('SELECT * FROM identity_idle_clocks WHERE key=?',(key,)).fetchone()
            signature=baselines(engine.root,members,old['inflight'] if old else None)
            encoded=json.dumps(signature,sort_keys=True)
            if old is None:
                rows=[existing[scope(s)] for s in members if scope(s) in existing]
                latest_in=max(rows,key=lambda r:r['last_in'])
                level=latest_in['level']
                due=after_awake(engine.root,max(r['last_time'] for r in rows),interval(level,selected))
                db.execute('INSERT INTO identity_idle_clocks VALUES(?,?,?,?,?,?,NULL,1)',
                    (key,json.dumps(members),json.dumps(selected),level,due,encoded))
            else:
                level,due=old['level'],old['due']
                if old['signature']!=encoded:
                    previous=json.loads(old['signature'])
                    changed_in=any(a['baseline'][0]!=b['baseline'][0] for a,b in zip(previous,signature))
                    if changed_in:level=0
                    # Shared clock observes activity on any account; own inflight output is excluded.
                    activity=max((r['last_time'] for r in sessions if r['scope'] in {scope(s) for s in members}),default=now)
                    due=after_awake(engine.root,activity,interval(level,selected))
                db.execute('UPDATE identity_idle_clocks SET members=?,scene=?,level=?,due=?,signature=?,enabled=1 WHERE key=?',
                    (json.dumps(members),json.dumps(selected),level,due,encoded,key))
        previous=db.execute('SELECT scope FROM identity_idle_membership').fetchall()
        for row in previous:
            if row[0] not in active_members:
                db.execute('UPDATE idle_sessions SET level=0,due=? WHERE scope=?',(after_awake(engine.root,now,interval(0,json.loads(existing[row[0]]['scene']))),row[0]))
                db.execute('DELETE FROM identity_idle_membership WHERE scope=?',(row[0],))
        for value,key in active_members.items():db.execute('INSERT OR REPLACE INTO identity_idle_membership VALUES(?,?)',(value,key))

def due(engine,db,permitted,now):
    created=[]
    for row in db.execute('SELECT * FROM identity_idle_clocks WHERE enabled=1 AND inflight IS NULL AND due<=? ORDER BY due LIMIT 20',(now,)).fetchall():
        scene=json.loads(row['scene'])
        if not private_idle_policy.enabled(active_gate.config(engine.root),scene,engine.root):continue
        if not permitted({'scene':scene,'internal_wake':{'kind':'idle_proactive'}}):continue
        members=json.loads(row['members']);signature=baselines(engine.root,members)
        import uuid
        batch='active:idle:'+uuid.uuid4().hex
        context={'key':row['key'],'members':members,'baselines':signature,'priority':['qq','wechat','bilibili','heybox']}
        primary=next(x['baseline'] for x in signature if x['scene']==scene)
        db.execute("INSERT INTO active_tasks(id,kind,scene,baseline,status,created,identity_context) VALUES(?,'idle',?,?,'queued',?,?)",
            (batch,json.dumps(scene),json.dumps(primary),now,json.dumps(context)))
        db.execute('UPDATE identity_idle_clocks SET inflight=? WHERE key=?',(batch,row['key']))
        created.append(batch)
    return created

def finish(engine,db,task,ok,at,now,retry_seconds):
    if not task['identity_context']:return False
    from active_system import interval
    from wakefulness import after_awake
    ctx=json.loads(task['identity_context']);row=db.execute('SELECT * FROM identity_idle_clocks WHERE key=?',(ctx['key'],)).fetchone()
    if row and row['inflight']==task['id']:
        current=baselines(engine.root,json.loads(row['members']),task['id'])
        unchanged=all(a['baseline'][0]==b['baseline'][0] for a,b in zip(ctx['baselines'],current))
        level=min(6,row['level']+1) if ok and unchanged else row['level']
        due=after_awake(engine.root,at,interval(level,json.loads(row['scene']))) if ok and unchanged else max(row['due'],after_awake(engine.root,now,retry_seconds))
        # Count own sent messages in the new stable signature, without restarting the clock on next sync.
        signature=baselines(engine.root,json.loads(row['members']))
        db.execute('UPDATE identity_idle_clocks SET level=?,due=?,signature=?,inflight=NULL WHERE key=?',
                   (level,due,json.dumps(signature,sort_keys=True),ctx['key']))
    return True

def task_note(root,context):
    channels=[]
    with closing(sqlite3.connect(Path(root)/'observations.sqlite3')) as db:
        for item in context['baselines']:
            scene=item['scene'];oid=item['baseline'][0]
            row=db.execute('SELECT received_at FROM observations WHERE id=?',(oid,)).fetchone() if oid else None
            channels.append({**scene,'最后收到消息时间':row[0] if row else '未记录'})
    return {'说明':'这些私聊账号已认定为同一人，共用空闲计时与退避。这是此人整体无新消息后的主动机会，不表示当前平台单独被冷落。按QQ＞微信＞B站＞小黑盒选择了可用会话作为本轮上下文；仍可自行决定表达或保持沉默。',
            '各账号最近活动':channels}
