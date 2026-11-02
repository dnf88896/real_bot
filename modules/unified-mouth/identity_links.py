"""Sourced account-pair judgments made by the existing private-profile API.

This registry changes context only. Raw memories, profiles, affinity and timers
stay keyed by their original platform accounts in this first version.
"""
import hashlib,json,sqlite3,time
import private_chat_state
from datetime import datetime,timezone,timedelta
from pathlib import Path
from contextlib import closing

STATES={'unknown':'未知','suspected':'疑似同一人','same':'认定同一人','different':'认定不同人'}
PLATFORMS={'qq','wechat','bilibili','heybox'}

def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS identity_nodes(id TEXT PRIMARY KEY,platform TEXT,account TEXT,user_id TEXT,name TEXT,UNIQUE(platform,account,user_id));
    CREATE TABLE IF NOT EXISTS identity_links(a TEXT,b TEXT,state TEXT,reason TEXT,sources TEXT,job TEXT,updated REAL,PRIMARY KEY(a,b));
    CREATE TABLE IF NOT EXISTS identity_link_history(id INTEGER PRIMARY KEY,a TEXT,b TEXT,old_state TEXT,new_state TEXT,reason TEXT,sources TEXT,job TEXT,created REAL,UNIQUE(a,b,job));
    ''')

def node(platform,account,user,name=''):
    key=hashlib.sha256(json.dumps([platform,str(account),str(user)],ensure_ascii=False).encode()).hexdigest()[:24]
    return {'id':key,'platform':platform,'account':str(account),'user_id':str(user),'name':name or str(user)}

def text(raw):
    parts=raw.get('raw_event',{}).get('message',[])
    return parts if isinstance(parts,str) else ''.join(x.get('data',{}).get('text','') for x in parts if x.get('type')=='text')

def event(row):
    oid,stamp,encoded=row;raw=json.loads(encoded)
    return {'id':oid,'time':stamp,'platform':raw.get('platform','qq'),
        'account':str(raw['account_id']),'user_id':str(raw['sender_id']),
        'name':raw.get('raw_event',{}).get('sender',{}).get('nickname') or str(raw['sender_id']),
        'text':text(raw)[:1800]}

def context(root,db,account,user,events):
    initialize(db)
    archive=Path(root)/'identity-source.sqlite3'
    events=[e for e in events if not private_chat_state.private_scope(root,e['raw'])]
    if not events:raise ValueError('No public profile evidence')
    nodes={};evidence={}
    with closing(sqlite3.connect(archive.resolve().as_uri()+'?mode=ro',uri=True)) as src:
        src.create_function('profile_public',1,lambda raw: not private_chat_state.private_scope(root,json.loads(raw)))
        rows=src.execute("SELECT id,received_at,raw_json FROM observations WHERE chat_type='private' AND sender_id=conversation_id AND sender_id!=account_id AND profile_public(raw_json) ORDER BY id DESC").fetchall()
        for row in rows:
            e=event(row)
            if e['platform'] not in PLATFORMS:continue
            n=node(e['platform'],e['account'],e['user_id'],e['name'])
            if len(nodes)<200 or n['id'] in nodes:nodes.setdefault(n['id'],n)
        for e in events:
            value=event((e['id'],e['time'],json.dumps(e['raw'],ensure_ascii=False)));evidence[value['id']]=value
            n=node(value['platform'],value['account'],value['user_id'],value['name']);nodes.setdefault(n['id'],n)
        # Identity-bearing context and adjacent replies resolve statements such as "那就是我".
        hits=src.execute("SELECT id FROM observations WHERE chat_type='private' AND (lower(text_content) LIKE '%qq%' OR text_content LIKE '%微信%' OR text_content LIKE '%账号%' OR text_content LIKE '%是我%' OR text_content LIKE '%本人%' OR text_content LIKE '%同一个人%') AND profile_public(raw_json) ORDER BY id DESC LIMIT 60").fetchall()
        for (oid,) in hits:
            for row in src.execute("SELECT id,received_at,raw_json FROM observations WHERE chat_type='private' AND id BETWEEN ? AND ? AND profile_public(raw_json) ORDER BY id",(oid-1,oid+1)):
                if len(evidence)<120:evidence.setdefault(row[0],event(row))
        platform=events[-1]['raw'].get('platform','qq')
        current=next((n for n in nodes.values() if n['platform']==platform and n['account']==str(account) and n['user_id']==str(user)),None)
        if not current:raise ValueError('Current profile account missing from identity catalog')
        for row in db.execute('SELECT id,platform,account,user_id,name FROM identity_nodes'):
            nodes.setdefault(row[0],dict(zip(('id','platform','account','user_id','name'),row)))
        links=[]
        edges=db.execute('SELECT a,b,state,reason,sources FROM identity_links').fetchall()
        connected={current['id']}
        while True:
            expanded=connected|{other for a,b,state,_,_ in edges if state=='same' for key,other in ((a,b),(b,a)) if key in connected}
            if expanded==connected:break
            connected=expanded
        for a,b,state,reason,sources in edges:
            if current['id'] not in (a,b) and not (a in connected and b in connected):continue
            links.append({'a':a,'b':b,'state':state,'reason':reason,'source_ids':json.loads(sources)})
            for oid in json.loads(sources):
                row=src.execute('SELECT id,received_at,raw_json FROM observations WHERE id=?',(oid,)).fetchone()
                if row and not private_chat_state.private_scope(root,json.loads(row[2])):evidence.setdefault(oid,event(row))
    output=Path(root)/'output-memory'/'output.sqlite3'
    if output.exists():
        with closing(sqlite3.connect(output.resolve().as_uri()+'?mode=ro',uri=True)) as out:
            out.create_function('profile_public',1,lambda raw: not private_chat_state.private_scope(root,json.loads(raw)))
            for e in evidence.values():
                try:
                    at=datetime.fromisoformat(e['time'].replace('Z','+00:00')).astimezone(timezone.utc)
                    before=at.isoformat(timespec='milliseconds').replace('+00:00','Z')
                    after=(at-timedelta(minutes=30)).isoformat(timespec='milliseconds').replace('+00:00','Z')
                    rows=out.execute("SELECT event_key,event_time,raw_json FROM events WHERE platform=? AND account_id=? AND chat_type='private' AND conversation_id=? AND phase='sent' AND event_time BETWEEN ? AND ? AND coalesce(json_extract(raw_json,'$.text'),'')!='' AND profile_public(raw_json) ORDER BY event_time DESC LIMIT 2",(e['platform'],e['account'],e['user_id'],after,before)).fetchall()
                    e['preceding_bot_messages']=[{'source_ref':key,'time':stamp,'text':json.loads(raw).get('text','')[:1000]} for key,stamp,raw in reversed(rows)]
                except (ValueError,TypeError):
                    pass
    return {'current':current['id'],'accounts':list(nodes.values()),'existing_links':links,'evidence':list(evidence.values())}

def validate(updates,ctx):
    if not isinstance(updates,list) or len(updates)>12:raise ValueError('Invalid identity judgments')
    known={n['id'] for n in ctx['accounts']};allowed={e['id'] for e in ctx['evidence']};seen=set()
    for u in updates:
        if not isinstance(u,dict) or set(u)!={'a','b','state','reason','source_ids'}:raise ValueError('Invalid identity judgment shape')
        if u['a'] not in known or u['b'] not in known or u['a']==u['b'] or ctx['current'] not in (u['a'],u['b']):raise ValueError('Unknown or out-of-scope identity')
        pair=tuple(sorted((u['a'],u['b'])))
        if pair in seen:raise ValueError('Duplicate identity pair')
        seen.add(pair)
        if u['state'] not in STATES or not isinstance(u['reason'],str) or not 1<=len(u['reason'])<=500:raise ValueError('Invalid identity state/reason')
        refs=u['source_ids']
        if not isinstance(refs,list) or not 1<=len(refs)<=30 or any(type(i)is not int or i not in allowed for i in refs):raise ValueError('Unsupported identity evidence')

def apply(db,updates,ctx,job):
    validate(updates,ctx)
    nodes={n['id']:n for n in ctx['accounts']}
    for u in updates:
        a,b=sorted((u['a'],u['b']))
        if db.execute('SELECT 1 FROM identity_link_history WHERE a=? AND b=? AND job=?',(a,b,job)).fetchone():continue
        for key in (a,b):
            n=nodes[key]
            db.execute('INSERT OR REPLACE INTO identity_nodes VALUES(?,?,?,?,?)',(key,n['platform'],n['account'],n['user_id'],n['name']))
        old=db.execute('SELECT state FROM identity_links WHERE a=? AND b=?',(a,b)).fetchone()
        refs=json.dumps(u['source_ids']);stamp=time.time()
        db.execute('INSERT INTO identity_link_history(a,b,old_state,new_state,reason,sources,job,created) VALUES(?,?,?,?,?,?,?,?)',(a,b,old[0] if old else 'unknown',u['state'],u['reason'],refs,job,stamp))
        db.execute('INSERT OR REPLACE INTO identity_links VALUES(?,?,?,?,?,?,?)',(a,b,u['state'],u['reason'],refs,job,stamp))

def snapshot(root,platform,account,user):
    path=Path(root)/'basic.sqlite3'
    if not path.exists():return {}
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='identity_links'").fetchone():return {}
        nodes={r[0]:dict(zip(('id','平台','机器人账号','用户ID','昵称'),r)) for r in db.execute('SELECT id,platform,account,user_id,name FROM identity_nodes')}
        current=node(platform,account,user)['id'];edges=db.execute('SELECT a,b,state,reason,sources FROM identity_links').fetchall()
    adjacent={}
    for a,b,state,_,_ in edges:
        if state=='same':adjacent.setdefault(a,set()).add(b);adjacent.setdefault(b,set()).add(a)
    component={current};pending=[current]
    while pending:
        key=pending.pop()
        for other in adjacent.get(key,set())-component:component.add(other);pending.append(other)
    conflicts=[(a,b) for a,b,state,_,_ in edges if state=='different' and a in component and b in component]
    relations=[]
    for a,b,state,reason,refs in edges:
        if current not in (a,b):continue
        other=b if a==current else a
        relations.append({'对方账号':nodes[other],'状态':STATES[state],
            '生效状态':'冲突待复核' if conflicts and state=='same' else STATES[state],
            '依据':reason,'来源记录':json.loads(refs)})
    if not relations and len(component)==1:return {}
    return {'当前账号':{'平台':platform,'用户ID':str(user)},'身份关系':relations,
        '同一人的已关联账号':[] if conflicts else [nodes[k] for k in sorted(component) if k in nodes],
        '存在冲突':bool(conflicts),
        '说明':'这些是资料整理程序根据来源记录作出、可以更正的身份判断。疑似不当作已确认；冲突时不扩展合并。这里只关联账号认知，不合并原始记忆、好感度或定时任务。'}
