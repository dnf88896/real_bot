import relay,sqlite3,json,time
from pathlib import Path
batch='7866fa13-b818-4f90-b95f-577ead66062d'
c=sqlite3.connect('/opt/qq-mouth/mouth.sqlite3');c.row_factory=sqlite3.Row
row=c.execute('SELECT o.text,j.packet,r.plan,r.directory,r.source_hash FROM outputs o JOIN jobs j USING(batch_id) JOIN relay_jobs r USING(batch_id) WHERE o.batch_id=?',(batch,)).fetchone()
old=[dict(r) for r in c.execute('SELECT * FROM relay_deliveries WHERE batch_id=?',(batch,))]
assert old and all(r['status']=='failed' and not r['message_id'] for r in old)
assert relay.digest(row['text'])==row['source_hash']
packet=json.loads(row['packet']);items=relay.split_bubbles(row['text'],relay.validate(json.loads(row['plan']),row['text'],json.loads(row['directory'])))
for item in items:relay.compile_parts(row['text'],item['parts'],packet.get('image_options',[]))
p=Path('/opt/qq-mouth/newline-recovery-original.json');p.write_text(json.dumps(old,ensure_ascii=False));p.chmod(0o600)
with c:
 assert all(r[0]=='failed' for r in c.execute('SELECT status FROM relay_deliveries WHERE batch_id=?',(batch,)))
 c.execute('DELETE FROM relay_deliveries WHERE batch_id=?',(batch,))
 for n,item in enumerate(items):c.execute('INSERT INTO relay_deliveries(id,batch_id,ordinal,destination,parts,updated) VALUES(?,?,?,?,?,?)',(batch+':'+str(n),batch,n,json.dumps(item['destination']),json.dumps(item['parts']),time.time()))
 c.execute("UPDATE relay_jobs SET status='planned',error=NULL WHERE batch_id=?",(batch,))
print('Recovered unsent reply into',len(items),'bubbles')
