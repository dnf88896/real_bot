import unittest
from compose_probe import Composer
from session_selector import NavigationError

class UI:
    def __init__(self):self.actions=[]
    def geometry(self):return (1,200,80,874,640)
    def frame(self):return None,self.geometry()
    def run(self,args):self.actions.append(args)
class Nav:
    def __init__(self,valid=True):self.valid=valid
    def verify(self,*args):return self.valid
class Clip:
    def __init__(self,reads):self.reads=iter(reads);self.values=[]
    def set(self,text):self.values.append(text)
    def get(self):
        value=next(self.reads)
        return self.values[-1] if value is None else value

class Tests(unittest.TestCase):
    def test_exact_multiline_unicode_no_send(self):
        body='第一行中文🙂\n第二行：123，。';ui=UI();clip=Clip([None,body])
        result=Composer(ui,Nav(),clip).write(body,'文件传输助手','private')
        self.assertTrue(result['draft_verified']);self.assertFalse(result['sent'])
        self.assertFalse(any('Return' in x or 'Escape' in x for args in ui.actions for x in args))
    def test_existing_draft_is_preserved(self):
        ui=UI();clip=Clip(['用户原有草稿'])
        with self.assertRaises(NavigationError):Composer(ui,Nav(),clip).write('测试','文件传输助手','private')
        self.assertNotIn('测试',clip.values)
    def test_mismatch_is_failure(self):
        with self.assertRaises(NavigationError):Composer(UI(),Nav(),Clip([None,'错误'])).write('测试','文件传输助手','private')
    def test_wrong_conversation_no_input(self):
        ui=UI()
        with self.assertRaises(NavigationError):Composer(ui,Nav(False),Clip([])).write('测试','文件传输助手','private')
        self.assertEqual(ui.actions,[])
    def test_keyboard_allowlist_rejects_send(self):
        c=Composer(UI(),Nav(),Clip([]))
        for key in ('Return','Escape','alt+F4'):
            with self.assertRaises(NavigationError):c.keys(key)

if __name__=='__main__':unittest.main()
