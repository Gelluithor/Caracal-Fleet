"""Global playlists: content defined once in the hub and deployed to many CARACAL nodes.

A deployment queues one import_playlist command per node (append or replace). Media are stored in the hub
(pinned, so the temporary-file cleanup keeps them) and downloaded by each agent with a checksum check.
"""
import json
import time
import urllib.parse

from fastapi import APIRouter, HTTPException, Request

from .core import DARK_MODES, audit, cfg, current_user, db, new_batch, queue_command, refresh_pin, require_device
from .devices import COLLECTION_KIND, MEDIA_KINDS, build, grafana_config

router = APIRouter()
ITEM_KINDS = ('web', 'image', 'video', COLLECTION_KIND, 'camera')
# cameras follow the node's rules (CARACAL 2026.10.13); the stream credentials are kept in secret_json, which the API
# never returns, and travel only to the nodes
CAMERA_SCHEMES = ('rtsp://', 'rtsps://', 'http://', 'https://')
CAMERA_MAX = 9
CAMERA_LAYOUTS = ('auto', '1', '2', '2x2', '3x3')
# a widget (clock, weather, news, table, text) is a web item; the agent (4.17) turns it into the node's widget page
WIDGET_PREFIX = 'caracal-widget:'
VERSION_KEEP = 50


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


def _raw(c, pid):
    """The stored rows of a playlist's items (with the camera credentials): what a version keeps."""
    return [dict(x) for x in c.execute('SELECT * FROM global_playlist_items WHERE playlist_id=? ORDER BY position, id', (pid,))]


def _items(c, pid):
    return _expand(c, _raw(c, pid))


def _expand(c, raw):
    """Items as the API shows them: with their file, without the camera passwords."""
    out = []
    for it in raw:
        it = dict(it)
        f = c.execute('SELECT name, size, sha256 FROM files WHERE id=?', (it['file_id'],)).fetchone() if it.get('file_id') else None
        it.update(file_name=f['name'] if f else None, file_size=f['size'] if f else None, sha256=f['sha256'] if f else None)
        out.append(it)
        secrets_ = _camera_secrets(it.pop('secret_json', ''))
        if it['kind'] == COLLECTION_KIND:
            it.update(grafana_config(it['source']))
        elif it['kind'] == 'camera':
            it.update(_camera_config(it['source']))
            # the user names for the edit dialog, never the passwords
            it['streams'] = [{**s, 'username': (secrets_[i] if i < len(secrets_) else {}).get('username', ''),
                              'has_password': bool((secrets_[i] if i < len(secrets_) else {}).get('password'))}
                             for i, s in enumerate(it['streams'])]
    return out


def _camera_config(source):
    try:
        cfg = json.loads(source or '{}')
    except ValueError:
        cfg = {}
    cfg = cfg if isinstance(cfg, dict) else {}
    return {'streams': [s for s in cfg.get('streams') or [] if isinstance(s, dict)],
            'layout': cfg.get('layout') if cfg.get('layout') in CAMERA_LAYOUTS else 'auto', 'audio': bool(cfg.get('audio'))}


def _camera_secrets(secret_json):
    try:
        v = json.loads(secret_json or '[]')
    except ValueError:
        return []
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def _camera_streams(streams, old):
    """[{name, url, username, password}] -> (public [{name, url}], secrets [{username, password}]), like the node: a user
    name or password written into the address is moved out of it; an empty password (with the same or an empty user
    name) keeps the stored credentials of the same position."""
    if not isinstance(streams, list) or not 1 <= len(streams) <= CAMERA_MAX:
        raise HTTPException(400, 'camera_streams')
    public, secrets_ = [], []
    for i, s in enumerate(streams):
        s = s if isinstance(s, dict) else {}
        url = _text(s.get('url'), 2000)
        if not url.lower().startswith(CAMERA_SCHEMES) or any(ch.isspace() for ch in url):
            raise HTTPException(400, 'camera_url')
        parts = urllib.parse.urlsplit(url)
        user, password = str(s.get('username') or '').strip(), str(s.get('password') or '')
        if parts.username is not None:   # rtsp://user:password@camera/... -> address without them
            user = user or urllib.parse.unquote(parts.username)
            password = password or urllib.parse.unquote(parts.password or '')
            url = urllib.parse.urlunsplit((parts.scheme, parts.netloc.rsplit('@', 1)[1], parts.path, parts.query,
                                           parts.fragment))
        if i < len(old) and not user and not password:      # both empty: keep what is stored
            user, password = old[i].get('username', ''), old[i].get('password', '')
        elif not password and i < len(old) and user == old[i].get('username'):
            password = old[i].get('password', '')
        public.append({'name': _text(s.get('name'), 80), 'url': url})
        secrets_.append({'username': user[:200], 'password': password[:200]})
    return public, secrets_


