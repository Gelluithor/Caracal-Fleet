"""Node templates: how a CARACAL node should look, defined once and applied to nodes.

A template holds any of these parts (a part that is not set is left alone on the node):

- playlist: a global playlist and the mode (append / replace)
- notifications: the settings and the look of on-screen notifications, and the picture of the look
- overlay: the countdown bar of the current page
- group and location in Fleet

Applying a template queues the commands of its parts for each node (the same commands the device page sends); a part
the node does not support yet (an older agent or CARACAL) is skipped and reported. New nodes get a template from their
zero-touch card, the one-command installation or the SSH installation: the hub remembers it at the enrollment
(devices.pending_template) and applies it as soon as the node reports what it supports.
"""
import json
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from .core import FILES, audit, current_user, db, new_batch, queue_command
from .devices import build, notifications_of, overlay_of

router = APIRouter()
NOTIFY_KEYS = ('enabled', 'position', 'duration', 'scale', 'max_queue', 'sound', 'volume', 'history_max',
               'history_days', 'style')
PLAYLIST_MODES = ('append', 'replace')


async def _json(r: Request):
    try:
        d = await r.json()
    except ValueError:
        raise HTTPException(400, 'invalid_json')
    if not isinstance(d, dict):
        raise HTTPException(400, 'invalid_json')
    return d


def _text(v, limit):
    return str(v if v is not None else '').strip()[:limit]


def _row(c, tid):
    row = c.execute('SELECT * FROM node_templates WHERE id=?', (tid,)).fetchone()
    if not row:
        raise HTTPException(404, 'not_found')
    return row


def _public(c, row):
    data = json.loads(row['data_json'] or '{}')
    t = {'id': row['id'], 'name': row['name'], 'description': row['description'], 'created': row['created'],
         'updated': row['updated'], 'updated_by': row['updated_by'], **data, 'image': None}
    if row['image_file_id']:
        f = c.execute('SELECT name, size, sha256 FROM files WHERE id=?', (row['image_file_id'],)).fetchone()
        t['image'] = dict(f) if f else None
    if t.get('playlist_id'):
        p = c.execute('SELECT name, (SELECT COUNT(*) FROM global_playlist_items WHERE playlist_id=g.id) items '
                      'FROM global_playlists g WHERE id=?', (t['playlist_id'],)).fetchone()
        t['playlist'] = dict(p) if p else None
    t['devices'] = c.execute('SELECT COUNT(*) FROM devices WHERE pending_template=?', (row['id'],)).fetchone()[0]
    return t


def _clean_parts(c, d, current):
    """Template parts from a request; keys that are not sent stay as they are, null removes a part."""
    from .main import validate_notify_settings, validate_overlay   # main imports this module
    out = dict(current)
    if 'playlist_id' in d:
        if d['playlist_id'] in (None, '', 0):
            out.pop('playlist_id', None)
        else:
            try:
                pid = int(d['playlist_id'])
            except (TypeError, ValueError):
                raise HTTPException(400, 'invalid_value')
            if not c.execute('SELECT 1 FROM global_playlists WHERE id=?', (pid,)).fetchone():
                raise HTTPException(400, 'playlist_not_found')
            out['playlist_id'] = pid
    if 'playlist_mode' in d:
        if d['playlist_mode'] not in PLAYLIST_MODES:
            raise HTTPException(400, 'invalid_value')
        out['playlist_mode'] = d['playlist_mode']
    if 'notify' in d:
        if not d['notify']:
            out.pop('notify', None)
        elif isinstance(d['notify'], dict):
            out['notify'] = validate_notify_settings({k: v for k, v in d['notify'].items() if k in NOTIFY_KEYS})
        else:
            raise HTTPException(400, 'invalid_value')
    if 'overlay' in d:
        if not d['overlay']:
            out.pop('overlay', None)
        elif isinstance(d['overlay'], dict):
            out['overlay'] = validate_overlay(d['overlay'])
        else:
            raise HTTPException(400, 'invalid_value')
    for key in ('group', 'location'):
        if key in d:
            value = _text(d[key], 120)
            if value:
                out[key] = value
            else:
                out.pop(key, None)
    return out


