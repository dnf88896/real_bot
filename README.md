# real_bot

以 AstrBot 为接入与上下文骨架的多平台仿真人项目：不同平台共用记忆、人格和情绪，由统一表达模型决定内容，外部程序负责路由、媒体处理、发送和回执。

本仓库保存 **2026-11-02 的现用模块源码与脱敏部署模板**，同时保留迁移前的脑口系统。它不是带登录状态的服务器镜像；没有 API Key、邮箱授权码、Cookie、聊天记录、人物资料、记忆数据库、截图或模型权重。所有账号均已换成示例身份。`config/examples/api.json` 的密钥为空，需在部署机器私下填写。

## 模块位置

| 目录 | 内容 |
|---|---|
| [modules/unified-mouth](modules/unified-mouth/) | AstrBot 插件、统一口、三层记忆、资料与身份绑定、调度、情绪/好感度/作息、主动系统、联网、各平台路由、媒体、回执、隐私与费用计量 |
| [modules/wechat-native](modules/wechat-native/) | 基于 Webox 的微信原生数据读取与接入端源码，保留上游许可证 |
| [modules/wechat-ui](modules/wechat-ui/) | 微信会话选择、标题复核、串行输入、@、媒体发送、持久队列与导航故障隔离 |
| [modules/wechat-voice-extraction](modules/wechat-voice-extraction/) | 微信语音消息与 SILK 文件提取 |
| [modules/wechat-asr](modules/wechat-asr/) | 独立 SenseVoice/Sherpa-ONNX 语音转文字服务 |
| [modules/virtual-mic](modules/virtual-mic/) | 虚拟麦克风、播放与微信录音发送 |
| [modules/genie-tts](modules/genie-tts/) | Genie 语音合成、模型装载、量化与工作进程；权重不入库 |
| [modules/heybox-bridge](modules/heybox-bridge/) | 小黑盒 REST/CLI 接入、搜索、发帖、评论和通知支持 |
| [tools/operator](tools/operator/) | 开关、人格上传、专用 Key 轮换、隐私状态和用量报告 |
| [config/examples](config/examples/) | 空凭据配置及参数示例，默认关闭实际发送 |
| [deploy/systemd](deploy/systemd/) | 生产服务布局参考，部署前按机器调整路径 |
| [archive](archive/) | 旧版长期运行脑、旧口、QQ眼、早期网页与迁移工具；不与新系统同时启动 |

## 当前能力

- QQ、微信私聊/群聊；B站私信、动态、视频评论与通知；小黑盒发帖、评论与通知；邮箱接收，短信入口作为停用模块保留。
- 统一提交协议，正文位置标记与确定性媒体提取，程序负责发送；图片、表情池、视频、语音、@、分气泡和节奏控制按平台能力执行。
- 当前会话焦点、全局近期记忆、递归中期摘要、长期记忆检索；跨平台身份疑似/确认关联、好感度和私聊隐私隔离。
- 私聊优先调度、群聊合批与参与控制、显式自唤醒、空闲触发主动、普通兴趣探索/表达两轮与周期性 DSH 浏览截图探索。
- 全局情绪、按身份好感度、作息及发送门禁；全局人格和主人 QQ 私聊独立测试人格。
- 专用机器人 Key 管理、模型/工具用量账本、按用途计费估算、开关与故障日志。

能力不等于每个平台完全一致。微信依赖唯一 UI 执行器与已登录客户端；QQ 引用与微信引用的启用策略不同；B站评论图片可能受平台审核影响。实际范围见代码与[部署说明](docs/DEPLOYMENT.md)。

## 从哪里开始

1. 阅读[架构与边界](docs/ARCHITECTURE.md)、[模块地图](docs/MODULES.md)和[外部依赖](docs/DEPENDENCIES.md)。
2. 按[部署说明](docs/DEPLOYMENT.md)准备 AstrBot、OneBot 接入和可选平台；不要直接运行旧迁移脚本。
3. 复制 `config/examples/` 到部署目录并私下填入自己的凭据。仓库中的示例账号不是可用账号。
4. 使用[运维工具说明](docs/OPERATIONS.md)管理开关、人格与费用。提交前运行 `python tools/check_public_tree.py`。

现用代码仍采用 `/opt/astrbot-mouth` 等固定服务目录；它们是兼容现有运行布局，不代表克隆后可以不配置直接启动。框架、模型、客户端和平台登录端通过外部依赖安装，不复制私人运行环境。

## 授权与来源

第三方代码保留原许可证和来源，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。仓库创建者尚未为原创部分单独选择许可证；这里没有代替作者额外授予授权。
