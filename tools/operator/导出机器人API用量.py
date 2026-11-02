"""Export a Beijing-day or rolling-window report; no secret/body is downloaded."""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
import deploy_cloud


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--day',default='today',help='today/yesterday/YYYY-MM-DD')
    parser.add_argument('--hours',type=float)
    args=parser.parse_args()
    if args.day not in ('today','yesterday'):
        datetime.strptime(args.day,'%Y-%m-%d')
        args.day=datetime.strptime(args.day,'%Y-%m-%d').strftime('%Y-%m-%d')
    if args.hours is not None and not 0 < args.hours <= 8760:
        parser.error('--hours must be positive and at most 8760')
    command='/usr/bin/python3 /opt/astrbot-mouth/usage_report.py --json --day '+args.day
    if args.hours is not None:command+=' --hours '+str(args.hours)
    c=deploy_cloud.connect()
    try:
        _,out,err=c.exec_command(command,timeout=30)
        raw=out.read().decode('utf8')
        if out.channel.recv_exit_status()!=0:
            raise RuntimeError('Cannot read server ledger')
        result=json.loads(raw)
    finally:c.close()
    root=Path(__file__).resolve().parent
    sys.path.insert(0,str(root.parents[1]/'modules'/'unified-mouth'))
    from usage_report import markdown
    dest=root/'research/api-usage'
    dest.mkdir(parents=True,exist_ok=True)
    label='rolling-'+str(args.hours)+'h' if args.hours is not None else args.day
    path=dest/(datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+label)
    path.with_suffix('.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    path.with_suffix('.md').write_text(markdown(result),encoding='utf-8')
    print(path.with_suffix('.md'))


if __name__=='__main__':main()
