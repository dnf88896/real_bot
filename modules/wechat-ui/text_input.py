"""Independent text input: requires selected conversation; writes verified drafts only."""
import argparse,fcntl,json,os,subprocess,time,uuid
from pathlib import Path
from session_selector import HOME,CONTAINER,Policy,Desktop,OCR,Navigator,NavigationError

class Clipboard:
    def __init__(self):self.process=None
    def set(self,text):
        self.close()
        self.process=subprocess.Popen(['docker','exec','-i','-e','DISPLAY=:1',CONTAINER,'xclip','-selection','clipboard','-target','UTF8_STRING','-loops','0','-quiet'],stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        self.process.stdin.write(text.encode('utf8'));self.process.stdin.close();time.sleep(0.15)
        if self.process.poll() is not None:raise NavigationError('Clipboard provider stopped')
    def get(self):
        r=subprocess.run(['docker','exec','-e','DISPLAY=:1',CONTAINER,'timeout','3','xclip','-selection','clipboard','-target','UTF8_STRING','-o'],capture_output=True,timeout=5)
        if r.returncode:raise NavigationError('Could not read draft clipboard')
        return r.stdout.decode('utf8')
    def close(self):
        if self.process:
            self.process.terminate()
            try:self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
            self.process=None

class Composer:
    def __init__(self,desktop,navigator,clipboard):self.ui=desktop;self.nav=navigator;self.clipboard=clipboard
    def keys(self,*keys):
        if not keys or any(k not in ('ctrl+a','ctrl+c','ctrl+v','Right') for k in keys):raise NavigationError('Draft-only keyboard action rejected')
        self.ui.run(['xdotool','key','--clearmodifiers',*keys]);time.sleep(0.15)
    def focus(self,g):
        if self.ui.geometry()!=g:raise NavigationError('Window changed before draft focus')
        _,x,y,w,h=g
        self.ui.run(['xdotool','mousemove',str(x+w-120),str(y+h-100),'click','1']);time.sleep(0.15)
    def read(self):
        sentinel='draft-empty-'+uuid.uuid4().hex
        self.clipboard.set(sentinel);self.keys('ctrl+a','ctrl+c');seen=self.clipboard.get();self.keys('Right')
        return '' if seen==sentinel else seen
    def write(self,text,target,kind):
        if not isinstance(text,str) or not text or len(text)>5000 or '\x00' in text:raise ValueError('Invalid draft text')
        if not self.nav.verify(target,kind):raise NavigationError('Wrong conversation before drafting')
        _,g=self.ui.frame();self.focus(g)
        existing=self.read()
        if existing:raise NavigationError('Existing draft preserved; refusing to overwrite')
        if not self.nav.verify(target,kind):raise NavigationError('Conversation changed before paste')
        self.focus(g);self.clipboard.set(text);self.keys('ctrl+v');time.sleep(0.3)
        seen=self.read()
        if seen.replace('\r\n','\n')!=text.replace('\r\n','\n'):raise NavigationError('Draft readback differs from original')
        if not self.nav.verify(target,kind):raise NavigationError('Conversation changed after paste')
        return {'draft_verified':True,'target':target,'characters':len(text),'lines':len(text.splitlines()),'sent':False}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--target',required=True);parser.add_argument('--kind',choices=['private','group'],default='private');parser.add_argument('--text-file',required=True);args=parser.parse_args()
    text=Path(args.text_file).read_text('utf8');os.umask(0o077)
    status=subprocess.run(['systemctl','is-active','wechat-native-api'],capture_output=True,text=True,timeout=3)
    if status.stdout.strip()!='inactive':raise SystemExit('Native UI automation must be stopped before using standalone input')
    with (HOME/'ui.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        policy=Policy(**json.loads((HOME/'policy.json').read_text()));ocr=OCR();clipboard=Clipboard()
        folder=HOME/'runs'/str(time.time_ns());folder.mkdir(parents=True)
        try:
            with (folder/'trace.jsonl').open('w') as trace:
                ui=Desktop(policy);nav=Navigator(ui,ocr,policy,lambda e:(trace.write(json.dumps(e,ensure_ascii=False)+'\n'),trace.flush()))
                result=Composer(ui,nav,clipboard).write(text,args.target,args.kind)
                ui.frame()[0].save(folder/'draft.png');(folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False))
        finally:clipboard.close();ocr.close()

if __name__=='__main__':main()
