"""Standalone WeChat conversation navigator. Never types, searches or sends.

Backend, OCR and policy are separate so navigation can be tested without UI.
All coordinates are relative to a verified main window. Requires exclusive UI.
"""
import argparse,base64,fcntl,hashlib,io,json,os,selectors,subprocess,time,unicodedata,re
from dataclasses import dataclass,asdict
from pathlib import Path
from PIL import Image

CONTAINER='wechat-webox-desktop-1'
HOME=Path('/opt/wechat-webox/session-selector')

class NavigationError(RuntimeError):pass

@dataclass
class Policy:
    list_left:int=118
    list_right:int=291
    list_top:int=82
    list_bottom_margin:int=8
    row_height:int=65
    name_height:int=28
    scroll_steps:int=1
    top_steps:int=18
    settle_seconds:float=0.35
    max_pages:int=12
    max_attempts:int=3
    deadline_seconds:float=90

def normal(text):
    value=''.join(unicodedata.normalize('NFKC',text).split()).casefold()
    return value

def title_matches(expected,seen,kind):
    if kind=='group':seen=re.sub(r'\s*[（(]\d+[)）]\s*$','',seen)
    return bool(normal(expected)) and normal(expected)==normal(seen)

class OCR:
    def __init__(self):
        self.p=subprocess.Popen(['/opt/astrbot-ocr-venv/bin/python','-u',str(Path(__file__).with_name('ocr_resident.py'))],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
        if self.reply(20).get('ready') is not True:raise NavigationError('OCR not ready')
    def reply(self,timeout):
        with selectors.DefaultSelector() as poll:
            poll.register(self.p.stdout,selectors.EVENT_READ)
            if not poll.select(timeout):raise NavigationError('OCR timed out')
        data=self.p.stdout.readline()
        if not data:raise NavigationError('OCR worker stopped')
        return json.loads(data)
    def read(self,image):
        out=io.BytesIO();image.resize((image.width*2,image.height*2)).save(out,'PNG')
        self.p.stdin.write(json.dumps({'png':base64.b64encode(out.getvalue()).decode()})+'\n');self.p.stdin.flush()
        data=self.reply(12)
        if 'error' in data:raise NavigationError('OCR failed: '+data['error'])
        for row in data['lines']:row['box']=[v/2 for v in row['box']]
        return data
    def close(self):
        self.p.terminate()
        try:self.p.wait(timeout=3)
        except subprocess.TimeoutExpired:self.p.kill();self.p.wait()

# XGetImage reads pixels directly. No VNC browser, keyboard, clipboard or focus.
CAPTURE=r'''
import ctypes as C,ctypes.util,json,sys
class XI(C.Structure):
 _fields_=[('width',C.c_int),('height',C.c_int),('xoffset',C.c_int),('format',C.c_int),('data',C.c_void_p),('byte_order',C.c_int),('bitmap_unit',C.c_int),('bitmap_bit_order',C.c_int),('bitmap_pad',C.c_int),('depth',C.c_int),('bytes_per_line',C.c_int),('bits_per_pixel',C.c_int),('red_mask',C.c_ulong),('green_mask',C.c_ulong),('blue_mask',C.c_ulong)]
x=C.CDLL(ctypes.util.find_library('X11'));x.XOpenDisplay.argtypes=[C.c_char_p];x.XOpenDisplay.restype=C.c_void_p
x.XDefaultRootWindow.argtypes=[C.c_void_p];x.XDefaultRootWindow.restype=C.c_ulong
x.XGetImage.argtypes=[C.c_void_p,C.c_ulong,C.c_int,C.c_int,C.c_uint,C.c_uint,C.c_ulong,C.c_int];x.XGetImage.restype=C.POINTER(XI)
x.XDestroyImage.argtypes=[C.POINTER(XI)];x.XCloseDisplay.argtypes=[C.c_void_p]
d=x.XOpenDisplay(b':1');assert d
left,top,w,h=map(int,sys.argv[1:]);assert 0<w<=1600 and 0<h<=1200
p=x.XGetImage(d,x.XDefaultRootWindow(d),left,top,w,h,0xffffffff,2);assert p
try:
 a=p.contents;assert a.bits_per_pixel==32 and a.byte_order==0 and a.red_mask==0xff0000 and a.green_mask==0xff00 and a.blue_mask==0xff
 sys.stdout.buffer.write((json.dumps({'width':a.width,'height':a.height,'stride':a.bytes_per_line})+'\n').encode());sys.stdout.buffer.write(C.string_at(a.data,a.bytes_per_line*a.height))
finally:x.XDestroyImage(p);x.XCloseDisplay(d)
'''

class Desktop:
    def __init__(self,policy):self.policy=policy
    def run(self,args,timeout=6):
        r=subprocess.run(['docker','exec','-e','DISPLAY=:1',CONTAINER,*args],capture_output=True,timeout=timeout)
        if r.returncode:raise NavigationError('Desktop command failed')
        return r.stdout
    def geometry(self):
        script='for id in $(xdotool search --onlyvisible --class wechat 2>/dev/null); do printf "NAME="; xdotool getwindowname "$id"; xdotool getwindowgeometry --shell "$id"; echo END; done'
        rows=self.run(['bash','-lc',script]).decode().split('END');windows=[]
        for row in rows:
            fields=dict(line.split('=',1) for line in row.strip().splitlines() if '=' in line)
            if 'WINDOW' in fields:windows.append(fields)
        if len(windows)!=1:raise NavigationError('Additional WeChat window/modal present; no action taken')
        w=windows[0]
        if w['NAME'] not in ('微信','WeChat') or not 800<=int(w['WIDTH'])<=900 or not 600<=int(w['HEIGHT'])<=700:raise NavigationError('Unexpected main window')
        return tuple(int(w[k]) for k in ('WINDOW','X','Y','WIDTH','HEIGHT'))
    def frame(self):
        g=self.geometry();_,x,y,w,h=g;data=self.run(['python3','-c',CAPTURE,str(x),str(y),str(w),str(h)])
        header,pixels=data.split(b'\n',1);meta=json.loads(header)
        if self.geometry()!=g:raise NavigationError('Window moved during capture')
        return Image.frombytes('RGB',(w,h),pixels,'raw','BGRX',meta['stride']),g
    def action(self,g,relative_x,relative_y,button,count=1):
        if button not in (1,4,5) or type(count) is not int or not 1<=count<=40:raise NavigationError('Unsupported mouse action')
        if self.geometry()!=g:raise NavigationError('Window changed before action')
        _,left,top,w,h=g
        if not 65<=relative_x<300 or not 82<=relative_y<h-8:raise NavigationError('Action outside conversation list')
        self.run(['xdotool','mousemove',str(left+int(relative_x)),str(top+int(relative_y)),'click','--repeat',str(count),'--delay','60',str(button)])
        time.sleep(self.policy.settle_seconds)
    def scroll(self,g,up=False,top=False):
        self.action(g,180,g[4]-70,4 if up else 5,self.policy.top_steps if top else self.policy.scroll_steps)
    def click(self,g,y):self.action(g,180,y,1)

class Navigator:
    def __init__(self,desktop,ocr,policy,trace=None):
        self.ui=desktop;self.ocr=ocr;self.p=policy;self.trace=trace or (lambda event:None);self.started=time.monotonic()
    def log(self,event,**data):self.trace({'event':event,'elapsed':round(time.monotonic()-self.started,3),**data})
    def check_time(self):
        if time.monotonic()-self.started>self.p.deadline_seconds:raise NavigationError('Navigation deadline reached')
    def list_crop(self,im):return im.crop((self.p.list_left,self.p.list_top,self.p.list_right,im.height-self.p.list_bottom_margin))
    def names(self,im):
        data=self.ocr.read(self.list_crop(im));rows=[]
        # Timestamp baselines move with rows while scrolling. They distinguish
        # a name from its message preview without assuming a fixed scroll phase.
        anchors=[(r['box'][1]+r['box'][3])/2 for r in data['lines'] if r['box'][0]>115]
        for row in data['lines']:
            x1,y1,x2,y2=row['box'];cy=(y1+y2)/2
            if x1>25:continue
            if anchors:
                # Older chats may have no timestamp at all. Infer the row-grid
                # phase from dated rows and apply it to undated rows as well.
                delta=(cy-anchors[0])%self.p.row_height
                if min(delta,self.p.row_height-delta)>=7:continue
            elif cy%self.p.row_height>=self.p.name_height:continue
            rows.append({**row,'y':self.p.list_top+cy})
        self.log('list_read',ocr_seconds=round(data['seconds'],3),names=[r['text'] for r in rows]);return rows
    def title(self,im):
        data=self.ocr.read(im.crop((300,35,im.width-180,76)))
        return ''.join(row['text'] for row in sorted(data['lines'],key=lambda r:r['box'][0]))
    def candidates(self,im,rows,target):
        exact=[r for r in rows if normal(r['text'])==normal(target)]
        if exact:
            if len(exact)>1:raise NavigationError('Duplicate visible names; use a unique remark')
            return exact
        expected=normal(target)
        # A near match permits navigation only; strict title verification still gates input.
        near=[r for r in rows if len(expected)>=4 and len(normal(r['text']))==len(expected)
              and sum(a!=b for a,b in zip(normal(r['text']),expected))==1]
        if not near:return []
        if len(near)!=1:
            self.log('ambiguous_candidates',target=target,names=[r['text'] for r in near]);return []
        row=near[0];x1,y1,x2,y2=row['box']
        crop=im.crop((max(0,self.p.list_left+int(x1)-3),max(0,self.p.list_top+int(y1)-3),
                      min(im.width,self.p.list_left+int(x2)+3),min(im.height,self.p.list_top+int(y2)+3)))
        data=self.ocr.read(crop.resize((crop.width*2,crop.height*2)))
        seen=''.join(r['text'] for r in sorted(data['lines'],key=lambda r:r['box'][0]))
        self.log('candidate_recheck',target=target,seen=row['text'],enlarged=seen)
        # If OCR still disagrees, clicking the unique candidate only opens a read-only
        # title check. It never permits typing or sending on fuzzy evidence.
        return [row]

    def verify(self,target,kind):
        for _ in range(3):
            self.check_time();im,g=self.ui.frame();seen=self.title(im);self.log('title_read',seen=seen)
            if title_matches(target,seen,kind):return True
            crop=im.crop((300,35,im.width-180,76))
            data=self.ocr.read(crop.resize((crop.width*2,crop.height*2)))
            enlarged=''.join(r['text'] for r in sorted(data['lines'],key=lambda r:r['box'][0]))
            self.log('title_recheck',seen=enlarged)
            if title_matches(target,enlarged,kind):return True
            time.sleep(0.25)
        return False
    def select(self,target,kind='private'):
        if not target.strip():raise ValueError('Target name required')
        for attempt in range(self.p.max_attempts):
            self.check_time();im,g=self.ui.frame();self.ui.scroll(g,up=True,top=True);previous=None
            for page in range(self.p.max_pages):
                self.check_time();im,g=self.ui.frame();rows=self.names(im)
                matches=self.candidates(im,rows,target)
                if len(matches)>1:raise NavigationError('Duplicate visible names; use a unique remark')
                if matches:
                    row=matches[0];top=self.p.list_top+int(row['box'][1])-3;bottom=self.p.list_top+int(row['box'][3])+3
                    fresh,g2=self.ui.frame()
                    if g2!=g or fresh.crop((self.p.list_left,top,self.p.list_right,bottom)).tobytes()!=im.crop((self.p.list_left,top,self.p.list_right,bottom)).tobytes():
                        self.log('row_changed_before_click');break
                    self.ui.click(g,row['y']);self.log('row_clicked',target=target,attempt=attempt+1,page=page)
                    if self.verify(target,kind):
                        _,g=self.ui.frame();self.ui.scroll(g,up=True,top=True)
                        if not self.verify(target,kind):raise NavigationError('Title changed while returning list to top')
                        result={'selected':True,'target':target,'kind':kind,'attempts':attempt+1,'pages':page+1,'seconds':round(time.monotonic()-self.started,3)}
                        self.log('selected',**result);return result
                    self.log('title_mismatch_retry');break
                fingerprint=hashlib.sha256(self.list_crop(im).tobytes()).hexdigest()
                if fingerprint==previous:self.log('bottom_reached');break
                previous=fingerprint;self.ui.scroll(g)
        raise NavigationError('Target not found or title verification failed within bounded attempts')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--target');parser.add_argument('--kind',choices=['private','group'],default='private');parser.add_argument('--inspect',action='store_true');parser.add_argument('--config');args=parser.parse_args()
    os.umask(0o077);HOME.mkdir(parents=True,exist_ok=True)
    config=Path(args.config) if args.config else HOME/'policy.json'
    p=Policy(**(json.loads(config.read_text()) if config.exists() else {}))
    if not args.inspect:
        # This prototype has its own lock. Until integrated into the native
        # sender's transaction lock, it must not run alongside that sender.
        status=subprocess.run(['systemctl','is-active','wechat-native-api'],capture_output=True,text=True,timeout=3)
        if status.stdout.strip()!='inactive':raise SystemExit('Stop native WeChat automation before running the standalone navigator')
    with (HOME/'ui.lock').open('w') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise SystemExit('Another selector is using the desktop')
        started=time.monotonic();ocr=OCR();init=time.monotonic()-started;ui=Desktop(p)
        folder=HOME/'runs'/str(time.time_ns());folder.mkdir(parents=True)
        with (folder/'trace.jsonl').open('w') as trace:
            nav=Navigator(ui,ocr,p,lambda event:(trace.write(json.dumps(event,ensure_ascii=False)+'\n'),trace.flush()))
            try:
                if args.inspect:
                    t=time.monotonic();im,g=ui.frame();capture=time.monotonic()-t
                    result={'inspection_only':True,'names':nav.names(im),'title':nav.title(im),'capture_seconds':capture,'ocr_initialization_seconds':init};im.save(folder/'inspect.png')
                else:result=nav.select(args.target or '',args.kind)
                (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False))
            except Exception as exc:
                nav.log('failed',error=str(exc))
                try:ui.frame()[0].save(folder/'failure.png')
                except Exception:pass
                raise
            finally:ocr.close()

if __name__=='__main__':main()
