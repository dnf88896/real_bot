"""Restrict the public compatibility prefix to its authenticated /v1 API."""
import os,re,shutil,subprocess,time
from pathlib import Path

def main():
    candidates=[]
    loaded=subprocess.run(['nginx','-T'],check=True,capture_output=True,text=True).stdout
    for name in re.findall(r'^# configuration file (.+):$',loaded,re.M):
        p=Path(name)
        if p.is_file() and 'location ^~ /kobato-api/ {' in p.read_text(errors='ignore'):candidates.append(p.resolve())
    candidates=list(set(candidates))
    if len(candidates)!=1:raise ValueError('Ambiguous compatibility location')
    p=candidates[0];old=p.read_text();anchor='location ^~ /kobato-api/ {'
    if old.count(anchor)!=1:raise ValueError('Unexpected location count')
    guard='\n        # Public compatibility endpoint must not expose chat state/history.\n        if ($uri !~ "^/kobato-api/v1/") { return 404; }'
    if guard in old:print('Already restricted');return
    folder=Path('/opt/astrbot-mouth/backups')/('before-legacy-api-route-hardening-'+time.strftime('%Y%m%d-%H%M%S'))
    folder.mkdir(mode=0o700);backup=folder/p.name;shutil.copy2(p,backup);os.chmod(backup,0o600)
    st=p.stat();temp=p.with_name(p.name+'.hardening-tmp')
    temp.write_text(old.replace(anchor,anchor+guard));os.chmod(temp,st.st_mode & 0o777);os.chown(temp,st.st_uid,st.st_gid);os.replace(temp,p)
    try:
        subprocess.run(['nginx','-t'],check=True,capture_output=True)
        subprocess.run(['systemctl','reload','nginx'],check=True)
    except Exception:
        p.write_text(old);subprocess.run(['nginx','-t'],check=True,capture_output=True)
        raise
    print('Restricted public API prefix; backup:',folder)

if __name__=='__main__':main()