def _save(c, tid, u, **cols):
    sets = ', '.join(f'{k}=?' for k in cols)
    c.execute(f'UPDATE node_templates SET {sets}, updated=?, updated_by=? WHERE id=?',
              (*cols.values(), time.time(), u['username'], tid))


# ---------------------------------------------------------------- applying

def commands_of(c, tpl):
    """(action, payload) of the template's parts, in the order they are sent."""
    data = json.loads(tpl['data_json'] or '{}')
    out = []
    if tpl['image_file_id']:
        out.append(('notify_image', {'file_id': tpl['image_file_id']}))
    if data.get('notify'):
        out.append(('notify_settings', data['notify']))
    if data.get('overlay'):
        out.append(('overlay_settings', data['overlay']))
    if data.get('playlist_id'):
        from .playlists import deploy_items
        items = deploy_items(c, data['playlist_id'], missing_ok=True)
        if items:
            name = c.execute('SELECT name FROM global_playlists WHERE id=?', (data['playlist_id'],)).fetchone()
            mode = data.get('playlist_mode') or 'replace'
            # replace: the template defines the whole playlist, its Grafana collections too (applying it again does
            # not add them twice)
            out.append(('import_playlist', {'items': items, 'mode': mode, 'global_playlist': name['name'] if name else '',
                                            'replace_collections': mode == 'replace' and any(
                                                it.get('type') == 'collection' for it in items)}))
    return out


def apply_to(c, tpl, row, username, batch):
    """Queue the template's commands for one node: {'device_id', 'queued': [actions], 'skipped': [{action, reason}]}."""
    from .main import _ensure_org, validate_command
    data = json.loads(tpl['data_json'] or '{}')
    result = {'device_id': row['id'], 'device_name': row['name'], 'queued': [], 'skipped': []}
    if data.get('group') or data.get('location'):
        c.execute("UPDATE devices SET device_group=COALESCE(NULLIF(?, ''), device_group), "
                  "location=COALESCE(NULLIF(?, ''), location) WHERE id=?",
                  (data.get('group', ''), data.get('location', ''), row['id']))
        _ensure_org(c, data.get('group', ''), data.get('location', ''))
        result['queued'].append('organization')
    for action, payload in commands_of(c, tpl):
        try:
            # import_playlist is internal (not sent from the UI): queued like a global playlist deployment
            if action != 'import_playlist':
                payload = validate_command(c, row, action, payload)
        except HTTPException as e:
            result['skipped'].append({'action': action, 'reason': str(e.detail)})
            continue
        queue_command(c, row['id'], action, payload, username, batch)
        result['queued'].append(action)
    return result


def apply_pending(c, did):
    """The template a new node got at its installation, applied once the node reports what it supports."""
    row = c.execute('SELECT * FROM devices WHERE id=?', (did,)).fetchone()
    if not row or not row['pending_template']:
        return None
    status = json.loads(row['status_json'] or '{}')
    if not isinstance(status.get('capabilities'), dict) or not status.get('api_ok', True):
        return None   # the node's API is not up yet: the next heartbeat tries again
    tpl = c.execute('SELECT * FROM node_templates WHERE id=?', (row['pending_template'],)).fetchone()
    c.execute('UPDATE devices SET pending_template=NULL WHERE id=?', (did,))
    if not tpl:
        return None
    result = apply_to(c, tpl, row, f'template:{tpl["name"]}'[:64], new_batch())
    detail = {'template': tpl['name'], 'template_id': tpl['id'], 'automatic': True, 'queued': result['queued'],
              'skipped': result['skipped']}
    c.execute('INSERT INTO audit(username, action, target, detail, created) VALUES(?,?,?,?,?)',
              ('system', 'template.apply', did, json.dumps(detail, ensure_ascii=False), time.time()))
    return result


