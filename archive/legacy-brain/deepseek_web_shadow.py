import importlib.util,sys,json
for name,path in [('web_lookup','/opt/qq-mouth/web_lookup_deepseek_staged.py'),('mouth_staged','/opt/qq-mouth/mouth_deepseek_staged.py')]:
 spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
m=sys.modules['mouth_staged'];w=sys.modules['web_lookup']
s=w.Session();r=s.execute({'action':'read','url':'https://www.bilibili.com/video/BV1pMJv6WEmr/'})
assert r.get('provider')=='DeepSeek Fetch' and r.get('lines'),r
print(json.dumps({'bilibili_fetch':True,'title':r['title'],'lines':len(r['lines'])},ensure_ascii=False),flush=True)
body={'model':'deepseek-flash','thinking':{'type':'disabled'},'max_tokens':1200,'stream':False,'messages':[{'role':'system','content':m.TASK},{'role':'user','content':'本次是联网接口验收：第一轮只输出一个search调用JSON，query为Python 官方文档 site:python.org；拿到真实结果后只输出read调用JSON读取其中一个链接；拿到正文后才用一句中文概括并附链接。不要先回复说明。'}]}
text,usage=m.invoke(body)
assert usage['web_calls']>=2 and m.parse_web_calls(text) is None,usage
assert any(x.get('provider')=='DeepSeek Search' for x in usage['web_audit'])
assert any(x.get('provider')=='DeepSeek Fetch' for x in usage['web_audit'])
print(json.dumps({'end_to_end':True,'web_calls':usage['web_calls'],'web_audit':usage['web_audit'],'final_chars':len(text)},ensure_ascii=False))
