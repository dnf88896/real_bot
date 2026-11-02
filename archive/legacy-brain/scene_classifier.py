"""Metadata-only scene classification; does not advertise or execute interfaces."""

def classify_scene(scene):
    if not isinstance(scene,dict):
        return {'category':'unknown','platform':'unknown','conversation_type':'unknown'}
    platform=scene.get('platform')
    platform=platform.strip().lower() if isinstance(platform,str) else ''
    kind=scene.get('chat_type')
    kind=kind.strip().lower() if isinstance(kind,str) else ''
    if platform=='qq':
        if kind in ('private','c2c','私聊'):
            return {'category':'qq_private','platform':'qq','conversation_type':'private'}
        if kind in ('group','群聊'):
            return {'category':'qq_group','platform':'qq','conversation_type':'group'}
        return {'category':'unknown','platform':'qq','conversation_type':'unknown'}
    if platform in ('bilibili','b站','bili'):
        # Only the platform family is classified now; Bilibili delivery is not implemented.
        interaction={'comment':'comment','评论':'comment','private':'private','私信':'private','dynamic':'dynamic','动态':'dynamic'}.get(kind,'unknown')
        return {'category':'bilibili','platform':'bilibili','conversation_type':interaction}
    return {'category':'unknown','platform':'unknown','conversation_type':'unknown'}
