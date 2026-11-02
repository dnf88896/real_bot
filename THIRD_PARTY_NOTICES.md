# Third-party notices

- `modules/wechat-native`: modified project snapshot of [netcat-ai/webox](https://github.com/netcat-ai/webox), MIT. Original `LICENSE`, `LICENSES/` and `THIRD_PARTY_NOTICES.md` are retained, including Apache-2.0 wx-cli portions and SQLCipher notices.
- `modules/heybox-bridge`: installed source derived from [AaronZ345/xiaoheihe-cli](https://github.com/AaronZ345/xiaoheihe-cli), baseline commit `954fb4753fb1a879572239f2a54b520a91dc814d`, MIT. Contains the project's deployed extensions; packaging metadata excludes unbundled skill files. See its `LICENSE`.
- `modules/unified-mouth/vendor`: attention/cooldown/concurrency code reused from [Him666233/astrbot_plugin_group_chat_plus](https://github.com/Him666233/astrbot_plugin_group_chat_plus), baseline V1.2.3.hotfix.2; proactive schedule excerpt from [Pancakes-Labs/astrbot_plugin_proactive_chat](https://github.com/Pancakes-Labs/astrbot_plugin_proactive_chat), commit `77a1d408ccd003a9d34ad4f51acfb8fc17bc35f5`. Existing AGPL-3.0 text is retained as `vendor/LICENSE`.
- AstrBot itself is an external framework dependency. Its source and installed environments are not bundled here; use and distribution remain subject to its own license.
- Bilibili Adapter client files are referenced as an external prerequisite, not redistributed, because the referenced upstream repository did not expose a LICENSE at export time.
- Voice/vision models, character voice weights, pictures, stickers and actual browser captures are not redistributed. Their provenance and usage conditions must be handled separately by deployers.

The source export anonymizes account identifiers, server addresses and workstation paths. Runtime behavior changes should be reviewed against the original deployment before enabling real accounts. The repository creator's original code and the integrated project are licensed under GNU AGPL-3.0; see the root LICENSE. Separately identified third-party components retain their original licenses and notices.