def _touch(c, pid, u):
    c.execute('UPDATE global_playlists SET updated=?, updated_by=? WHERE id=?', (time.time(), u['username'], pid))


def _validate_item(c, d, kind, old_secrets=None):
    """Normalized item fields following the CARACAL rules (>= 5 s, videos loop; scale 0.5 to 3.0)."""
    if kind not in ITEM_KINDS:
        raise HTTPException(400, 'unsupported_file')
    item = {'name': _text(d.get('name'), 200), 'source': '', 'file_id': '', 'scale': 1.0, 'dark': '', 'secret_json': ''}
    try:
        item['duration'] = int(float(d.get('duration', 60 if kind == COLLECTION_KIND else 30 if kind == 'web' else 15)))
        item['scale'] = float(str(d.get('scale') or 1).replace(',', '.'))
    except (TypeError, ValueError):
        raise HTTPException(400, 'invalid_duration')
    if item['duration'] < 5:
        raise HTTPException(400, 'invalid_duration')
    if not 0.5 <= item['scale'] <= 3:
        raise HTTPException(400, 'invalid_scale')
    if kind in ('web', COLLECTION_KIND):   # dark mode: '' = as the node
        from .main import validate_dark   # main imports this module
        item['dark'] = validate_dark(d.get('dark'))
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
    elif kind == 'camera':
        if not item['name']:
            raise HTTPException(400, 'name_required')
        public, secrets_ = _camera_streams(d.get('streams'), old_secrets or [])
        item['source'] = json.dumps({'streams': public, 'layout': d.get('layout') if d.get('layout') in CAMERA_LAYOUTS
                                     else 'auto', 'audio': bool(d.get('audio'))}, ensure_ascii=False)
        item['secret_json'] = json.dumps(secrets_)
    elif kind == 'web':
        src = _text(d.get('source'), 4000)
        if not src.lower().startswith(('http://', 'https://', WIDGET_PREFIX)):
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


def record_version(c, pid, username, note=''):
    """Keeps the playlist's items as a version when they changed since the last one (the oldest ones go)."""
    data = _version_json(_raw(c, pid))
    last = c.execute('SELECT items_json FROM global_playlist_versions WHERE playlist_id=? ORDER BY id DESC LIMIT 1',
                     (pid,)).fetchone()
    if last and last[0] == data:
        return None
    vid = c.execute('INSERT INTO global_playlist_versions(playlist_id, items_json, username, created, note) '
                    'VALUES(?,?,?,?,?)', (pid, data, username, time.time(), note)).lastrowid
    c.execute('DELETE FROM global_playlist_versions WHERE playlist_id=? AND id NOT IN (SELECT id FROM '
              'global_playlist_versions WHERE playlist_id=? ORDER BY id DESC LIMIT ?)', (pid, pid, VERSION_KEEP))
    return vid


def _version_json(raw):
    # without the row ids, so a restored version is the same as the one it came from
    return json.dumps([{k: v for k, v in x.items() if k not in ('id', 'playlist_id')} for x in raw], ensure_ascii=False,
                      sort_keys=True)


def _before_change(c, pid):
    # the state before the first tracked change, so it can be brought back too
    if not c.execute('SELECT 1 FROM global_playlist_versions WHERE playlist_id=?', (pid,)).fetchone():
        record_version(c, pid, '', 'base')


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
        require_device(u, 'view', row)
        device = build(row, full=True)
        name = _text(d.get('name'), 120) or device['name']
        pid, skipped = playlist_from_assets(c, name, device['assets'], u['username'])
    audit(u, 'global.create', str(pid), {'name': name, 'from_device': row['id']})
    return {'id': pid, 'skipped': skipped}


