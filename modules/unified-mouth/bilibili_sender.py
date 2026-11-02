"""One acknowledged Bilibili write per durable relay delivery. No blind retries."""
import asyncio
import base64
import importlib
import json
import sys
import types
from pathlib import Path

ROOT=Path('/opt/astrbot-mouth')


class Rejected(RuntimeError):
    definite_failure=True


def client_class():
    name='_unified_bilibili_transport'
    if name not in sys.modules:
        package=types.ModuleType(name);package.__path__=[str(ROOT/'bilibili-client')]
        sys.modules[name]=package
    upstream=importlib.import_module(name+'.bilibili_client')

    class Client(upstream.BilibiliClient):
        async def _send_message(self,payload):
            # A timeout after posting is uncertain; never reissue the write here.
            session=await self._get_session()
            await self._ensure_buvid()
            params=await self._sign_wbi_params({'w_sender_uid':payload['msg[sender_uid]'],
                'w_receiver_id':payload['msg[receiver_id]'],'w_dev_id':self._device_id})
            if params is None:raise Rejected('Bilibili signing unavailable')
            async with session.post(upstream.SEND_MSG_URL,params=params,data=payload) as response:
                data=await self._safe_json_from_response(response)
                if response.status!=200 or not isinstance(data,dict) or data.get('code')!=0:
                    code=data.get('code') if isinstance(data,dict) else response.status
                    raise Rejected('Bilibili send rejected, code '+str(code))
                result=data.get('data') or {}
                return {'message_id':str(result.get('msg_key') or result.get('msg_seqno') or ''),
                        'accepted':True,'platform_receipt':result}
    return Client


async def send_async(destination,message):
    cfg=json.loads((ROOT/'bilibili-account.json').read_text('utf8'))
    if not cfg.get('enabled') or str(cfg.get('account_id'))!=str(destination['account']):
        raise ValueError('Bilibili account unavailable or mismatched')
    if destination['chat_type']!='private':raise ValueError('Only Bilibili private delivery is implemented')
    client=client_class()(sessdata=cfg['SESSDATA'],bili_jct=cfg['bili_jct'],
        device_id=cfg['device_id'],user_agent=cfg['user_agent'],buvid3=cfg.get('buvid3',''),buvid4=cfg.get('buvid4',''))
    client._self_uid=int(cfg['account_id'])
    try:
        kinds={p['type'] for p in message}
        if kinds=={'text'}:
            receipt=await client.send_text_message(int(destination['id']),''.join(p['data']['text'] for p in message))
        elif len(message)==1 and kinds=={'image'}:
            value=message[0]['data']['file']
            if not value.startswith('base64://'):raise ValueError('Only validated local image bytes can be sent')
            blob=base64.b64decode(value[9:],validate=True)
            if not 0<len(blob)<=4*1024*1024:raise ValueError('Image size invalid')
            uploaded=await client.upload_image(blob)
            if not uploaded:raise Rejected('Bilibili image upload failed')
            receipt=await client.send_image_message(int(destination['id']),uploaded)
        else:raise ValueError('Bilibili delivery must contain a single atomic text or image message')
        if not isinstance(receipt,dict) or not receipt.get('accepted'):raise RuntimeError('No Bilibili acceptance receipt')
        return receipt
    finally:await client.close()


def send(destination,message):
    return asyncio.run(send_async(destination,message))
