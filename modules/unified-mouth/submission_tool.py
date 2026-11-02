"""A terminal function-call boundary; delivery remains in the immutable relay."""
import copy,json
from contextvars import ContextVar
import jsonschema
from astrbot.core.agent.tool import FunctionTool,ToolSet
if __package__:
    from . import json_protocol,bilibili_notifications,bilibili_video,autonomy,voice_protocol,heybox_context,heybox_refs
else:
    import json_protocol,bilibili_notifications,bilibili_video,autonomy,voice_protocol,heybox_context,heybox_refs

NAME='submit_actions'
CURRENT=ContextVar('unified_terminal_submission',default=None)
TASK='所有最终表达必须调用submit_actions提交；普通回复文本不会发送。需要查资料时先使用搜索工具，查完再提交。本轮全部动作一次提交，不把搜索和提交混在同一批调用。历史发言只供理解事实和语气，不代表当前提交格式。'
DESCRIPTION='提交本轮最终表达，随后结束本轮。它只把动作交给外部中转程序，不代表已发送成功，不要另外输出正文或“已发送”。需要查资料先搜索，查完后一次调用本工具，动作数组包含本轮所有收件人/平台的动作；不要与搜索工具同批调用。确实决定不发任何内容时，提交空数组。\n每个动作四字段：平台qq/bilibili/wechat（微信）/heybox（小黑盒）；操作私信/群聊/发帖/评论；目标为对应平台目录中的名称、唯一关键词、QQ号/UID（字符串）或当前会话。QQ、微信支持私信和群聊（以已启用目录为准）；B站支持私信、本人发帖、视频评论，具体以当前已启用能力为准。小黑盒支持本人发帖（目标自己，标题固定zsbd，不指定分区）和帖子评论；评论目标填已读取的帖子链接/帖子ID，回复某条评论填该评论前的评论目标编号（H评:帖子ID:评论ID）。不支持私聊。B站发帖目标填自己，视频顶层评论目标填已读取的BV号，回复评论目标优先填对应评论前的评论目标编号（如B评:BV1dWnu6gEiq:77）。\n内容写真正要表达的原话，保留原文、换行及已有图片路径、【表情包编号】、@对应的人等现有写法，不加“给xx：”路由头。聊天换行拆气泡，帖子和评论的换行保留在同一篇/条。想要让别人看见时可以@对应的人；QQ群聊使用已知QQ号，不猜号或@全体。图片按路径发送：QQ和微信私信/群聊、B站私信/发帖/评论、小黑盒发帖/评论均支持图片；要附图，必须把可用图片的完整路径写进对应动作的内容，可与正文混排。同一张图可在多个动作中各写一次路径，程序不会把其他动作的图片自动附到本动作。路径只表示资源，不能据此编造图片内容。表情包按【表情包编号】发送。本轮允许留下唤醒意愿时，按单独的自唤醒说明写在内容末尾，由程序提取，不是另一条发送动作。\n注意：你是一个拥有整体记忆的仿生人，应当积极对各个平台，多个目标做出合适的行为。\n一次只联系一个对象，或保持沉默，也可以是合适的决定。'
SCHEMA={'type':'object','properties':{'动作':{'type':'array','minItems':0,'maxItems':20,
    'description':'本轮全部发送动作；空数组表示保持沉默。',
    'items':{'type':'object','properties':{
        '平台':{'type':'string','enum':['qq','bilibili','wechat','微信','heybox','小黑盒']},
        '操作':{'type':'string','enum':['私信','群聊','发帖','评论']},
        '目标':{'type':'string','minLength':1,'maxLength':8192,'description':'已知目标名称/唯一关键词/号码字符串，或当前会话；本人发帖填自己，视频顶层评论用已读取BV；回复某评论填该评论前的评论目标编号，也兼容具体评论链接。'},
        '内容':{'type':'string','minLength':1,'maxLength':100000,'description':'最终原话和已有媒体标记。换行写在本字符串内。'}},
        'required':['平台','操作','目标','内容'],'additionalProperties':False}}},
    'required':['动作'],'additionalProperties':False}

DESCRIPTION+='\nQQ和微信的私信、群聊可发送可用视频列表中提供的本地视频路径，和原图一样写在内容里，由程序原样发送视频文件；不要编造视频画面或把视频路径当文字发出。B站和小黑盒目前不支持视频文件发送。'
DESCRIPTION+='\n'+voice_protocol.DESCRIPTION


