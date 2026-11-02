# 历史模块

- `legacy-brain/`：迁移AstrBot之前的持久脑、调度、记忆、口和工具相关源码。
- `qq-eyes/`：早期QQ消息客观采集程序。
- `web-chat/`：最初网页/API聊天入口。
- `inbox-local/`：本地早期邮箱/短信接收版本，现用服务器版在 `modules/inbox`。
- `migration-tools/`：只用于对照迁移过程的脚本，不是新机安装入口。

这些模块已与现用链路分开。没有附带旧数据、人物卡、授权码、凭据或历史对话；不要与当前AstrBot服务并行回复同一账号。
