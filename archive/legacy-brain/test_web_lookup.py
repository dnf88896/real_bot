import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import web_lookup as web
import mouth

class WebTest(unittest.TestCase):
    def test_search_read_links_pagination_and_errors(self):
        s=web.Session()
        markdown='# 标题\n'+''.join('段落'+str(i)+'\n' for i in range(60))+'[后续](/next)'
        with patch.object(web,'api_request',side_effect=[{'results':[{'title':'官网','url':'https://example.com/','snippet':'摘要','summary':'长摘要','date':None,'rerank_score':2.2}]},{'title':'标题','content':markdown}]) as api,patch.object(web,'check_url'):
            result=s.execute({'action':'search','query':'官网'})
            self.assertEqual(result['provider'],'DeepSeek Search');self.assertEqual(result['results'][0]['title'],'官网')
            self.assertEqual(api.call_args.args[:2],('search',{'query':'官网','count':6,'need_content':False}))
            first=s.execute({'action':'read','url':'https://example.com/'})
            self.assertIsNotNone(first['next_line']);self.assertIn('https://example.com/next',first['links'])
            last=s.execute({'action':'read','url':'https://example.com/','start_line':first['next_line']})
            self.assertIsNone(last['next_line']);self.assertEqual(api.call_count,2)
            self.assertEqual(first['retrieved_at_unix'],last['retrieved_at_unix'])
        for url in ['file:///etc/passwd','http://127.0.0.1/','http://169.254.169.254/latest']:
            with self.assertRaises(ValueError):web.check_url(url)
        with patch.object(web,'api_request',side_effect=TimeoutError()),patch.object(web,'check_url'):
            self.assertIn('error',s.execute({'action':'read','url':'https://example.com/fail'}))
    def test_deepseek_empty_malformed_and_query_limit(self):
        with patch.object(web,'api_request',return_value={'results':[]}) as api:
            self.assertEqual(web.Session().execute({'action':'search','query':'无匹配'})['results'],[])
            self.assertEqual(web.Session().execute({'action':'search','query':'词'*129})['error'],'InvalidQuery')
            self.assertEqual(api.call_count,1)
        with patch.object(web,'api_request',return_value={'results':'invalid'}):
            self.assertIn('error',web.Session().execute({'action':'search','query':'词'}))
        session=web.Session();session.deadline=mouth.time.monotonic()-1
        with patch.object(web,'api_request') as api:
            self.assertIn('error',session.execute({'action':'search','query':'词'}));api.assert_not_called()
    def test_multiround_tool_loop_keeps_only_final_and_totals_usage(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d,'api.json').write_text('{"api_key":"fixture"}')
            original={'model':'deepseek-flash','messages':[{'role':'system','content':mouth.TASK},{'role':'user','content':'查一下'}]};saved=copy.deepcopy(original)
            requests=[]
            def transport(body,timeout):
                requests.append(copy.deepcopy(body));n=len(requests)
                content=['{"action":"search","query":"词"}','{"action":"read","url":"https://example.com/"}','最终答案'][n-1]
                self.assertNotIn('tools',body);self.assertNotIn('tool_choice',body)
                return {'choices':[{'message':{'role':'assistant','content':content},'finish_reason':'stop'}],'usage':{'total_tokens':10}}
            session=web.Session()
            with patch.object(mouth,'ROOT',Path(d)),patch.object(web,'api_request',side_effect=[{'results':[]},{'title':'网页','content':'网页正文'}]),patch.object(web,'check_url'):
                text,usage=mouth.invoke(original,transport,session)
            self.assertEqual(text,'最终答案');self.assertEqual(usage['web_calls'],2);self.assertEqual(usage['total_tokens'],30)
            self.assertTrue(any('【web_lookup返回的外部资料】' in m.get('content','') for m in requests[-1]['messages']));self.assertEqual(original,saved)
            clock=mouth.current_time();self.assertTrue(clock['datetime'].endswith('+08:00'));self.assertIn('星期',clock['weekday'])
    def test_prose_examples_and_invalid_objects_never_execute(self):
        examples=['格式如下：\n{"action":"search","query":"天气"}',
                  '```json\n{"action":"search","query":"天气"}\n```',
                  '{"action":"search","query":"天气"}\n这只是示例',
                  '{"action":"search"}', '{"action":"read","url":"x","start_line":true}',
                  '{"action":"search","query":"x","extra":1}',
                  '{"action":"search","query":"x","query":"y"}']
        with tempfile.TemporaryDirectory() as d:
            Path(d,'api.json').write_text('{"api_key":"fixture"}')
            for example in examples:
                self.assertIsNone(mouth.parse_web_call(example))
                session=web.Session()
                def transport(body,timeout):
                    return {'choices':[{'message':{'content':example},'finish_reason':'stop'}]}
                with patch.object(mouth,'ROOT',Path(d)):
                    text,usage=mouth.invoke({'messages':[]},transport,session)
                self.assertEqual(text,example);self.assertEqual(usage['web_calls'],0)
        self.assertEqual(mouth.parse_web_call(' \n{"action":"read","url":"https://example.com"} '),{'action':'read','url':'https://example.com'})
    def test_exhausted_budget_does_not_execute_or_return_call(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d,'api.json').write_text('{"api_key":"fixture"}')
            session=web.Session();session.calls=web.MAX_CALLS
            def transport(body,timeout):
                return {'choices':[{'message':{'content':'{"action":"search","query":"x"}'},'finish_reason':'stop'}]}
            with patch.object(mouth,'ROOT',Path(d)),self.assertRaises(ValueError):
                mouth.invoke({'messages':[]},transport,session)
            self.assertEqual(session.calls,web.MAX_CALLS)
    def test_batch_calls_and_atomic_rejection(self):
        calls=[{'action':'search','query':'词'+str(i)} for i in range(4)]
        raw='\n\n'.join(json.dumps(x,ensure_ascii=False) for x in calls)
        self.assertEqual(mouth.parse_web_calls(raw),calls)
        self.assertEqual(mouth.parse_web_calls(''.join(json.dumps(x) for x in calls)),calls)
        for suffix in ['\n这只是例子','\n{"action":"search"}','\n{"action":']:
            self.assertIsNone(mouth.parse_web_calls(raw+suffix))
        with tempfile.TemporaryDirectory() as d:
            Path(d,'api.json').write_text('{"api_key":"fixture"}')
            session=web.Session();session.calls=web.MAX_CALLS-2;requests=[]
            def transport(body,timeout):
                requests.append(copy.deepcopy(body))
                if len(requests)==1:content=raw
                else:
                    values=json.loads(body['messages'][-2]['content'].split('\n',1)[1])
                    self.assertEqual(len(values),4)
                    self.assertIn('error',values[2]['result'])
                    self.assertIn('未执行',values[3]['result']['error'])
                    content='最终结果'
                return {'choices':[{'message':{'content':content},'finish_reason':'stop'}]}
            with patch.object(mouth,'ROOT',Path(d)),patch.object(web,'api_request',return_value={'results':[]}) as fetch:
                text,usage=mouth.invoke({'messages':[]},transport,session)
            self.assertEqual(text,'最终结果');self.assertEqual(fetch.call_count,2)
            self.assertEqual(usage['web_calls'],web.MAX_CALLS)

    def test_twenty_search_rounds_then_final(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d,'api.json').write_text('{"api_key":"fixture"}')
            count=0
            def transport(body,timeout):
                nonlocal count
                count+=1
                content=json.dumps({'action':'search','query':'query'+str(count)}) if count<=20 else '完成'
                if count==21:self.assertIn('联网预算已到',body['messages'][-1]['content'])
                return {'choices':[{'message':{'content':content},'finish_reason':'stop'}]}
            with patch.object(mouth,'ROOT',Path(d)),patch.object(web,'api_request',return_value={'results':[]}) as api:
                text,usage=mouth.invoke({'messages':[]},transport,web.Session())
            self.assertEqual(count,21);self.assertEqual(api.call_count,20)
            self.assertEqual(usage['web_calls'],20);self.assertEqual(text,'完成')

    def test_tool_budget_forces_final_answer(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d,'api.json').write_text('{"api_key":"fixture"}')
            session=web.Session();session.calls=web.MAX_CALLS
            def transport(body,timeout):
                self.assertNotIn('tools',body)
                self.assertIn('联网预算已到',body['messages'][-1]['content'])
                return {'choices':[{'message':{'content':'资料不足，不能确认。'},'finish_reason':'stop'}]}
            with patch.object(mouth,'ROOT',Path(d)):
                text,_=mouth.invoke({'messages':[]},transport,session)
            self.assertEqual(text,'资料不足，不能确认。')

if __name__=='__main__':unittest.main()
