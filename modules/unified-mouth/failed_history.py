"""Failed generation is audit data, never a message that the bot has said."""
if __package__:
    from .memory_engine import signature
else:
    from memory_engine import signature


def exclude_final(messages):
    """The native runner appends its final assistant before invoking our hook."""
    for message in reversed(messages):
        if message.role=='assistant':
            message._no_save=True
            return True
    return False


def text(message):
    content=message.get('content','')
    if isinstance(content,str):return content
    return ''.join(part.get('text','') for part in content or [] if isinstance(part,dict) and part.get('type')=='text')


def prune(history,positions,failed):
    """Remove only matching source-linked failures; never infer identity by text."""
    if len(history)!=len(positions) or any(signature(m)!=p.get('signature') for m,p in zip(history,positions)):
        return history,positions,[],False
    kept=[];refs=[];removed=[]
    for message,position in zip(history,positions):
        matches=[ref for ref in position.get('refs',[]) if ref in failed]
        if message.get('role')=='assistant' and matches and any(text(message)==failed[ref] for ref in matches):
            removed.extend(matches)
        else:
            kept.append(message);refs.append(position)
    return kept,refs,removed,True