def playlist_from_assets(c, name, assets, username):
    """New global playlist with the web pages and Grafana collections of a node's playlist: (id, skipped names)."""
    now = time.time()
    pid = c.execute('INSERT INTO global_playlists(name, description, created, updated, updated_by) '
                    'VALUES(?,?,?,?,?)', (name, '', now, now, username)).lastrowid
    skipped = []
    for pos, a in enumerate(assets):
        web = a['kind'] == 'web' and str(a.get('source', '')).lower().startswith(('http://', 'https://'))
        if not web and a['kind'] != COLLECTION_KIND:
            skipped.append(a['name'])
            continue
        c.execute('INSERT INTO global_playlist_items(playlist_id, position, kind, name, source, duration, scale, dark) '
                  'VALUES(?,?,?,?,?,?,?,?)', (pid, pos, a['kind'], a['name'], a['source'],
                                              max(5, int(a.get('duration') or 30)),
                                              min(3.0, max(0.5, float(a.get('scale') or 1))),
                                              a.get('dark') if a.get('dark') in DARK_MODES else ''))
    return pid, skipped


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
        _before_change(c, pid)
        item = _validate_item(c, d, _text(d.get('kind'), 16))
        from .main import validate_validity   # late import: main imports this module
        valid = validate_validity(d)
        pos = c.execute('SELECT COALESCE(MAX(position), -1) + 1 FROM global_playlist_items WHERE playlist_id=?',
                        (pid,)).fetchone()[0]
        iid = c.execute('INSERT INTO global_playlist_items(playlist_id, position, kind, name, source, duration, scale, '
                        'file_id, dark, secret_json) VALUES(?,?,?,?,?,?,?,?,?,?)',
                        (pid, pos, d['kind'], item['name'], item['source'], item['duration'], item['scale'],
                         item['file_id'], item['dark'], item['secret_json'])).lastrowid
        c.execute('UPDATE global_playlist_items SET valid_from=?, valid_until=? WHERE id=?',
                  (valid.get('valid_from'), valid.get('valid_until'), iid))
        _touch(c, pid, u)
        record_version(c, pid, u['username'], 'item_add')
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
        _before_change(c, pid)
        merged = {**dict(row), **{k: d[k] for k in ('name', 'source', 'duration', 'scale', 'grafana_url', 'tag',
                                                    'kiosk', 'dark', 'layout', 'audio') if k in d}}
        old_secrets = _camera_secrets(row['secret_json'])
        if row['kind'] == 'camera':   # streams, layout and audio not sent stay as they are
            cfg = _camera_config(row['source'])
            merged.update({k: cfg[k] for k in ('layout', 'audio') if k not in d})
            merged['streams'] = d['streams'] if 'streams' in d else [{**s, **(old_secrets[i] if i < len(old_secrets) else {})}
                                                                    for i, s in enumerate(cfg['streams'])]
        item = _validate_item(c, merged, row['kind'], old_secrets)
        c.execute('UPDATE global_playlist_items SET name=?, source=?, duration=?, scale=?, dark=?, secret_json=? WHERE id=?',
                  (item['name'], item['source'], item['duration'], item['scale'], item['dark'],
                   item['secret_json'] or row['secret_json'] or '', iid))
        from .main import validate_validity   # late import: main imports this module
        valid = validate_validity({**{k: row[k] for k in ('valid_from', 'valid_until')},
                                   **{k: d[k] for k in ('valid_from', 'valid_until') if k in d}})
        c.execute('UPDATE global_playlist_items SET valid_from=?, valid_until=? WHERE id=?',
                  (valid.get('valid_from'), valid.get('valid_until'), iid))
        _touch(c, pid, u)
        record_version(c, pid, u['username'], 'item_edit')
    audit(u, 'global.item_edit', str(pid), {'item': iid, 'name': item['name']})
    return {'ok': True}


