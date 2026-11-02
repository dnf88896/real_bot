"""On-demand Tomori daily Genie INT8 synthesis for the shared QQ/WeChat voice path."""
import os,sys,json,tempfile,subprocess,fcntl,gc,ctypes
from pathlib import Path
BASE=Path('/opt/genie-tts-lab')

def main():
    job=json.load(sys.stdin)
    text=job.get('text')
    if not isinstance(text,str) or not text.strip() or len(text)>1000:
        raise ValueError('Invalid voice text')
    output=Path(job['output'])
    if output.parent.resolve()!=Path('/opt/astrbot-mouth/outgoing-voice').resolve() or output.suffix!='.mp3':
        raise ValueError('Invalid voice output path')
    with open('/opt/tomori-tts/synthesis.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        os.chdir(BASE);sys.path.insert(0,str(BASE))
        os.environ['GENIE_DATA_DIR']=str(BASE/'GenieData')
        os.environ['OPEN_JTALK_DICT_DIR']='/opt/tomori-tts/open_jtalk_dic_utf_8-1.11'
        os.environ['Max_Cached_Character_Models']='1'
        os.environ.setdefault('OMP_NUM_THREADS','2')
        import genie_tts as genie
        from lowmem_loader import install
        install()
        import numpy as np,soundfile as sf
        from genie_tts.ModelManager import model_manager
        genie.load_character('tomori',str(BASE/'tomori-onnx-int8'),'zh')
        genie.set_reference_audio('tomori','/opt/tomori-tts/reference.wav',
            '練習で遅くなっちゃったね。でも、星がよく見えるね。',language='ja')
        model_manager.cn_hubert=None
        gc.collect();ctypes.CDLL('libc.so.6').malloc_trim(0)
        with tempfile.TemporaryDirectory(prefix='genie-',dir=output.parent) as work:
            wav=Path(work)/'voice.wav'
            genie.tts('tomori',text,play=False,split_sentence=True,save_path=str(wav))
            audio,rate=sf.read(wav)
            if len(audio)<rate*.1 or not np.isfinite(audio).all() or np.max(np.abs(audio))<.005:
                raise ValueError('Invalid generated voice')
            subprocess.run(['/opt/tomori-tts/bin/ffmpeg','-nostdin','-hide_banner','-loglevel','error',
                '-y','-i',str(wav),'-codec:a','libmp3lame','-b:a','128k',str(output)],check=True,timeout=30)
            print(json.dumps({'ok':True,'engine':'genie-int8','audio_seconds':round(len(audio)/rate,3)}),flush=True)

if __name__=='__main__':main()
