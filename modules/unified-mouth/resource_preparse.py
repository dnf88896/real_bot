"""Validated media extraction with reversible Unicode source positions.

Only explicit JSON action scopes supply destinations. Legacy unscoped outputs
remain with the semantic marker instead of guessing a recipient.
"""
import bisect,copy,re
from dataclasses import dataclass


def code_ranges(text):
    return [m.span() for m in re.finditer(r'```[\s\S]*?(?:```|$)|`[^`\n]*(?:`|$)',text)]


def excluded_ranges(text):
    from voice_protocol import ranges
    result=code_ranges(text)+[(r['start'],r['end']) for r in ranges(text)]
    result += [m.span() for m in re.finditer(r'(?m)^\s*唤醒意愿\s*[:：].*$',text)]
    return result


def overlaps(start,end,ranges):return any(start<b and end>a for a,b in ranges)


def literal_markers(text,options):
    """Known marker forms, including wrappers. No filesystem or route decisions."""
    pairs={'【':'】','[':']','「':'」','（':'）','(':')'};items=[]
    for left,right in pairs.items():
        pattern=re.escape(left)+r'[^\n'+re.escape(left+right)+r']*'+re.escape(right)
        for m in re.finditer(pattern,text):
            inside=m.group()[1:-1];label=re.match(r'\s*(?:图片路径|视频路径|图片|视频|路径|image|video|file)\s*[:：]\s*',inside,re.I)
            ref=inside[label.end():].strip() if label else inside.strip()
            if len(ref)>=2 and ref[0]==ref[-1] and ref[0] in ('"',"'"):ref=ref[1:-1].strip()
            item={'start':m.start(),'end':m.end(),'ref':ref}
            items.append(item)
    return items


@dataclass
class Prepared:
    original:str
    text:str
    packet:dict
    positions:list
    resources:list

    def restore(self,plan):
        """Map secondary annotations back, split around resources, then reinsert."""
        result={**plan,'segments':[]}
        def chunks(start,end):
            if type(start)is not int or type(end)is not int or not 0<=start<end<=len(self.positions):
                raise ValueError('Invalid secondary source range')
            values=self.positions[start:end];a=previous=values[0];out=[]
            for position in values[1:]:
                if position!=previous+1:out.append((a,previous+1));a=position
                previous=position
            out.append((a,previous+1));return out
        for segment in plan.get('segments',[]):
            for a,b in chunks(segment['start'],segment['end']):result['segments'].append({**segment,'start':a,'end':b})
        if 'wakeups' in plan:
            result['wakeups']=[]
            for wake in plan['wakeups']:
                ranges=chunks(wake['start'],wake['end'])
                if len(ranges)!=1:raise ValueError('Wake intention crosses a program-extracted resource')
                a,b=ranges[0];result['wakeups'].append({**wake,'start':a,'end':b})
        for item in self.resources:
            a,b=item['inner_start'],item['inner_end']
            if item['start']<a:result['segments'].append({'kind':'omit','destination':None,'start':item['start'],'end':a})
            result['segments'].append({'kind':item['kind'],'destination':item['destination'],'start':a,'end':b})
            if b<item['end']:result['segments'].append({'kind':'omit','destination':None,'start':b,'end':item['end']})
        result['segments'].sort(key=lambda s:(s['start'],s['end']))
        return result


