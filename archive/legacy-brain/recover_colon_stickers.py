import relay,sqlite3,json,time
from pathlib import Path
batch='f08734b5-5f90-4ef5-9487-d06dcf7ae104'
c=sqlite3.connect('/opt/qq-mouth/mouth.sqlite3');c.row_factory=sqlite3.Row
source,raw=c.execute('SELECT o.text,j.packet FROM outputs o JOIN jobs j USING(batch_id) WHERE batch_id=?',(batch,)).fetchone();packet=json.loads(raw)
rows=[dict(r) for r in c.execute("SELECT * FROM relay_deliveries WHERE batch_id=? AND status='failed' AND error='Sticker missing or ambiguous' AND message_id IS NULL ORDER BY ordinal",(batch,))]
assert len(rows)==3
for r in rows:
 parts=json.loads(r['parts']);assert all(p['kind']=='sticker' for p in parts)
 relay.compile_parts(source,parts,packet['image_options'])
p=Path('/opt/qq-mouth/colon-sticker-recovery-original.json');p.write_text(json.dumps(rows,ensure_ascii=False));p.chmod(0o600)
with c:
 for r in rows:
  assert c.execute("UPDATE relay_deliveries SET status='pending',error=NULL,updated=? WHERE id=? AND status='failed' AND message_id IS NULL",(time.time(),r['id'])).rowcount==1
 c.execute("UPDATE relay_jobs SET status='planned' WHERE batch_id=?",(batch,))
print('Recovered only',len(rows),'unsent stickers; sent text unchanged')
