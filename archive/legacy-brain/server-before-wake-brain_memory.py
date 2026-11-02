"""Scoped memory loading for expression; no model calls or summarization."""
import json,sqlite3
from pathlib import Path
from message_identity import display
PARTITION=Path('/opt/qq-short-memory/short.sqlite3')
MIDDLE=Path('/opt/qq-middle-memory/middle.sqlite3')

def render(raw):
    if raw.get('direction')=='outgoing':
        result={'time':raw.get('received_at'),'speaker':'自己','text':raw.get('text',''),'images':raw.get('image_paths',[]),**display(raw,raw.get('text',''))}
        if raw.get('delivery_mode')=='group_preview':result['delivery_note']='本群拟发消息，测试时投递给检验者私聊，未实际发入群聊'
        return result
    event=raw.get('raw_event',{});parts=event.get('message',[])
    text=parts if isinstance(parts,str) else ''.join(x.get('data',{}).get('text','') for x in parts if isinstance(x,dict) and x.get('type')=='text')
    media=[] if isinstance(parts,str) else [x.get('type') for x in parts if isinstance(x,dict) and x.get('type')!='text']
    return {'time':raw.get('received_at'),'speaker':raw.get('sender_id'),'text':text,'uninterpreted_media_types':media,**display(raw,text)}

def load_layers(packet):
    scene=packet['scene'];new=packet['new_messages']
    cutoff=max((x['observation'].get('received_at','') for x in new),default='') or '9999'
    scope=(str(scene['account']),scene['chat_type'],str(scene['conversation']))
    recent=[];short_keys=set();long={}
    if PARTITION.exists():
        db=sqlite3.connect(PARTITION.resolve().as_uri()+'?mode=ro',uri=True)
        try:
            db.execute('BEGIN')
            rows=db.execute('SELECT event_key,raw_json FROM entries WHERE account_id=? AND chat_type=? AND conversation_id=? AND event_time<=? ORDER BY event_time,event_key',scope+(cutoff,)).fetchall()
            for key,raw in rows:
                short_keys.add(key);obj=json.loads(raw)
                if obj.get('direction')=='outgoing' and obj.get('phase')!='sent':continue
                recent.append((key,render(obj)))
            requested={'input:'+str(x['id']) for x in packet.get('related_memories',[])}|set(packet.get('related_event_keys',[]))
            for key in requested:
                row=db.execute('SELECT raw_json FROM long_entries WHERE event_key=? AND account_id=? AND chat_type=? AND conversation_id=? AND event_time<=?',(key,)+scope+(cutoff,)).fetchone()
                if row and key not in short_keys:long[key]=json.loads(row[0])
        finally:db.close()
    # Keep this wake's messages exactly once, even if the rolling cache is a tick behind.
    current={'input:'+str(x['id']):x['observation'] for x in new}
    existing={key for key,_ in recent}
    for key,raw in current.items():
        short_keys.add(key);long.pop(key,None)
        if key not in existing:recent.append((key,render(raw)))
    for key,obj in recent:
        if key in current:obj['current']=True
    middle=[];covered=set()
    if MIDDLE.exists():
        db=sqlite3.connect(MIDDLE.resolve().as_uri()+'?mode=ro',uri=True)
        try:
            rows=db.execute("SELECT id,content FROM memories WHERE platform=? AND account_id=? AND chat_type=? AND conversation_id=? AND status='active' AND created_at<=? AND coverage_end<=? ORDER BY coverage_end,id",(scene.get('platform','qq'),)+scope+(cutoff,cutoff)).fetchall()
            for mid,content in rows:
                refs={r[0] for r in db.execute('SELECT event_key FROM source_refs WHERE memory_id=?',(mid,))}
                # A mixed overlapping summary cannot be safely split without rewriting it.
                if not refs or refs&short_keys or refs&covered:continue
                middle.append(content);covered|=refs
        finally:db.close()
    related=[]
    for key,raw in long.items():
        if key in covered:continue
        if raw.get('direction')=='outgoing' and raw.get('phase')!='sent':continue
        related.append(render(raw))
    recent.sort(key=lambda x:(str(x[1].get('time') or ''),x[0]))
    return {'近期记忆':[x[1] for x in recent],'概括记忆':middle,'相关记忆':related}
