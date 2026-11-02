import unittest,sqlite3,tempfile,json
from pathlib import Path
from unittest.mock import patch
import group_reply_policy as policy
class PolicyTest(unittest.TestCase):
 def item(self,i,kind='group',internal=False):return {'id':i,'observation':{'account_id':'a','chat_type':kind,'conversation_id':'g','internal_event':internal}}
 def test_probability_boundary_and_no_redraw(self):
  with tempfile.TemporaryDirectory() as d:
   db=sqlite3.connect(Path(d)/'state.db');path=Path(d)/'config.json'
   items=[self.item(i) for i in range(1,5)]
   with patch.object(policy.secrets,'randbelow',side_effect=[0,19999,20000,999999]) as draw:
    self.assertEqual(policy.required(db,items,path),[1,2]);self.assertEqual(policy.required(db,items,path),[1,2]);self.assertEqual(draw.call_count,4)
   db.close();db=sqlite3.connect(Path(d)/'state.db')
   with patch.object(policy.secrets,'randbelow') as draw:self.assertEqual(policy.required(db,items,path),[1,2]);draw.assert_not_called()
   db.close()
 def test_private_internal_and_historical_not_sampled(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'config.json';path.write_text(json.dumps({'probability':0.02,'start_observation_id':10}))
   db=sqlite3.connect(':memory:')
   with patch.object(policy.secrets,'randbelow',return_value=0) as draw:
    self.assertEqual(policy.required(db,[self.item(1),self.item(10,'private'),self.item(11,internal=True),self.item(12)],path),[12]);self.assertEqual(draw.call_count,1)
   db.close()
if __name__=='__main__':unittest.main()
