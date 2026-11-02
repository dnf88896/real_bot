# 运维工具

操作端需要Python 3.12、Paramiko、能连接服务器的SSH账户，以及预先确认的SSH主机密钥。配置只从环境变量读取，见 `.env.example`；不再读取原开发机器的私人脚本或密码文件。

```powershell
$env:REAL_BOT_SSH_HOST = 'your-server'
$env:REAL_BOT_SSH_USER = 'root'
$env:REAL_BOT_SSH_KEY_FILE = 'C:\private\ssh_key'
python tools/operator/control.py menu
```

菜单包含机器人总开关、QQ/微信群消息触发开关、其他私聊空闲触发主动开关。后者关闭时只保留主人QQ私聊，微信主人私聊也属于“其他私聊”；兴趣主动和普通来信回复不受该开关影响。

## 人格

在自己的本地文件夹保存 `首部人格.md` 和 `尾部人格.md`，运行：

```powershell
python tools/operator/upload_persona.py 'C:\private\人格文件'
```

该工具现在只更新主人QQ私聊的 `persona-owner-qq.md` / `persona-owner-qq-tail.md`。全局 `persona.md` / `persona-tail.md` 不被覆盖。上传前验证两份文件，保存旧版并校验装载，保留原有服务启停状态。示例主人与机器人账号需要先适配自己的部署。

## 密钥、隐私与费用

- `更换机器人API Key.py`：读取同目录的本地 `机器人专用API Key.txt`，只轮换机器人专用Key；此文件被Git忽略，仓库不附真实值。
- `私聊隐私状态.py`：管理员操作任意平台私聊的隐私状态。聊天内还支持 `/隐私`、`/隐私 开`、`/隐私 关`、`/隐私 状态`，命令不进入仿真人记忆。
- `导出机器人API用量.py --day yesterday`：读取用量账本，不读取聊天正文或Key。模型步骤、格式修复和工具分别计量；未知费用不能当作免费。

服务端估算单价以 `usage-pricing.json` 为配置快照，需按供应方公告维护；本仓库不保证示例单价一直有效。特殊DSH工具费用是否完整暴露也要独立核对。
