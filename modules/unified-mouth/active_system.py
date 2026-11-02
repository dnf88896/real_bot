"""Conversation backoff and recurring interest, coordinated with explicit idle intentions."""

import json,sqlite3,time,secrets,uuid

from pathlib import Path

from contextlib import closing

from datetime import datetime,UTC

if __package__:

    from . import active_gate,wakefulness,autonomy,identity_idle,private_idle_policy

else:

    import active_gate,wakefulness,autonomy,identity_idle,private_idle_policy



INTEREST_TASK='这是一轮兴趣主动。请积极通过多个渠道寻找一些自己觉得有趣的事情，结合自己最近的兴趣与活动，尝试不同来源和方向，而不是只围绕单一渠道或已有话题。使用现有联网工具搜索、读取来源正文，并按需要继续追查，不必只追逐大众热点。了解后在本轮决定是否立即分享、联系谁或发布什么，也可以保持沉默。一次只联系一个对象，或保持沉默，也可以是合适的决定。搜索和实际分享会由程序记录，供以后聊天时自然运用；不要为了完成探索任务编造经历。'



IDLE_TASK='当前对话已经太久没有新消息，这是一轮触发主动，不是对方发来了新消息。请结合当前对话记录、记忆和自身状态，主动寻找一个适合与这里的人继续聊的话题，自然地开启或延续对话。不要把主动任务当作对方来信，不要只解释对话安静了或催促对方回复。没有合适话题或不宜打扰时可以保持沉默。'



def awake(root,now=None):return wakefulness.state(root,now)!='睡眠中'

def interval(level,scene=None):

    base=60 if scene is None or scene.get("platform") in ("qq","wechat") else 30

    low=min(1440,base*2**min(6,level));return secrets.randbelow(low*60+1)+low*60

def idle_scene_enabled(cfg,scene,root=Path('/opt/astrbot-mouth')):
    return private_idle_policy.enabled(cfg,scene,root) and not (scene.get('platform') in ('qq','wechat') and scene.get('chat_type')=='group' and not cfg.get('group_idle_enabled',True))

def random_interest(cfg):return cfg['interest_min_seconds']+secrets.randbelow(cfg['interest_max_seconds']-cfg['interest_min_seconds']+1)

def scope(scene):return json.dumps(scene,sort_keys=True)



def latest(root,scene,exclude=None):

    """Platform-separated activity; preview is activity in its logical group."""

    with closing(sqlite3.connect(Path(root)/'observations.sqlite3')) as db:

        incoming=db.execute("SELECT coalesce(max(id),0) FROM observations WHERE account_id=? AND chat_type=? AND conversation_id=? AND sender_id!=account_id AND coalesce(json_extract(raw_json,'$.platform'),'qq')=? AND coalesce(json_extract(raw_json,'$.raw_event.post_type'),'message')='message'",(scene['account'],scene['chat_type'],scene['conversation'],scene['platform'])).fetchone()[0]

    with closing(sqlite3.connect(Path(root)/'mouth.sqlite3')) as db:

        out=db.execute("SELECT id FROM relay_deliveries WHERE status='sent' AND batch_id!=? AND json_extract(record_json,'$.platform')=? AND json_extract(record_json,'$.account_id')=? AND json_extract(record_json,'$.chat_type')=? AND json_extract(record_json,'$.conversation_id')=? ORDER BY updated DESC,rowid DESC LIMIT 1",(exclude or '',scene['platform'],scene['account'],scene['chat_type'],scene['conversation'])).fetchone() if db.execute("SELECT 1 FROM sqlite_master WHERE name='relay_deliveries'").fetchone() else None

    return [incoming,out[0] if out else '']



def valid(root,wid,now=None):

    p=Path(root)/'active-system.sqlite3';cfg=active_gate.config(root)

    if not cfg['enabled'] or not p.exists():return False

    with closing(sqlite3.connect(p)) as db:

        row=db.execute('SELECT kind,scene,baseline,status,identity_context FROM active_tasks WHERE id=?',(wid,)).fetchone()

    if not row or row[3] not in ('queued','completed'):return False

    if not cfg[row[0]+'_enabled'] or not awake(root,now):return False

    if row[0]=='idle':
        if not idle_scene_enabled(cfg,json.loads(row[1]),root):return False

        context=json.loads(row[4]) if row[4] else None

        if not identity_idle.task_valid(root,json.loads(row[1]),context,wid):return False

        with closing(sqlite3.connect(p)) as db:

            if db.execute("SELECT 1 FROM sqlite_master WHERE name='idle_scope_status'").fetchone():

                state=db.execute('SELECT allowed FROM idle_scope_status WHERE scope=?',(scope(json.loads(row[1])),)).fetchone()

                if state and state[0]==0:return False

        return latest(root,json.loads(row[1]),wid)==json.loads(row[2])

    return True



