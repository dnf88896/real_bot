"""Local image references; neither brain nor expression API receives pixels."""
import base64,json
from pathlib import Path
LIBRARY=Path('/opt/astrbot-mouth/images')
INCOMING=Path('/opt/astrbot-mouth/incoming-images')

def checked_image(value):
    if not isinstance(value,str):raise ValueError('Image path must be text')
    p=Path(value).resolve();root=LIBRARY.resolve()
    variant_root=root/'variants'
    # Exactly one numeric category directory; resolved paths cannot escape it.
    category=p.parent.name
    pooled=(p.parent.parent==variant_root and category.isascii() and category.isdecimal()
            and 1<=int(category)<=24 and category==f'{int(category):02d}')
    if (p.parent not in (root,INCOMING.resolve()) and not pooled) or not p.is_file():raise ValueError('Image outside allowed directories or missing')
    if p.suffix.lower() not in ('.jpg','.jpeg','.png','.gif','.webp'):raise ValueError('Unsupported image extension')
    if not 0<p.stat().st_size<=4*1024*1024:raise ValueError('Image exceeds size limit')
    with p.open('rb') as f:head=f.read(16)
    if not (head.startswith(b'\xff\xd8\xff') or head.startswith(b'\x89PNG\r\n\x1a\n') or head.startswith((b'GIF87a',b'GIF89a')) or (head.startswith(b'RIFF') and head[8:12]==b'WEBP')):raise ValueError('Not a recognized image')
    return p

def catalog():
    file=LIBRARY/'catalog.json'
    if not file.exists():return []
    result=[]
    for row in json.loads(file.read_text('utf8')):
        p=checked_image(str(LIBRARY/row['file']))
        if p.parent!=LIBRARY.resolve():raise ValueError('Sticker catalog points outside library')
        result.append({'path':str(p),'description':str(row.get('short',row.get('description','')))[:500],'kind':'sticker'})
    return result

def select_images(paths,offered):
    if not isinstance(paths,list) or len(paths)>2:raise ValueError('At most two image paths allowed')
    permitted={x['path']:x for x in offered};result=[]
    for value in paths:
        p=str(checked_image(value))
        if p not in permitted:raise ValueError('Image not offered for this reply')
        if p not in [x['path'] for x in result]:result.append(permitted[p])
    return result

def segments_for(paths,offered):
    return [{'type':'image','data':{'file':'base64://'+base64.b64encode(checked_image(x['path']).read_bytes()).decode('ascii')}} for x in select_images(paths,offered)]
