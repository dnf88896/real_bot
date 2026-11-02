"""Program-only sticker pools; one durable selection per delivery and part."""
import json,secrets,time
from pathlib import Path
import image_library


def candidates(canonical):
    canonical=image_library.checked_image(str(canonical))
    # Only catalog-style aliases in the library root denote a sticker category.
    if canonical.parent!=image_library.LIBRARY.resolve() or not canonical.stem.isascii() or not canonical.stem.isdecimal():
        return [str(canonical)]
    directory=image_library.LIBRARY/'variants'/canonical.stem
    valid=[]
    if directory.is_dir():
        manifest=directory/'active-files.json'
        if manifest.exists():
            names=json.loads(manifest.read_text('utf8'))
            if not isinstance(names,list) or any(not isinstance(n,str) or Path(n).name!=n for n in names):
                raise ValueError('Invalid sticker pool file list')
            paths=[directory/n for n in names]
        else:
            paths=sorted(directory.iterdir())
        for path in paths:
            try:
                resolved=image_library.checked_image(str(path))
                if resolved.parent==directory.resolve() and str(resolved) not in valid:
                    valid.append(str(resolved))
            except (ValueError,OSError):
                continue
    return valid or [str(canonical)]


def choose(db,delivery_id,slot,canonical):
    category=str(image_library.checked_image(str(canonical)))
    db.execute('''CREATE TABLE IF NOT EXISTS sticker_choices(
      delivery_id TEXT,slot INTEGER,category TEXT,path TEXT,created REAL,
      PRIMARY KEY(delivery_id,slot))''')
    row=db.execute('SELECT category,path FROM sticker_choices WHERE delivery_id=? AND slot=?',(delivery_id,slot)).fetchone()
    if row:
        if row[0]!=category:raise ValueError('Sticker category changed for a durable delivery')
        return str(image_library.checked_image(row[1]))
    # Already-rendered pre-migration messages must retain their original image.
    rendered=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='delivery_renderings'").fetchone()
    if rendered and db.execute('SELECT 1 FROM delivery_renderings WHERE delivery_id=?',(delivery_id,)).fetchone():
        return category
    options=candidates(category)
    last=db.execute('SELECT path FROM sticker_choices WHERE category=? ORDER BY rowid DESC LIMIT 1',(category,)).fetchone()
    if len(options)>1 and last:options=[p for p in options if p!=last[0]] or options
    selected=secrets.choice(options)
    with db:
        db.execute('INSERT OR IGNORE INTO sticker_choices VALUES(?,?,?,?,?)',(delivery_id,slot,category,selected,time.time()))
    row=db.execute('SELECT category,path FROM sticker_choices WHERE delivery_id=? AND slot=?',(delivery_id,slot)).fetchone()
    if row[0]!=category:raise ValueError('Sticker category changed for a durable delivery')
    return str(image_library.checked_image(row[1]))
