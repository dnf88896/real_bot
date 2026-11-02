"""Extract video references from text and share cards, without network or instructions."""
import html
import json
import re
from urllib.parse import urlsplit,parse_qs

BV = r'BV[0-9A-Za-z]{10}'
COMMENT_NUMBER = r'B评:('+BV+r'):([1-9][0-9]*)'


def comment_number(bvid,rpid):
    value='B评:'+str(bvid)+':'+str(rpid)
    if not re.fullmatch(COMMENT_NUMBER,value):raise ValueError('Invalid comment target number')
    return value


def present_comments(value,bvid):
    """Return a presentation copy; routing labels never enter cached source facts."""
    if isinstance(value,list):return [present_comments(v,bvid) for v in value]
    if not isinstance(value,dict):return value
    result={}
    if '评论ID' in value:result['评论目标编号']=comment_number(bvid,value['评论ID'])
    if '触发评论ID' in value:result['目标评论编号']=comment_number(bvid,value['触发评论ID'])
    result.update({k:present_comments(v,bvid) for k,v in value.items() if k not in ('评论目标编号','目标评论编号')})
    return result


def without_comment_numbers(value):
    if isinstance(value,list):return [without_comment_numbers(v) for v in value]
    if isinstance(value,dict):return {k:without_comment_numbers(v) for k,v in value.items() if k not in ('评论目标编号','目标评论编号')}
    return value


def clean_presentation_history(messages):
    """Drop tool-result labels after generation, before native history is saved."""
    for message in messages:
        for part in message.content or []:
            text=getattr(part,'text',None)
            if not isinstance(text,str) or not (message.role=='tool' or text.startswith('[对应回复链] ')):continue
            start=text.find('{')
            if start<0:continue
            try:value,end=json.JSONDecoder().raw_decode(text[start:])
            except ValueError:
                # Native tool-output limits may truncate JSON midway through a
                # label. Remove that presentation field even without valid JSON.
                part.text=re.sub(r'"(?:评论目标编号|目标评论编号)"\s*:\s*"(?:B评|H评):[^"\n]*(?:"\s*,?)?', '', text)
                continue
            cleaned=without_comment_numbers(value)
            if cleaned!=value:part.text=text[:start]+json.dumps(cleaned,ensure_ascii=False)+text[start+end:]


def references(value):
    text=html.unescape(str(value)).replace('\\/','/')
    found=[]
    pattern=(r'(?<![A-Za-z0-9])'+BV+r'(?![A-Za-z0-9])|https?://(?:www\.|m\.)?bilibili\.com/video/(?:'+BV+r'|av[0-9]+)(?:[/?#][A-Za-z0-9_~:/?#\[\]@!$&()*+,;=%.-]*)?|https?://b23\.tv/[A-Za-z0-9]+')
    for m in re.finditer(pattern,text):
        ref=m.group().rstrip('）)。，,;；')
        if ref not in found:found.append(ref)
    return found[:8]


def card_text(value):
    if isinstance(value,dict):
        value=json.dumps(value,ensure_ascii=False)
    if not isinstance(value,str) or len(value)>100000:return ''
    refs=references(value)
    return '[B站分享] '+' '.join(refs) if refs else ''


def target(value):
    """Only a concrete single BV or trusted video URL; preserve case-sensitive BV."""
    value=value.strip()
    numbered=re.fullmatch(COMMENT_NUMBER,value)
    if numbered:return numbered[1],numbered[2]
    canonical=re.fullmatch('('+BV+r'):([1-9][0-9]*)',value)
    if canonical:return canonical[1],canonical[2]
    if re.fullmatch(BV,value):return value,''
    url=urlsplit(value)
    if url.scheme not in ('http','https') or url.hostname not in ('www.bilibili.com','bilibili.com','m.bilibili.com') or url.username or url.password:
        raise ValueError('Not a Bilibili video URL')
    match=re.fullmatch('/video/('+BV+')/?',url.path)
    if not match:raise ValueError('Missing concrete BV video identifier')
    query=parse_qs(url.query)
    rpid=(query.get('comment_id') or query.get('rpid') or [''])[0]
    if not rpid and re.fullmatch(r'reply\d+',url.fragment):rpid=url.fragment[5:]
    if rpid and (not rpid.isascii() or not rpid.isdecimal() or int(rpid)<=0):raise ValueError('Invalid comment identifier')
    return match[1],rpid


def segment(part):
    kind=part.get('type');data=part.get('data',{})
    if kind=='text':return data.get('text','')
    if kind=='video':
        path=data.get('cached_path')
        return '【视频路径：'+str(path)+'】' if path else ('【视频】（缓存不可用，不能原样转发）' if data.get('cache_status')=='unavailable' else '【视频】')
    if kind=='at':return '@'+str(data.get('qq',''))
    if kind in ('json','xml'):
        return card_text(data.get('data','')) or '['+kind+']'
    return '['+str(kind or '附件')+']'