def template_id(value):
    """A template chosen for a new node (SD card, installation): its id, None when not chosen; 400 when unknown."""
    if value in (None, '', 0, '0'):
        return None
    try:
        tid = int(value)
    except (TypeError, ValueError):
        raise HTTPException(400, 'template_not_found')
    with db() as c:
        if not c.execute('SELECT 1 FROM node_templates WHERE id=?', (tid,)).fetchone():
            raise HTTPException(400, 'template_not_found')
    return tid


# ---------------------------------------------------------------- API

@router.get('/api/templates')
def list_templates(r: Request):
    current_user(r)
    with db() as c:
        return [_public(c, x) for x in c.execute('SELECT * FROM node_templates ORDER BY name COLLATE NOCASE')]


@router.post('/api/templates')
async def create_template(r: Request):
    u = current_user(r, 'manage')
    d = await _json(r)
    name = _text(d.get('name'), 120)
    if not name:
        raise HTTPException(400, 'name_required')
    now = time.time()
    with db() as c:
        data = _clean_parts(c, d, {})
        tid = c.execute('INSERT INTO node_templates(name, description, data_json, created, updated, updated_by) '
                        'VALUES(?,?,?,?,?,?)', (name, _text(d.get('description'), 1000), json.dumps(data), now, now,
                                                u['username'])).lastrowid
    audit(u, 'template.create', str(tid), {'name': name})
    return {'id': tid}


@router.post('/api/templates/from-device')
async def template_from_device(r: Request):
    """A template with what a node has now: notifications and their look (with the picture when it was sent through
    Fleet), the countdown bar, group and location, and optionally its playlist as a new global playlist."""
    u = current_user(r, 'manage')
    d = await _json(r)
    with db() as c:
        row = c.execute('SELECT * FROM devices WHERE id=?', (_text(d.get('device_id'), 64),)).fetchone()
        if not row:
            raise HTTPException(404, 'device_not_found')
        status = json.loads(row['status_json'] or '{}')
        device = build(row, full=True)
        name = _text(d.get('name'), 120) or device['name']
        data = {}
        n = notifications_of(status)
        if n and n['settings']:
            data['notify'] = {k: v for k, v in n['settings'].items() if k in NOTIFY_KEYS}
        overlay = overlay_of(status)
        if overlay:
            data['overlay'] = overlay
        for key, col in (('group', 'device_group'), ('location', 'location')):
            if row[col]:
                data[key] = row[col]
        image_id = ''
        if n and n['image'] and n['image'].get('sha256'):
            f = c.execute("SELECT id FROM files WHERE sha256=? AND kind='image' ORDER BY rowid DESC LIMIT 1",
                          (n['image']['sha256'],)).fetchone()
            if f and (FILES / f['id']).exists():
                image_id = f['id']
                c.execute('UPDATE files SET pinned=1 WHERE id=?', (image_id,))
        now = time.time()
        skipped = []
        if d.get('playlist') and device['assets']:
            from .playlists import playlist_from_assets
            data['playlist_id'], skipped = playlist_from_assets(c, f'{name}', device['assets'], u['username'])
            data['playlist_mode'] = 'replace'
        tid = c.execute('INSERT INTO node_templates(name, description, data_json, image_file_id, created, updated, '
                        'updated_by) VALUES(?,?,?,?,?,?,?)',
                        (name, _text(d.get('description'), 1000), json.dumps(data), image_id, now, now,
                         u['username'])).lastrowid
    audit(u, 'template.create', str(tid), {'name': name, 'from_device': row['id']})
    return {'id': tid, 'skipped': skipped, 'image_missing': bool(n and n['image'] and not image_id)}


@router.get('/api/templates/{tid}')
def get_template(tid: int, r: Request):
    current_user(r)
    with db() as c:
        t = _public(c, _row(c, tid))
        t['applied'] = [dict(x) for x in c.execute(
            "SELECT a.target device_id, d.name device_name, a.detail, a.username, a.created FROM audit a "
            "LEFT JOIN devices d ON d.id=a.target WHERE a.action='template.apply' AND "
            "CASE WHEN json_valid(a.detail) THEN json_extract(a.detail, '$.template_id') END=? "
            "ORDER BY a.id DESC LIMIT 30", (tid,))]
        for x in t['applied']:
            detail = json.loads(x.pop('detail'))
            x.update(queued=detail.get('queued') or [], skipped=detail.get('skipped') or [],
                     automatic=bool(detail.get('automatic')))
    return t


