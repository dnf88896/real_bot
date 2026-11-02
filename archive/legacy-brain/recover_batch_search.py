import json,sqlite3,time,hashlib,urllib.request
from pathlib import Path
import mouth
batch='b557feac-6334-4a21-9aa5-2582fb3b767e'
c=sqlite3.connect('/opt/qq-mouth/mouth.sqlite3');c.row_factory=sqlite3.Row
row=c.execute('SELECT rowid,* FROM outputs WHERE batch_id=?',(batch,)).fetchone()
state=c.execute('SELECT status FROM relay_jobs WHERE batch_id=?',(batch,)).fetchone()
assert row and state['status']=='blocked' and not row['sent_to_qq']
assert not c.execute('SELECT 1 FROM relay_deliveries WHERE batch_id=?',(batch,)).fetchone()
calls=mouth.parse_web_calls(row['text']);assert calls and len(calls)==4
body=json.loads(row['request_json']);key=json.loads((mouth.ROOT/'api.json').read_text())['api_key'];requests=0
session=mouth.Session()
def transport(payload,timeout):
 global requests
 requests+=1
 if requests==1:return {'choices':[{'message':{'content':row['text']},'finish_reason':'stop'}]}
 req=urllib.request.Request('https://api.deepseek.com/chat/completions',data=json.dumps(payload,ensure_ascii=False).encode(),headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=timeout) as response:return json.load(response)
text,usage=mouth.invoke(body,transport,session)
assert usage['web_calls']>=4 and mouth.parse_web_calls(text) is None
backup=Path('/opt/qq-mouth/blocked-search-original-20261022.json');backup.write_text(json.dumps(dict(row),ensure_ascii=False));backup.chmod(0o600)
with c:
 current=c.execute('SELECT text,sent_to_qq FROM outputs WHERE batch_id=?',(batch,)).fetchone()
 assert current['text']==row['text'] and not current['sent_to_qq']
 assert c.execute('SELECT status FROM relay_jobs WHERE batch_id=?',(batch,)).fetchone()['status']=='blocked'
 assert not c.execute('SELECT 1 FROM relay_deliveries WHERE batch_id=?',(batch,)).fetchone()
 c.execute('UPDATE outputs SET text=?,usage_json=? WHERE batch_id=?',(text,json.dumps(usage),batch))
 c.execute("UPDATE relay_jobs SET status='pending',source_hash=?,plan=NULL,attempts=0,next_retry=0,error=NULL WHERE batch_id=?",(hashlib.sha256(text.encode()).hexdigest(),batch))
print(json.dumps({'recovered':True,'web_calls':usage['web_calls'],'web_audit':usage['web_audit'],'final_chars':len(text)},ensure_ascii=False))
