# 小黑盒桥接

这里保留项目实际安装的 `xiaoheihe-cli` 0.1.0 源码，包括图文发帖、评论、通知、浏览器/会话和本地REST桥接相关扩展。上游地址、提交和MIT许可证见根目录第三方声明。

```bash
python -m pip install ./modules/heybox-bridge
xiaoheihe-cli api --port 8010
```

首次使用需自行登录；不要提交Cookie、会话JSON或浏览器目录。统一口通过本机REST访问，不把登录凭据交给模型。本仓库没有携带任何可用的用户会话。
