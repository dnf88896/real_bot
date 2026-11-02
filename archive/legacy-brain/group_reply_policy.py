"""One durable Bernoulli trial per new group observation; no reply direction."""
import json,secrets,time,math
from pathlib import Path

def required(db,items,config_path):
    config={'probability':0.02,'start_observation_id':0}
    if Path(config_path).exists():config.update(json.loads(Path(config_path).read_text('utf8')))
    probability=config['probability'];start=config['start_observation_id']
    if type(probability) not in (int,float) or not math.isfinite(probability) or not 0<=probability<=1:raise ValueError('Invalid group reply probability')
    if type(start) is not int or start<0:raise ValueError('Invalid group reply cutover')
    db.execute('CREATE TABLE IF NOT EXISTS group_reply_draws(observation_id INTEGER PRIMARY KEY,account TEXT,conversation TEXT,probability REAL,draw INTEGER,hit INTEGER,created REAL)')
    hits=[]
    with db:
        db.execute('BEGIN IMMEDIATE')
        for item in items:
            raw=item['observation'];oid=item['id']
            if raw.get('internal_event') or raw.get('chat_type')!='group' or oid<start:continue
            row=db.execute('SELECT hit FROM group_reply_draws WHERE observation_id=?',(oid,)).fetchone()
            if row is None:
                draw=secrets.randbelow(1000000);hit=int(draw<probability*1000000)
                db.execute('INSERT OR IGNORE INTO group_reply_draws VALUES(?,?,?,?,?,?,?)',(oid,str(raw.get('account_id')),str(raw.get('conversation_id')),probability,draw,hit,time.time()))
                row=db.execute('SELECT hit FROM group_reply_draws WHERE observation_id=?',(oid,)).fetchone()
            if row[0]:hits.append(oid)
    return hits
