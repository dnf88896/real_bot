"""Admin-only toggle for any private conversation; no bot tool is added."""
import argparse
import shlex
import deploy_cloud


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['enable','disable','status'])
    parser.add_argument('--platform',default='qq')
    parser.add_argument('--account',default='100000002')
    parser.add_argument('--conversation',default='100000001')
    args=parser.parse_args()
    command=['runuser','-u','qq-social','--','/usr/bin/python3',
             '/opt/astrbot-mouth/private_chat_state.py',args.command]
    if args.command!='status':
        command+=['--platform',args.platform,'--account',args.account,'--conversation',args.conversation]
    c=deploy_cloud.connect()
    try:deploy_cloud.run(c,shlex.join(command))
    finally:c.close()


if __name__=='__main__':main()
