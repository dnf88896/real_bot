"""AstrBot owns context; the relay owns all external delivery."""

import asyncio
import copy
import json
import sqlite3
import sys
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.agent.message import TextPart, dump_messages_with_checkpoints
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.astr_main_agent_resources import TOOL_CALL_PROMPT, TOOL_CALL_PROMPT_SKILLS_LIKE_MODE

from .coordinator import Coordinator
from . import wakefulness
from . import autonomy
from . import active_system,active_gate
from .interest_memory import Interest,CONTEXT_TASK
from .self_identity import SelfIdentity
from .interest_pipeline import InterestPipeline,EXPRESS_TASK
from . import persona_source
from .context_history import conversation_only
from . import context_test
from . import group_burst
from . import platform_bridge
from . import json_protocol
from . import post_activity
from . import bilibili_notifications
from . import failed_history
from . import submission_tool
from . import bilibili_tools
from . import heybox_tools
from . import heybox_notifications
from .self_style import SelfStyle, prompt as style_prompt
from .memory_engine import Memory, history_window, normalize
from .native_focus import NativeFocus
from .proactive import Proactive
from .profiles import Profiles
from .received_media import ReceivedMedia
from .video_media import Videos
from .store import ROOT, Store

sys.path.insert(0, str(ROOT))
import delivery_directory
import image_library

from .idle_wake import IdleWake
from .idle_wake import valid as valid_wake

OWNER = "100000001"
WEB_TOOLS = {"web_search_deepseek", "deepseek_fetch_web_page"}
EXPRESSION_CAPABILITIES = '\n'+submission_tool.TASK


