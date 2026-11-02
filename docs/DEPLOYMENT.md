# 部署说明

这是现网源码快照的部署地图，不是已经验证的全自动新机安装器。以下操作仅在你自己的目标机器执行，本次发布不会修改原服务器。

## 目录布局

| 仓库源 | 运行位置 |
|---|---|
| `modules/unified-mouth` | `/opt/astrbot-mouth` |
| AstrBot上游源码/虚拟环境 | `/opt/astrbot-src` |
| AstrBot配置/数据 | `/opt/astrbot-runtime/data` |
| 统一插件同版本副本或链接 | `/opt/astrbot-runtime/data/plugins/astrbot_plugin_unified_mouth` |
| `modules/wechat-ui` | `/opt/wechat-webox/session-selector` |
| `modules/virtual-mic` | `/opt/wechat-webox/virtual-mic` |
| `modules/wechat-voice-extraction` | `/opt/wechat-webox/voice-extraction` |
| `modules/wechat-asr` | `/opt/wechat-asr` |
| `modules/genie-tts` | `/opt/genie-tts-lab` |
| `modules/inbox` | `/opt/bot-inbox` |

核心服务默认使用 `qq-social` 用户。用户/组ID、文件权限和Python路径按机器调整；示例账号 `100000001` 是主人QQ，`100000002` 是机器人QQ，均非真实账号。遗留兼容代码仍有显式账号判断，部署时需要统一替换这些示例值，不能只改某一处配置。

## 配置

1. 准备AstrBot与OneBot网关；WebUI和内部桥接端口默认只绑定本机，不直接公开控制入口。
2. 将核心源码放入运行目录，安装依赖并注册插件。旧迁移工具仅供对照，不能作为新机启动入口。
3. 将 `config/examples` 对应JSON复制到运行目录：`memory-config.json` 放到 `memory/config.json`；`persona*.md` 放到根目录。微信坐标配置放到对应UI模块。
4. 在服务器私下填写 `api.json` 的 `api_key`，以及需要的平台登录配置；文件权限设为0600或仅服务账号可读。
5. 配置AstrBot的 `unified-flash` provider及模型。`bot_credentials.py`/`rotate_bot_key.py`维护同一个机器人Key的配置副本；不要混用其他项目的Key。
6. 逐个平台登录、验证接收和回执，再在配置里启用发送。示例配置默认关闭实际投递，并不携带可用账号。
7. 审阅 `deploy/systemd` 的路径和依赖后安装所需服务。不要启用 `archive/` 对应旧脑口服务。

## 平台特别要求

- 微信：需要固定桌面环境、正确窗口尺寸和音频服务；首次配置时检查UI区块和偏移，所有操作由唯一队列串行执行。数据库密钥与微信登录状态必须留在部署机器，绝不上传。
- B站：完成二维码登录后配置私信、动态、评论能力，并安装底层客户端依赖。成功上传不代表平台审核即时可见。
- 小黑盒：安装 `modules/heybox-bridge`，用本人账号登录。会话文件不入库；REST默认本地8010端口。
- 邮箱：IMAP授权码只放 `/opt/bot-inbox/mail-secret.json`。短信入口留作可选模块，默认不启用。
- 语音：另行安装ASR/TTS模型及系统音频依赖。没有模型时保留文字功能，不把模型下载失败伪装成已发送语音。

## 初次验收

先关闭主动、群触发与公开发帖，只在自己的私聊验证文字/图片/表情/语音/视频、队列去重、标题复核与记忆归属，再逐项开放。仓库未自动调用真实平台或模型执行测试。