def prepare(text,packet,contacts,resolve_sticker,check_image,check_video):
    from json_protocol import destinations
    scopes=packet.get('output_scopes') or []
    identity=Prepared(text,text,packet,list(range(len(text))),[])
    if not scopes:return identity
    protected=excluded_ranges(text)
    options=packet.get('image_options',[])+packet.get('video_options',[])
    allowed={o['path']:o for o in options};accepted=[];wrapped=literal_markers(text,options)
    def accept(start,end,ref):
        if overlaps(start,end,protected) or overlaps(start,end,[(r['start'],r['end']) for r in accepted]):return
        owners=[s for s in scopes if s['start']<=start<end<=s['end']]
        if len(owners)!=1:return
        try:
            route=next(iter(destinations(owners[0],contacts,packet['scene'])))
            dest=dict(zip(('platform','account','chat_type','id'),route))
            if ref in allowed:
                kind='video' if allowed[ref].get('media_type')=='video' else 'image'
                checked=check_video(ref,options) if kind=='video' else check_image(ref,options)
                if checked!=ref:return
            else:
                path=resolve_sticker(ref,options)
                if not any(o.get('kind')=='sticker' and o['path']==path for o in options):return
                check_image(path,options);kind='sticker'
            inner=text.find(ref,start,end)
            if inner<0:return
            accepted.append({'start':start,'end':end,'inner_start':inner,'inner_end':inner+len(ref),
                             'kind':kind,'destination':dest})
        except (ValueError,OSError):
            # Unknown, ambiguous, invalid or missing resources stay visible to
            # the secondary stage; never swallow an unvalidated marker.
            return
    for item in wrapped:accept(item['start'],item['end'],item['ref'])
    wrapper_spans=[(m['start'],m['end']) for m in wrapped]
    for path in sorted(allowed,key=len,reverse=True):
        offset=0
        while (start:=text.find(path,offset))>=0:
            end=start+len(path);offset=end
            if overlaps(start,end,wrapper_spans):continue
            before=text[start-1] if start else '';after=text[end] if end<len(text) else ''
            if before and (before.isascii() and (before.isalnum() or before in '/_.-')):continue
            if after and (after.isascii() and (after.isalnum() or after in '/_.-')):continue
            outer_start,outer_end=start,end
            if before in ('"',"'") and after==before:
                outer_start-=1;outer_end+=1
            prefix=re.search(r'(?:图片路径|视频路径|图片|视频|路径|image|video|file)\s*[:：]\s*$',text[:outer_start],re.I)
            if prefix:
                if prefix.start()>0 and text[prefix.start()-1] in '【[「（(':continue
                outer_start=prefix.start()
            accept(outer_start,outer_end,path)
    for m in re.finditer(r'表情包\s*[0-9]+',text):
        if overlaps(*m.span(),wrapper_spans):continue
        before=text[m.start()-1] if m.start() else '';after=text[m.end()] if m.end()<len(text) else ''
        if (before and not before.isspace()) or (after and not after.isspace()):continue
        accept(*m.span(),m.group())
    if not accepted:return identity
    accepted.sort(key=lambda r:r['start']);removed=bytearray(len(text))
    for item in accepted:removed[item['start']:item['end']]=b'\x01'*(item['end']-item['start'])
    positions=[i for i in range(len(text)) if not removed[i]]
    compact=''.join(text[i] for i in positions);updated=copy.deepcopy(packet)
    updated['output_scopes']=[{**s,'start':bisect.bisect_left(positions,s['start']),'end':bisect.bisect_left(positions,s['end'])} for s in scopes]
    updated['program_resource_slots']=[{'position':bisect.bisect_left(positions,r['start']),'kind':r['kind'],'destination':r['destination']} for r in accepted]
    return Prepared(text,compact,updated,positions,accepted)


def check_text(text,start,end,options):
    """Reject resource delimiters leaked into text, without stripping normal brackets."""
    protected=code_ranges(text)
    for m in re.finditer(r'/opt/|base64://|【表情包\s*[0-9]+】|【语音：',text[start:end]):
        a,b=start+m.start(),start+m.end()
        if not any(x<=a<b<=y for x,y in protected):raise ValueError('Resource marker leaked into text')
    refs={o.get('path') for o in options}|{o.get('description') for o in options if o.get('kind')=='sticker'}
    for marker in literal_markers(text,options):
        if overlaps(marker['start'],marker['end'],protected):continue
        if marker['ref'] not in refs and not re.search(r'表情包\s*[0-9]+',marker['ref']):continue
        if overlaps(start,end,[(marker['start'],marker['end'])]):
            raise ValueError('Resource wrapper leaked into text; classify the complete marker')