@router.delete('/api/global-playlists/{pid}/items/{iid}')
def delete_item(pid: int, iid: int, r: Request):
    u = current_user(r, 'content')
    with db() as c:
        row = c.execute('SELECT * FROM global_playlist_items WHERE id=? AND playlist_id=?', (iid, pid)).fetchone()
        if not row:
            raise HTTPException(404, 'not_found')
        _before_change(c, pid)
        c.execute('DELETE FROM global_playlist_items WHERE id=?', (iid,))
        if row['file_id']:
            refresh_pin(c, row['file_id'])
        _touch(c, pid, u)
        record_version(c, pid, u['username'], 'item_delete')
    audit(u, 'global.item_delete', str(pid), {'item': iid, 'name': row['name']})
    return {'ok': True}


@router.put('/api/global-playlists/{pid}/order')
async def reorder_items(pid: int, r: Request):
    u = current_user(r, 'content')
    d = await _json(r)
    with db() as c:
        _playlist(c, pid)
        _before_change(c, pid)
        ids = [x['id'] for x in _items(c, pid)]
        order = [int(x) for x in d.get('order') or [] if str(x).isdigit() and int(x) in ids]
        order += [x for x in ids if x not in order]
        c.executemany('UPDATE global_playlist_items SET position=? WHERE id=?', list(enumerate(order)))
        _touch(c, pid, u)
        record_version(c, pid, u['username'], 'reorder')
    audit(u, 'global.reorder', str(pid))
    return {'ok': True}


def deploy_items(c, pid, missing_ok=False, raw=None):
    """Items of an import_playlist command with the global playlist's content (also used by node templates);
    raw: the rows of a version instead of the current ones."""
    raw = _raw(c, pid) if raw is None else raw
    items = _expand(c, raw)
    if not items and not missing_ok:
        raise HTTPException(400, 'nothing_to_copy')
    payload_items = []
    for index, it in enumerate(items):
        entry = {'type': 'asset', 'kind': it['kind'], 'name': it['name'], 'duration': it['duration'],
                 'scale': it['scale']}
        if it.get('dark'):   # only a choice of its own: nodes before CARACAL 2026.10.12 do not know it
            entry['dark'] = it['dark']
        for key in ('valid_from', 'valid_until'):   # likewise: older nodes play the item all the time
            if it.get(key):
                entry[key] = it[key]
        if it['kind'] == COLLECTION_KIND:
            entry.update(type='collection', **grafana_config(it['source']))
        elif it['kind'] == 'camera':
            cred = _camera_secrets(raw[index].get('secret_json'))
            entry.update(layout=it['layout'], audio=it['audio'],
                         streams=[{'name': s.get('name', ''), 'url': s.get('url', ''),
                                   **(cred[i] if i < len(cred) else {})} for i, s in enumerate(it['streams'])])
        elif it['kind'] == 'web':
            entry['source'] = it['source']
        else:
            if not it['sha256']:
                if missing_ok:
                    continue
                raise HTTPException(400, 'file_not_found')
            entry.update(file_id=it['file_id'], sha256=it['sha256'], filename=it['file_name'])
        payload_items.append(entry)
    return payload_items


def is_widget(it):
    return str(it.get('source') or '').startswith(WIDGET_PREFIX)


def items_for(caps, items):
    """The items a node can take: cameras only through agents that create them on import (agent 4.17 and newer,
    CARACAL 2026.10.13 and newer), older agents would add them as web pages; widgets only on nodes with the widget
    page (CARACAL 2026.10.23.1). (items, names left out)"""
    ok = lambda it: (it.get('kind') != 'camera' or caps.get('import_camera') is True) and \
        (not is_widget(it) or caps.get('widgets') is True)
    return [it for it in items if ok(it)], [it['name'] for it in items if not ok(it)]


