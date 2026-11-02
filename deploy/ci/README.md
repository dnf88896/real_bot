本目录保存可选GitHub Actions检查模板。仓库发布不申请额外workflow权限；需要自动CI时，由仓库管理员把source-check.yml放到.github/workflows/。本地等价命令为python tools/check_public_tree.py，检查不会调用模型或平台。
