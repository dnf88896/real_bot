"""Private local queue RPC; UI stays exclusively in the existing worker."""
import json,os,socketserver
from contextlib import closing
from pathlib import Path
from unified_sender import submit,connect

SOCKET='/run/wechat-ui-broker/queue.sock'

class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(10)
        try:
            raw=self.rfile.readline(65537)
            if len(raw)>65536 or not raw.endswith(b'\n'):raise ValueError('Invalid request size')
            request=json.loads(raw);action=request['action']
            if action=='submit':
                key=submit(request['request']);result={'id':key}
            else:
                with closing(connect()) as db:
                    key=request['id'];row=db.execute('SELECT * FROM jobs WHERE id=?',(key,)).fetchone()
                    if not row:raise ValueError('Unknown job')
                    if action=='cancel':
                        with db:db.execute('UPDATE jobs SET cancel=1 WHERE id=?',(key,))
                    elif action!='status':raise ValueError('Unknown operation')
                    result={k:row[k] for k in ('id','status','phase','result','error')}
                    if result['result']:result['result']=json.loads(result['result'])
            self.wfile.write(json.dumps({'ok':True,**result},ensure_ascii=False).encode()+b'\n')
        except Exception as error:
            self.wfile.write(json.dumps({'ok':False,'error':type(error).__name__+': '+str(error)[:300]}).encode()+b'\n')

if __name__=='__main__':
    os.umask(0o077);Path(SOCKET).unlink(missing_ok=True)
    with socketserver.ThreadingUnixStreamServer(SOCKET,Handler) as server:
        os.chown(SOCKET,0,990);os.chmod(SOCKET,0o660);server.serve_forever()
