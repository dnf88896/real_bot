"""Portable SSH transport. Reads only explicit environment configuration."""
import os
import paramiko

def connect():
    host=os.environ.get('REAL_BOT_SSH_HOST','').strip()
    if not host:raise RuntimeError('Set REAL_BOT_SSH_HOST before using operator tools')
    client=paramiko.SSHClient();client.load_system_host_keys()
    # Register the host key yourself; never silently trust a new remote host.
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    options={'hostname':host,'username':os.environ.get('REAL_BOT_SSH_USER','root'),
             'port':int(os.environ.get('REAL_BOT_SSH_PORT','22')),'timeout':20}
    key=os.environ.get('REAL_BOT_SSH_KEY_FILE')
    password=os.environ.get('REAL_BOT_SSH_PASSWORD')
    if key:options['key_filename']=key
    if password:options['password']=password
    client.connect(**options)
    return client

def upload(sftp,path,content,mode=0o644):
    with sftp.open(path,'wb') as stream:
        sftp.chmod(path,mode)
        stream.write(content.encode('utf8') if isinstance(content,str) else content)

def run(client,cmd):
    _,stdout,stderr=client.exec_command(cmd,timeout=180)
    output=stdout.read().decode('utf8','replace');error=stderr.read().decode('utf8','replace')
    if stdout.channel.recv_exit_status():raise RuntimeError(error.strip() or 'Remote command failed')
    print(output,end='')
