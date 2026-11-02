"""Refresh display names through read-only QQ APIs; no model or message sending."""
import json,os,sys,time,sqlite3
from pathlib import Path
sys.path.insert(0,'/opt/qq-mouth')
from qq_sender import onebot,ACCOUNT
from basic_info import initialize,observe,sync
ROOT=Path('/opt/qq-identity')

def refresh(call=onebot):
    friends=call('get_friend_list',{});groups=call('get_group_list',{});own=call('get_login_info',{})
    if not isinstance(friends,list) or not isinstance(groups,list) or str(own.get('user_id'))!=ACCOUNT:raise ValueError('Invalid identity response')
    db=sqlite3.connect(ROOT/'basic.sqlite3');initialize(db)
    try:
        policy=json.loads(Path('/opt/qq-mouth/delivery.json').read_text('utf8'))
        allowed=list(policy.get('allow_groups',[]))
        if policy.get('group_delivery_mode')=='owner_preview':allowed+=policy.get('preview_groups',[])
        observe(db,ACCOUNT);sync(db,ACCOUNT,friends,groups,own,allowed)
    finally:db.close()
    users={str(x['user_id']):x['nickname'] for x in friends if x.get('user_id') and x.get('nickname')}
    if own.get('nickname'):users[ACCOUNT]=own['nickname']
    result={'account_id':ACCOUNT,'updated_at':time.time(),'users':users,'groups':{str(x['group_id']):x['group_name'] for x in groups if x.get('group_id') and x.get('group_name')}}
    temp=ROOT/'contacts.json.tmp';temp.write_text(json.dumps(result,ensure_ascii=False),encoding='utf8');os.replace(temp,ROOT/'contacts.json')
    return len(users),len(result['groups'])

def main():
    os.umask(0o077);ROOT.mkdir(mode=0o700,exist_ok=True)
    while True:
        try:
            n,g=refresh();print(f'Display names synchronized: {n} accounts, {g} groups.',flush=True)
        except Exception as e:print('Name sync unavailable; previous cache retained: '+type(e).__name__,flush=True)
        time.sleep(30)

if __name__=='__main__':main()
