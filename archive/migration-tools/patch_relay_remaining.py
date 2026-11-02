"""Adapt only the new relay copy for preview admission and internal wakes."""

from pathlib import Path


def main():
    path = Path("/opt/astrbot-mouth/relay.py")
    text = path.read_text("utf8")
    if "Idle intention cancelled before delivery" in text:
        return
    old = "packet=json.loads(raw)\n        with db:db.execute('INSERT INTO relay_jobs"
    new = """packet=json.loads(raw)
        if text.strip()=='【保持沉默】':
            with db:db.execute('INSERT INTO relay_jobs(batch_id,status,source_hash,directory) VALUES(?,?,?,?)',(batch,'control_only',digest(text),'[]'))
            continue
        with db:db.execute('INSERT INTO relay_jobs"""
    if old not in text:
        raise RuntimeError("Unexpected relay discovery source")
    text = text.replace(old, new, 1)
    old = "config.get('group_delivery_mode')=='owner_preview' and dest['id'] in [str(x) for x in config.get('preview_groups',[])]"
    new = "config.get('group_delivery_mode')=='owner_preview' and (config.get('preview_all_groups') is True or dest['id'] in [str(x) for x in config.get('preview_groups',[])])"
    if old not in text:
        raise RuntimeError("Unexpected relay preview policy")
    text = text.replace(old, new, 1)
    old = "destination_allowed(dest,config)\n        message,texts,images=compile_parts"
    new = """destination_allowed(dest,config)
        if packet.get('internal_wake'):
            from idle_wake import valid
            if not valid(ROOT,packet['internal_wake']['wake_id']):raise Unavailable('Idle intention cancelled before delivery')
        message,texts,images=compile_parts"""
    if old not in text:
        raise RuntimeError("Unexpected relay delivery source")
    text = text.replace(old, new, 1)
    path.with_suffix(".before-remaining.py").write_text(
        path.read_text("utf8"), encoding="utf8"
    )
    path.write_text(text, encoding="utf8")


if __name__ == "__main__":
    main()
