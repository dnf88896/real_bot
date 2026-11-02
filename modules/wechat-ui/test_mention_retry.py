import unittest
from mention_input import insert_with_retry,contains,visible_name
from session_selector import NavigationError

class Tests(unittest.TestCase):
    def test_three_failures_stop(self):
        clicks=[];result=insert_with_retry('ExampleFriend',lambda:'',lambda name:clicks.append(name))
        self.assertFalse(result['success']);self.assertEqual(result['attempts'],3);self.assertEqual(len(clicks),3)
    def test_third_attempt_success(self):
        state={'draft':'','clicks':0}
        def click(name):
            state['clicks']+=1
            if state['clicks']==3:state['draft']='\u2005@ExampleFriend\u2005'
        result=insert_with_retry('ExampleFriend',lambda:state['draft'],click)
        self.assertTrue(result['success']);self.assertEqual(result['attempts'],3)
    def test_already_present_no_duplicate(self):
        clicks=[];result=insert_with_retry('ExampleFriend',lambda:'\u2005@ExampleFriend\u2005',lambda name:clicks.append(name))
        self.assertTrue(result['success']);self.assertEqual(clicks,[])
    def test_successful_click_then_exception_does_not_repeat(self):
        state={'draft':'','clicks':0}
        def click(name):state.update(draft='@ExampleFriend ',clicks=state['clicks']+1);raise NavigationError('Interrupted after click')
        result=insert_with_retry('ExampleFriend',lambda:state['draft'],click)
        self.assertTrue(result['success']);self.assertEqual(state['clicks'],1)
    def test_unreadable_post_click_recovery_no_duplicate(self):
        reads=iter(['',NavigationError('read timeout'),'@ExampleFriend ']);clicks=[]
        def read():
            value=next(reads)
            if isinstance(value,Exception):raise value
            return value
        result=insert_with_retry('ExampleFriend',read,lambda name:clicks.append(name))
        self.assertTrue(result['success']);self.assertEqual(len(clicks),1)
    def test_other_draft_change_stops(self):
        reads=iter(['original','original changed']);clicks=[]
        result=insert_with_retry('ExampleFriend',lambda:next(reads),lambda name:clicks.append(name))
        self.assertFalse(result['success']);self.assertEqual(len(clicks),1)
    def test_name_prefix_is_not_same_person(self):
        self.assertFalse(contains('@ExampleFriend2 ','ExampleFriend'));self.assertTrue(contains('你好\u2005@ExampleFriend\u2005','ExampleFriend'))
    def test_repeated_messages_use_latest_visible_avatar(self):
        rows=[{'text':'ExampleFriend','box':[70,30,110,45]},{'text':'ExampleFriend','box':[70,130,110,145]},{'text':'ExampleFriend2','box':[70,230,110,245]}]
        self.assertEqual(visible_name(rows,'ExampleFriend')['box'][1],130)

if __name__=='__main__':unittest.main()
