"""Disable built-in status replies in base and every saved account, preserving tokens."""
import json,os,subprocess,time
from pathlib import Path

def main():
    root=Path('/opt/astrbot-mouth')
    backup=root/'backups'/('snowluma-status-hardening-'+time.strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(mode=0o700)
    names=json.loads(subprocess.check_output(['docker','exec','snowluma-lab','python3','-c',
        "import json;from pathlib import Path;p=Path('/app/data/config');print(json.dumps(['onebot.json']+[x.name for x in p.glob('onebot_*.json')]))"],text=True))
    results=[]
    for name in names:
        raw=subprocess.check_output(['docker','exec','snowluma-lab','cat','/app/data/config/'+name])
        saved=backup/name;saved.write_bytes(raw);os.chmod(saved,0o600)
        cfg=json.loads(raw);status=cfg.setdefault('statusCommand',{})
        old=status.get('enabled','inherited');status['enabled']=False
        script="""import sys,json,os
from pathlib import Path
job=json.load(sys.stdin);p=Path('/app/data/config')/job['name'];st=p.stat()
t=p.with_name(p.name+'.hardening-tmp');t.write_text(json.dumps(job['config'],ensure_ascii=False,indent=2))
os.chmod(t,st.st_mode & 0o777);os.chown(t,st.st_uid,st.st_gid);os.replace(t,p)
"""
        subprocess.run(['docker','exec','-i','-u','root','snowluma-lab','python3','-c',script],
                       input=json.dumps({'name':name,'config':cfg},ensure_ascii=False).encode(),check=True)
        results.append({'file':name,'previous':old,'enabled':False})
    host=Path('/opt/snowluma/config/onebot_3654076639.json')
    if host.exists():
        raw=host.read_bytes();saved=backup/'host-onebot_3654076639.json';saved.write_bytes(raw);os.chmod(saved,0o600)
        cfg=json.loads(raw);cfg.setdefault('statusCommand',{})['enabled']=False
        host.write_text(json.dumps(cfg,ensure_ascii=False,indent=2))
    (backup/'manifest.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
    print(json.dumps({'backup':str(backup),'results':results},ensure_ascii=False))

if __name__=='__main__':main()