def deploy_now(c, pid, targets, mode, username, raw=None):
    """Queue the import on the devices (already checked): {batch, media_unsupported, items_unsupported}."""
    p = _playlist(c, pid)
    batch, warnings, skipped = new_batch(), [], []
    items = _expand(c, _raw(c, pid) if raw is None else raw)
    payload_items = deploy_items(c, pid, raw=raw)
    has_media = any(it['kind'] in MEDIA_KINDS for it in items)
    has_tags = any(it['kind'] == COLLECTION_KIND for it in items)
    for t in targets:
        row = c.execute('SELECT * FROM devices WHERE id=?', (t,)).fetchone()
        if not row:
            continue
        caps = build(row)['capabilities']
        if (has_media and caps.get('upload') is False) or (has_tags and caps.get('add_grafana_tag') is False):
            warnings.append(row['name'])
        node_items, left_out = items_for(caps, payload_items)
        if left_out:
            skipped.append(row['name'])
        queue_command(c, t, 'import_playlist', {'items': node_items, 'mode': mode, 'global_playlist': p['name']},
                      username, batch)
    c.execute('INSERT INTO global_deployments(playlist_id, batch, mode, targets_json, username, created) '
              'VALUES(?,?,?,?,?,?)', (pid, batch, mode, json.dumps(targets), username, time.time()))
    return {'batch': batch, 'media_unsupported': warnings, 'items_unsupported': skipped, 'name': p['name']}


def _deploy_targets(c, u, d):
    mode = 'replace' if d.get('mode') == 'replace' else 'append'
    targets = list(dict.fromkeys(str(x) for x in d.get('target_ids') or []))
    if not targets:
        raise HTTPException(400, 'no_devices')
    rows = []
    for t in targets:
        row = c.execute('SELECT * FROM devices WHERE id=?', (t,)).fetchone()
        if not row:
            raise HTTPException(404, 'device_not_found')
        require_device(u, 'content', row)
        rows.append(row)
    return mode, targets, rows


def approval_required(u, rows):
    """Deployments need an approval when it is switched on and the user may not approve for all the devices."""
    return bool(cfg().get('require_approval')) and not all(u['access'].on('approve', row) for row in rows)


@router.post('/api/global-playlists/{pid}/deploy')
async def deploy(pid: int, r: Request):
    u = current_user(r, 'content')
    d = await _json(r)
    with db() as c:
        p = _playlist(c, pid)
        mode, targets, rows = _deploy_targets(c, u, d)
        deploy_items(c, pid)   # 400 when there is nothing to deploy
        if approval_required(u, rows):
            # what is approved is this version, also when the playlist changes in the meantime
            record_version(c, pid, u['username'], 'approval')
            vid = c.execute('SELECT MAX(id) FROM global_playlist_versions WHERE playlist_id=?', (pid,)).fetchone()[0]
            aid = c.execute('INSERT INTO approvals(kind, playlist_id, payload_json, summary, username, created) '
                            'VALUES(?,?,?,?,?,?)', ('global_deploy', pid, json.dumps(
                                {'targets': targets, 'mode': mode, 'version_id': vid, 'note': _text(d.get('note'), 500)}),
                                p['name'], u['username'], time.time())).lastrowid
            res = None
        else:
            res = deploy_now(c, pid, targets, mode, u['username'])
    if res is None:
        audit(u, 'approval.request', str(aid), {'playlist': p['name'], 'targets': targets, 'mode': mode})
        from .approvals import notify_approvers
        notify_approvers(p['name'], u['username'], len(targets))
        return {'ok': True, 'pending_approval': aid}
    audit(u, 'global.deploy', str(pid), {'name': p['name'], 'targets': targets, 'mode': mode})
    return {'ok': True, 'batch': res['batch'], 'media_unsupported': res['media_unsupported'],
            'items_unsupported': res['items_unsupported'], 'camera_unsupported': res['items_unsupported']}


# ---------------------------------------------------------------- what a deployment changes

def _key(it):
    return (str(it.get('kind') or ''), str(it.get('name') or '').strip().lower())


