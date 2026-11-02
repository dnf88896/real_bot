"""Read-only private database snapshots; never exposes encryption material."""
import contextlib,io,json,os,re,shutil,sqlite3,struct,sys,tempfile
from pathlib import Path
sys.path.insert(0,'/opt/wechat-webox/key-tool')
import wcdb_key_tool as crypto
import resource
resource.setrlimit(resource.RLIMIT_CORE,(0,0))
from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes

def aes_decrypt(key,iv,data):
    decryptor=Cipher(algorithms.AES(key),modes.CBC(iv)).decryptor()
    return decryptor.update(data)+decryptor.finalize()

# Avoid the old helper's untyped ctypes OpenSSL calls on 64-bit Python.
crypto.aes_cbc_decrypt=aes_decrypt

def snapshot(relative,folder):
    profile=json.loads(Path('/opt/wechat-webox/state/weagent/wechat.key').read_text())
    # Prefer the actual fixed state mount over paths meaningful only in container.
    roots=list(Path('/opt/wechat-webox/state/home/xwechat_files').glob('*/db_storage'))
    if len(roots)!=1:raise RuntimeError('Expected one bot account')
    root=roots[0];source=root/relative
    keys=profile['keys']
    if relative not in keys:
        verified=json.loads(Path('/opt/wechat-webox/state/weagent/runtime-verified-keys.json').read_text())
        if verified.get('db_dir')!=profile['dbDir']:raise RuntimeError('Verified database account differs')
        keys=verified['keys']
    key=keys[relative]
    if key.lower().startswith("x'"):key=key[2:-1]
    key=bytes.fromhex(key[:64]);target=folder/Path(relative).name
    with contextlib.redirect_stdout(io.StringIO()):
        if not crypto._decrypt_database(str(source),str(target),key):raise RuntimeError('Snapshot key validation failed')
    wal=Path(str(source)+'-wal')
    if wal.exists():
        data=wal.read_bytes()
        if len(data)>=32:
            frames=[];committed=0;size=0
            for offset in range(32,len(data)-24-4096+1,24+4096):
                head=data[offset:offset+24]
                if head[8:16]!=data[16:24]:break
                page,dbsize=struct.unpack('>II',head[:8])
                if not 1<=page<=100000:break
                frames.append((page,data[offset+24:offset+24+4096]))
                if dbsize:committed=len(frames);size=dbsize
            if committed:
                with target.open('r+b') as output:
                    for page,encrypted in frames[:committed]:output.seek((page-1)*4096);output.write(crypto._decrypt_page(key,encrypted,page))
                    output.truncate(size*4096)
    return sqlite3.connect(target.resolve().as_uri()+'?mode=ro',uri=True)

def quote(name):return '"'+name.replace('"','""')+'"'

def main():
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix='wechat-voice-read-') as temp:
        folder=Path(temp);result={}
        with contextlib.closing(snapshot('message/message_resource.db',folder)) as db:
            tables=db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall();result['resource_tables']={}
            for name, in tables:
                cols=db.execute('PRAGMA table_info('+quote(name)+')').fetchall()
                result['resource_tables'][name]=[(r[1],r[2]) for r in cols]
                if re.search('voice|audio|media',name,re.I):
                    result[name+'_count']=db.execute('SELECT count(*) FROM '+quote(name)).fetchone()[0]
        with contextlib.closing(snapshot('message/message_0.db',folder)) as db:
            voices=[];tables=db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'").fetchall()
            for name, in tables:
                cols=[r[1] for r in db.execute('PRAGMA table_info('+quote(name)+')')]
                if 'local_type' not in cols:continue
                count=db.execute('SELECT count(*) FROM '+quote(name)+' WHERE (local_type & 4294967295)=34').fetchone()[0]
                if count:
                    rows=db.execute('SELECT server_id,create_time,length(message_content) FROM '+quote(name)+' WHERE (local_type & 4294967295)=34 ORDER BY create_time DESC LIMIT 3').fetchall()
                    voices.append({'table':name,'count':count,'latest_id_time_content_bytes':rows})
            result['voice_messages']=voices
        print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
