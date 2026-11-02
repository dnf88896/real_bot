"""Upload the desktop's two personality files, preserving service state and rollback."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import deploy_cloud

FILES={'首部人格.md':'persona-owner-qq.md','尾部人格.md':'persona-owner-qq-tail.md'}

def read_files(folder):
    result={}
    for local,remote in FILES.items():
        path=folder/local
        content=path.read_text(encoding='utf-8-sig').strip()
        if not content:raise ValueError(local+'为空，未上传任何文件')
        if '\x00' in content or len(content.encode('utf8'))>200000:
            raise ValueError(local+'含无效字符或超过200KB，未上传任何文件')
        result[remote]=content+'\n'
    return result

REMOTE=r'''
import fcntl,hashlib,json,os,subprocess,time,uuid
from pathlib import Path
root=Path('/opt/astrbot-mouth')
def active():return subprocess.run(['systemctl','is-active','--quiet','astrbot-unified']).returncode==0
def service(action):subprocess.run(['systemctl',action,'astrbot-unified'],check=True,capture_output=True,timeout=180)
def replace(path,body,metadata):
    temp=path.with_name(path.name+'.upload-'+uuid.uuid4().hex)
    try:
        with temp.open('xb') as f:f.write(body);f.flush();os.fsync(f.fileno())
        os.chown(temp,metadata[0],metadata[1]);os.chmod(temp,metadata[2]);os.replace(temp,path)
    finally:temp.unlink(missing_ok=True)
with (root/'.persona-upload.lock').open('a') as lock:
    fcntl.flock(lock,fcntl.LOCK_EX)
    assert set(PAYLOAD)=={'persona-owner-qq.md','persona-owner-qq-tail.md'}
    originals={};bodies={};metadata={}
    for name,text in PAYLOAD.items():
        if not isinstance(text,str) or not text.strip() or '\x00' in text or len(text.encode('utf8'))>200000:
            raise ValueError('Invalid personality content')
        path=root/name;originals[name]=path.read_bytes() if path.exists() else None;bodies[name]=text.encode('utf8')
        st=path.stat() if path.exists() else None
        metadata[name]=(st.st_uid,st.st_gid,st.st_mode & 0o777) if st else (990,990,0o640)
    changed=any(originals[n]!=bodies[n] for n in bodies);was_active=active();backup=None
    if changed:
        backup=root/'operator-backups'/'persona'/str(time.time_ns());backup.mkdir(parents=True,mode=0o700)
        for name,body in originals.items():
            if body is not None:
                p=backup/name;p.write_bytes(body);p.chmod(0o600)
        (backup/'manifest.json').write_text(json.dumps({'existed':{n:b is not None for n,b in originals.items()}}))
        stopped=False
        try:
            if was_active:service('stop');stopped=True
            for name,body in bodies.items():replace(root/name,body,metadata[name])
            # Validate exactly what the running mouth will read, not just file existence.
            import sys
            sys.path.insert(0,str(root))
            from persona_source import expression_persona
            head,tail=expression_persona(root,{'scene':{'platform':'qq','account':'100000002','chat_type':'private','conversation':'100000001'}})
            assert head==PAYLOAD['persona-owner-qq.md'].strip()
            assert tail==PAYLOAD['persona-owner-qq-tail.md'].strip()
            if was_active:
                service('start')
                if not active():raise RuntimeError('Unified service did not start')
                stopped=False
        except Exception:
            for name,body in originals.items():
                if body is None:(root/name).unlink(missing_ok=True)
                else:replace(root/name,body,metadata[name])
            if was_active:service('restart')
            raise
    print(json.dumps({'changed':changed,'backup':str(backup) if backup else None,'service_active':active(),
        'files':{n:{'chars':len(t.strip()),'sha256':hashlib.sha256(t.encode('utf8')).hexdigest()} for n,t in PAYLOAD.items()}},ensure_ascii=False))
'''

def main():
    parser=argparse.ArgumentParser();parser.add_argument('folder',type=Path);args=parser.parse_args()
    payload=read_files(args.folder)
    print('正在上传你的QQ私聊测试人格，其他会话的全局人格不会被覆盖……',flush=True)
    client=deploy_cloud.connect()
    try:
        stdin,out,err=client.exec_command('python3 -',timeout=240)
        stdin.write('import json\nPAYLOAD=json.loads('+repr(json.dumps(payload,ensure_ascii=False))+')\n'+REMOTE)
        stdin.flush();stdin.channel.shutdown_write()
        raw=out.read().decode('utf8');error=err.read().decode('utf8')
        if out.channel.recv_exit_status()!=0:raise RuntimeError(error[-1500:] or '服务器未确认上传成功')
        result=json.loads(raw)
    finally:client.close()
    print('上传成功，仅用于你的QQ私聊，两份文件均已校验。' if result['changed'] else '你的QQ私聊测试人格与服务器一致，无需替换。')
    if result['backup']:print('原版本备份：'+result['backup'])
    print('机器人运行状态：'+('运行中' if result['service_active'] else '关闭（未自动启动）'))

if __name__=='__main__':
    try:main()
    except Exception as exc:print('上传失败，不能视为生效：'+str(exc),file=sys.stderr);sys.exit(1)
