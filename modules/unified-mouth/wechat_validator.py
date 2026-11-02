"""Private host-side verifier. No model calls and no mouse/keyboard operations.

Called by the transport while it holds its UI transaction lock. Only this root
service can capture the dedicated container; the mouth needs no Docker access.
"""
import hmac
import json
import os
import tempfile
import re
import sqlite3
import subprocess
import time
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import wechat_title

ROOT=Path('/opt/astrbot-mouth')
TOKEN=Path('/opt/wechat-webox/state/weagent/verify-token')


def source_message(room, mid, root=ROOT):
    from wechat_bridge import lookup_message
    cfg=json.loads((Path(root)/'wechat-config.json').read_text())
    message=lookup_message(cfg['account_id'],room,mid,root)
    return (message['text'],message['sender']) if message else ('',None)


def verify(body, root=ROOT):
    deadline=time.monotonic()+29
    cfg=json.loads((Path(root)/'wechat-config.json').read_text())
    room=body.get('roomid')
    if not cfg.get('enabled') or not cfg.get('send_enabled'):raise ValueError('WeChat sending disabled')
    draft=body.get('draft') is True
    if draft:
        if room not in cfg.get('draft_test_groups',[]):raise ValueError('Draft group not authorized')
        with closing(sqlite3.connect(Path(root)/'wechat.sqlite3',timeout=5)) as db:
            count=db.execute('SELECT count(*) FROM directory WHERE account=? AND kind=? AND lower(name)=lower(?)',(cfg['account_id'],'private',body.get('name',''))).fetchone()[0]
        if count!=1:raise ValueError('Draft mention name unknown or ambiguous')
    elif cfg.get('preview_only') and room!=cfg.get('preview_recipient'):raise ValueError('Owner preview required')
    with closing(sqlite3.connect(Path(root)/'wechat.sqlite3',timeout=5)) as db:
        row=db.execute('SELECT name,kind FROM directory WHERE account=? AND id=?',(cfg['account_id'],room)).fetchone()
        if not row or not row[0]:raise ValueError('Unknown recipient title')
        names=db.execute('SELECT id,name FROM directory WHERE account=?',(cfg['account_id'],)).fetchall()
    if sum(wechat_title.normal(name)==wechat_title.normal(row[0]) for _,name in names)!=1:
        raise ValueError('Recipient title ambiguous')
    seen=wechat_title.title(root,deadline=deadline)
    if row[1]=='group':seen=re.sub(r'\(\d+\)$','',wechat_title.normal(seen))
    if not wechat_title.matches(row[0],seen):raise ValueError('Recipient title mismatch')
    result={'ok':True,'roomid':room,'quote_point':None}
    mid=body.get('reply_to')
    if mid and cfg.get('quote_check'):
        text,sender=source_message(room,str(mid),root)
        if text and sender:
            import wechat_quote
            point=wechat_quote.point(root,expected=text,side='right' if sender==cfg['account_id'] else 'left',deadline=deadline)
            if point:result['quote_point']={'x':point['x'],'y':point['y']}
    return result


def quote_menu(body, root=ROOT, picker=False):
    """Find the actual menu label, never infer an item from its row number."""
    cfg=json.loads((Path(root)/'wechat-config.json').read_text())
    if picker:
        if body.get('roomid') not in cfg.get('draft_test_groups',[]) and cfg.get('preview_only'):raise ValueError('Picker group not authorized')
    else:
        if not cfg.get('send_enabled') or not cfg.get('quote_check'):raise ValueError('Quote disabled')
        if cfg.get('preview_only') and body.get('roomid')!=cfg.get('preview_recipient'):raise ValueError('Owner required')
    output=subprocess.run(['docker','exec',wechat_title.CONTAINER,'bash','-lc',
        'export DISPLAY=:1; for id in $(xdotool search --onlyvisible --class wechat); do '
        'xdotool getwindowgeometry --shell "$id"; echo END; done'],capture_output=True,text=True,timeout=4)
    menus=[]
    for block in output.stdout.split('END'):
        fields=dict(line.split('=',1) for line in block.splitlines() if '=' in line)
        if not fields.get('WIDTH'):continue
        w,h=int(fields['WIDTH']),int(fields['HEIGHT'])
        if (120<=w<=260 and (30 if picker else 180)<=h<=800):menus.append(tuple(int(fields[k]) for k in ('X','Y','WIDTH','HEIGHT')))
    if len(menus)!=1:raise ValueError('Quote menu not unique')
    x,y,w,h=menus[0];image=wechat_title._frame(timeout=8)
    if x<0 or y<0 or x+w>image.width or y+h>image.height:raise ValueError('Invalid menu bounds')
    with tempfile.TemporaryDirectory(prefix='wechat-menu-',dir=Path(root)/'incoming-images') as folder:
        path=Path(folder)/'menu.png';image.crop((x,y,x+w,y+h)).save(path)
        result=subprocess.run([wechat_title.OCR_PYTHON,str(Path(root)/'wechat_ocr_boxes.py'),str(path),'0.5'],
            capture_output=True,text=True,timeout=10)
        if result.returncode:raise ValueError('Menu OCR failed')
        wanted=wechat_title.normal(body.get('name','')) if picker else '引用'
        rows=[r for r in json.loads(result.stdout).get('lines',[]) if wechat_title.normal(r['text'])==wanted]
    if len(rows)!=1:raise ValueError('Quote menu item not unique')
    row=rows[0]
    return {'ok':True,'x':x+(row['x1']+row['x2'])//2,'y':y+(row['y1']+row['y2'])//2}


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup();self.connection.settimeout(35)
    def log_message(self,*args):pass
    def do_POST(self):
        if self.path not in ('/verify','/quote-menu','/mention-picker'):self.send_error(404);return
        if not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+TOKEN.read_text().strip()):
            self.send_error(401);return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=4096:raise ValueError('Invalid request length')
            body=json.loads(self.rfile.read(size))
            result=verify(body) if self.path=='/verify' else quote_menu(body,picker=self.path=='/mention-picker')
            status=200
        except Exception as exc:
            print('WeChat verifier rejected: '+type(exc).__name__+': '+str(exc),flush=True)
            result={'ok':False,'error':'Conversation verification unavailable or rejected'};status=422
        data=json.dumps(result).encode()
        self.send_response(status);self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)


if __name__=='__main__':
    os.umask(0o077)
    inspect=json.loads(subprocess.check_output(['docker','inspect',wechat_title.CONTAINER]))[0]
    networks=list(inspect['NetworkSettings']['Networks'].values())
    if len(networks)!=1 or not networks[0].get('Gateway'):raise RuntimeError('Private Docker bridge unavailable')
    # Never listen on a public interface or publish this port through Docker.
    server=ThreadingHTTPServer((networks[0]['Gateway'],38084),Handler)
    server.timeout=35
    server.serve_forever()
