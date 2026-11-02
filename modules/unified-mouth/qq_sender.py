"""Send post-cutover outputs to private conversations allowed by configuration."""
import json,os,sqlite3,time,urllib.request,urllib.error
from pathlib import Path
from image_library import segments_for

ROOT=Path('/opt/astrbot-mouth')
ARCHIVE=Path('/opt/astrbot-mouth/observations.sqlite3')
ACCOUNT='100000002'

def initialize(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS deliveries(
      batch_id TEXT PRIMARY KEY,status TEXT NOT NULL,updated REAL NOT NULL,
      error TEXT,message_id TEXT,record_json TEXT);
    ''')
    db.execute('CREATE TABLE IF NOT EXISTS media_outputs(batch_id TEXT PRIMARY KEY,paths_json TEXT NOT NULL)');db.commit()
    # An interrupted HTTP send is ambiguous; never automatically repeat it.
    with db:db.execute("UPDATE deliveries SET status='uncertain',error='interrupted_send' WHERE status='sending'")

def eligible(packet,config):
    scene=packet.get('scene',{})
    if scene.get('platform')!='qq' or str(scene.get('account'))!=ACCOUNT:return 'wrong_account'
    recipient=str(scene.get('conversation',''))
    if scene.get('chat_type')!='private' or not recipient.isascii() or not recipient.isdecimal() or int(recipient)<=0 or recipient==ACCOUNT:return 'not_allowed'
    listed=recipient in [str(x) for x in config.get('allow_private',[])]
    if not listed and config.get('allow_all_private') is not True:return 'not_allowed'
    ids=[m['id'] for m in packet.get('new_messages',[])]
    if not ids or min(ids)<=config['cutover_observation_id']:return 'before_cutover'
    if not listed and min(ids)<=config.get('all_private_cutover_observation_id',config['cutover_observation_id']):return 'before_cutover'
    return None

class GatewayRejected(ValueError):
    definite_failure=True

def onebot(action,params):
    cfg=json.loads(Path('/opt/snowluma/config/onebot_'+ACCOUNT+'.json').read_text('utf8'))
    net=cfg['networks']['httpServers'][0]
    if net['host']!='127.0.0.1' or int(net['port'])!=13000:raise ValueError('Unexpected gateway endpoint')
    req=urllib.request.Request('http://127.0.0.1:13000/'+action,data=json.dumps(params,ensure_ascii=False).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+net['accessToken']})
    timeout=120 if any(p.get('type')=='video' for p in params.get('message',[]) if isinstance(p,dict)) else 20
    with urllib.request.urlopen(req,timeout=timeout) as r:result=json.load(r)
    if result.get('retcode')!=0 or result.get('status')!='ok':raise GatewayRejected('Gateway rejected request: '+json.dumps({k:str(result.get(k,''))[:350] for k in ('retcode','status','message','wording')},ensure_ascii=False))
    return result.get('data',{})

def send_one(db,config,call=onebot):
    row=db.execute('''SELECT o.batch_id,o.text,j.packet,coalesce(m.paths_json,'[]') FROM outputs o JOIN jobs j ON j.batch_id=o.batch_id LEFT JOIN media_outputs m ON m.batch_id=o.batch_id
      WHERE o.sent_to_qq=0 AND NOT EXISTS(SELECT 1 FROM deliveries d WHERE d.batch_id=o.batch_id)
      ORDER BY o.created LIMIT 1''').fetchone()
    if not row:return False
    batch,text,raw,images_json=row;packet=json.loads(raw);images=json.loads(images_json)
    reason=eligible(packet,config)
    if reason:
        with db:db.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?)',(batch,'skipped',time.time(),reason,None,None))
        return True
    recipient=str(packet['scene']['conversation'])
    try:
        message=([{'type':'text','data':{'text':text}}] if text else [])+segments_for(images,packet.get('image_options',[]))
        if not message:raise ValueError('Empty message')
    except Exception:
        with db:db.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?)',(batch,'failed',time.time(),'invalid_image_or_text',None,None))
        return True
    # Do not send an obsolete answer if a newer inbound message arrived.
    source=sqlite3.connect(ARCHIVE.resolve().as_uri()+'?mode=ro',uri=True)
    try:latest=source.execute('SELECT max(id) FROM observations WHERE account_id=? AND chat_type=? AND conversation_id=?',(ACCOUNT,'private',recipient)).fetchone()[0]
    finally:source.close()
    if latest and latest>max(m['id'] for m in packet['new_messages']):
        with db:db.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?)',(batch,'skipped',time.time(),'newer_inbound_exists',None,None))
        return True
    # Probe before admitting the irreversible send. A probe failure is safe to retry.
    try:
        who=call('get_login_info',{})
        if str(who.get('user_id'))!=ACCOUNT:raise ValueError('Account mismatch')
    except Exception:
        print('QQ preflight unavailable; keeping reply queued.',flush=True);return False
    with db:db.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?)',(batch,'sending',time.time(),None,None,None))
    try:
        # Array text segment prevents CQ text from being interpreted as commands/media.
        data=call('send_private_msg',{'user_id':int(recipient),'message':message})
        record={'direction':'outgoing','platform':'qq','account_id':ACCOUNT,'chat_type':'private','conversation_id':recipient,'batch_id':batch,'sent_at':time.time(),'message_id':str(data.get('message_id','')),'text':text}
        record['image_paths']=images
        with db:
            db.execute("UPDATE deliveries SET status='sent',updated=?,message_id=?,record_json=? WHERE batch_id=?",(time.time(),record['message_id'],json.dumps(record,ensure_ascii=False),batch))
            db.execute('UPDATE outputs SET sent_to_qq=1 WHERE batch_id=?',(batch,))
        print('Brain-mouth reply sent to allowed private conversation.',flush=True)
    except Exception as e:
        # Even an apparent transport error can happen after QQ accepted the message.
        with db:db.execute("UPDATE deliveries SET status='uncertain',updated=?,error=? WHERE batch_id=?",(time.time(),type(e).__name__,batch))
        print('QQ send outcome uncertain; manual review required, no automatic resend.',flush=True)
    return True

def main():
    os.umask(0o077)
    db=sqlite3.connect(ROOT/'mouth.sqlite3');initialize(db)
    while True:
        config=json.loads((ROOT/'delivery.json').read_text('utf8'))
        if config.get('enabled') is True:send_one(db,config)
        time.sleep(2)

if __name__=='__main__':main()
