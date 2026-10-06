"""Global playlists: content defined once in the hub and deployed to many CARACAL nodes.

A deployment queues one import_playlist command per node (append or replace). Media are stored in the hub
(pinned, so the temporary-file cleanup keeps them) and downloaded by each agent with a checksum check.
"""
import json
import time

from fastapi import APIRouter, HTTPException, Request

from .core import audit, current_user, db, new_batch, queue_command, refresh_pin
from .devices import COLLECTION_KIND, MEDIA_KINDS, build, grafana_config

router = APIRouter()
ITEM_KINDS = ('web', 'image', 'video', COLLECTION_KIND)


async def _json(r: Request):
    try:
        d = await r.json()
    except ValueError:
        raise HTTPException(400, 'invalid_json')
    if not isinstance(d, dict):
        raise HTTPException(400, 'invalid_json')
    return d


def _text(v, limit=500):
    return str(v if v is not None else '').strip()[:limit]


def _playlist(c, pid):
    row = c.execute('SELECT * FROM global_playlists WHERE id=?', (pid,)).fetchone()
    if not row:
        raise HTTPException(404, 'not_found')
    return dict(row)


def _items(c, pid):
    out = [dict(x) for x in c.execute(
        'SELECT i.*, f.name file_name, f.size file_size, f.sha256 FROM global_playlist_items i '
        'LEFT JOIN files f ON f.id=i.file_id WHERE playlist_id=? ORDER BY position, id', (pid,))]
    for it in out:
        if it['kind'] == COLLECTION_KIND:
            it.update(grafana_config(it['source']))
    return out


def _touch(c, pid, u):
    c.execute('UPDATE global_playlists SET updated=?, updated_by=? WHERE id=?', (time.time(), u['username'], pid))


def _validate_item(c, d, kind):
    """Normalized item fields following the CARACAL rules (>= 5 s, videos loop; scale 0.5 to 3.0)."""
    if kind not in ITEM_KINDS:
        raise HTTPException(400, 'unsupported_file')
    item = {'name': _text(d.get('name'), 200), 'source': '', 'file_id': '', 'scale': 1.0}
    try:
        item['duration'] = int(float(d.get('duration', 60 if kind == COLLECTION_KIND else 30 if kind == 'web' else 15)))
        item['scale'] = float(str(d.get('scale') or 1).replace(',', '.'))
    except (TypeError, ValueError):
        raise HTTPException(400, 'invalid_duration')
    if item['duration'] < 5:
        raise HTTPException(400, 'invalid_duration')
    if not 0.5 <= item['scale'] <= 3:
        raise HTTPException(400, 'invalid_scale')
    if kind == COLLECTION_KIND:
        # stored like on the node: {"grafana_url", "tag", "kiosk"} as JSON in 'source'
        cfg = {**grafana_config(d.get('source')), **{k: d[k] for k in ('grafana_url', 'tag', 'kiosk') if k in d}}
        url, tag = _text(cfg.get('grafana_url'), 1000).rstrip('/'), _text(cfg.get('tag'), 200)
        if not url.lower().startswith(('http://', 'https://')):
            raise HTTPException(400, 'invalid_url')
        if not tag:
            raise HTTPException(400, 'tag_required')
        if not item['name']:
            raise HTTPException(400, 'name_required')
        item['source'] = json.dumps({'grafana_url': url, 'tag': tag, 'kiosk': bool(cfg.get('kiosk', True))},
                                    ensure_ascii=False)
    elif kind == 'web':
        src = _text(d.get('source'), 4000)
        if not src.lower().startswith(('http://', 'https://')):
            raise HTTPException(400, 'invalid_url')
        item['source'] = src
        item['name'] = item['name'] or src
    else:
        f = c.execute('SELECT * FROM files WHERE id=?', (_text(d.get('file_id'), 64),)).fetchone()
        if not f or f['kind'] != kind:
            raise HTTPException(400, 'file_not_found')
        item['file_id'] = f['id']
        item['name'] = item['name'] or f['name']
        c.execute('UPDATE files SET pinned=1 WHERE id=?', (f['id'],))
    return item


def _deployments(c, pid):
    out = []
    for dep in c.execute('SELECT * FROM global_deployments WHERE playlist_id=? ORDER BY id DESC LIMIT 20', (pid,)):
        dep = dict(dep)
        states = [dict(x) for x in c.execute(
            "SELECT c.device_id, d.name device_name, c.state, c.result FROM commands c "
            "LEFT JOIN devices d ON d.id=c.device_id WHERE c.batch=? AND c.action='import_playlist'", (dep['batch'],))]
        dep['targets'] = json.loads(dep.pop('targets_json') or '[]')
        dep['results'] = states
        out.append(dep)
    return out