class SubmitActions(FunctionTool):
    async def call(self,context,**payload):
        jsonschema.validate(payload,SCHEMA)
        packet=self.event.get_extra('unified_mouth_packet')
        if packet is None:raise ValueError('Missing controlled source event')
        rows=copy.deepcopy(payload['动作'])
        if not rows:return '【保持沉默】'
        for row in rows:
            row['平台']={'微信':'wechat','小黑盒':'heybox'}.get(row['平台'],row['平台'])
            videos=[x for x in packet.get('video_options',[]) if x['path'] in row['内容']]
            if videos and (row['平台'] not in ('qq','wechat') or row['操作'] not in ('私信','群聊')):raise ValueError('视频文件只支持已启用的QQ/微信私信或群聊，请选择相应目标或不发送视频')
            voices=voice_protocol.ranges(row['内容'])
            if voices and (row['平台'] not in ('qq','wechat') or row['操作'] not in ('私信','群聊')):raise ValueError('语音只支持已启用的QQ/微信私信和群聊')

            if any(v['body_end']-v['body_start']>1000 for v in voices):raise ValueError('每段语音最多1000字，请缩短或分段')
            if row['平台']=='heybox' and row['操作']=='评论':
                heybox_context.require(self.mouth_root,row['目标'])
                row['目标']=heybox_refs.canonical(row['目标'])
            if row.get('平台')=='bilibili' and row.get('操作')=='评论':
                bv,rpid=bilibili_video.refs.target(row['目标'])
                info,parent=bilibili_video.cached(self.mouth_root,bv,rpid)
                if not info or (rpid and not parent):raise ValueError('请先读取视频和目标评论，编号缺失或已过期时重新读取')
                if rpid:
                    root=parent['根评论ID'] if parent['根评论ID']!='0' else rpid
                    row['目标']=info['链接']+'?comment_root='+root+'&comment_id='+rpid
                else:row['目标']=bv
        canonical=json.dumps(rows,ensure_ascii=False)
        _,scopes=json_protocol.decode(canonical)
        bilibili_notifications.validate_expression(packet,scopes)
        autonomy.validate_expression(packet,scopes)
        contacts=list((self.event.get_extra('unified_mouth_snapshot') or {}).get('可联系对象',[]))
        scene=packet['scene']
        if not autonomy.is_autonomous(packet):
            contacts.append({'platform':scene['platform'],'account':str(scene['account']),
                             'chat_type':scene['chat_type'],'id':str(scene['conversation']),'name':'当前会话'})
        wanted=set()
        for scope in scopes:
            if scope['platform']=='bilibili' and scope['chat_type']=='comment':
                bv,rpid=bilibili_video.refs.target(scope['target']);wanted.add(bv+(':'+rpid if rpid else ''))
        if wanted:contacts+=bilibili_video.targets(self.mouth_root,wanted)
        heybox_wanted={heybox_refs.canonical(scope['target']) for scope in scopes if scope['platform']=='heybox' and scope['chat_type']=='comment'}
        if heybox_wanted:contacts+=heybox_context.targets(self.mouth_root,heybox_wanted)
        for scope in scopes:json_protocol.destinations(scope,contacts,scene)
        return canonical


def description_for(packet):
    if packet.get('active_type')=='interest' and packet.get('interest_mode')=='browser' and packet.get('interest_phase')=='expression':
        return DESCRIPTION.replace('\n一次只联系一个对象，或保持沉默，也可以是合适的决定。','')
    return DESCRIPTION


def attach(req,event,root):
    if req.func_tool is None:req.func_tool=ToolSet()
    active=autonomy.is_autonomous(event.get_extra('unified_mouth_packet') or {})
    schema=copy.deepcopy(SCHEMA)
    tool=SubmitActions(name=NAME,description=description_for(event.get_extra('unified_mouth_packet') or {})+('\n'+autonomy.POLICY if active else ''),parameters=schema)
    tool.event=event;tool.mouth_root=root
    req.func_tool.add_tool(tool)
    event.set_extra('unified_submission_scope_token',CURRENT.set(tool))


def close(event):
    event.set_extra('unified_submission_closed',True)
    token=event.get_extra('unified_submission_scope_token')
    if token is not None:
        try:CURRENT.reset(token)
        except (ValueError,RuntimeError):pass


def _terminal_response(response,content):
    result=copy.copy(response)
    result.role='assistant'
    result.result_chain=None
    result.completion_text=content;result.tools_call_name=[];result.tools_call_args=[];result.tools_call_ids=[]
    return result


