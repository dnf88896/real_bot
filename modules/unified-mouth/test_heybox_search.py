import asyncio,json,unittest
import heybox_search as heybox


def info(linkid,award,comment):
    return {'type':'link','info':{'linkid':linkid,'title':'标题'+str(linkid),'link_award_num':award,
        'comment_num':comment,'description':'摘要','user':{'name':'作者'}}}


def comment(comment_id,floor,child=0,children=None):
    return {'comment_id':str(comment_id),'floor_num':floor,'content':'正文'+str(comment_id),'likes':3,
        'time':'2026-10-26','is_top':1 if floor==1 else 0,'child_num':child,
        'author':{'name':'用户'+str(comment_id)},'child_comments':children or []}


class Routing(unittest.TestCase):
    def test_keyword_detection_and_extraction(self):
        self.assertTrue(heybox.mentions('帮我看看小黑盒上的黑神话'))
        self.assertTrue(heybox.mentions('heybox 黑神话'))
        self.assertFalse(heybox.mentions('看看这个视频'))
        self.assertEqual(heybox.keyword('小黑盒 黑神话 攻略！'),'黑神话 攻略')
        self.assertEqual(heybox.keyword('HEYBOX 黑神话'),'黑神话')
        self.assertEqual(heybox.keyword('小黑盒'),'')

    def test_comment_number_matches_bilibili_style(self):
        self.assertEqual(heybox.comment_number(400000006,400000007),'H评:400000006:400000007')
        self.assertEqual(heybox.number_target('H评:400000006:400000007'),('400000006','400000007'))
        self.assertIsNone(heybox.number_target('400000006:400000007'))
        for value in [('x',1),(1,'y'),('-1',2)]:
            with self.assertRaises(ValueError):heybox.comment_number(*value)


class Shaping(unittest.TestCase):
    def test_rank_keeps_posts_and_orders_by_heat(self):
        items=[info(1,5,5),{'type':'space','info':{'color':'#fff'}},info(2,1,200),info(3,30,0)]
        ranked=heybox.rank(items,limit=2)
        self.assertEqual([row['linkid'] for row in ranked],[2,3])
        self.assertEqual([row['linkid'] for row in heybox.rank(items)],[2,3,1])

    def test_entry_limits_floors_and_replies_and_labels_targets(self):
        children=[{'comment_id':str(900+i),'author':{'name':'回复者'+str(i)},'content':'回复正文','likes':1} for i in range(8)]
        floors=[comment(100+i,floor=i+1,child=8,children=children) for i in range(5)]
        entry=heybox.post_entry(info(400000006,33,36)['info'],{'url':'https://www.xiaoheihe.cn/app/bbs/link/400000006','author':{'name':'楼主'}},floors)
        self.assertEqual(entry['帖子ID'],'400000006')
        self.assertEqual(len(entry['前三楼']),3)
        first=entry['前三楼'][0]
        self.assertEqual(first['评论目标编号'],'H评:400000006:100')
        self.assertEqual(len(first['回复']),5)
        self.assertEqual(first['回复'][0]['评论目标编号'],'H评:400000006:900')
        self.assertEqual(first['更多回复'],3)
        self.assertTrue(first.get('置顶'))
        json.dumps(entry,ensure_ascii=False)

    def test_payload_is_json_with_platform_and_ordering(self):
        value=json.loads(heybox.payload('黑神话',[]))
        self.assertEqual(value['平台'],'小黑盒')
        self.assertIn('热度',value['排序'])
        self.assertEqual(value['关键词'],'黑神话')


class Reads(unittest.IsolatedAsyncioTestCase):
    async def test_lookup_falls_through_without_keyword(self):
        async def reader(query):raise AssertionError('reader must not run')
        self.assertIsNone(await heybox.lookup('/tmp',"看看这个视频",reader))

    async def test_lookup_returns_posts_and_derives_query(self):
        seen=[]
        async def reader(query):
            seen.append(query)
            return [heybox.post_entry(info(9,1,2)['info'],None,[comment(11,1,child=0)])]
        value=json.loads(await heybox.lookup('/tmp','小黑盒 黑神话 攻略',reader))
        self.assertEqual(seen,['黑神话 攻略'])
        self.assertEqual(len(value['帖子']),1)
        self.assertEqual(value['帖子'][0]['前三楼'][0]['评论目标编号'],'H评:9:11')

    async def test_lookup_without_query_asks_for_one(self):
        async def reader(query):raise AssertionError('reader must not run')
        value=json.loads(await heybox.lookup('/tmp','小黑盒',reader))
        self.assertEqual(value['帖子'],[])

    async def test_lookup_reports_read_failure(self):
        async def reader(query):raise ValueError('Heybox read failed: 500')
        value=json.loads(await heybox.lookup('/tmp','小黑盒 黑神话',reader))
        self.assertIn('搜索失败',value)


if __name__=='__main__':unittest.main()
