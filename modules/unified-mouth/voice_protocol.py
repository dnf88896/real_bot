"""Deterministic voice spans; synthesis and transport are separate adapters."""
import re

OPEN='【语音：'
DESCRIPTION='QQ和微信的私信、群聊可以用语音表达，将要说的原话写成【语音：正文】，可与普通文字、图片或表情混排，按出现顺序表达。每段最多1000字，标记必须完整闭合且正文非空，不嵌套语音标记；声音和语速由外部配置。B站和小黑盒暂用普通文字。微信通过录音发送，单段合成音频最多50秒，超长时请缩短或分段。仅展示格式示例时放在代码中。'

def ranges(text):
    excluded=[m.span() for m in re.finditer(r'```[\s\S]*?(?:```|$)|`[^`\n]*(?:`|$)',text)]
    result=[];offset=0
    while (start:=text.find(OPEN,offset))>=0:
        if any(a<=start<b for a,b in excluded):offset=start+len(OPEN);continue
        body=start+len(OPEN);end=text.find('】',body)
        if end<0:raise ValueError('语音标记未闭合，请使用【语音：正文】')
        if OPEN in text[body:end]:raise ValueError('语音标记不能嵌套')
        if not text[body:end].strip():raise ValueError('语音正文不能为空')
        result.append({'start':start,'end':end+1,'body_start':body,'body_end':end})
        offset=end+1
    return result

def expand(text,deliveries,ignored=()):
    """Keep recipients and exact source indices; strip only protocol delimiters."""
    blocks=[b for b in ranges(text) if not any(w['start']<=b['start']<b['end']<=w['end'] for w in ignored)];seen=set();result=[]
    for delivery in deliveries:
        parts=[]
        for part in delivery['parts']:
            matches=[b for b in blocks if b['start']<part['end'] and b['end']>part['start']]
            if not matches:parts.append(dict(part));continue
            if part['kind']!='text' or any(not part['start']<=b['start']<b['end']<=part['end'] for b in matches):
                raise ValueError('完整语音标记须作为同一段text交给程序解析，不能拆分或改标其他组件')
            offset=part['start']
            for block in matches:
                if text[offset:block['start']].strip():parts.append({**part,'start':offset,'end':block['start']})
                parts.append({'kind':'voice','start':block['body_start'],'end':block['body_end']})
                seen.add((block['start'],block['end']));offset=block['end']
            if text[offset:part['end']].strip():parts.append({**part,'start':offset})
        result.append({**delivery,'parts':parts})
    if any((b['start'],b['end']) not in seen for b in blocks):
        raise ValueError('语音标记被遗漏，必须保留其原始正文与收件对象')
    return result
