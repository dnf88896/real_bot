"""Restore the previous prompts/code, retaining all messages received since backup."""
import argparse,os,pwd,shutil,subprocess,time
from pathlib import Path
root=Path('/opt/astrbot-mouth');plugin=Path('/opt/astrbot-runtime/data/plugins/astrbot_plugin_unified_mouth')
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--backup',default=None);parser.add_argument('--apply',action='store_true')
args=parser.parse_args();backup=Path(args.backup or (root/'context-revision-backup.txt').read_text().strip()).resolve()
if not (backup/'manifest.json').is_file():raise ValueError('Missing backup manifest')
names=['persona.md','persona_source.py','submission_tool.py','main.py','bilibili_tools.py','bilibili_notifications.py','relay.py','memory_engine.py']
print('BACKUP',backup);print('RESTORE',', '.join(names));print('Memory and conversation databases will remain unchanged.')
if not args.apply:print('Dry run only. Add --apply to restore.');raise SystemExit(0)
units=['astrbot-unified','astrbot-relay'];active={u:subprocess.run(['systemctl','is-active','--quiet',u]).returncode==0 for u in units}
user=pwd.getpwnam('qq-social');current=root/'backups'/time.strftime('before-context-rollback-%Y%m%d-%H%M%S');current.mkdir(mode=0o700)
for u in units:
    if active[u]:subprocess.run(['systemctl','stop',u],check=True)
try:
    for label,folder,source in [('shared',root,backup/'shared'),('plugin',plugin,backup/'plugin')]:
        (current/label).mkdir()
        for name in names:
            target=folder/name
            if target.exists():shutil.copy2(target,current/label/name)
            if (source/name).exists():
                p=folder/(name+'.rollback-new');shutil.copyfile(source/name,p);os.chmod(p,0o640);os.chown(p,user.pw_uid,user.pw_gid);os.replace(p,target)
            elif target.exists():target.unlink()
finally:
    for u in reversed(units):
        if active[u]:subprocess.run(['systemctl','start',u],check=True)
print('Previous context restored. Pre-rollback code saved at',current)
