import unittest
from PIL import Image
from session_selector import Policy,Navigator,NavigationError,title_matches

class FakeUI:
    def __init__(self):self.page=0;self.actions=[];self.chosen='other';self.bad_title=False;self.reorder=False;self.reads=0
    def frame(self):
        self.reads+=1
        image=Image.new('RGB',(874,640),(self.page,0,0))
        if self.reorder and self.reads%3==0:image.paste((255,0,0),(118,98,291,125))
        return image,(1,200,80,874,640)
    def scroll(self,g,up=False,top=False):
        self.actions.append(('scroll',up));self.page=0 if up else min(1,self.page+1)
    def click(self,g,y):self.actions.append(('click',y));self.chosen='wrong' if self.bad_title else '目标'

class FakeOCR:
    def __init__(self,ui,duplicate=False):self.ui=ui;self.duplicate=duplicate
    def read(self,image):
        if image.height==41:return {'seconds':0.01,'lines':[{'text':self.ui.chosen,'box':[1,1,25,15]}]}
        text='目标' if self.ui.page else '其他'
        lines=[{'text':text,'box':[1,14,40,28]}, {'text':'00:08','box':[143,15,171,26]}, {'text':'目标','box':[1,40,40,55]}]
        if self.duplicate:lines.append({'text':text,'box':[1,79,40,93]});lines.append({'text':'昨天','box':[143,80,171,91]})
        return {'seconds':0.01,'lines':lines}

class Tests(unittest.TestCase):
    def setup_nav(self,ui,duplicate=False):
        p=Policy(settle_seconds=0,max_attempts=2,max_pages=3)
        nav=Navigator(ui,FakeOCR(ui,duplicate),p);return nav
    def test_scroll_click_verify_and_restore(self):
        ui=FakeUI();result=self.setup_nav(ui).select('目标')
        self.assertTrue(result['selected']);self.assertEqual(ui.page,0);self.assertEqual(sum(x[0]=='click' for x in ui.actions),1)
    def test_title_mismatch_restarts_and_is_bounded(self):
        ui=FakeUI();ui.bad_title=True
        with self.assertRaises(NavigationError):self.setup_nav(ui).select('目标')
        self.assertEqual(sum(x[0]=='click' for x in ui.actions),2)
    def test_missing_target_stops_at_bottom(self):
        ui=FakeUI()
        with self.assertRaises(NavigationError):self.setup_nav(ui).select('不存在')
        self.assertFalse(any(x[0]=='click' for x in ui.actions))
    def test_duplicate_name_is_not_clicked(self):
        ui=FakeUI();nav=self.setup_nav(ui,True)
        with self.assertRaises(NavigationError):nav.select('其他')
        self.assertFalse(any(x[0]=='click' for x in ui.actions))
    def test_message_preview_and_timestamp_are_not_names(self):
        ui=FakeUI();rows=self.setup_nav(ui).names(ui.frame()[0])
        self.assertEqual([r['text'] for r in rows],['其他'])
    def test_undated_bottom_conversation_is_still_a_name(self):
        ui=FakeUI();nav=self.setup_nav(ui)
        base=nav.ocr.read
        def read(im):
            data=base(im);data['lines'].append({'text':'ExampleFriend','box':[1,79,40,93]});return data
        nav.ocr.read=read
        self.assertEqual([r['text'] for r in nav.names(ui.frame()[0])],['其他','ExampleFriend'])
    def test_group_member_count_only_for_groups(self):
        self.assertTrue(title_matches('投屏用群聊','投屏用群聊(3)','group'))
        self.assertFalse(title_matches('投屏用群聊','投屏用群聊(3)','private'))
        self.assertFalse(title_matches('小明','小明的群','private'))

if __name__=='__main__':unittest.main()