@router.patch('/api/templates/{tid}')
async def edit_template(tid: int, r: Request):
    u = current_user(r, 'manage')
    d = await _json(r)
    with db() as c:
        row = _row(c, tid)
        name = _text(d.get('name', row['name']), 120)
        if not name:
            raise HTTPException(400, 'name_required')
        data = _clean_parts(c, d, json.loads(row['data_json'] or '{}'))
        _save(c, tid, u, name=name, description=_text(d.get('description', row['description']), 1000),
              data_json=json.dumps(data))
    audit(u, 'template.edit', str(tid), {'name': name, 'parts': sorted(data)})
    return {'ok': True}


@router.delete('/api/templates/{tid}')
def delete_template(tid: int, r: Request):
    u = current_user(r, 'manage')
    with db() as c:
        row = _row(c, tid)
        c.execute('DELETE FROM node_templates WHERE id=?', (tid,))
        c.execute('UPDATE devices SET pending_template=NULL WHERE pending_template=?', (tid,))
        _unpin(c, row['image_file_id'])
    audit(u, 'template.delete', str(tid), {'name': row['name']})
    return {'ok': True}


def _unpin(c, fid):
    if fid and not c.execute('SELECT 1 FROM node_templates WHERE image_file_id=?', (fid,)).fetchone():
        from .core import refresh_pin
        refresh_pin(c, fid)


@router.post('/api/templates/{tid}/image')
async def template_image(tid: int, r: Request):
    """The picture of the notification look: an uploaded image (file_id) or {"reset": true}."""
    from .main import validate_notify_image
    u = current_user(r, 'manage')
    d = await _json(r)
    with db() as c:
        row = _row(c, tid)
        if d.get('reset'):
            fid = ''
        else:
            fid = validate_notify_image(c, d)['file_id']
            c.execute('UPDATE files SET pinned=1 WHERE id=?', (fid,))
        _save(c, tid, u, image_file_id=fid)
        if row['image_file_id'] and row['image_file_id'] != fid:
            _unpin(c, row['image_file_id'])
    audit(u, 'template.image', str(tid), {'reset': not fid})
    return {'ok': True}


@router.get('/api/templates/{tid}/image')
def template_image_file(tid: int, r: Request):
    import mimetypes
    current_user(r)
    with db() as c:
        row = _row(c, tid)
        f = c.execute('SELECT * FROM files WHERE id=?', (row['image_file_id'],)).fetchone() \
            if row['image_file_id'] else None
    if not f or not (FILES / f['id']).exists():
        raise HTTPException(404, 'not_found')
    media = mimetypes.guess_type(f['name'] or '')[0] or 'application/octet-stream'
    return FileResponse(FILES / f['id'], media_type=media if media.startswith('image/') else 'application/octet-stream',
                        headers={'Cache-Control': 'no-store'})


@router.post('/api/templates/{tid}/apply')
async def apply_template(tid: int, r: Request):
    u = current_user(r, 'manage')
    d = await _json(r)
    targets = list(dict.fromkeys(str(x) for x in d.get('target_ids') or []))
    if not targets:
        raise HTTPException(400, 'no_devices')
    batch = new_batch()
    with db() as c:
        tpl = _row(c, tid)
        rows = []
        for t in targets:
            row = c.execute('SELECT * FROM devices WHERE id=?', (t,)).fetchone()
            if not row:
                raise HTTPException(404, 'device_not_found')
            rows.append(row)
        results = [apply_to(c, tpl, row, u['username'], batch) for row in rows]
    for res in results:
        audit(u, 'template.apply', res['device_id'], {'template': tpl['name'], 'template_id': tid, 'batch': batch,
                                                      'queued': res['queued'], 'skipped': res['skipped']})
    return {'ok': True, 'batch': batch, 'results': results}