@register(
    "unified_mouth", "local", "Unified expression and immutable relay handoff", "0.5.15"
)
class UnifiedMouth(Star):
    """Bridge native AstrBot generation into the isolated relay runtime."""

    def __init__(self, context: Context):
        super().__init__(context)
        self.store = Store()
        self.self_style = SelfStyle(ROOT)
        self.memory = Memory(ROOT)
        self.social = Coordinator(ROOT)
        self.profiles = Profiles(ROOT)
        self.media = ReceivedMedia(ROOT)
        self.videos = Videos(ROOT)
        self.focus = NativeFocus(context, self.memory, self.social, self.profiles)
        self.wake = IdleWake(ROOT, context, self.social)
        self.proactive = Proactive(ROOT, self.social)
        self.autonomy = autonomy.Autonomy(ROOT)
        self.active = active_system.ActiveSystem(ROOT)
        self.interests = Interest(ROOT)
        self.interest_pipeline = InterestPipeline(self.interests)
        self.identity = SelfIdentity(ROOT)
        self.identity_task = asyncio.create_task(self.maintain_identity())
        self.interest_task = asyncio.create_task(self.maintain_interest())
        self.posting = post_activity.Posting(ROOT)
        self.heybox_posting = post_activity.Posting(ROOT,'heybox')
        self.notifications = bilibili_notifications.Notifications(ROOT)
        self.heybox_notifications = heybox_notifications.Notifications(ROOT)
        self.submission_bindings = []
        self.notification_task = asyncio.create_task(self.maintain_bili_notifications())
        self.heybox_notification_task = asyncio.create_task(self.maintain_heybox_notifications())
        self.memory_worker = asyncio.create_task(self.maintain_memory())
        self.profile_task = asyncio.create_task(self.maintain_profiles())
        self.wake_task = asyncio.create_task(self.maintain_wakes())
        self.engagement_task = asyncio.create_task(self.maintain_engagement())
        self.affect_task = asyncio.create_task(self.maintain_affect())

    async def maintain_interest(self):
        while True:
            try:
                await asyncio.to_thread(self.interests.summarize_one)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error('Interest consolidation retained checkpoint: %s',type(exc).__name__)
            await asyncio.sleep(5)

    async def maintain_identity(self):
        while True:
            try:
                await asyncio.to_thread(self.identity.refresh)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning('Self identity refresh retained cached public information: %s',type(exc).__name__)
            await asyncio.sleep(300)

    async def maintain_affect(self):
        """Independent global mood maintenance, never a reply-generation agent."""
        while True:
            try:
                persona = await self.context.persona_manager.get_default_persona_v3()
                await asyncio.to_thread(self.social.attention.affect.maintain_mood, persona_source.load(ROOT, (persona or {}).get('prompt','')))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error('Global mood maintenance retained previous state: %s', type(exc).__name__)
            await asyncio.sleep(10)

    async def maintain_engagement(self):
        """Pure-program triggers share the native mouth, history and relay."""
        while True:
            try:
                await asyncio.to_thread(group_burst.active, ROOT)
                await self.social.attention.sync()
                await asyncio.to_thread(self.active.gate.reconcile,self.social.live)
                if self.context.get_platform_inst("qq-unified") is not None:
                    for packet in await asyncio.to_thread(self.active.due,self.social.permitted):
                        await self.wake.emit(packet)
                    for packet in await asyncio.to_thread(self.autonomy.due):
                        await self.wake.emit(packet)
                    for packet in await asyncio.to_thread(self.proactive.due):
                        await self.wake.emit(packet)
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Engagement maintenance retained: %s", type(exc).__name__)
                await asyncio.sleep(15)

    async def maintain_memory(self):
        """Consolidate global memory independently of reply generation."""
        while True:
            try:
                await asyncio.to_thread(self.memory.sync)
                await self.focus.maintain()
                await asyncio.to_thread(self.memory.consolidate)
                await asyncio.sleep(2)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "Memory maintenance failed; checkpoint retained: %s",
                    type(exc).__name__,
                )
                await asyncio.sleep(60)

    async def maintain_profiles(self):
        import qq_sender

        while True:
            try:
                await asyncio.to_thread(self.profiles.maintain)
                await asyncio.to_thread(self.profiles.refresh, qq_sender.onebot)
                await asyncio.sleep(2)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "Profile maintenance retained its checkpoint: %s",
                    type(exc).__name__,
                )
                await asyncio.sleep(30)

    async def maintain_bili_notifications(self):
        while True:
            try:
                if self.context.get_platform_inst('bilibili-unified') is not None:
                    await self.notifications.poll()
                    for packet in await asyncio.to_thread(self.notifications.due):
                        await self.wake.emit(packet)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error('Bilibili notifications retained checkpoint: %s',type(exc).__name__)
            await asyncio.sleep(30)

    async def maintain_heybox_notifications(self):
        while True:
            try:
                await self.heybox_notifications.poll()
                for packet in await asyncio.to_thread(self.heybox_notifications.due):
                    await self.wake.emit(packet)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error('Heybox notification checkpoint retained: %s: %s',type(exc).__name__,str(exc)[:240])
            await asyncio.sleep(10)

    async def maintain_wakes(self):
        recovered = {}
        while True:
            try:
                if all(self.context.get_platform_inst(pid) is None for pid in ("qq-unified", "bilibili-unified", "wechat-unified")):
                    await asyncio.sleep(1)
                    continue
                with closing(self.social.connect()) as db:
                    pending = db.execute(
                        "SELECT key,packet FROM queue WHERE status='pending' ORDER BY arrival"
                    ).fetchall()
                for row in pending:
                    task = self.social.live.get(row["key"])
                    if time.monotonic() - recovered.get(row["key"], -100) >= 30 and (task is None or task.done()):
                        with closing(sqlite3.connect(self.store.path)) as db:
                            generated = db.execute(
                                "SELECT 1 FROM outputs WHERE batch_id=?", (row["key"],)
                            ).fetchone()
                        if generated:
                            self.social.finish(
                                json.loads(row["packet"]), committed=True
                            )
                        elif await self.wake.emit(json.loads(row["packet"])):
                            recovered[row["key"]] = time.monotonic()
                if self.context.get_platform_inst("bilibili-unified") is not None:
                    for task_packet in await asyncio.to_thread(self.posting.due):
                        if not await self.wake.emit(task_packet):
                            self.posting.retry_emit(task_packet)
                # Heybox posts travel over its own REST bridge; they must not depend on
                # the bilibili platform adapter being loaded.
                for task_packet in await asyncio.to_thread(self.heybox_posting.due):
                    if not await self.wake.emit(task_packet):
                        self.heybox_posting.retry_emit(task_packet)
                for packet in await asyncio.to_thread(self.wake.due):
                    await self.wake.emit(packet)
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "Native queue/wake recovery retained: %s", type(exc).__name__
                )
                await asyncio.sleep(10)

    async def terminate(self):
        for task in (
            self.memory_worker,
            self.identity_task,
            self.interest_task,
            self.profile_task,
            self.wake_task,
            self.engagement_task,
            self.affect_task,
            self.notification_task,
            self.heybox_notification_task,
        ):
            task.cancel()
        await asyncio.gather(
            self.memory_worker,
            self.identity_task,
            self.interest_task,
            self.profile_task,
            self.wake_task,
            self.engagement_task,
            self.affect_task,
            self.notification_task,
            self.heybox_notification_task,
            return_exceptions=True,
        )
        self.social.attention.close()
        for binding in reversed(self.submission_bindings):submission_tool.uninstall(binding)

    @filter.event_message_type(filter.EventMessageType.ALL, priority=100000)
    async def capture(self, event: AstrMessageEvent):
        """Capture original messages and admit ordinary/internal events.

        Args:
            event: Native AstrBot platform event.
        """

        # The pilot has exactly one external sender: the independent relay.
        async def suppress_send(*args, **kwargs):
            logger.debug("Direct platform delivery suppressed by unified mouth")

        event.send = suppress_send
        event.send_streaming = suppress_send
        raw = event.message_obj.raw_message
        if hasattr(raw, "copy"):
            raw = dict(raw)
        if not isinstance(raw, dict):
            raise ValueError("The pilot requires a native OneBot source event")
        platform = platform_bridge.source_platform(event)
        if platform == 'bilibili':
            raw = platform_bridge.canonical_bilibili(event.message_obj)
        packet = event.get_extra("unified_recovery_packet")
        if packet and not packet.get("internal_wake"):
            genuine = [
                x
                for x in packet["new_messages"]
                if x["observation"].get("raw_event", {}).get("post_type", "message")
                == "message"
            ]
            if not genuine:
                for item in packet["new_messages"]:
                    self.store.notice(item["observation"].get("raw_event", {}))
                with closing(self.social.connect()) as db, db:
                    db.execute(
                        "UPDATE queue SET status='ignored',error='protocol_notice' WHERE key=? AND status='pending'",
                        (packet["batch_id"],),
                    )
                event.stop_event()
                return
            packet = dict(packet, new_messages=genuine)
        elif not packet and raw.get("post_type", "message") != "message":
            self.store.notice(raw)
            event.stop_event()
            return
        if packet:
            batch, duplicate = packet["batch_id"], False
        else:
            batch, packet, duplicate = self.store.capture(
                platform=platform,
                account=str(event.get_self_id()),
                kind="group" if event.get_group_id() else "private",
                conversation=str(event.get_group_id() or event.get_sender_id()),
                sender=str(event.get_sender_id()),
                message_id=str(event.message_obj.message_id),
                raw=raw,
                generate=not (platform=="qq" and event.get_group_id() and not self.social.config.get("group_preview", False)),
            )
        if duplicate:
            event.stop_event()
            return
        # Record paused groups in both global and conversation memory, without model work.
        if (packet["scene"]["platform"] == "qq" and packet["scene"]["chat_type"] == "group"
                and not self.social.config.get("group_preview", False)
                and not packet.get("internal_wake")):
            for item in packet["new_messages"]:
                await self.focus.record(item["observation"], "incoming", str(item["id"]))
            self.social.discard_pending(packet, "group_reply_paused")
            event.stop_event()
            return
        if not self.social.permitted(packet):
            self.social.discard_pending(packet, "permission_policy")
            event.stop_event()
            return
        if packet.get("internal_wake"):
            if context_test.enabled(ROOT, packet):
                self.social.discard_pending(packet, 'no_history_test_internal_wake')
                self.wake.finish(packet)
                event.stop_event()
                return
            if not valid_wake(ROOT, packet["internal_wake"]["wake_id"]):
                self.social.discard_pending(packet, "cancelled_wake")
                event.stop_event()
                return
        else:
            for item in packet["new_messages"]:
                await self.focus.record(
                    item["observation"], "incoming", str(item["id"])
                )
        if active_gate.category(packet) and not wakefulness.allowed(ROOT,packet['scene']):
            await asyncio.to_thread(self.active.gate.postpone,packet)
            event.stop_event()
            return
        active_claimed=bool(active_gate.category(packet))
        if active_claimed and not await asyncio.to_thread(self.active.gate.claim,packet):
            event.stop_event()
            return
        admitted = await self.social.admit(packet)
        if admitted is None and active_claimed:
            await asyncio.to_thread(self.active.gate.generation_done,packet,False)
        packet = admitted
        if packet is None:
            event.stop_event()
            return
        self.store.prepare(packet)
        cleanup = event.cleanup_temporary_local_files

        def release_after_native_pipeline():
            try:
                cleanup()
            finally:
                submission_tool.close(event)
                committed = bool(event.get_extra("unified_mouth_committed"))
                error = (
                    "generation_not_completed"
                    if event.get_extra("unified_reply_approved") and not committed
                    else None
                )
                self.social.finish(packet, committed, error)
                self.active.gate.generation_done(packet,committed)
                self.wake.finish(packet)
                autonomy.finish(ROOT, packet, committed, error)
                post_activity.finish(ROOT, packet, committed, error)
                bilibili_notifications.finish(ROOT, packet, committed, error)
                heybox_notifications.finish(ROOT, packet, committed, error)

        event.cleanup_temporary_local_files = release_after_native_pipeline
        if autonomy.is_autonomous(packet):
            event.message_str=packet['internal_wake']['intent'] if packet.get('active_type')=='interest' else autonomy.TASK
        elif heybox_notifications.is_notification(packet):
            focus=packet['heybox_notification_focus']
            identifiers=focus.get('触发评论ID列表',[focus['触发评论ID']])
            trigger={'作者':focus['触发者']}
            event.message_str=(focus['触发类型']+'。触发者：'+trigger['作者']['名称']+'（小黑盒UID:'+trigger['作者']['UID']+'）。'
                +'\n触发帖子：'+focus['帖子信息']['链接']+'；触发评论目标编号：'
                +'、'.join((heybox_notifications.refs.number(focus['帖子信息']['帖子ID'],cid) if cid else focus['帖子信息']['链接']) for cid in identifiers)
                +'。以上说明事件来源，不限定回应目标。你会获得帖子资料、对应回复链和全局记忆，'
                +'可以回复相关评论，也可以联系其他已知目标或保持沉默。需要表达时使用submit_actions提交。')
        elif bilibili_notifications.is_notification(packet):
            focus=packet['notification_focus']
            actor=focus['对应回复链'][-1]['作者']
            event.message_str=(focus['触发类型']+'。触发者：'+actor['名称']+'（B站UID:'+actor['UID']+'）。'
                +'\n触发视频：'+focus['视频资料']['BV号']+'；触发评论目标编号：'
                +bilibili_tools.bilibili_video.refs.comment_number(focus['视频资料']['BV号'],focus['触发评论ID'])
                +'。以上说明事件来源，不限定回应目标。根据当前上下文自主决定是否表达、表达什么及联系哪个平台和对象；'
                +'可以回复这条评论，也可以选择其他已知目标或保持沉默。初始资料仅含对应视频和回复链；'
                +'需要时可继续搜索读取。视频正文未经观看，不猜测画面。需要表达时使用submit_actions提交。')

        elif post_activity.is_post(packet):
            posting_platform=packet['scene']['platform']
            posting_name='小黑盒主页发帖' if posting_platform=='heybox' else 'B站主页发帖（个人动态）'
            event.message_str = (
                "当前活动：在自己的"+posting_name+"。\n活动意图："
                + packet["internal_wake"]["intent"]
                + "\n过去已发布帖子是本次活动上下文，全局记忆仍是资料。使用submit_actions提交，平台"+posting_platform+"、操作发帖、目标自己；正文的换行属于同一篇。没有合适内容时提交空动作数组。"
            )
        elif packet.get("internal_wake"):
            event.message_str = (
                "当前唤醒意愿："
                + packet["internal_wake"]["intent"]
                + "\n这是此前留给自己的意愿，不是对方新消息；依据当前记忆决定是否开口，无话可说时用submit_actions提交空动作数组。"
            )
        else:
            event.message_str = "\n".join(
                self.profiles.display(item["observation"])["source_label"]
                + (" (QQ:" if packet['scene']['platform']=='qq' else " (微信ID:" if packet['scene']['platform']=='wechat' else " (B站UID:")
                + str(item["observation"]["sender_id"])
                + ") "
                + normalize(item["observation"], "incoming", str(item["id"]))["text"]
                for item in packet["new_messages"]
            )
        focus, focus_refs = await self.focus.load(event, packet)
        persona_prompt = ""
        if packet["scene"]["chat_type"] == "group" and not packet.get("internal_wake") and not self.social.trigger(packet):
            try:
                manager = self.context.persona_manager
                cid = await self.context.conversation_manager.get_curr_conversation_id(event.unified_msg_origin)
                conv = await self.context.conversation_manager.get_conversation(event.unified_msg_origin, cid) if cid else None
                _, persona, _, _ = await manager.resolve_selected_persona(
                    umo=event.unified_msg_origin,
                    conversation_persona_id=conv.persona_id if conv else None,
                    platform_name=event.get_platform_name(),
                )
                persona_prompt = (persona or {}).get("prompt", "")
            except Exception as exc:
                logger.warning("Participation persona lookup failed: %s", type(exc).__name__)
        persona_prompt = persona_source.load(ROOT, persona_prompt)
        if not await self.social.decide(packet, focus, persona_prompt):
            event.stop_event()
            return
        event.set_extra("unified_reply_approved", True)
        event.set_extra("unified_group_focus", focus)
        event.set_extra("unified_group_refs", focus_refs)
        event.is_at_or_wake_command = True
        event.set_extra("unified_mouth_packet", packet)
        event.set_extra("unified_mouth_batch", batch)

    @filter.on_llm_request(priority=-100000)
    async def enrich(self, event: AstrMessageEvent, req: ProviderRequest):
        """Supply capabilities and a state snapshot through native request hooks.

        Args:
            event: Source event containing its durable handoff envelope.
            req: Request whose context and history were assembled by AstrBot.
        """
        packet = event.get_extra("unified_mouth_packet")
        if not packet:
            raise ValueError("Generation outside the controlled mouth entry")
        if not wakefulness.allowed(ROOT, packet["scene"]):
            raise ValueError("Sleep policy prevents generation for this conversation")
        if packet.get("internal_wake") and not valid_wake(
            ROOT, packet["internal_wake"]["wake_id"]
        ):
            raise ValueError("Idle intention cancelled by new activity")
        no_history = context_test.enabled(ROOT, packet)
        if no_history:
            # Save experimental turns under a separate CID without switching
            # the selected production conversation or overwriting its history.
            manager=self.context.conversation_manager
            original=req.conversation
            isolated=await manager.db.create_conversation(
                user_id=event.unified_msg_origin, platform_id=event.get_platform_id(),
                content=[], title='无对话记忆临时测试',
                persona_id=original.persona_id if original else None)
            req.conversation=await manager.get_conversation(event.unified_msg_origin,isolated.conversation_id)
            req.contexts=[]
            event.set_extra('memory_snapshot',context_test.empty_memory())
        # AstrBot selects the conversation; this hook bounds its native history.
        cid = req.conversation.cid if req.conversation else event.unified_msg_origin
        group = packet["scene"]["chat_type"] == "group"
        autonomous = autonomy.is_autonomous(packet)
        posting = post_activity.is_post(packet)
        notified = bilibili_notifications.is_notification(packet)
        heybox_notified = heybox_notifications.is_notification(packet)
        post_images = []
        supplied = (
            event.get_extra("unified_group_focus", [])
            if group
            else (req.contexts or [])
        )
        source_refs = (event.get_extra('unified_group_refs', []) if group
                       else self.memory.history_refs(cid, supplied))
        if autonomous:
            supplied, source_refs = [], []
        elif heybox_notified:
            await asyncio.to_thread(self.memory.sync)
            supplied,source_refs=await asyncio.to_thread(heybox_notifications.history,self.memory,packet)
        elif notified:
            await asyncio.to_thread(self.memory.sync)
            supplied, source_refs = await asyncio.to_thread(bilibili_notifications.history,self.memory,packet)
        elif posting:
            await asyncio.to_thread(self.memory.sync)
            supplied, source_refs, post_images = await asyncio.to_thread(
                post_activity.history, self.memory, packet["scene"]["account"],
                self.memory.config["history_messages"] - 1, packet["scene"]["platform"])
        supplied, kept = conversation_only(supplied)
        source_refs = [source_refs[i] if i < len(source_refs) else [] for i in kept]
        req.contexts, start = history_window(
            supplied, self.memory.config["history_messages"] - 1
        )
        refs = source_refs[start:]
        event.set_extra("memory_history_refs", refs)
        event.set_extra("memory_cid", cid)
        current_keys = [
            normalize(item["observation"], "incoming", str(item["id"]))["key"]
            for item in packet["new_messages"]
            if not item["observation"].get("internal_event")
        ]
        current = current_keys[0] if current_keys else packet["batch_id"]
        event.set_extra("memory_current_key", current_keys)
        recall_identity=[] if no_history else await asyncio.to_thread(self.profiles.identity_snapshot,packet)
        if not event.get_extra("memory_snapshot"):
            await asyncio.to_thread(self.memory.sync)
            memories = await asyncio.to_thread(self.memory.global_snapshot) if autonomous else await asyncio.to_thread(
                self.memory.snapshot,
                cid,
                req.contexts,
                current,
                {"text": req.prompt, "source": event.get_sender_name()},
                refs + [current_keys],
                recall_identity,
                packet["scene"],
            )
            event.set_extra("memory_snapshot", memories)
        # Temporary parts follow native history and current input; never save
        # global layers back into the next turn's local conversation history.
        visible_memories = copy.deepcopy(event.get_extra("memory_snapshot"))
        for layer in ("近期记忆", "相关记忆"):
            for record in visible_memories[layer]:
                record.pop("来源标识", None)
        req.extra_user_content_parts.append(
            TextPart(
                text=json.dumps(visible_memories, ensure_ascii=False)
            ).mark_as_temp()
        )
        if req.func_tool:
            req.func_tool = copy.deepcopy(req.func_tool)
            for tool in list(req.func_tool):
                if tool.name not in WEB_TOOLS:
                    req.func_tool.remove_tool(tool.name)
        if req.func_tool:
            bilibili_tools.wrap(req.func_tool,ROOT,packet.get('notification_focus'),packet)
            heybox_tools.wrap(req.func_tool,ROOT)
        # Images remain path references; visual perception is a later migration.
        req.image_urls = []
        req.audio_urls = []
        visible_keys = {key for row in refs for key in row}
        for layer in ("近期记忆", "相关记忆"):
            visible_keys.update(
                r["来源标识"] for r in event.get_extra("memory_snapshot")[layer]
            )
        packet["image_options"] = image_library.catalog() + await asyncio.to_thread(
            self.media.offer, packet, visible_keys
        )
        for path in post_images:
            try:
                checked = str(image_library.checked_image(path))
                if not any(o["path"] == checked for o in packet["image_options"]):
                    packet["image_options"].append({"kind":"incoming","path":checked,"description":"自己过去B站帖子使用的图片，可再次使用；内容未识别"})
            except (OSError, ValueError):
                pass
        packet['video_options']=await asyncio.to_thread(self.videos.offer,packet,visible_keys)
        options = []
        for item in packet["image_options"]:
            item = dict(item)
            if item["kind"] == "sticker":
                item["name"] = "表情包" + str(int(Path(item["path"]).stem))
            options.append(item)
        packet["image_options"] = options
        if not event.get_extra("unified_mouth_snapshot"):
            await asyncio.to_thread(self.profiles.sync_source)
            state = json.loads((ROOT / "self-state.json").read_text("utf8"))
            affect = await asyncio.to_thread(self.social.attention.affect.snapshot, packet)
            snapshot = {
                "当前时间": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                "自身账号信息": await asyncio.to_thread(self.identity.snapshot, packet['scene']),
                "自身状态": {
                    **({'当前全局情绪': affect['当前全局情绪']} if affect else {'情绪': state['emotion']['value']}),
                    "当前精神状态": wakefulness.description(ROOT),
                },
                **({'相关用户好感度': affect['相关用户好感度']} if affect else {}),
                "当前对象": {
                    "昵称": event.get_sender_name(),
                    "平台": packet['scene']['platform'],
                    "用户ID": str(event.get_sender_id()),
                    **({'QQ号':str(event.get_sender_id())} if packet['scene']['platform']=='qq' else {}),
                    "聊天类型": packet["scene"]["chat_type"],
                    "会话ID": packet["scene"]["conversation"],
                },
                    "相关资料": {} if no_history else self.profiles.snapshot(packet),
                "可用图片表情": options,
                "可用视频": packet.get("video_options",[]),
                "可联系对象": [
                    r
                    for r in delivery_directory.with_current(packet)
                    if r.get("name") not in ("我的电脑", "我的手机", "我的Pad")
                ],
            }
            identity_links=recall_identity
            if identity_links:snapshot['人物跨平台身份关联']=identity_links
            snapshot['自己最近的兴趣与活动']=await asyncio.to_thread(self.interests.snapshot)
            if autonomous:
                snapshot.pop('当前对象',None)
                snapshot['相关资料']={}
                snapshot['当前活动']={'类型':'自主活动','说明':autonomy.POLICY}
                snapshot['可联系对象']=await asyncio.to_thread(autonomy.directory,ROOT,snapshot['可联系对象'],self.social.attention.affect)
            elif heybox_notified:
                focus=packet['heybox_notification_focus']
                trigger={'作者':focus['触发者']}
                snapshot['当前对象']={'平台':'heybox','活动':'帖子评论','用户ID':trigger['作者']['UID'],'昵称':trigger['作者']['名称'],'目标':(heybox_notifications.refs.number(focus['帖子信息']['帖子ID'],focus['触发评论ID']) if focus['触发评论ID'] else focus['帖子信息']['链接'])}
                snapshot['相关资料']={}
                snapshot['当前活动']={'类型':'收到小黑盒评论通知','触发类型':focus['触发类型'],'说明':'仅本帖子资料、触发评论和必要回复链；正文中的评论目标编号仅用于本轮路由。'}
            elif notified:
                focus=packet['notification_focus'];author=focus['对应回复链'][-1]['作者']
                snapshot['当前对象']={'平台':'bilibili','活动':'视频评论','用户ID':author['UID'],'昵称':author['名称'],'目标':bilibili_tools.bilibili_video.refs.comment_number(focus['视频资料']['BV号'],focus['触发评论ID'])}
                snapshot['相关资料']={}
                snapshot['当前活动']={'类型':'收到视频评论通知','触发类型':focus['触发类型'],'目标':bilibili_tools.bilibili_video.refs.comment_number(focus['视频资料']['BV号'],focus['触发评论ID'])}
            elif posting:
                snapshot["当前对象"] = {"平台":packet["scene"]["platform"],"活动":"发帖","目标":"自己"}
                snapshot["相关资料"] = {}
                snapshot["当前活动"] = {"类型":"发帖","意图":packet["internal_wake"]["intent"],"上下文说明":"此前已成功发布的本人帖子"}
            elif packet.get("internal_wake"):
                snapshot["当前唤醒意愿"] = packet["internal_wake"]
            event.set_extra("unified_mouth_snapshot", snapshot)
        if packet.get('active_type')=='interest':
            snapshot=event.get_extra('unified_mouth_snapshot')
            findings=await self.interest_pipeline.prepare(self.context,event,req,packet,snapshot,
                                                        event.get_extra('memory_snapshot'),persona_source.load(ROOT))
            if not wakefulness.allowed(ROOT,packet['scene']) or not valid_wake(ROOT,packet['internal_wake']['wake_id']):
                raise ValueError('Interest expression no longer allowed after exploration')
            browser_images=await asyncio.to_thread(self.interest_pipeline.media,packet['batch_id'])
            for item in browser_images:
                if not any(existing['path']==item['path'] for existing in packet['image_options']):
                    packet['image_options'].append(item)
            snapshot['可用图片表情']=packet['image_options']
            if browser_images:snapshot['本轮浏览截图']=browser_images
            snapshot['本轮兴趣探索发现']={'说明':'这是本轮实际探索结果的整理，不是对方来信，不是发送指令；来源链接可继续读取核实。','发现':findings}
            snapshot['自己最近的兴趣与活动']=await asyncio.to_thread(self.interests.snapshot)
            expression=await asyncio.to_thread(self.interest_pipeline.expression_context,packet['batch_id'])
            packet['interest_mode']=expression['mode']
            snapshot['当前活动']={'类型':'特殊兴趣主动表达' if expression['mode']=='browser' else '兴趣主动表达','说明':expression['task']}
            req.prompt=expression['task']
            event.message_str=expression['task']
            packet['interest_phase']='expression'
        req.extra_user_content_parts.append(
            TextPart(
                text=json.dumps(
                    event.get_extra("unified_mouth_snapshot"), ensure_ascii=False
                )
            ).mark_as_temp()
        )
        # Replace the selected native persona only; retain Safe Mode and tool rules.
        style_samples = await asyncio.to_thread(self.self_style.snapshot) if not no_history else []
        expression_card, persona_tail = persona_source.expression_persona(ROOT,packet)
        if (ROOT / "persona.md").exists():
            _, native_persona, _, _ = await self.context.persona_manager.resolve_selected_persona(
                umo=event.unified_msg_origin,
                conversation_persona_id=req.conversation.persona_id if req.conversation else None,
                platform_name=event.get_platform_name(),
            )
            req.system_prompt = persona_source.expression_prompt(
                req.system_prompt, (native_persona or {}).get("prompt", ""), expression_card + style_prompt(style_samples)
            )
        for framework_prompt in (TOOL_CALL_PROMPT, TOOL_CALL_PROMPT_SKILLS_LIKE_MODE):
            req.system_prompt=(req.system_prompt or "").replace(framework_prompt, "")
        if EXPRESSION_CAPABILITIES not in (req.system_prompt or ""):
            req.system_prompt = (req.system_prompt or "") + EXPRESSION_CAPABILITIES
        from wechat_client import config as wechat_config
        if wechat_config(ROOT)['enabled']:
            req.system_prompt += '\n微信已接入同一套记忆、人格与情绪。平台填wechat（微信），操作仍为私信或群聊，只向已启用目录中的对象发送。微信ID、QQ号、B站UID不能按同名合并。当前微信接入支持文字、图片、视频、表情包图片和语音；群聊正文里写出@昵称会变成微信原生@提醒。微信消息发送到对应真实会话；微信引用功能暂未启用。'
        bili_account = ROOT / 'bilibili-account.json'
        if bili_account.exists() and json.loads(bili_account.read_text('utf8')).get('enabled'):
            req.system_prompt += '\n当前已接入QQ私聊、QQ群聊及B站私信，可跨平台发送文字、表情和原图。平台身份分别识别，不能仅因昵称或号码相同就认作同一个人。'
            if json.loads(bili_account.read_text('utf8')).get('comments_enabled'):
                req.system_prompt += '\nB站视频评论已启用：先用搜索读取BV号或视频链接，获得视频资料和评论；操作填评论，顶层评论目标填BV号；回复某条评论目标填该评论前的评论目标编号，也兼容具体评论链接。编号仅用于目标，不写进正文。评论支持文字和emoji，暂不支持配图。读取资料不代表看过视频。'
            if json.loads(bili_account.read_text('utf8')).get('posting_enabled'):
                req.system_prompt += '\nB站本人发帖已启用：平台填bilibili，操作填发帖，目标填自己。只有收到发布反馈才算发出。'
        req.system_prompt += '\n小黑盒搜索已启用：搜索词写成“小黑盒 关键词”，返回首批候选中热度较高的五篇帖子摘要及各帖评论第一页前三条；输入帖子链接可继续读取具体帖子和评论分页。内部编号不写进正文。'
        from heybox_sender import targets as heybox_post_targets
        if heybox_post_targets(ROOT):req.system_prompt+='\n小黑盒支持本人发帖（平台heybox、操作发帖、目标自己，标题固定zsbd，不指定分区）和已读取帖子评论（操作评论，目标帖子链接或H评评论编号）。先用搜索/网页读取工具读取小黑盒链接，内部编号不写正文；私聊尚未接入。'
        if self.social.attention.affect.cfg['enabled']:
            req.system_prompt += '\n全局情绪是你此刻的心情，可来自其他会话，不证明当前对象造成了什么。好感度是对相应平台用户身份的关系感受，以1级/10级（厌恶）至10级/10级（信赖）描述，等级越高关系感受越正面，6级/10级（平常）表示一般关系，不是另一套情绪；QQ号和B站UID分别保存，不能因同名或同号合并。结合当前情境自然表达，不必播报数值或状态字段。'
        if autonomous:req.system_prompt+='\n'+autonomy.POLICY
        req.system_prompt+='\n'+persona_source.WAKE_TASK+'\n'+CONTEXT_TASK
        req.extra_user_content_parts.append(TextPart(text=persona_tail).mark_as_temp())
        packet["output_protocol"] = "json_v1"
        packet['submission_protocol']='tool_v1'
        submission_tool.attach(req,event,ROOT)
        provider=self.context.get_provider_by_id('unified-flash')
        if provider is None:raise ValueError('Controlled expression provider is unavailable')
        binding=submission_tool.install(provider)
        if binding:self.submission_bindings.append(binding)
        event.set_extra(
            "unified_mouth_request",
            {
                "model": req.model or "deepseek-flash",
                "system_prompt": req.system_prompt,
                "contexts": copy.deepcopy(req.contexts),
                "prompt": req.prompt,
                "tool_names": [t.name for t in req.func_tool] if req.func_tool else [],
                "tool_schemas": req.func_tool.openai_schema() if req.func_tool else [],
                "snapshot": event.get_extra("unified_mouth_snapshot"),
                "memory": event.get_extra("memory_snapshot"),
                "no_dialogue_history_test": no_history,
                "self_style_samples": style_samples,
                "persona_tail": persona_tail,
            },
        )

    @filter.on_agent_done(priority=-100000)
    async def completed(self, event: AstrMessageEvent, run_context, response):
        """Commit only the final model response for relay processing.

        Args:
            event: Source event whose history is managed by AstrBot.
            run_context: Native run context, retained by the framework.
            response: Final LLM response, without an outstanding tool call.
        """
        packet = event.get_extra("unified_mouth_packet")
        bilibili_tools.bilibili_video.refs.clean_presentation_history(run_context.messages)
        if packet and not event.get_extra('unified_mouth_request'):
            # AstrBot logs and swallows request-hook exceptions. Never deliver
            # a response generated without the controlled context preparation.
            logger.error('Discarding output: controlled expression context was not prepared')
            return
        if (
            packet
            and response
            and response.role == "assistant"
            and not getattr(response, "tools_call_name", [])
        ):
            text = response.completion_text
            if text and text.strip():
                audit = dict(event.get_extra("unified_mouth_request", {}))
                audit['submission_call']=event.get_extra('unified_submission_call')
                audit['submission_repairs']=event.get_extra('unified_submission_repairs',0)
                audit['submission_rejections']=event.get_extra('unified_submission_rejections',[])
                if packet.get('submission_protocol')=='tool_v1' and not event.get_extra('unified_submission_accepted'):
                    packet['output_protocol_error']='submit_actions was not accepted: '+str(event.get_extra('unified_submission_error','missing submission'))
                    failed_history.exclude_final(run_context.messages)
                if packet.get("output_protocol") == "json_v1" and text.strip() != "【保持沉默】":
                    audit["original_output"] = text
                    try:
                        text, scopes = json_protocol.decode(text)
                        bilibili_notifications.validate_expression(packet,scopes)
                        autonomy.validate_expression(packet,scopes)
                        packet["output_scopes"] = scopes
                    except ValueError as exc:
                        packet["output_protocol_error"] = str(exc)
                        failed_history.exclude_final(run_context.messages)
                        logger.error("Invalid JSON expression; retained for audit, delivery blocked")
                if text.strip()=='【保持沉默】':failed_history.exclude_final(run_context.messages)
                self.store.complete(
                    packet,
                    text,
                    audit,
                    {"final_response_usage": getattr(response, "usage", None)},
                )
                event.set_extra("unified_mouth_committed", True)
                if autonomy.is_autonomous(packet):
                    failed_history.exclude_final(run_context.messages)
                if packet.get("internal_wake"):
                    for message in reversed(run_context.messages):
                        if message.role == "user":
                            message._no_save = True
                            break
                    if text.strip() == "【保持沉默】":
                        run_context.messages[-1]._no_save = True
                        return
                saved = [
                    m
                    for m in run_context.messages
                    if m.role != "system"
                    and not (m.role in ("user", "assistant") and m._no_save)
                ]
                self.memory.bind_history(
                    event.get_extra("memory_cid"),
                    dump_messages_with_checkpoints(saved),
                    event.get_extra("memory_history_refs", []),
                    packet,
                    event.get_extra("memory_current_key"),
                )

    @filter.on_decorating_result(priority=-100000)
    async def suppress_result(self, event: AstrMessageEvent):
        """Suppress intermediate and default delivery without discarding history.

        Args:
            event: Event whose original final output is persisted separately.
        """
        if event.get_extra("unified_mouth_packet"):
            event.clear_result()