class ActiveSystem:

    def __init__(self,root,roster=None):

        from active_roster import Roster

        self.roster=roster if roster is not None else Roster(root=root)

        self.root=Path(root);self.gate=active_gate.Gate(root,roster=self.roster);self.path=self.gate.path

        with closing(self.gate.connect()) as db,db:

            db.executescript('''

            CREATE TABLE IF NOT EXISTS idle_scope_status(scope TEXT PRIMARY KEY,allowed INTEGER,checked REAL,reason TEXT);

            CREATE TABLE IF NOT EXISTS active_meta(key TEXT PRIMARY KEY,value TEXT);

            CREATE TABLE IF NOT EXISTS idle_sessions(scope TEXT PRIMARY KEY,scene TEXT,last_in INTEGER,last_out TEXT,last_time REAL,level INTEGER,due REAL,inflight TEXT);

            CREATE TABLE IF NOT EXISTS active_tasks(id TEXT PRIMARY KEY,kind TEXT,scene TEXT,baseline TEXT,status TEXT,created REAL,emitted REAL DEFAULT 0,finished REAL,error TEXT);

            CREATE TABLE IF NOT EXISTS interest_clock(id INTEGER PRIMARY KEY,due REAL,sleeping INTEGER,inflight TEXT);

            INSERT OR IGNORE INTO interest_clock VALUES(1,NULL,0,NULL);

            ''')

            identity_idle.initialize(db)



    def migrate_idle_clock(self,now):

        with closing(self.gate.connect()) as db,db:

            db.execute('BEGIN IMMEDIATE')

            if db.execute("SELECT 1 FROM active_meta WHERE key='idle_awake_clock_v1'").fetchone():return

            sleep_start=wakefulness.sleep_started(self.root,now)

            reference=now if sleep_start is None else sleep_start

            cancelled=[]

            for row in db.execute('SELECT * FROM idle_sessions').fetchall():

                batch=row['inflight']

                if batch:

                    admission=db.execute('SELECT phase FROM admissions WHERE batch=?',(batch,)).fetchone()

                    with closing(sqlite3.connect(self.root/'mouth.sqlite3')) as mouth:

                        generated=mouth.execute('SELECT 1 FROM outputs WHERE batch_id=?',(batch,)).fetchone()

                    # Never discard committed output or interrupt an active generation here.

                    if generated or (admission and admission[0] in ('running','delivering')):continue

                    db.execute("UPDATE active_tasks SET status='cancelled',finished=?,error='awake_clock_migration' WHERE id=? AND status='queued'",(now,batch))

                    db.execute("UPDATE admissions SET phase='cancelled',updated=?,error='awake_clock_migration' WHERE batch=? AND phase='deferred'",(now,batch))

                    cancelled.append(batch)

                remaining=max(0,row['due']-reference)

                if remaining==0 or batch:remaining=interval(row['level'],json.loads(row['scene']))

                due=wakefulness.after_awake(self.root,now,remaining)

                db.execute('UPDATE idle_sessions SET due=?,inflight=NULL WHERE scope=?',(due,row['scope']))

            db.execute("INSERT INTO active_meta VALUES('idle_awake_clock_v1',?)",(str(now),))

        if cancelled and (self.root/'social.sqlite3').exists():

            with closing(sqlite3.connect(self.root/'social.sqlite3')) as social,social:

                if social.execute("SELECT 1 FROM sqlite_master WHERE name='queue'").fetchone():

                    for batch in cancelled:social.execute("UPDATE queue SET status='ignored',error='awake_clock_migration' WHERE key=? AND status IN ('pending','failed','ignored')",(batch,))



    def sync(self,now):

        """Initialize from latest conversations, then consume only new source/receipt rows."""

        with closing(self.gate.connect()) as db,db,closing(sqlite3.connect(self.root/'observations.sqlite3')) as source:

            row=db.execute("SELECT value FROM active_meta WHERE key='input_cursor'").fetchone();initial=row is None

            cursor=int(row[0]) if row else 0

            base="chat_type IN ('private','group') AND sender_id!=account_id AND coalesce(json_extract(raw_json,'$.raw_event.post_type'),'message')='message'"

            fields="id,coalesce(json_extract(raw_json,'$.platform'),'qq'),account_id,chat_type,conversation_id,received_at"

            if initial:

                incoming=source.execute('SELECT '+fields+' FROM observations WHERE id IN (SELECT max(id) FROM observations WHERE '+base+" GROUP BY coalesce(json_extract(raw_json,'$.platform'),'qq'),account_id,chat_type,conversation_id)").fetchall()

                cursor=source.execute('SELECT coalesce(max(id),0) FROM observations').fetchone()[0]

            else:incoming=source.execute('SELECT '+fields+' FROM observations WHERE id>? AND '+base+' ORDER BY id LIMIT 1000',(cursor,)).fetchall()

            for oid,platform,account,kind,conversation,stamp in incoming:

                scene={'platform':platform,'account':account,'chat_type':kind,'conversation':conversation};key=scope(scene)

                try:at=datetime.fromisoformat(stamp.replace('Z','+00:00')).timestamp()

                except (ValueError,TypeError):at=now

                old=db.execute('SELECT last_time,last_in FROM idle_sessions WHERE scope=?',(key,)).fetchone()

                if old is None:db.execute('INSERT INTO idle_sessions VALUES(?,?,?,\'\',?,0,?,NULL)',(key,json.dumps(scene),oid,at,wakefulness.after_awake(self.root,now,interval(0,scene))))

                elif oid>old['last_in']:db.execute('UPDATE idle_sessions SET last_in=?,last_time=?,level=0,due=? WHERE scope=?',(oid,max(at,old['last_time']),wakefulness.after_awake(self.root,max(at,old['last_time']),interval(0,scene)),key))

                cursor=max(cursor,oid)

            db.execute("INSERT OR REPLACE INTO active_meta VALUES('input_cursor',?)",(str(cursor),))

            with closing(sqlite3.connect(self.root/'mouth.sqlite3')) as mouth:

                if not mouth.execute("SELECT 1 FROM sqlite_master WHERE name='relay_deliveries'").fetchone():return

                old_cursor=db.execute("SELECT value FROM active_meta WHERE key='output_cursor'").fetchone()

                out_initial=old_cursor is None;stamp,rid=json.loads(old_cursor[0]) if old_cursor else [0,0]

                rows=mouth.execute("SELECT rowid,id,batch_id,updated,json_extract(record_json,'$.platform'),json_extract(record_json,'$.account_id'),json_extract(record_json,'$.chat_type'),json_extract(record_json,'$.conversation_id') FROM relay_deliveries WHERE status='sent' AND record_json IS NOT NULL AND (updated>? OR (updated=? AND rowid>?)) ORDER BY updated,rowid",(stamp,stamp,rid)).fetchall()

            for rid,did,batch,at,platform,account,kind,conversation in rows:

                stamp=at

                if kind not in ('private','group'):continue

                scene={'platform':platform,'account':account,'chat_type':kind,'conversation':conversation};key=scope(scene)

                old=db.execute('SELECT * FROM idle_sessions WHERE scope=?',(key,)).fetchone()

                if old is None:continue # Do not initiate recurring contact with a never-participating recipient.

                db.execute('UPDATE idle_sessions SET last_out=?,last_time=? WHERE scope=?',(did,max(at,old['last_time']),key))

                if not out_initial and old['inflight']!=batch:db.execute('UPDATE idle_sessions SET due=? WHERE scope=?',(wakefulness.after_awake(self.root,at,interval(old['level'],scene)),key))

            if rows:rid=rows[-1][0];stamp=rows[-1][3]

            db.execute("INSERT OR REPLACE INTO active_meta VALUES('output_cursor',?)",(json.dumps([stamp,rid]),))



    def sync_roster(self,now):

        with closing(self.gate.connect()) as db:

            sessions=[dict(r) for r in db.execute('SELECT scope,scene,inflight FROM idle_sessions')]

        checked=[(s,self.roster(json.loads(s['scene']))) for s in sessions]

        with closing(self.gate.connect()) as db,db:

            for session,allowed in checked:

                reason='roster_unavailable' if allowed is None else None if allowed else 'not_in_current_roster'

                db.execute('INSERT OR REPLACE INTO idle_scope_status VALUES(?,?,?,?)',(session['scope'],allowed,now,reason))

                if allowed is False and session['inflight']:

                    batch=session['inflight']

                    db.execute("UPDATE active_tasks SET status='cancelled',finished=?,error=? WHERE id=? AND status='queued'",(now,reason,batch))

                    db.execute("UPDATE admissions SET phase='cancelled',updated=?,error=? WHERE batch=? AND phase='deferred'",(now,reason,batch))

                    db.execute('UPDATE idle_sessions SET inflight=NULL WHERE scope=?',(session['scope'],))



    def reconcile(self,now):

        """Next backoff starts on confirmed delivery or a completed silence decision."""

        cfg=active_gate.config(self.root)

        summaries=[]

        with closing(self.gate.connect()) as db,db:

            for task in db.execute("SELECT * FROM active_tasks WHERE status='queued'").fetchall():

                result=active_gate.delivery_outcome(self.root,task['id'])

                if not result:

                    admission=db.execute('SELECT phase FROM admissions WHERE batch=?',(task['id'],)).fetchone()

                    if admission and admission['phase']=='failed':

                        retry=None

                        if task['kind']=='interest' and (self.root/'interest.sqlite3').exists():

                            with closing(sqlite3.connect(self.root/'interest.sqlite3')) as interests:

                                if interests.execute("SELECT 1 FROM sqlite_master WHERE name='exploration_retries'").fetchone():

                                    retry=interests.execute("SELECT r.attempts,r.next_retry FROM exploration_retries r JOIN exploration_runs e USING(batch) WHERE r.batch=? AND e.status='explored'",(task['id'],)).fetchone()

                        if retry and retry[0]<3:

                            with closing(sqlite3.connect(self.root/'mouth.sqlite3')) as mouth:

                                generated=mouth.execute('SELECT 1 FROM outputs WHERE batch_id=?',(task['id'],)).fetchone()

                            if not generated:

                                with closing(sqlite3.connect(self.root/'social.sqlite3')) as social,social:

                                    social.execute("DELETE FROM queue WHERE key=? AND status IN ('failed','ignored','done')",(task['id'],))

                                db.execute("UPDATE admissions SET phase='deferred',due=?,updated=?,error='summary_retry' WHERE batch=?",(retry[1],now,task['id']))

                                db.execute('UPDATE active_tasks SET emitted=0 WHERE id=?',(task['id'],))

                                continue

                        result=(False,now,'generation_failed')

                    elif task['kind']=='idle' and not identity_idle.task_valid(self.root,json.loads(task['scene']),json.loads(task['identity_context']) if task['identity_context'] else None,task['id']):result=(False,now,'identity_or_person_activity_changed')

                    elif task['kind']=='idle' and latest(self.root,json.loads(task['scene']),task['id'])!=json.loads(task['baseline']):result=(False,now,'new_activity')

                    else:continue

                ok,at,error=result;db.execute('UPDATE active_tasks SET status=?,finished=?,error=? WHERE id=?',('completed' if ok else 'cancelled',at,error,task['id']))

                if task['kind']=='idle':

                    if not identity_idle.finish(self,db,task,ok,at,now,cfg['failure_retry_seconds']):

                        key=scope(json.loads(task['scene']));old=db.execute('SELECT * FROM idle_sessions WHERE scope=?',(key,)).fetchone()

                        if old and old['inflight']==task['id']:

                            unchanged=old['last_in']==json.loads(task['baseline'])[0]

                            level=min(6,old['level']+1) if ok and unchanged else old['level']

                            due=wakefulness.after_awake(self.root,at,interval(level,json.loads(task['scene']))) if ok and unchanged else max(old['due'],wakefulness.after_awake(self.root,now,cfg['failure_retry_seconds']))

                            db.execute('UPDATE idle_sessions SET level=?,due=?,inflight=NULL WHERE scope=?',(level,due,key))

                else:

                    is_awake=awake(self.root,now)

                    db.execute('UPDATE interest_clock SET inflight=NULL,sleeping=?,due=? WHERE id=1',(not is_awake,now+random_interest(cfg) if is_awake else None))

                    summaries.append(task['id'])

                if not ok:db.execute("UPDATE admissions SET phase='cancelled',updated=? WHERE batch=? AND phase='deferred'",(now,task['id']))

        if summaries:

            module=__import__((__package__+'.interest_memory') if __package__ else 'interest_memory',fromlist=['Interest'])

            for batch in summaries:module.Interest(self.root).enqueue(batch)



    def packet(self,row):

        scene=json.loads(row['scene']);interest=row['kind']=='interest'

        intention=(autonomy.TASK+'\n'+INTEREST_TASK) if interest else IDLE_TASK

        shared=json.loads(row['identity_context']) if row.get('identity_context') else None

        person_note=identity_idle.task_note(self.root,shared) if shared else None

        if shared:intention='这是一轮触发主动：已认定同一人的多个私聊账号整体已太久没有新消息，共用空闲计时和退避。当前会话按平台优先级选定，用于本轮上下文；不代表这个平台单独被冷落，也不是新来信。结合记录、身份关系、记忆和自身状态自然找话题，或保持沉默。'

        return {'batch_id':row['id'],'scene':scene,'active_type':row['kind'],'wake_depth':1,'engine':'astrbot','image_options':[],

                'internal_wake':{'kind':'autonomous' if interest else 'idle_proactive','wake_id':row['id'],'intent':intention,**({'人物共享主动':person_note} if person_note else {})},

                'new_messages':[{'id':json.loads(row['baseline'])[0] if not interest else 0,'observation':{'internal_event':True,'platform':scene['platform'],'account_id':scene['account'],'chat_type':scene['chat_type'],'conversation_id':scene['conversation'],'sender_id':scene['account'],'received_at':datetime.now(UTC).isoformat(),'raw_event':{'message':[]}}}]}



    def due(self,permitted,now=None):

        now=time.time() if now is None else now;cfg=active_gate.config(self.root)

        if not cfg['enabled']:return []

        self.migrate_idle_clock(now);self.sync(now);self.sync_roster(now);identity_idle.sync(self,now);self.reconcile(now);packets=[]

        with closing(self.gate.connect()) as db,db:

            clock=db.execute('SELECT * FROM interest_clock WHERE id=1').fetchone()

            if not awake(self.root,now):

                # Interest never builds a nighttime backlog. Idle deadlines are retained.

                db.execute('UPDATE interest_clock SET sleeping=1,due=NULL WHERE id=1')

                if clock['inflight']:

                    adm=db.execute('SELECT phase FROM admissions WHERE batch=?',(clock['inflight'],)).fetchone()

                    if not adm or adm['phase']=='deferred':

                        db.execute("UPDATE active_tasks SET status='cancelled',error='sleep_skipped',finished=? WHERE id=?",(now,clock['inflight']))

                        db.execute('UPDATE interest_clock SET inflight=NULL WHERE id=1')

                        db.execute("UPDATE admissions SET phase='cancelled',updated=? WHERE batch=? AND phase='deferred'",(now,clock['inflight']))

                return []

            if cfg['interest_enabled']:

                if clock['sleeping'] or clock['due'] is None:

                    db.execute('UPDATE interest_clock SET sleeping=0,due=? WHERE id=1',(now+random_interest(cfg),))

                elif not clock['inflight'] and clock['due']<=now:

                    wid='active:interest:'+uuid.uuid4().hex

                    db.execute("INSERT INTO active_tasks(id,kind,scene,baseline,status,created) VALUES(?,'interest',?,'[]','queued',?)",(wid,json.dumps(autonomy.SCENE),now))

                    db.execute('UPDATE interest_clock SET inflight=? WHERE id=1',(wid,))

            if cfg['idle_enabled']:

                for session in db.execute('SELECT * FROM idle_sessions WHERE due<=? AND inflight IS NULL AND scope NOT IN (SELECT scope FROM identity_idle_membership) AND scope IN (SELECT scope FROM idle_scope_status WHERE allowed=1) ORDER BY due LIMIT 20',(now,)).fetchall():

                    scene=json.loads(session['scene'])

                    if not idle_scene_enabled(cfg,scene,self.root):continue
                    if not permitted({'scene':scene,'internal_wake':{'kind':'idle_proactive'}}):continue

                    wid='active:idle:'+uuid.uuid4().hex;baseline=[session['last_in'],session['last_out']]

                    db.execute("INSERT INTO active_tasks(id,kind,scene,baseline,status,created) VALUES(?,'idle',?,?,'queued',?)",(wid,session['scene'],json.dumps(baseline),now))

                    db.execute('UPDATE idle_sessions SET inflight=? WHERE scope=?',(wid,session['scope']))

                identity_idle.due(self,db,permitted,now)

            candidates=[dict(r) for r in db.execute("SELECT * FROM active_tasks WHERE status='queued' AND emitted<? ORDER BY created LIMIT 20",(now-30,))]

        for row in candidates:

            packet=self.packet(row)

            if row['kind']=='idle' and self.roster(packet['scene']) is not True:continue

            if permitted(packet) and active_gate.ready(self.root,row['id'],now=now) and valid(self.root,row['id'],now):

                with closing(self.gate.connect()) as db,db:db.execute('UPDATE active_tasks SET emitted=? WHERE id=?',(now,row['id']))

                packets.append(packet)

        return packets

