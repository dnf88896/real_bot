import importlib.util,json
spec=importlib.util.spec_from_file_location("mouth_text_staged","/opt/qq-mouth/mouth_text_staged.py")
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
body={"model":"deepseek-flash","thinking":{"type":"disabled"},"max_tokens":1200,"stream":False,"messages":[{"role":"system","content":m.TASK},{"role":"user","content":"隔离联网测试：请先搜索 Python 官方文档，再读取你找到的 python.org 官方页面正文，最后用一句中文概括页面，附链接。"}]}
text,usage=m.invoke(body)
assert usage['web_calls']>=2,usage
assert m.parse_web_call(text) is None
print(json.dumps({"web_calls":usage['web_calls'],"audit":usage['web_audit'],"reply":text},ensure_ascii=False))
