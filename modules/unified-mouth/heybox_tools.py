"""Route 小黑盒 queries through the Heybox reader instead of the web search."""
import copy
from . import heybox_search
from .bilibili_tools import record_call


def wrap(tools,root):
    """Compose on top of whatever already replaced web_search_deepseek (B站 first)."""
    for tool in list(tools):
        if tool.name not in ('web_search_deepseek','deepseek_fetch_web_page'):continue
        base=type(tool)
        class Search(base):
            async def call(self,context,**kwargs):
                text=kwargs.get('query',kwargs.get('url',''))
                if heybox_search.mentions(text) or heybox_search.refs.reference(text):
                    return await record_call(self,context,kwargs,lambda:heybox_search.lookup(getattr(self,'mouth_root',root),text))
                return await super().call(context,**kwargs)
        replacement=Search(name=tool.name,
            description=tool.description+' 查询包含“小黑盒”时，读取首批搜索候选中按点赞加评论数排序的五篇帖子摘要、每帖评论第一页前三条及每条最多五条回复。输入小黑盒帖子链接或评论目标编号可读取单帖及评论；可沿下一页URL继续读。本工具只读。',
            parameters=copy.deepcopy(tool.parameters))
        replacement.mouth_root=getattr(tool,'mouth_root',root)
        replacement.comment_focus=getattr(tool,'comment_focus',None)
        replacement.activity_packet=getattr(tool,'activity_packet',None)
        tools.remove_tool(tool.name);tools.add_tool(replacement)
