import json,sqlite3,tempfile,unittest
from pathlib import Path
import message_scheduler as sch

class SchedulerTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'state.db'
        self.db=sqlite3.connect(self.path);self.db.row_factory=sqlite3.Row
        self.db.execute('CREATE TABLE batches(id TEXT PRIMARY KEY,ids TEXT,account TEXT,kind TEXT,conversation TEXT,notified REAL DEFAULT 0,done INTEGER DEFAULT 0)');self.db.commit()
        self.src=sqlite3.connect(':memory:');self.src.execute('CREATE TABLE observations(id INTEGER PRIMARY KEY,account_id TEXT,chat_type TEXT,conversation_id TEXT,received_at TEXT,raw_json TEXT)')
        self.cfg={**sch.DEFAULTS,'private_coalesce_seconds':0,'group_coalesce_seconds':0}
    def tearDown(self):self.src.close();self.db.close();self.temp.cleanup()
    def add(self,i,kind='group',conv='g',stamp='1970-01-01T00:00:00Z',size=1):
        self.src.execute('INSERT INTO observations VALUES(?,?,?,?,?,?)',(i,'a',kind,conv,stamp,'x'*size));self.src.commit()
    def poll(self,now=100):return sch.poll(self.db,self.src,self.cfg,now)
    def done(self,b):self.db.execute('UPDATE batches SET done=1 WHERE id=?',(b['id'],));self.db.commit()
    def test_priority_merges_interleaved_conversation_without_skipping_older_ids(self):
        for i,k,c in [(1,'group','g'),(2,'private','p'),(3,'group','g'),(4,'private','p'),(5,'group','h')]:self.add(i,k,c)
        b=self.poll();self.assertEqual(json.loads(b['ids']),[2,4])
        self.assertEqual(self.poll()['id'],b['id']) # No second active brain batch.
        self.done(b);g=self.poll();self.assertEqual(json.loads(g['ids']),[1,3]);self.done(g)
        h=self.poll();self.assertEqual(json.loads(h['ids']),[5]);self.done(h)
        self.assertIsNone(self.poll());self.assertEqual(self.db.execute('SELECT count(*) FROM scheduler_claims').fetchone()[0],5)
    def test_round_robin_group_fairness_private_streak_and_wait_override(self):
        self.cfg.update(private_max_batch=1,max_private_batches_before_group=2,group_max_wait_seconds=1000,group_max_batch=1)
        for i,k,c in [(1,'group','g'),(2,'group','h'),(3,'group','g'),(4,'private','p'),(5,'private','p'),(6,'private','p')]:self.add(i,k,c)
        for expected in ['private','private','group','private','group','group']:
            b=self.poll();self.assertEqual(b['kind'],expected)
            if expected=='group':
                existing=getattr(self,'groups',[]);existing.append(b['conversation']);self.groups=existing
            self.done(b)
        self.assertEqual(self.groups,['g','h','g'])
        self.add(7,'group','g');self.add(8,'private','p');self.add(9,'private','p');self.cfg['group_max_wait_seconds']=10
        b=self.poll();self.assertEqual(b['kind'],'private');self.done(b)
        b=self.poll();self.assertEqual(b['kind'],'group')
    def test_coalescing_size_caps_and_single_oversized_record_not_dropped(self):
        self.cfg.update(group_coalesce_seconds=8,group_max_batch=3,group_max_chars=10)
        self.add(1,stamp='1970-01-01T00:01:40Z',size=4)
        self.assertIsNone(self.poll(100));self.add(2,stamp='1970-01-01T00:01:41Z',size=4)
        self.assertIsNone(self.poll(107));b=self.poll(108);self.assertEqual(json.loads(b['ids']),[1,2]);self.done(b)
        self.add(3,size=100);self.add(4,size=2)
        b=self.poll(109);self.assertEqual(json.loads(b['ids']),[3]);self.done(b)
        b=self.poll(110);self.assertEqual(json.loads(b['ids']),[4])
    def test_restart_and_legacy_migration_preserve_exact_assignments(self):
        for i in range(1,6):self.add(i)
        self.db.execute('INSERT INTO batches(id,ids,account,kind,conversation,done) VALUES(?,?,?,?,?,1)',('old','[1,3]','a','group','g'))
        self.db.execute('INSERT INTO batches(id,ids,account,kind,conversation) VALUES(?,?,?,?,?)',('active','[2]','a','group','g'));self.db.commit()
        self.assertEqual(self.poll()['id'],'active');self.db.close()
        self.db=sqlite3.connect(self.path);self.db.row_factory=sqlite3.Row
        self.assertEqual(self.poll()['id'],'active');self.done(self.poll())
        b=self.poll();self.assertEqual(json.loads(b['ids']),[4,5])
        self.assertEqual(self.db.execute('SELECT count(*) FROM scheduler_claims').fetchone()[0],5)
    def wake(self,key,due,kind='group'):
        sch.initialize(self.db)
        self.db.execute('INSERT INTO scheduler_internal_queue(id,due,account,kind,conversation,event) VALUES(?,?,?,?,?,?)',(key,due,'a',kind,'watch',json.dumps({'kind':'self_wake','wake_id':key})));self.db.commit()
    def test_wakes_share_private_fifo_priority_even_when_watching_group(self):
        self.cfg['group_max_wait_seconds']=1000
        self.add(1,'group','g');self.add(2,'private','p',stamp='1970-01-01T00:00:40Z')
        self.wake('early',20);self.wake('late',60)
        b=self.poll();self.assertEqual(b['id'],'wake:early');self.assertEqual(b['kind'],'group');self.done(b)
        b=self.poll();self.assertEqual(json.loads(b['ids']),[2]);self.done(b)
        b=self.poll();self.assertEqual(b['id'],'wake:late');self.done(b)
        self.assertEqual(self.poll()['conversation'],'g')
    def test_internal_priority_does_not_preempt_active_or_starve_groups(self):
        self.cfg.update(group_max_wait_seconds=1000,max_private_batches_before_group=1)
        self.add(1,'group','g');active=self.poll();self.wake('one',1);self.wake('two',2)
        self.assertEqual(self.poll()['id'],active['id']);self.done(active)
        self.add(2,'group','g');b=self.poll();self.assertEqual(b['id'],'wake:one');self.done(b)
        b=self.poll();self.assertEqual(b['conversation'],'g');self.done(b)
        self.assertEqual(self.poll()['id'],'wake:two')
    def test_internal_queue_restart_and_cancelled_entries(self):
        self.wake('cancelled',0);self.wake('kept',1)
        self.db.execute("UPDATE scheduler_internal_queue SET status='cancelled' WHERE id='cancelled'");self.db.commit()
        self.db.close();self.db=sqlite3.connect(self.path);self.db.row_factory=sqlite3.Row
        b=self.poll();self.assertEqual(b['id'],'wake:kept');self.done(b)
        self.assertIsNone(self.poll())
        self.assertEqual(self.db.execute('SELECT count(*) FROM internal_wakes').fetchone()[0],1)

    def test_quiet_window_waits_for_append_but_has_hard_cap(self):
        self.cfg.update(group_coalesce_seconds=8,group_quiet_seconds=2,group_coalesce_cap_seconds=12)
        self.add(1,stamp='1970-01-01T00:01:40Z')
        self.add(2,stamp='1970-01-01T00:01:47Z')
        self.assertIsNone(self.poll(108))
        self.add(3,stamp='1970-01-01T00:01:50Z')
        self.assertIsNone(self.poll(111))
        self.add(4,stamp='1970-01-01T00:01:51Z')
        batch=self.poll(112)
        self.assertEqual(json.loads(batch['ids']),[1,2,3,4])

    def test_config_validation(self):
        path=Path(self.temp.name)/'config.json';path.write_text('{"private_max_batch":0}')
        with self.assertRaises(ValueError):sch.config(path)
        path.write_text('{"private_coalesce_seconds":0,"group_max_batch":40}')
        self.assertEqual(sch.config(path)['group_max_batch'],40)

if __name__=='__main__':unittest.main()
