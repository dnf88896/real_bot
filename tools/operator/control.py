"""Operator controls for the new pilot only; the old switch is unchanged."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import deploy_cloud

UNITS = ["astrbot-transport", "astrbot-unified", "astrbot-relay", "astrbot-output-memory"]


def group_control(client, action):
    remote='/opt/astrbot-mouth/group_input_control.py'
    with client.open_sftp() as sftp:
        deploy_cloud.upload(sftp,remote,Path(__file__).with_name('group_input_control.py').read_bytes(),0o600)
    _,stdout,stderr=client.exec_command('/opt/astrbot-src/.venv/bin/python '+remote+' '+action,timeout=120)
    raw=stdout.read().decode('utf8');error=stderr.read().decode('utf8')
    if stdout.channel.recv_exit_status()!=0:raise RuntimeError(error[-1000:] or '群消息开关执行失败')
    import json
    state=json.loads(raw)
    print('群消息触发：QQ '+('开启' if state['qq'] else '关闭')+' / 微信 '+('开启' if state['wechat'] else '关闭'))
    if action!='status':
        print('包含普通群消息、@和必回规则；私聊及记忆记录不变。')
        if not state.get('service_was_active'):print('机器人原本未运行，仅保存开关设置。')


def private_idle_control(client,action):
    remote='/opt/astrbot-mouth/private_idle_control.py'
    with client.open_sftp() as sftp:
        deploy_cloud.upload(sftp,remote,Path(__file__).with_name('private_idle_control.py').read_bytes(),0o600)
    _,stdout,stderr=client.exec_command('/opt/astrbot-src/.venv/bin/python '+remote+' '+action,timeout=180)
    raw=stdout.read().decode('utf8');error=stderr.read().decode('utf8')
    if stdout.channel.recv_exit_status()!=0:raise RuntimeError(error[-1000:] or '其他私聊触发主动开关执行失败')
    import json
    state=json.loads(raw)
    print('其他私聊触发主动：'+('开启' if state['other_private_idle_enabled'] else '关闭'))
    print('仅保留你的QQ私聊不受此开关限制；你的微信也属于其他私聊。')
    if action!='status':
        print('普通私聊来信回复、记忆、兴趣主动和显式自唤醒规则不变。')
        if not state.get('service_was_active'):print('机器人原本未运行，仅保存开关设置。')


def operate(action):
    client = deploy_cloud.connect()
    try:
        services = " ".join(UNITS)
        if action.startswith('groups-'):
            group_control(client,action.removeprefix('groups-'))
            return
        if action.startswith('private-idle-'):
            private_idle_control(client,action.removeprefix('private-idle-'))
            return
        if action == "off":
            deploy_cloud.run(client, "systemctl stop " + services)
            deploy_cloud.run(client, "systemctl disable " + services)
        elif action == "on":
            deploy_cloud.run(client, "systemctl enable --now " + services)
        deploy_cloud.run(client, "systemctl show " + services + " -p Id -p ActiveState -p UnitFileState --no-pager")
        group_control(client,'status')
        private_idle_control(client,'status')
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["on", "off", "status", "menu", "groups-on", "groups-off", "groups-status", "private-idle-on", "private-idle-off", "private-idle-status"], default="menu", nargs="?")
    action = parser.parse_args().action
    if action != "menu":
        operate(action)
        return
    while True:
        print("\nAstrBot 控制菜单\n1. 关闭机器人并禁用自启\n2. 开启机器人\n3. 查看机器人和各开关状态\n4. 开启QQ＋微信群消息触发\n5. 关闭QQ＋微信群消息触发（含@和必回）\n6. 只查看群消息开关\n7. 开启其他私聊触发主动\n8. 关闭其他私聊触发主动（仅保留你的QQ私聊）\n9. 只查看私聊触发主动开关\n0. 退出")
        choice = input("请输入数字：").strip()
        if choice == "0":
            return
        if choice in ("1", "2", "3", "4", "5", "6", "7", "8", "9"):
            try:
                operate({"1": "off", "2": "on", "3": "status", "4":"groups-on", "5":"groups-off", "6":"groups-status", "7":"private-idle-on", "8":"private-idle-off", "9":"private-idle-status"}[choice])
            except Exception as exc:
                print("操作未成功，请检查网络后重试：" + str(exc))


if __name__ == "__main__":
    main()
