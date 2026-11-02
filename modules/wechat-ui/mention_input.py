"""Bounded native @ insertion. Caller holds the shared desktop lock throughout."""
import re,time,unicodedata
from session_selector import NavigationError,normal

def contains(draft,name):
    text=unicodedata.normalize('NFKC',draft).casefold()
    name=unicodedata.normalize('NFKC',name).casefold()
    return re.search(r'(?<!\S)@'+re.escape(name)+r'(?=\s|$)',text) is not None

def visible_name(rows,target):
    matches=[r for r in rows if normal(r['text'])==normal(target) and 50<=r['box'][0]<150]
    if not matches:raise NavigationError('Visible nickname not found')
    # Repeated messages from a uniquely named member are not multiple recipients.
    # Use the latest visible message. Recipient-name uniqueness belongs upstream.
    return max(matches,key=lambda row:row['box'][3])

def insert_with_retry(target,read_draft,click_mention,log=None,max_attempts=3):
    """Re-read before every click, including after ambiguous prior outcomes."""
    if not target.strip() or max_attempts!=3:raise ValueError('A target and exactly three maximum attempts are required')
    trace=log or (lambda event:None);baseline=None;errors=[]
    for attempt in range(1,4):
        try:
            before=read_draft()
            if contains(before,target):return {'success':True,'attempts':attempt,'already_present':True,'sent':False}
            if baseline is None:baseline=before
            elif before!=baseline:return {'success':False,'attempts':attempt,'error':'Draft changed; no further clicks','sent':False}
            try:click_mention(target)
            except NavigationError as exc:
                # The click may already have worked; inspect instead of clicking again.
                errors.append(str(exc));trace({'event':'mention_action_error','attempt':attempt,'error':str(exc)})
            after=read_draft()
            if contains(after,target):return {'success':True,'attempts':attempt,'already_present':False,'sent':False}
            if after!=baseline:return {'success':False,'attempts':attempt,'error':'Unexpected draft change; no further clicks','sent':False}
            errors.append('Mention missing from verified draft')
        except NavigationError as exc:errors.append(str(exc))
        trace({'event':'mention_attempt_failed','attempt':attempt,'target':target,'error':errors[-1]})
    return {'success':False,'attempts':3,'error':'Three attempts failed','details':errors[-3:],'sent':False}

class AvatarMentionInput:
    def __init__(self,ui,ocr,navigator,composer,offsets,group,log=None):
        self.ui=ui;self.ocr=ocr;self.nav=navigator;self.composer=composer;self.offsets=offsets;self.group=group;self.log=log
    def read(self):
        if not self.nav.verify(self.group,'group'):raise NavigationError('Expected group is not open')
        _,g=self.ui.frame();self.composer.focus(g);return self.composer.read()
    def click(self,target):
        im,g=self.ui.frame();rows=self.ocr.read(im.crop((300,85,im.width,im.height-150)))['lines']
        row=visible_name(rows,target);one=self.offsets['nickname_to_avatar'];two=self.offsets['avatar_to_mention_menu']
        ax=round(300+row['box'][0]+one['dx']);ay=round(85+(row['box'][1]+row['box'][3])/2+one['dy'])
        _,x,y,w,h=g
        if not 315<ax<360 or not 100<ay<h-150:raise NavigationError('Avatar/menu too close to an edge')
        fresh,g2=self.ui.frame()
        if g2!=g or fresh.crop((310,ay-35,550,ay+35)).tobytes()!=im.crop((310,ay-35,550,ay+35)).tobytes():raise NavigationError('Message moved before right click')
        opened=False
        try:
            self.ui.run(['xdotool','mousemove',str(x+ax),str(y+ay),'click','3']);opened=True;time.sleep(0.35)
            # Menu origin is the pointer; fixed offset, deliberately no menu OCR.
            self.ui.run(['xdotool','mousemove',str(x+ax+two['dx']),str(y+ay+two['dy']),'click','1']);opened=False;time.sleep(0.25)
        finally:
            if opened:self.ui.run(['xdotool','mousemove',str(x+w-90),str(y+380),'click','1'])
    def add(self,target):return insert_with_retry(target,self.read,self.click,self.log)

def main():
    import argparse,fcntl,json,os,subprocess
    from session_selector import HOME,Desktop,Policy,OCR,Navigator
    from text_input import Composer,Clipboard
    parser=argparse.ArgumentParser();parser.add_argument('--group',required=True);parser.add_argument('--target',action='append',required=True);args=parser.parse_args()
    os.umask(0o077)
    status=subprocess.run(['systemctl','is-active','wechat-native-api'],capture_output=True,text=True,timeout=3)
    if status.stdout.strip()!='inactive':raise SystemExit('Native automation must remain stopped for standalone @ input')
    with (HOME/'ui.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        policy=Policy();ocr=OCR();ui=Desktop(policy);nav=Navigator(ui,ocr,policy);clipboard=Clipboard()
        folder=HOME/'runs'/str(time.time_ns());folder.mkdir(parents=True)
        try:
            with (folder/'trace.jsonl').open('w') as trace:
                log=lambda event:(trace.write(json.dumps(event,ensure_ascii=False)+'\n'),trace.flush())
                writer=AvatarMentionInput(ui,ocr,nav,Composer(ui,nav,clipboard),json.loads((HOME/'mention-offsets.json').read_text()),args.group,log)
                results=[]
                for target in args.target:
                    result={'target':target,**writer.add(target)};results.append(result)
                    (folder/'mentions.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
                    if not result['success']:break
                print(json.dumps(results,ensure_ascii=False))
        finally:clipboard.close();ocr.close()

if __name__=='__main__':main()
