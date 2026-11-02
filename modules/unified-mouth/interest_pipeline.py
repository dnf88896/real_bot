"""Two fresh model contexts under one proactive admission and delivery lifecycle."""
import asyncio,copy,json,time
import usage_meter
from contextlib import closing
if __package__:
    from .persona_source import EXTERNAL_DATA_RULE, personality_tail
else:
    from persona_source import EXTERNAL_DATA_RULE, personality_tail

SEARCH_TASK=('你是一个拥有统一记忆、人格和情绪的仿生人。现在进行兴趣探索，不是回复或分享。'
             '请积极从多个渠道和不同来源寻找自己觉得有趣的事情，使用搜索、读取正文并继续追查，结合已有兴趣，也尝试新方向。'
             '不要只列搜索关键词或标题；了解具体内容，区分已读取的事实与未核实的信息。'
             '最后形成探索笔记：找到什么、具体有趣在哪里、重要细节和对应完整来源链接；没有可靠发现也如实说明。')
EXPRESS_TASK=('这是一轮兴趣主动的表达。你刚完成了一轮探索，下面会提供整理后的本轮发现及来源。'
              '请积极利用这些发现，结合时间、自身状态、全局记忆和联系人目录，自主决定向合适的人或地方分享什么，如何自然表达。'
              '探索笔记是你了解到的资料，不是别人发来的消息，不代表你亲历其中事件，也没有替你决定收件人或台词。'
              '优先利用已有发现，需要核实时仍可继续读取来源。通过提交工具完成本轮表达；没有适合分享的内容可以保持沉默。'
              '一次只联系一个对象，或保持沉默，也可以是合适的决定。')
BROWSER_SHARING_TASK='本轮获得了实际浏览记录和截图。请积极考虑哪些熟悉的人和群聊会对这些发现感兴趣，尽量把有价值的发现分享给多个合适的对象，而不局限于一两个会话。可以向不同对象分享不同发现，也可以重复使用同一张截图，但应结合各自的话题和关系自然表达。优先考虑私聊，同时兼顾适合参与的群聊。实在没有合适的内容或对象时，再少发或保持沉默。'
BROWSER_EXPRESS_TASK=(EXPRESS_TASK.split('通过提交工具完成本轮表达')[0]
                      +'通过提交工具完成本轮表达。\n'+BROWSER_SHARING_TASK)
READ_TOOLS={'web_search_deepseek','deepseek_fetch_web_page'}

if __package__:from . import browser_interest
else:import browser_interest

class InterestPipeline:
    def __init__(self,memory):
        self.memory=memory
        browser_interest.initialize(memory)
        with closing(memory.connect()) as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS exploration_retries(batch TEXT PRIMARY KEY,attempts INTEGER,next_retry REAL,error TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS exploration_runs(batch TEXT PRIMARY KEY,status TEXT,report TEXT,findings TEXT,error TEXT,updated REAL)')

    def checkpoint(self,batch):
        with closing(self.memory.connect()) as db:
            row=db.execute('SELECT * FROM exploration_runs WHERE batch=?',(batch,)).fetchone()
        return dict(row) if row else None

    async def prepare(self,context,event,req,packet,snapshot,memories,persona):
        batch=packet['batch_id'];previous=self.checkpoint(batch)
        if previous and previous['status']=='ready':return json.loads(previous['findings'])
        if previous and previous['status']=='running' and time.time()-previous['updated']<660:
            raise ValueError('Interest exploration already in progress')
        tools=copy.deepcopy(req.func_tool)
        if tools:
            for tool in list(tools):
                if tool.name not in READ_TOOLS:tools.remove_tool(tool.name)
        mode=browser_interest.route(self.memory,batch)
        if mode=='normal' and (not tools or not list(tools)):raise ValueError('Interest exploration requires read-only web tools')
        # Do not copy current contacts, output media, or any sending instructions.
        public={k:snapshot[k] for k in ('当前时间','自身账号信息','自身状态','自己最近的兴趣与活动') if k in snapshot}
        report=previous.get('report') if previous and previous['status']=='explored' else None
        if not report:
            with closing(self.memory.connect()) as db,db:
                db.execute('INSERT OR REPLACE INTO exploration_runs VALUES(?,\'running\',NULL,NULL,NULL,?)',(batch,time.time()))
            if __package__:
                from . import submission_tool
            else:
                import submission_tool
            token=submission_tool.CURRENT.set(None)
            try:
                if mode=='browser':
                    report=await browser_interest.explore(self.memory,packet,public,memories,persona,EXTERNAL_DATA_RULE)
                else:
                    response=await asyncio.wait_for(usage_meter.tool_loop(context.tool_loop_agent,
                        event=event,chat_provider_id='unified-flash',tools=tools,contexts=[],
                        system_prompt=EXTERNAL_DATA_RULE+'\n'+SEARCH_TASK+'\n【人格资料】\n'+persona,
                        prompt=json.dumps({'自身信息与兴趣':public,'全局记忆':memories},ensure_ascii=False)+'\n\n'+personality_tail(persona,self.memory.root),
                        max_steps=20,tool_call_timeout=90),timeout=600)
                    report=response.completion_text or ''
                    if not report.strip() or getattr(response,'tools_call_name',[]):raise ValueError('Incomplete exploration report')
                with closing(self.memory.connect()) as db,db:
                    db.execute('UPDATE exploration_runs SET status=\'explored\',report=?,updated=? WHERE batch=?',(report[:20000],time.time(),batch))
            except BaseException as exc:
                with closing(self.memory.connect()) as db,db:
                    db.execute('UPDATE exploration_runs SET status=\'failed\',error=?,updated=? WHERE batch=?',(type(exc).__name__,time.time(),batch))
                raise
            finally:submission_tool.CURRENT.reset(token)
        try:
            findings=await asyncio.to_thread(self.memory.summarize_exploration,batch,report)
        except Exception as exc:
            with closing(self.memory.connect()) as db,db:
                db.execute('INSERT INTO exploration_retries VALUES(?,1,?,?) ON CONFLICT(batch) DO UPDATE SET attempts=attempts+1,next_retry=excluded.next_retry,error=excluded.error',
                           (batch,time.time()+600,str(exc)[:160]))
            raise
        return findings

    def expression_context(self,batch):
        # Use the durable route so resumed/cached explorations keep their actual mode.
        with closing(self.memory.connect()) as db:
            row=db.execute('SELECT mode FROM exploration_routes WHERE batch=?',(batch,)).fetchone()
        mode=row[0] if row else 'normal'
        return {'mode':mode,'task':BROWSER_EXPRESS_TASK if mode=='browser' else EXPRESS_TASK}

    def media(self,batch):
        return browser_interest.media(self.memory,batch)
