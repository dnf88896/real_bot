"""Webox backend for the unified WeChat adapter; no model calls or public API."""
import base64
import hashlib
import io
import json
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


class Webox:
    def __init__(self, cfg, request=None):
        self.cfg = cfg
        self.request = request
        self.contacts = {}

    def api(self, path, body=None):
        from wechat_client import Rejected, Uncertain
        if not self.cfg.get('enabled'): raise Rejected('WeChat transport disabled')
        if self.request:
            result = self.request(path, body, 'GET' if body is None else 'POST', False)
        else:
            base = self.cfg['base_url'].rstrip('/')
            parsed = urllib.parse.urlsplit(base)
            if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost') or parsed.username or parsed.query or parsed.fragment:
                raise Rejected('Webox requires a loopback API')
            secret = json.loads(Path(self.cfg['credentials_file']).read_text())
            req = urllib.request.Request(base+path,
                data=None if body is None else json.dumps(body, ensure_ascii=False).encode(),
                headers={'Content-Type':'application/json', 'AuthorizationType':'ilink_bot_token',
                         'Authorization':'Bearer '+secret['access_token'], 'X-WECHAT-UIN':'unified-mouth'})
            try:
                with urllib.request.urlopen(req, timeout=100 if path=='/ilink/bot/sendmessage' else 55) as r:
                    data = r.read(16*1024*1024+1)
                    if len(data)>16*1024*1024: raise Uncertain('Webox response too large')
                    result = json.loads(data)
            except urllib.error.HTTPError as e:
                if e.code in (400,401,403,404,413,422,501): raise Rejected('Webox rejected operation') from None
                raise Uncertain('Webox result uncertain') from None
            except (OSError,ValueError): raise Uncertain('Webox result unavailable') from None
        if not isinstance(result, dict): raise Uncertain('Invalid Webox envelope')
        if path != '/healthz' and (type(result.get('ret')) is not int or result['ret'] != 0):
            raise Rejected('Webox session or operation unavailable')
        return result

    def room_path(self, room, relative, *, incoming=False):
        from wechat_client import Rejected
        root = Path(self.cfg['shared_rooms']).resolve()
        if not isinstance(room, str) or not room or '/' in room or '\\' in room or room in ('.','..'):
            raise Rejected('Invalid Webox room')
        folder = root/room
        rel = Path(relative)
        if rel.is_absolute() or '..' in rel.parts or not rel.parts or rel.parts[0] not in (('inbox',) if incoming else ('outbox',)):
            raise Rejected('Invalid Webox media reference')
        target = (folder/rel).resolve()
        if not target.is_relative_to(folder.resolve()) or not folder.resolve().is_relative_to(root):
            raise Rejected('Webox media outside room')
        return target

    def message(self, row):
        from wechat_client import Rejected
        account = self.cfg['account_id']
        room, sender, mid = row.get('roomid'), row.get('from'), row.get('msgid')
        if not all(isinstance(x,str) and x for x in (room,sender,mid)): raise Rejected('Incomplete Webox message identity')
        kind = row.get('msgtype')
        text, image, msg_type = '', None, 1
        if kind == 'text': text = (row.get('text') or {}).get('content','')
        elif kind == 'reply': text = (row.get('reply') or {}).get('title','')
        elif kind == 'image':
            msg_type = 3; ref = (row.get('image') or {}).get('sdkfileid','')
            if ref: image = {'download_context':{'roomid':room,'sdkfileid':ref}}
        elif kind == 'link':
            link = row.get('link') or {}; text = '\n'.join(str(link.get(k) or '') for k in ('title','description','link_url')).strip()
        elif kind == 'voice':
            msg_type = 34
        else: text = '[微信消息类型:'+str(kind)+']'
        contact = self.contacts.get(sender,{})
        result = {'new_msg_id':mid,'from_user_id':sender,'to_user_id':account,'conversation_id':room,
                  'chat_type':'group' if room.endswith('@chatroom') else 'private',
                  'type':msg_type,'text':text,'timestamp':row.get('msgtime'),
                  'sender_name':contact.get('nickName') or sender,
                  'group_name':self.contacts.get(room,{}).get('nickName',''),
                  'is_self':sender==account,'webox_original':row}
        if kind == 'voice' and sender != account:
            from wechat_asr import transcribe
            result['voice'] = transcribe(mid, room)
        if kind=='reply':result['reply_context']=(row.get('reply') or {}).get('parent')
        if image: result['image']=image
        return result

    def call(self, path, body=None, method='POST', binary=False):
        from wechat_client import Rejected
        if path == '/Login/GetLoginStatus':
            ready = self.api('/healthz').get('ready') is True
            if ready:
                profile=self.api('/ilink/bot/userinfo')
                if profile.get('account_id')!=self.cfg['account_id']:raise Rejected('Webox account changed')
            return {'loginState':'ready' if ready else 'offline','longLinkReady':ready}
        if path == '/User/GetContractProfile':
            r=self.api('/ilink/bot/userinfo')
            if self.cfg.get('account_id') and r.get('account_id')!=self.cfg['account_id']:raise Rejected('Webox account changed')
            return {'wxid':r.get('account_id'),'nickName':r.get('nickname'),'wechat_id':r.get('wechat_id')}
        if path == '/Msg/Sync':
            cursor=(body or {}).get('Synckey','')
            if cursor=='initial':cursor=str(self.cfg['receive_after_ms']-1)
            r=self.api('/ilink/bot/getupdates',{'cursor':cursor})
            if r.get('messages') is None:r['messages']=[]
            if not isinstance(r.get('messages'),list) or not isinstance(r.get('cursor'),str):raise Rejected('Invalid Webox updates')
            return {'items':[self.message(x) for x in r['messages']], 'Synckey':r['cursor']}
        if path == '/Friend/GetContractList':
            for remark in self.cfg.get('contact_remarks',[]):
                r=self.api('/ilink/bot/contacts?'+urllib.parse.urlencode({'remark':remark}))
                for row in r.get('contacts',[]):
                    uid=row.get('roomid')
                    if uid in ('filehelper','fmessage','medianote','newsapp','notifymessage','weixin'):continue
                    if uid:self.contacts[uid]={'userName':uid,'nickName':row.get('nickname') or uid,'remark':row.get('remark') or ''}
            return {'contacts':list(self.contacts.values()),'has_more':False}
        if path == '/Friend/GetContractDetail':return [self.contacts[x] for x in body['userName'].split(',') if x in self.contacts]
        if path in ('/Tools/CdnDownloadImage','/Tools/DownloadImg'):
            r=self.api('/ilink/bot/getmedia', {'roomid':body['roomid'],'sdkfileid':body['sdkfileid']})
            data=base64.b64decode(r['base64'],validate=True)
            if not 0<len(data)<=4*1024*1024:raise Rejected('Webox image size invalid')
            return data
        raise Rejected('Webox operation unsupported')

    def open_chat(self, uid):
        if not isinstance(uid,str) or not uid:raise Rejected('Invalid WeChat room to open')
        return self.api('/ilink/bot/openchat',{'roomid':uid})

    def sent(self, path, body):
        from wechat_client import Rejected,Uncertain
        uid=body.get('ToWxid','')
        if path=='/Msg/SendTxt':
            # Content may carry the sender-side markers (mention names, quote
            # request); they are transport detail, never part of the mouth text.
            if not isinstance(body.get('Content'),str) or not body['Content']:
                raise Rejected('Empty WeChat text')
            message={'roomid':uid,'tolist':[],'msgtype':'text','text':{'content':body['Content']}}
            if body.get('ReplyTo'):message.update(msgtype='reply',reply={'title':body['Content'],'parent':{'msgid':str(body['ReplyTo'])}});message.pop('text',None)
        elif path=='/Msg/UploadImg':
            data=base64.b64decode(body['Base64'],validate=True)
            if not 0<len(data)<=4*1024*1024:raise Rejected('Webox image size invalid')
            # Qt's X11 image clipboard accepts PNG reliably on this client;
            # JPEG clipboard requests were not confirmed as an image send.
            # This changes the transport encoding, not the original media path.
            from PIL import Image
            with Image.open(io.BytesIO(data)) as image:
                if image.width*image.height>16000000:raise Rejected('WeChat image dimensions too large')
                if getattr(image,'is_animated',False):raise Rejected('Native animated image delivery not verified')
                output=io.BytesIO()
                image.save(output,'PNG')
                data=output.getvalue()
            if len(data)>4*1024*1024:raise Rejected('WeChat PNG exceeds media size limit')
            result=self.api('/ilink/bot/putimage',{'roomid':uid,'base64':base64.b64encode(data).decode()})
            ref=result.get('sdkfileid','')
            if not isinstance(ref,str) or not ref.startswith('outbox/') or '..' in ref.split('/'):
                raise Rejected('Invalid Webox image reference')
            message={'roomid':uid,'tolist':[],'msgtype':'image','image':{'sdkfileid':ref}}
        else:raise Rejected('Native WeChat voice sending not yet implemented')
        message['msgid']='unified:'+uuid.uuid4().hex
        result=self.api('/ilink/bot/sendmessage',{'msgs':[message]})
        if result.get('client_msg_id')!=message['msgid']:raise Uncertain('Webox confirmation missing')
        return {'message_id':str(result.get('native_message_id') or result['client_msg_id']),'platform_receipt':result,'confirmed_by':'wechat_ui_and_database','reply_to':result.get('reply_to')}
