"""Short-lived QR login. Cookies stay in a private server file, never logs."""
import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit,parse_qs

import aiohttp
import qrcode

ROOT=Path('/opt/astrbot-mouth')
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'


def status(value,**fields):
    dest=ROOT/'bilibili-login-status.json';temp=dest.with_suffix('.tmp')
    temp.write_text(json.dumps({'status':value,'updated':time.time(),**fields},ensure_ascii=False))
    os.replace(temp,dest)


async def main():
    os.umask(0o077)
    async with aiohttp.ClientSession(headers={'User-Agent':UA,'Referer':'https://www.bilibili.com/'},timeout=aiohttp.ClientTimeout(total=30)) as session:
        async with session.get('https://passport.bilibili.com/x/passport-login/web/qrcode/generate') as response:
            generated=await response.json(content_type=None)
        if generated.get('code')!=0:raise RuntimeError('QR generation rejected')
        data=generated['data'];key=data['qrcode_key']
        qrcode.make(data['url']).save(ROOT/'bilibili-login.png')
        status('waiting_for_scan')
        for _ in range(90):
            async with session.get('https://passport.bilibili.com/x/passport-login/web/qrcode/poll',params={'qrcode_key':key}) as response:
                result=await response.json(content_type=None)
                cookies={k:v.value for k,v in response.cookies.items()}
            if result.get('code')!=0:raise RuntimeError('QR polling rejected')
            login=result['data'];code=login.get('code')
            if code==0:
                query=parse_qs(urlsplit(login.get('url','')).query)
                for name in ['SESSDATA','bili_jct','DedeUserID']:
                    if name not in cookies and name in query:cookies[name]=query[name][0]
                if not all(cookies.get(k) for k in ['SESSDATA','bili_jct','DedeUserID']):
                    raise RuntimeError('Login did not return complete credentials')
                async with session.get('https://api.bilibili.com/x/web-interface/nav',cookies=cookies) as response:
                    identity=await response.json(content_type=None)
                if identity.get('code')!=0 or not identity['data'].get('isLogin'):raise RuntimeError('Login identity verification failed')
                uid=str(identity['data']['mid'])
                if uid!=str(cookies['DedeUserID']):raise RuntimeError('Login account mismatch')
                cfg={'enabled':False,'account_id':uid,'nickname':identity['data'].get('uname',''),
                     'SESSDATA':cookies['SESSDATA'],'bili_jct':cookies['bili_jct'],
                     'device_id':str(uuid.uuid4()),'user_agent':UA,'buvid3':cookies.get('buvid3',''),'buvid4':cookies.get('buvid4','')}
                (ROOT/'bilibili-account.json').write_text(json.dumps(cfg,ensure_ascii=False,indent=2))
                status('logged_in',account_id=uid,nickname=cfg['nickname'])
                return
            if code==86038:
                status('expired');return
            status('waiting_for_confirmation' if code==86090 else 'waiting_for_scan')
            await asyncio.sleep(2)
        status('expired')


if __name__=='__main__':
    try:asyncio.run(main())
    except Exception as exc:
        status('error',error=type(exc).__name__)
        raise SystemExit(1)
