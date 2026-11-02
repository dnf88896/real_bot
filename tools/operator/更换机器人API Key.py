"""Read a local secret file and rotate only the robot's server credentials."""
import secrets
from pathlib import Path
import deploy_cloud


def main():
    path = Path(__file__).resolve().with_name('机器人专用API Key.txt')
    value = path.read_text('utf-8-sig').strip()
    if not value.startswith('sk-') or not 16 <= len(value) <= 512 or any(c.isspace() for c in value):
        raise ValueError('请在“机器人专用API Key.txt”中仅填写新 Key。')
    client = deploy_cloud.connect()
    remote = '/opt/astrbot-mouth/.new-api-key-' + secrets.token_hex(12)
    sftp = client.open_sftp()
    try:
        deploy_cloud.upload(sftp, remote, value, 0o600)
        deploy_cloud.run(client, '/usr/bin/python3 /opt/astrbot-mouth/rotate_bot_key.py --key-file ' + remote)
        print('机器人专用 Key 已更新，口、搜索、后台整理和特殊探索将使用同一 Key。')
    finally:
        try:
            sftp.remove(remote)
        except OSError:
            pass
        sftp.close()
        client.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('更新未完成（' + type(exc).__name__ + '）。请检查 Key 文件、服务器连接或服务状态；程序不会显示 Key。')
        raise SystemExit(1) from None
