# 外部依赖与未分发资源

| 依赖 | 说明 |
|---|---|
| [AstrBot](https://github.com/AstrBotDevs/AstrBot) | 现网基准4.29.1，原生接入、历史、模型和工具循环；不复制整个框架运行目录 |
| OneBot QQ网关 | 需自行登录并配置本地WebSocket；本仓库不附QQ客户端、会话与授权 |
| [Webox](https://github.com/netcat-ai/webox) | `modules/wechat-native` 为项目中使用的修改版，源码与MIT/第三方声明保留；需微信客户端持续登录 |
| [xiaoheihe-cli](https://github.com/AaronZ345/xiaoheihe-cli) | 基于上游0.1.0、提交 `954fb4753fb1a879572239f2a54b520a91dc814d` 的已安装源码，含项目现用桥接扩展；MIT保留 |
| [Bilibili Adapter](https://github.com/Hina-Chat/astrbot_plugin_bilibili_adapter) | 底层客户端外部依赖；上游未提供明确LICENSE，因此不直接收录其两个客户端文件。项目自己的发送/动态/评论/通知模块已收录。部署时按下节自行获取 |
| DeepSeek API / DSH | 模型、搜索和隔离浏览；不附任何账号、API Key或DSH用户目录。特殊轮MCP入口是 `browser_explore_mcp.py` |
| SenseVoiceSmall / Sherpa-ONNX | 微信语音识别，需准备 `model.int8.onnx` 和 `tokens.txt` |
| RapidOCR / ONNX Runtime | 图片OCR；模型、缓存和识别结果不入库 |
| Genie TTS / 音色权重 | 工作进程与量化源码已收录；模型、角色参考音频、语音样本不分发，需自行准备可使用资源 |
| PulseAudio / FFmpeg / X11 / OCR依赖 | 微信虚拟麦克风与桌面操作所需系统组件 |

## B站客户端放置

自行获取上游适配器的 `bilibili_client.py`、`bilibili_wbi.py`，按其使用条件安装到 `/opt/astrbot-mouth/bilibili-client/`。上层的 `bilibili_sender.py` 会创建独立包命名空间加载它们。

现网对客户端有局部适配，通用上游版本并不保证与当前接口完全一致；接新环境时需验证登录、上传、发送回执和WBI签名。此仓库未把没有明确授权的上游源码再发布为自己的文件。

## 安装环境分离

核心Python环境、OCR/ASR环境、TTS环境、浏览器环境分别管理。根 `requirements.txt` 只列核心与操作端依赖；各语音/桥接模块有独立需求。不要把浏览器、模型文件或整个虚拟环境提交进Git。