@router.get('/api/global-playlists')
def list_playlists(r: Request):
    current_user(r)
    with db() as c:
        rows = [dict(x) for x in c.execute('SELECT * FROM global_playlists ORDER BY name COLLATE NOCASE')]
        for p in rows:
            p['items'] = c.execute('SELECT COUNT(*) FROM global_playlist_items WHERE playlist_id=?',
                                   (p['id'],)).fetchone()[0]
            last = c.execute('SELECT created, targets_json, mode FROM global_deployments WHERE playlist_id=? '
                             'ORDER BY id DESC LIMIT 1', (p['id'],)).fetchone()
            p['last_deployment'] = {'created': last['created'], 'targets': len(json.loads(last['targets_json'])),
                                    'mode': last['mode']} if last else None
    return rows


@router.post('/api/global-playlists')
async def create_playlist(r: Request):
    u = current_user(r, 'content')
    d = await _json(r)
    name = _text(d.get('name'), 120)
    if not name:
        raise HTTPException(400, 'name_required')
    now = time.time()
    with db() as c:
        pid = c.execute('INSERT INTO global_playlists(name, description, created, updated, updated_by) '
                        'VALUES(?,?,?,?,?)', (name, _text(d.get('description'), 1000), now, now,
                                              u['username'])).lastrowid
    audit(u, 'global.create', str(pid), {'name': name})
    return {'id': pid}


@router.post('/api/global-playlists/from-device')
async def playlist_from_device(r: Request):
    """New global playlist with the web pages of a node's playlist (media and Grafana tags stay on the node)."""
    u = current_user(r, 'content')
    d = await _json(r)
    with db() as c:
        row = c.execute('SELECT * FROM devices WHERE id=?', (_text(d.get('device_id'), 64),)).fetchone()
        if not row:
            raise HTTPException(404, 'device_not_found')
        device = build(row, full=True)
        name = _text(d.get('name'), 120) or device['name']
        now = time.time()
        pid = c.execute('INSERT INTO global_playlists(name, description, created, updated, updated_by) '
                        'VALUES(?,?,?,?,?)', (name, '', now, now, u['username'])).lastrowid
        skipped = []
        for pos, a in enumerate(device['assets']):
            web = a['kind'] == 'web' and str(a.get('source', '')).lower().startswith(('http://', 'https://'))
            if not web and a['kind'] != COLLECTION_KIND:
                skipped.append(a['name'])
                continue
            c.execute('INSERT INTO global_playlist_items(playlist_id, position, kind, name, source, duration, scale) '
                      'VALUES(?,?,?,?,?,?,?)', (pid, pos, a['kind'], a['name'], a['source'],
                                                max(5, int(a.get('duration') or 30)),
                                                min(3.0, max(0.5, float(a.get('scale') or 1)))))
    audit(u, 'global.create', str(pid), {'name': name, 'from_device': row['id']})
    return {'id': pid, 'skipped': skipped}


@router.get('/api/global-playlists/{pid}')
def get_playlist(pid: int, r: Request):
    current_user(r)
    with db() as c:
        p = _playlist(c, pid)
        p['items'] = _items(c, pid)
        p['deployments'] = _deployments(c, pid)
    return p


@router.patch('/api/global-playlists/{pid}')
async def edit_playlist(pid: int, r: Request):
    u = current_user(r, 'content')
    d = await _json(r)
    with db() as c:
        p = _playlist(c, pid)
        name = _text(d.get('name', p['name']), 120)
        if not name:
            raise HTTPException(400, 'name_required')
        c.execute('UPDATE global_playlists SET name=?, description=? WHERE id=?',
                  (name, _text(d.get('description', p['description']), 1000), pid))
        _touch(c, pid, u)
    audit(u, 'global.edit', str(pid), {'name': name})
    return {'ok': True}


@router.delete('/api/global-playlists/{pid}')
def delete_playlist(pid: int, r: Request):
    u = current_user(r, 'content')
    with db() as c:
        p = _playlist(c, pid)
        files = [r['file_id'] for r in c.execute(
            "SELECT DISTINCT file_id FROM global_playlist_items WHERE playlist_id=? AND file_id != ''", (pid,))]
        c.execute('DELETE FROM global_playlist_items WHERE playlist_id=?', (pid,))
        c.execute('DELETE FROM global_deployments WHERE playlist_id=?', (pid,))
        c.execute('DELETE FROM global_playlists WHERE id=?', (pid,))
        for fid in files:  # media no longer used fall back to the normal retention
            refresh_pin(c, fid)
    audit(u, 'global.delete', str(pid), {'name': p['name']})
    return {'ok': True}


@router.post('/api/global-playlists/{pid}/items')
async def add_item(pid: int, r: Request):
    u = current_user(r, 'content')
    d = await _json(r)
    with db() as c:
        _playlist(c, pid)
        item = _validate_item(c, d, _text(d.get('kind'), 16))
        pos = c.execute('SELECT COALESCE(MAX(position), -1) + 1 FROM global_playlist_items WHERE playlist_id=?',
                        (pid,)).fetchone()[0]
        iid = c.execute('INSERT INTO global_playlist_items(playlist_id, position, kind, name, source, duration, scale, '
                        'file_id) VALUES(?,?,?,?,?,?,?,?)', (pid, pos, d['kind'], item['name'], item['source'],
                                                             item['duration'], item['scale'],
                                                             item['file_id'])).lastrowid
        _touch(c, pid, u)
    audit(u, 'global.item_add', str(pid), {'name': item['name'], 'kind': d['kind']})
    return {'id': iid}


