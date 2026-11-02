# 邮箱与短信接收

`receiver.py` 接收邮箱，`sms_ingress.py` 保留短信转发Webhook入口，测试为本地接口测试。短信功能此前因手机后台稳定性停用，源码仍保留。

IMAP账号和授权码只放在运行机器的 `mail-secret.json`；仓库示例的账号和授权码均为空。实际邮件、验证码、附件、短信路由密钥、数据库和消息内容不分发。

收信账号由环境变量 `BOT_INBOX_ACCOUNT` 指定；授权码由运行目录 `mail-secret.json` 的 `authorization_code` 提供。
