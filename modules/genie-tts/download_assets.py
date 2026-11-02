from pathlib import Path
import json,urllib.request,urllib.parse,concurrent.futures,time
root=Path('/opt/genie-tts-lab')
files=[x for x in json.loads((root/'assets.json').read_text()) if '/EnglishG2P/' not in x['path']]
def get(x):
 target=root/x['path'];target.parent.mkdir(parents=True,exist_ok=True)
 if target.exists() and target.stat().st_size==x['size']:return
 errors=[]
 for domain in ['hf-mirror.com','huggingface.co']:
  url='https://'+domain+'/High-Logic/Genie/resolve/main/'+urllib.parse.quote(x['path'])
  try:
   print('DOWNLOAD',x['path'],flush=True)
   with urllib.request.urlopen(url,timeout=45) as r,target.with_suffix(target.suffix+'.download').open('wb') as f:
    while b:=r.read(1024*1024):f.write(b)
   assert target.with_suffix(target.suffix+'.download').stat().st_size==x['size']
   target.with_suffix(target.suffix+'.download').replace(target);print('DONE',x['path'],flush=True);return
  except Exception as e:errors.append(str(e));print('RETRY',x['path'],str(e),flush=True)
 raise RuntimeError(str(errors))
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(get,files))
print('ALL ASSETS READY',flush=True)
