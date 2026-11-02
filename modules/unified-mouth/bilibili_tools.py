"""Route known video references through the existing two web tools."""
import copy,json
from astrbot.core.tools.web_search_tools import DeepSeekWebSearchTool,DeepSeekFetchWebPageTool
from . import bilibili_video
from .interest_memory import Interest,is_interest


async def record_call(tool,context,kwargs,run):
    packet=getattr(tool,'activity_packet',{}) or {}
    try:
        result=await run()
    except Exception as exc:
        if is_interest(packet):Interest(tool.mouth_root).record(packet,tool.name,kwargs,type(exc).__name__,False)
        raise
    if is_interest(packet):Interest(tool.mouth_root).record(packet,tool.name,kwargs,result)
    return result


def focused(tool,text):
    # Initial notification context already has the relevant reply chain.
    # An explicit tool read may explore other videos or more comments freely.
    return None


class VideoSearch(DeepSeekWebSearchTool):
    async def call(self,context,**kwargs):
        return await record_call(self,context,kwargs,lambda:self.read(context,**kwargs))

    async def read(self,context,**kwargs):
        narrow=focused(self,kwargs.get('query',''))
        if narrow is not None:return narrow
        result=await bilibili_video.lookup(self.mouth_root,kwargs.get('query',''))
        return result if result is not None else await super().call(context,**kwargs)


class VideoRead(DeepSeekFetchWebPageTool):
    async def call(self,context,**kwargs):
        return await record_call(self,context,kwargs,lambda:self.read(context,**kwargs))

    async def read(self,context,**kwargs):
        narrow=focused(self,kwargs.get('url',''))
        if narrow is not None:return narrow
        result=await bilibili_video.lookup(self.mouth_root,kwargs.get('url',''))
        return result if result is not None else await super().call(context,**kwargs)


def wrap(tools,root,comment_focus=None,activity_packet=None):
    classes={'web_search_deepseek':VideoSearch,'deepseek_fetch_web_page':VideoRead}
    for tool in list(tools):
        cls=classes.get(tool.name)
        if cls:
            replacement=cls(name=tool.name,description=tool.description+' 输入B站视频链接或BV号时读取视频资料和评论，可继续读取结果中的分页及回复链接。每条评论前的评论目标编号可直接填入submit_actions的目标，操作仍为评论。',parameters=copy.deepcopy(tool.parameters))
            replacement.mouth_root=root
            replacement.comment_focus=comment_focus
            replacement.activity_packet=activity_packet
            tools.remove_tool(tool.name);tools.add_tool(replacement)
