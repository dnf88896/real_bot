"""Public self-account identities, cached independently of persona and contacts."""
import asyncio,json,sqlite3,time
from pathlib import Path
from contextlib import closing

QQ_ACCOUNT='100000002'

class SelfIdentity:
    def __init__(self,root):
        self.root=Path(root);self.path=self.root/'self-identity.sqlite3'
        with closing(self.connect()) as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS accounts(platform TEXT,account TEXT,name TEXT,updated REAL,error TEXT,PRIMARY KEY(platform,account))')

    def connect(self):return sqlite3.connect(self.path,timeout=10)

    def expected(self):
        result=[('qq',QQ_ACCOUNT)]
        path=self.root/'bilibili-account.json'
        if path.exists():
            cfg=json.loads(path.read_text('utf8'))
            if cfg.get('enabled') and str(cfg.get('account_id','')).isdecimal():result.append(('bilibili',str(cfg['account_id'])))
        from wechat_client import config as wechat_config
        wx=wechat_config(self.root)
        if wx['enabled'] and wx['account_id']:result.append(('wechat',wx['account_id']))
        from heybox_sender import config as heybox_config
        hx=heybox_config(self.root)
        if hx['enabled'] and str(hx['account_id']).isdecimal():result.append(('heybox',str(hx['account_id'])))
        return result

    def fetch(self,platform):
        if platform=='heybox':
            from heybox_sender import profile
            return profile(self.root)
        if platform=='qq':
            from qq_sender import onebot
            info=onebot('get_login_info',{})
            return str(info['user_id']),str(info.get('nickname') or '')
        if platform=='wechat':
            from wechat_client import Client
            info=Client(self.root).call('/User/GetContractProfile')
            return str(info.get('wxid') or info.get('userName') or ''),str(info.get('nickName') or info.get('nickname') or '')
        async def bili():
            if __package__:
                from . import bilibili_video as video
            else:
                import bilibili_video as video
            client,_=video.client_for(self.root)
            try:
                info=await video.get(client,'/x/web-interface/nav',{})
                if not info.get('isLogin'):raise ValueError('Bilibili account not logged in')
                return str(info['mid']),str(info.get('uname') or '')
            finally:await client.close()
        return asyncio.run(bili())

    def refresh(self,fetch=None):
        fetch=fetch or self.fetch
        for platform,account in self.expected():
            try:
                actual,name=fetch(platform)
                if actual!=account or not name.strip():raise ValueError('Self-account identity mismatch or missing nickname')
                with closing(self.connect()) as db,db:
                    db.execute('INSERT OR REPLACE INTO accounts VALUES(?,?,?,?,NULL)',(platform,account,name,time.time()))
            except Exception as exc:
                with closing(self.connect()) as db,db:
                    db.execute('UPDATE accounts SET error=? WHERE platform=? AND account=?',(type(exc).__name__,platform,account))

    def snapshot(self,scene):
        accounts=[]
        with closing(self.connect()) as db:
            for platform,account in self.expected():
                row=db.execute('SELECT name,error FROM accounts WHERE platform=? AND account=?',(platform,account)).fetchone()
                item={'平台':platform,'昵称':row[0] if row else '昵称暂未获取',
                      'QQ号' if platform=='qq' else '微信ID' if platform=='wechat' else 'UID':account}
                if platform=='bilibili':item['主页链接']='https://space.bilibili.com/'+account
                if platform=='heybox':item['说明']='小黑盒账号，支持本人发帖、帖子评论及回复'
                if row and row[1]:item['资料状态']='昵称更新暂不可用，沿用上次平台确认值'
                accounts.append(item)
        return {'说明':'以下账号都属于你自己，是同一个人的不同平台身份，不是聊天对象；QQ号与B站UID是不同平台的标识。',
                '账号':accounts,'当前活动来源':{'平台':scene['platform'],'类型':scene['chat_type'],
                **({'说明':'自主活动，没有固定来源会话'} if scene['platform']=='global' else {'会话ID':str(scene['conversation'])})}}