@router.post('/api/global-playlists/{pid}/preview')
async def preview(pid: int, r: Request):
    """Per device: which items a deployment adds, removes and keeps (by kind and name), and what the node cannot
    take. Grafana collections of the node stay in both modes, like the deployment does."""
    u = current_user(r, 'content')
    d = await _json(r)
    with db() as c:
        _playlist(c, pid)
        mode, _targets, rows = _deploy_targets(c, u, d)
        payload_items = deploy_items(c, pid, missing_ok=True)
        out = []
        for row in rows:
            dev = build(row, full=True)
            new, left_out = items_for(dev['capabilities'], payload_items)
            if dev['capabilities'].get('upload') is False:
                left_out += [it['name'] for it in new if it.get('file_id')]
                new = [it for it in new if not it.get('file_id')]
            current = [a for a in dev['assets'] if a['kind'] != COLLECTION_KIND or mode == 'append']
            kept_tags = [a['name'] for a in dev['assets'] if a['kind'] == COLLECTION_KIND and mode == 'replace']
            new_names = [(_key({'kind': 'grafana-tag' if it.get('type') == 'collection' else it['kind'], 'name': it['name']}), it['name']) for it in new]
            cur_keys = {_key(a) for a in current}
            if mode == 'replace':
                new_keys = {k for k, _ in new_names}
                added = [n for k, n in new_names if k not in cur_keys]
                same = [n for k, n in new_names if k in cur_keys]
                removed = [a['name'] for a in current if _key(a) not in new_keys]
                kept = kept_tags
            else:
                added, same, removed, kept = [n for _, n in new_names], [], [], [a['name'] for a in current]
            out.append({'device_id': row['id'], 'name': row['name'], 'online': dev['online'], 'added': added,
                        'same': same, 'removed': removed, 'kept': kept, 'skipped': left_out,
                        'duplicates': [n for k, n in new_names if k in cur_keys] if mode == 'append' else []})
    return {'mode': mode, 'devices': out}


# ---------------------------------------------------------------- earlier versions

def _summary(items):
    return {'count': len(items), 'names': [it.get('name') or '' for it in items][:8]}


@router.get('/api/global-playlists/{pid}/versions')
def list_versions(pid: int, r: Request):
    current_user(r)
    with db() as c:
        _playlist(c, pid)
        rows = c.execute('SELECT * FROM global_playlist_versions WHERE playlist_id=? ORDER BY id DESC', (pid,)).fetchall()
        current = _version_json(_raw(c, pid))
    return [{'id': x['id'], 'created': x['created'], 'username': x['username'], 'note': x['note'],
             'current': x['items_json'] == current, **_summary(json.loads(x['items_json']))} for x in rows]


@router.get('/api/global-playlists/{pid}/versions/{vid}')
def get_version(pid: int, vid: int, r: Request):
    current_user(r)
    with db() as c:
        row = c.execute('SELECT * FROM global_playlist_versions WHERE id=? AND playlist_id=?', (vid, pid)).fetchone()
        if not row:
            raise HTTPException(404, 'not_found')
        return {'id': vid, 'created': row['created'], 'username': row['username'], 'note': row['note'],
                'items': _expand(c, json.loads(row['items_json']))}


@router.post('/api/global-playlists/{pid}/versions/{vid}/restore')
def restore_version(pid: int, vid: int, r: Request):
    """The playlist gets the items of an earlier version again (as a new version; nothing is deployed)."""
    u = current_user(r, 'content')
    with db() as c:
        _playlist(c, pid)
        row = c.execute('SELECT * FROM global_playlist_versions WHERE id=? AND playlist_id=?', (vid, pid)).fetchone()
        if not row:
            raise HTTPException(404, 'not_found')
        _before_change(c, pid)
        old_files = {x['file_id'] for x in _raw(c, pid) if x.get('file_id')}
        items = json.loads(row['items_json'])
        missing = [it['name'] for it in items if it.get('file_id') and not
                   c.execute('SELECT 1 FROM files WHERE id=?', (it['file_id'],)).fetchone()]
        c.execute('DELETE FROM global_playlist_items WHERE playlist_id=?', (pid,))
        cols = [x[1] for x in c.execute('PRAGMA table_info(global_playlist_items)')]
        for it in items:
            if it.get('file_id') and it['name'] in missing:
                continue
            it = {k: v for k, v in it.items() if k in cols and k != 'id'}
            it['playlist_id'] = pid
            c.execute(f"INSERT INTO global_playlist_items({', '.join(it)}) VALUES({', '.join('?' * len(it))})", list(it.values()))
        for fid in old_files | {it['file_id'] for it in items if it.get('file_id')}:
            refresh_pin(c, fid)
        _touch(c, pid, u)
        record_version(c, pid, u['username'], f'restore:{vid}')
    audit(u, 'global.restore', str(pid), {'version': vid})
    return {'ok': True, 'missing': missing}