@router.patch('/api/global-playlists/{pid}/items/{iid}')
async def edit_item(pid: int, iid: int, r: Request):
    u = current_user(r, 'content')
    d = await _json(r)
    with db() as c:
        row = c.execute('SELECT * FROM global_playlist_items WHERE id=? AND playlist_id=?', (iid, pid)).fetchone()
        if not row:
            raise HTTPException(404, 'not_found')
        merged = {**dict(row), **{k: d[k] for k in ('name', 'source', 'duration', 'scale', 'grafana_url', 'tag',
                                                    'kiosk') if k in d}}
        item = _validate_item(c, merged, row['kind'])
        c.execute('UPDATE global_playlist_items SET name=?, source=?, duration=?, scale=? WHERE id=?',
                  (item['name'], item['source'], item['duration'], item['scale'], iid))
        _touch(c, pid, u)
    audit(u, 'global.item_edit', str(pid), {'item': iid, 'name': item['name']})
    return {'ok': True}


@router.delete('/api/global-playlists/{pid}/items/{iid}')
def delete_item(pid: int, iid: int, r: Request):
    u = current_user(r, 'content')
    with db() as c:
        row = c.execute('SELECT * FROM global_playlist_items WHERE id=? AND playlist_id=?', (iid, pid)).fetchone()
        if not row:
            raise HTTPException(404, 'not_found')
        c.execute('DELETE FROM global_playlist_items WHERE id=?', (iid,))
        if row['file_id']:
            refresh_pin(c, row['file_id'])
        _touch(c, pid, u)
    audit(u, 'global.item_delete', str(pid), {'item': iid, 'name': row['name']})
    return {'ok': True}


@router.put('/api/global-playlists/{pid}/order')
async def reorder_items(pid: int, r: Request):
    u = current_user(r, 'content')
    d = await _json(r)
    with db() as c:
        _playlist(c, pid)
        ids = [x['id'] for x in _items(c, pid)]
        order = [int(x) for x in d.get('order') or [] if str(x).isdigit() and int(x) in ids]
        order += [x for x in ids if x not in order]
        c.executemany('UPDATE global_playlist_items SET position=? WHERE id=?', list(enumerate(order)))
        _touch(c, pid, u)
    audit(u, 'global.reorder', str(pid))
    return {'ok': True}


@router.post('/api/global-playlists/{pid}/deploy')
async def deploy(pid: int, r: Request):
    u = current_user(r, 'content')
    d = await _json(r)
    mode = 'replace' if d.get('mode') == 'replace' else 'append'
    targets = list(dict.fromkeys(str(x) for x in d.get('target_ids') or []))
    if not targets:
        raise HTTPException(400, 'no_devices')
    batch = new_batch()
    warnings = []
    with db() as c:
        p = _playlist(c, pid)
        items = _items(c, pid)
        if not items:
            raise HTTPException(400, 'nothing_to_copy')
        payload_items = []
        for it in items:
            entry = {'type': 'asset', 'kind': it['kind'], 'name': it['name'], 'duration': it['duration'],
                     'scale': it['scale']}
            if it['kind'] == COLLECTION_KIND:
                entry.update(type='collection', **grafana_config(it['source']))
            elif it['kind'] == 'web':
                entry['source'] = it['source']
            else:
                if not it['sha256']:
                    raise HTTPException(400, 'file_not_found')
                entry.update(file_id=it['file_id'], sha256=it['sha256'], filename=it['file_name'])
            payload_items.append(entry)
        has_media = any(it['kind'] in MEDIA_KINDS for it in items)
        has_tags = any(it['kind'] == COLLECTION_KIND for it in items)
        for t in targets:
            row = c.execute('SELECT * FROM devices WHERE id=?', (t,)).fetchone()
            if not row:
                raise HTTPException(404, 'device_not_found')
            caps = build(row)['capabilities']
            if (has_media and caps.get('upload') is False) or (has_tags and caps.get('add_grafana_tag') is False):
                warnings.append(row['name'])
            queue_command(c, t, 'import_playlist', {'items': payload_items, 'mode': mode,
                                                    'global_playlist': p['name']}, u['username'], batch)
        c.execute('INSERT INTO global_deployments(playlist_id, batch, mode, targets_json, username, created) '
                  'VALUES(?,?,?,?,?,?)', (pid, batch, mode, json.dumps(targets), u['username'], time.time()))
    audit(u, 'global.deploy', str(pid), {'name': p['name'], 'targets': targets, 'mode': mode})
    return {'ok': True, 'batch': batch, 'media_unsupported': warnings}
