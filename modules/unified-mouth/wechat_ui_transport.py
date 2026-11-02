"""Unprivileged relay client for the single WeChat UI executor."""
import base64,hashlib,json,socket,time,uuid
from pathlib import Path
from contextlib import closing
from wechat_client import Rejected,Uncertain

class NavigationPaused(Rejected):
    navigation_paused=True


def rpc(action,**body):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as conn:
        conn.settimeout(12);conn.connect('/run/wechat-ui-broker/queue.sock')
        conn.sendall(json.dumps({'action':action,**body},ensure_ascii=False).encode()+b'\n')
        with conn.makefile('rb') as stream:raw=stream.readline(1048577)
    if len(raw)>1048576:raise Uncertain('WeChat broker result too large')
    result=json.loads(raw)
    if result.get('ok') is not True:raise Rejected('WeChat queue rejected: '+result.get('error','unknown'))
    return result

def messages(destination,components,root):
    from wechat_bridge import db
    result=[];parts=[]
    def flush():
        if parts:result.append({'parts':list(parts)});parts.clear()
    for item in components:
        kind=item['type'];data=item.get('data',{})
        if kind=='reply':continue
        if kind=='text':parts.append({'type':'text','text':data['text']})
        elif kind=='at':
            if destination['chat_type']!='group' or data.get('platform')!='wechat':raise Rejected('Invalid WeChat @')
            with closing(db(root)) as connection:
                row=connection.execute('SELECT name FROM members WHERE account=? AND conversation=? AND id=?',(destination['account'],destination['id'],str(data['qq']))).fetchone()
                if not row or not row[0]:raise Rejected('Unknown WeChat member')
                count=connection.execute('SELECT count(*) FROM members WHERE account=? AND conversation=? AND name=?',(destination['account'],destination['id'],row[0])).fetchone()[0]
                if count!=1:raise Rejected('Ambiguous WeChat member name')
            parts.append({'type':'mention','name':row[0]})
        elif kind=='image':
            flush();value=data.get('file','')
            if not value.startswith('base64://'):raise Rejected('Expected relay-validated image bytes')
            blob=base64.b64decode(value[9:],validate=True)
            if not 0<len(blob)<=4*1024*1024:raise Rejected('Invalid image size')
            folder=Path(root)/'wechat-outgoing';folder.mkdir(mode=0o700,exist_ok=True)
            path=folder/(hashlib.sha256(blob).hexdigest()+'.image')
            if not path.exists():
                temp=path.with_suffix('.'+uuid.uuid4().hex+'.tmp');temp.write_bytes(blob);temp.chmod(0o600);temp.replace(path)
            result.append({'image':str(path)})
        elif kind in ('voice','video'):
            flush();result.append({'voice':data['file'],'text':data['text']} if kind=='voice' else {'video':data['file']})
        else:raise Rejected('Unsupported WeChat component: '+kind)
    flush()
    if not result:raise Rejected('Empty WeChat delivery')
    return result

def send(destination,components,root,delivery_id):
    if not delivery_id:raise Rejected('Stable WeChat delivery ID required')
    from wechat_bridge import directory
    matching=[r for r in directory(root) if r['id']==destination['id'] and r['account']==destination['account'] and r['chat_type']==destination['chat_type']]
    if len(matching)!=1 or not matching[0].get('name'):raise Rejected('Unknown WeChat recipient')
    key='relay-'+hashlib.sha256(str(delivery_id).encode()).hexdigest()
    request={'id':key,'conversation':matching[0]['name'],'roomid':destination['id'],
        'chat_type':destination['chat_type'],'record_memory':False,'messages':messages(destination,components,root)}
    try:rpc('submit',request=request)
    except Rejected:raise
    except Exception as error:raise Uncertain('WeChat queue admission unavailable') from error
    deadline=time.monotonic()+240
    while time.monotonic()<deadline:
        state=rpc('status',id=key)
        if state['status']=='sent':
            result=state['result'];receipts=result.get('receipts') or []
            if not receipts:raise Uncertain('Missing WeChat native receipt')
            return {'message_id':str(receipts[-1]['receipt']['MessageID']),
                    'platform_receipt':result,'ui_job':key}
        if state['status']=='paused_navigation':raise NavigationPaused('WeChat target paused: '+str(state.get('error')))
        if state['status'] in ('failed','cancelled','partial','uncertain','interrupted'):
            # The worker knows precisely whether anything may have been sent.
            if state['status'] in ('failed','cancelled'):raise Rejected('WeChat UI delivery failed: '+str(state.get('error')))
            raise Uncertain('WeChat UI delivery requires reconciliation: '+str(state.get('error')))
        time.sleep(0.5)
    rpc('cancel',id=key)
    raise Uncertain('WeChat queue timed out; cancellation requested, no automatic resend')
