"""Read-only, short-lived QQ roster cache for automatic idle activity."""
import threading,time,json,sqlite3
from pathlib import Path
from contextlib import closing

class Roster:
    def __init__(self,call=None,ttl=60,root=None):
        self.root=Path(root or '/opt/astrbot-mouth')
        self.call=call;self.ttl=ttl;self.lock=threading.Lock();self.until=0;self.snapshot=None
    def __call__(self,scene):
        if scene.get('platform')!='qq':return self.other_platform(scene)
        with self.lock:
            if time.monotonic()>=self.until:
                try:
                    from qq_sender import onebot,ACCOUNT
                    call=self.call or onebot
                    who=call('get_login_info',{})
                    if str(who.get('user_id'))!=str(ACCOUNT):raise ValueError('Roster account mismatch')
                    friends=call('get_friend_list',{});groups=call('get_group_list',{})
                    if not isinstance(friends,list) or not isinstance(groups,list):raise ValueError('Invalid roster response')
                    self.snapshot=(str(ACCOUNT),{'private':{str(x['user_id']) for x in friends},'group':{str(x['group_id']) for x in groups}})
                    self.until=time.monotonic()+self.ttl
                except Exception:
                    self.snapshot=None;self.until=time.monotonic()+10
            if self.snapshot is None:return None # Pause, never remove scopes on a failed request.
            account,roster=self.snapshot
            return str(scene.get('account'))==account and str(scene.get('conversation')) in roster.get(scene.get('chat_type'),set())

    def other_platform(self,scene):
        platform=scene.get('platform');account=str(scene.get('account',''));uid=str(scene.get('conversation',''));kind=scene.get('chat_type')
        if platform not in ('wechat','bilibili') or kind not in ('private','group') or not uid or uid==account:return False
        try:
            if platform=='wechat':
                from wechat_client import config
                cfg=config(self.root)
                if not (cfg['enabled'] and cfg['receive_enabled'] and cfg['send_enabled']) or account!=str(cfg['account_id']):return False
                if not (cfg.get('allow_all_'+kind,False) or uid in [str(x) for x in cfg['allow_private' if kind=='private' else 'allow_groups']]):return False
                path=self.root/'wechat.sqlite3'
                if not path.exists():return None
                with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
                    return bool(db.execute('SELECT 1 FROM directory WHERE account=? AND kind=? AND id=?',(account,kind,uid)).fetchone())
            cfg=json.loads((self.root/'bilibili-account.json').read_text('utf8'))
            if not cfg.get('enabled') or account!=str(cfg.get('account_id')) or kind!='private' or not uid.isascii() or not uid.isdecimal() or int(uid)<=0:return False
            path=self.root/'platform-directory.sqlite3'
            if not path.exists():return None
            with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
                return bool(db.execute("SELECT 1 FROM contacts WHERE platform='bilibili' AND account=? AND id=?",(account,uid)).fetchone())
        except (OSError,ValueError,sqlite3.Error,KeyError):return None