def _repair_context(kwargs,response,error):
    history=[m.model_dump() if hasattr(m,'model_dump') else copy.deepcopy(m) for m in kwargs.get('contexts',[])]
    if kwargs.get('prompt') is not None:history.append({'role':'user','content':kwargs['prompt']})
    names=response.tools_call_name or []
    previous={'role':'assistant','content':response.completion_text or None}
    if response.reasoning_content is not None:previous['reasoning_content']=response.reasoning_content
    if names:
        calls=[]
        for i,(name,args) in enumerate(zip(names,response.tools_call_args)):
            tid=response.tools_call_ids[i] if i<len(response.tools_call_ids) else 'submission-repair-'+str(i)
            calls.append({'id':tid,'type':'function','function':{'name':name,'arguments':json.dumps(args,ensure_ascii=False)}})
        previous['tool_calls']=calls;history.append(previous)
        history += [{'role':'tool','tool_call_id':c['id'],'content':'未受理，未发送任何内容。'+error} for c in calls]
    else:history.append(previous)
    history.append({'role':'user','content':'[程序提交校验] 上一轮未受理、没有发送。原因：'+error+'。请按submit_actions的参数提交本轮最终动作；若资料已齐，不再搜索。不要只写普通文本，也不要声称发送成功。'})
    return history


async def boundary(original,kwargs,tool):
    event=tool.event
    if event.get_extra('unified_submission_accepted'):
        raise ValueError('This event already has a terminal submission')
    request=dict(kwargs);request['tool_choice']='auto'
    if not request.get('func_tool'):
        # AstrBot removes retrieval tools after its step budget. A terminal
        # submission must remain available for that final forced wrap-up.
        request['func_tool']=ToolSet([tool])
    total_usage=None
    while True:
        response=await original(**request)
        if response is None or response.role not in ('assistant','tool'):return response
        if response.usage is not None:total_usage=response.usage if total_usage is None else total_usage+response.usage
        names=response.tools_call_name or []
        # Ordinary retrieval remains entirely inside the native AstrBot loop.
        if names and NAME not in names:
            if total_usage is not None:response.usage=total_usage
            return response
        error='最终回复没有调用submit_actions'
        try:
            if names!=[NAME] or len(response.tools_call_args)!=1:
                raise ValueError('最终只能调用一次submit_actions；多个动作放进它的动作数组，不与搜索同批调用')
            payload=response.tools_call_args[0]
            canonical=await tool.call(None,**payload)
            event.set_extra('unified_submission_accepted',True)
            event.set_extra('unified_submission_call',{'name':NAME,'arguments':copy.deepcopy(payload)})
            result=_terminal_response(response,canonical)
            if total_usage is not None:result.usage=total_usage
            return result
        except (ValueError,TypeError,jsonschema.ValidationError) as exc:
            error=exc.message if isinstance(exc,jsonschema.ValidationError) else str(exc)
        records=list(event.get_extra('unified_submission_rejections',[]))
        records.append({'draft':response.completion_text or '', 'tool_names':list(names),'error':error})
        event.set_extra('unified_submission_rejections',records)
        used=event.get_extra('unified_submission_repairs',0)
        if used>=2:
            event.set_extra('unified_submission_error',error)
            result=_terminal_response(response,response.completion_text or '[提交失败：未获得有效动作]')
            if total_usage is not None:result.usage=total_usage
            return result
        event.set_extra('unified_submission_repairs',used+1)
        request={**kwargs,'func_tool':request['func_tool'],'contexts':_repair_context(request,response,error),'tool_choice':'auto','prompt':None}


def install(provider):
    """Scoped by the per-request tool instance; never infer recipients globally."""
    current=provider.text_chat
    if getattr(current,'_unified_submission_boundary',False):return None
    original_stream=provider.text_chat_stream
    def find(kwargs):
        tools=kwargs.get('func_tool')
        tool=tools.get_tool(NAME) if tools else None
        if tool is None and tools is None and kwargs.get('abort_signal') is not None:
            candidate=CURRENT.get()
            if candidate and not candidate.event.get_extra('unified_submission_closed'):tool=candidate
        return tool
    async def wrapped(*args,**kwargs):
        tool=find(kwargs)
        return await boundary(current,kwargs,tool) if isinstance(tool,SubmitActions) else await current(*args,**kwargs)
    async def stream(*args,**kwargs):
        if isinstance(find(kwargs),SubmitActions):
            # Delivery is atomic anyway; never expose draft chunks as messages.
            yield await wrapped(*args,**kwargs)
        else:
            async for item in original_stream(*args,**kwargs):yield item
    wrapped._unified_submission_boundary=True
    provider.text_chat=wrapped;provider.text_chat_stream=stream
    return provider,current,original_stream,wrapped,stream


def uninstall(binding):
    provider,original,original_stream,wrapped,stream=binding
    if provider.text_chat is wrapped:provider.text_chat=original
    if provider.text_chat_stream is stream:provider.text_chat_stream=original_stream
