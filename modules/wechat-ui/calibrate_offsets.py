"""Measure avatar/menu offsets without selecting menu items or sending."""
import fcntl,json,os,subprocess,time
from session_selector import HOME,Desktop,Policy,OCR,Navigator,NavigationError,normal
from inspect_avatar_menu import capture

def main():
    os.umask(0o077)
    status=subprocess.run(['systemctl','is-active','wechat-native-api'],capture_output=True,text=True,timeout=3)
    if status.stdout.strip()!='inactive':raise NavigationError('Other native automation must be stopped')
    with (HOME/'ui.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        policy=Policy();ui=Desktop(policy);ocr=OCR();nav=Navigator(ui,ocr,policy);results=[]
        folder=HOME/'runs'/str(time.time_ns());folder.mkdir(parents=True)
        try:
            if not nav.verify('群聊7','group'):raise NavigationError('Expected 群聊7')
            for index,target in enumerate(['ExampleFriend','ExampleOwner']):
                im,g=ui.frame();rows=ocr.read(im.crop((300,85,im.width,im.height-150)))['lines']
                names=[r for r in rows if normal(r['text'])==normal(target) and 50<=r['box'][0]<150]
                if len(names)!=1:raise NavigationError('Visible member not unique')
                row=names[0];left=300+row['box'][0];cy=85+(row['box'][1]+row['box'][3])/2
                ax=round(left-32);ay=round(cy+12)
                fresh,g2=ui.frame()
                if g2!=g or fresh.crop((310,ay-35,550,ay+35)).tobytes()!=im.crop((310,ay-35,550,ay+35)).tobytes():raise NavigationError('Member moved before click')
                _,x,y,w,h=g;opened=False
                try:
                    ui.run(['xdotool','mousemove',str(x+ax),str(y+ay),'click','3']);opened=True;time.sleep(0.4)
                    picture=capture(ui,g);picture.save(folder/(str(index)+'-menu.png'))
                    lines=ocr.read(picture.crop((ax,ay,min(w,ax+280),min(h,ay+110))))['lines']
                    marks=[r for r in lines if r['text'].strip().startswith('@') and r['box'][1]<45]
                    if len(marks)!=1:raise NavigationError('Mention row could not be identified')
                    mark=marks[0];box=mark['box'];dx=round(box[0]+min(7,(box[2]-box[0])/2));dy=round((box[1]+box[3])/2)
                    if not 8<=dx<=35 or not 10<=dy<=35:raise NavigationError('Unexpected menu layout')
                    # The @ icon lies inside the first clickable menu row.
                    results.append({'nickname':target,'nickname_anchor':[left,cy],'avatar_center_click':[ax,ay],'offset1':[-32,12],'measured_offset2':[dx,dy],'menu_rows':lines,'clicked_mention':False,'sent':False})
                finally:
                    if opened:ui.run(['xdotool','mousemove',str(x+w-90),str(y+380),'click','1']);time.sleep(0.2)
                if not nav.verify('群聊7','group'):raise NavigationError('Conversation changed after menu inspection')
            (folder/'offset-measurements.json').write_text(json.dumps(results,ensure_ascii=False,indent=2));print(json.dumps(results,ensure_ascii=False))
        finally:ocr.close()

if __name__=='__main__':main()
