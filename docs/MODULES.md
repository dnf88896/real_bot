# 核心模块地图

`modules/unified-mouth` 同时是 AstrBot 插件源和独立程序共享源。部署时安装到 `/opt/astrbot-mouth`，并在 AstrBot 插件路径保留同版本副本或链接，避免两边漂移。

| 职责 | 主要文件 |
|---|---|
| AstrBot请求/事件整合 | `main.py`, `run.py`, `transport.py`, `store.py`, `platform_bridge.py` |
| 上下文、历史与失败过滤 | `native_focus.py`, `failed_history.py`, `message_semantics.py` |
| 三层记忆 | `memory_engine.py`, `recall_identity.py`, `output_memory.py` |
| 资料、跨平台身份 | `profiles.py`, `profile_support/`, `identity_links.py`, `identity_idle.py` |
| 调度、参与、群聚合 | `coordinator.py`, `group_burst.py`, `attention_support.py`, `proactive.py`, `active_roster.py` |
| 作息、情绪与好感 | `wakefulness.py`, `affect.py`, `behavior.py` |
| 主动与兴趣 | `active_system.py`, `active_gate.py`, `idle_wake.py`, `wakeups.py`, `post_activity.py`, `interest_pipeline.py`, `interest_memory.py`, `browser_interest.py`, `browser_explore_mcp.py` |
| 表达、人格、提交 | `persona_source.py`, `submission_tool.py`, `json_protocol.py` |
| 中转与媒体 | `relay.py`, `resource_preparse.py`, `image_library.py`, `sticker_pool.py`, `received_media.py`, `video_media.py`, `outgoing_mentions.py`, `group_reply.py` |
| QQ | `qq_sender.py`, `qq_voice.py`, `share_cards.py`；接入使用AstrBot原生OneBot |
| 微信 | `wechat_bridge.py`, `wechat_client.py`, `wechat_sender.py`, `wechat_webox.py`, `wechat_ui_transport.py`, `wechat_validator.py`, `wechat_title.py`, `wechat_ocr_boxes.py`, `wechat_asr.py` |
| B站 | `bilibili_login.py`, `bilibili_sender.py`, `bilibili_posts.py`, `bilibili_comments.py`, `bilibili_notifications.py`, `bilibili_video.py`, `bilibili_tools.py`, `bilibili_refs.py` |
| 小黑盒 | `heybox_sender.py`, `heybox_notifications.py`, `heybox_search.py`, `heybox_tools.py`, `heybox_context.py`, `heybox_refs.py` |
| OCR、识图、合成 | `image_ocr.py`, `ocr_worker.py`, `image_vision.py`, `voice_protocol.py`, `voice_output.py`, `tts_worker.py` |
| 隐私与运维 | `private_chat_state.py`, `privacy_commands.py`, `group_input_control.py`, `private_idle_control.py`, `private_idle_policy.py`, `transport_alerts.py` |
| API与用量 | `bot_credentials.py`, `rotate_bot_key.py`, `usage_meter.py`, `usage_report.py` |

`archive/` 只保留源代码。旧 DSH 脑不再是现用主链路；不要因看到历史 worker 就同时启用旧回复服务。没有复制本地调查报告、真实对话样本、模型试音、生成截图和迁移期数据库。
