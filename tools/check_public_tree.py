"""Offline publication guard: syntax, forbidden runtime files and blank secrets."""
import ast,json,re,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
FORBIDDEN={'.env','.deploy-access.json','机器人专用API Key.txt','mail-secret.local.json'}
RUNTIME_EXT={'.sqlite','.sqlite3','.db','.log','.jsonl','.onnx','.ckpt','.pth','.silk','.mp3','.wav','.mp4','.pem','.pfx'}
SECRET_FIELDS={'api_key','apikey','password','passwd','secret','sessdata','bili_jct','buvid3','buvid4','token','access_token','refresh_token','cookie','authorization','authorization_code','device_id'}

def main():
    errors=[];count=0
    for path in ROOT.rglob('*'):
        if not path.is_file() or '.git' in path.parts or '__pycache__' in path.parts:continue
        rel=path.relative_to(ROOT).as_posix();count+=1
        if path.name in FORBIDDEN or path.suffix in RUNTIME_EXT:errors.append(rel+': runtime/secret artifact')
        if path.suffix=='.py':
            try:ast.parse(path.read_text('utf-8-sig'),filename=rel)
            except SyntaxError as exc:errors.append(rel+': syntax line '+str(exc.lineno))
        if path.suffix=='.json':
            try:value=json.loads(path.read_text('utf-8-sig'))
            except ValueError:errors.append(rel+': invalid JSON');continue
            def visit(v):
                if isinstance(v,dict):
                    for k,x in v.items():
                        if k.lower() in SECRET_FIELDS and x not in ('',None,[],{}):errors.append(rel+': credential field is not empty: '+k)
                        visit(x)
                elif isinstance(v,list):
                    for x in v:visit(x)
            visit(value)
    if errors:
        print('\n'.join(errors));return 1
    print(f'Offline publication checks passed: {count} files, Python syntax valid, JSON credential fields empty.')
    return 0

if __name__=='__main__':sys.exit(main())
