"""Export one exact voice record from a read-only snapshot, without UI or ASR."""
import argparse,contextlib,hashlib,json,os,struct,tempfile
from pathlib import Path
from voice_inventory import snapshot

DESTINATION=Path('/opt/astrbot-mouth/incoming-wechat-voice')

def silk_packets(data):
    header=b'\x02#!SILK_V3'
    if not data.startswith(header):raise ValueError('Expected Tencent SILK V3 header')
    offset=len(header);count=0
    while offset<len(data):
        if offset+2>len(data):raise ValueError('Truncated SILK packet length')
        size=struct.unpack_from('<h',data,offset)[0];offset+=2
        if size==-1 and offset==len(data):break
        if not 0<size<=4096 or offset+size>len(data):raise ValueError('Invalid/truncated SILK packet')
        offset+=size;count+=1
    if not count:raise ValueError('No SILK packets')
    return count

def extract(message_id):
    message_id=int(message_id)
    if message_id<=0:raise ValueError('Positive message ID required')
    roots=list(Path('/opt/wechat-webox/state/home/xwechat_files').glob('*/db_storage'))
    if len(roots)!=1:raise RuntimeError('Dedicated account not found')
    with tempfile.TemporaryDirectory(prefix='wechat-voice-export-') as temp:
        folder=Path(temp)
        for source in sorted((roots[0]/'message').glob('media_*.db')):
            with contextlib.closing(snapshot('message/'+source.name,folder)) as db:
                if not db.execute("SELECT 1 FROM sqlite_master WHERE name='VoiceInfo'").fetchone():continue
                row=db.execute('SELECT v.svr_id,v.local_id,v.create_time,v.voice_data,v.data_index,n.user_name FROM VoiceInfo v LEFT JOIN Name2Id n ON n.rowid=v.chat_name_id WHERE v.svr_id=?',(message_id,)).fetchone()
                if not row:continue
                mid,local,stamp,blob,index,room=row
                if not isinstance(blob,bytes) or not 1<=len(blob)<=16*1024*1024:raise RuntimeError('Voice stored externally or missing; KVDB reader required')
                packets=silk_packets(blob)
                DESTINATION.mkdir(mode=0o700,parents=True,exist_ok=True);os.chown(DESTINATION,990,990)
                target=DESTINATION/(str(mid)+'.silk');temporary=target.with_suffix('.silk.part')
                temporary.write_bytes(blob);temporary.chmod(0o600);os.chown(temporary,990,990);temporary.replace(target)
                info={'platform':'wechat','chat_type':'group' if room and room.endswith('@chatroom') else 'private','conversation_id':room,'message_id':str(mid),'local_id':local,'created_at':stamp,'path':str(target),'format':'Tencent SILK V3','bytes':len(blob),'packets':packets,'sha256':hashlib.sha256(blob).hexdigest(),'source_database':source.name,'decoded':False,'transcribed':False}
                meta=target.with_suffix('.json');meta.write_text(json.dumps(info,ensure_ascii=False,indent=2));meta.chmod(0o600);os.chown(meta,990,990)
                return info
    raise LookupError('Voice message not found')

if __name__=='__main__':
    os.umask(0o077);parser=argparse.ArgumentParser();parser.add_argument('--message-id',type=int,required=True);args=parser.parse_args();print(json.dumps(extract(args.message_id),ensure_ascii=False))
