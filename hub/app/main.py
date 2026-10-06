"""CARACAL Fleet Controller - central management of CARACAL signage nodes."""
import base64
import hashlib
import json
import secrets
import time
from urllib.parse import unquote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import images, playlists, provisioning, releases, sdcard, system
from .core import (ACTIONS, AGENT_VERSION, BOOT, FILES, HUB_VERSION, LANGUAGES, PERMS, ROLES, USERNAME_RE, APP_DIR,
                   audit, cfg, check_password, cleanup_files, current_user, db, device_auth, hash_password, init,
                   make_session, new_batch, public_user, queue_command, redact, redact_json, scrub_secrets,
                   sweep_commands, token_hash)
from .devices import COLLECTION_KIND, MEDIA_KINDS, build, collections_of, grafana_config

app = FastAPI(title='CARACAL Fleet Controller', version=HUB_VERSION, docs_url=None, redoc_url=None,
              openapi_url=None)
init()
STATIC = APP_DIR / 'static'
app.include_router(system.router)
app.include_router(playlists.router)
app.include_router(releases.router)
app.include_router(images.router)
app.include_router(sdcard.router)       # before /api/bootstrap/{name} (node-config)

SECURITY_HEADERS = {
    # no inline scripts, no third-party resources; inline styles are used for progress bars
    'Content-Security-Policy': "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                               "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
                               "form-action 'self'; object-src 'none'",
    'X-Content-Type-Options': 'nosniff',
    'X-Frame-Options': 'DENY',
    'Referrer-Policy': 'no-referrer',
    'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
    'Strict-Transport-Security': 'max-age=31536000',
}


@app.middleware('http')
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    if request.url.path.startswith('/api/'):
        response.headers.setdefault('Cache-Control', 'no-store')
    return response

LIVE_ACTIONS = {'next', 'unfreeze', 'show', 'freeze', 'show_collection', 'freeze_collection', 'restart_player',
                'reboot'}
INTERNAL_ACTIONS = {'import_playlist', 'export_assets'}
ORG = {'groups': ('device_groups', 'device_group'), 'locations': ('device_locations', 'location')}
MAX_UPLOAD = 2 * 1024 ** 3
_login_failures = {}
LOGIN_WINDOW, LOGIN_MAX_PER_IP, LOGIN_MAX_PER_USER = 300, 8, 20
_DUMMY_HASH = hash_password(secrets.token_hex(8))


def _recent_failures(key):
    now = time.time()
    if len(_login_failures) > 10000:  # the client address can be spoofed, keep the table bounded
        for k in [k for k, v in _login_failures.items() if not v or v[-1] < now - LOGIN_WINDOW]:
            _login_failures.pop(k, None)
    return [t for t in _login_failures.get(key, []) if t > now - LOGIN_WINDOW]


async def body(r: Request):
    try:
        d = await r.json()
    except ValueError:
        raise HTTPException(400, 'invalid_json')
    if not isinstance(d, dict):
        raise HTTPException(400, 'invalid_json')
    return d


def text(v, limit=500):
    return str(v if v is not None else '').strip()[:limit]


def get_device_row(c, did):
    row = c.execute('SELECT * FROM devices WHERE id=?', (did,)).fetchone()
    if not row:
        raise HTTPException(404, 'device_not_found')
    return row


def failed_counts(c):
    since = time.time() - 3600
    return {r['device_id']: r['n'] for r in c.execute(
        "SELECT device_id, COUNT(*) n FROM commands WHERE state IN ('failed','timeout') AND updated>? "
        "GROUP BY device_id", (since,))}


# ---------------------------------------------------------------- pages and session

@app.get('/', response_class=HTMLResponse)
def home():
    return (STATIC / 'index.html').read_text(encoding='utf-8').replace('{{VERSION}}', HUB_VERSION)


@app.get('/api/health')
def health():
    return {'ok': True, 'version': HUB_VERSION, 'agent_version': AGENT_VERSION}


@app.post('/api/login')
async def login(r: Request):
    d = await body(r)
    username = text(d.get('username') or 'admin', 64)
    # Limited per client address and per account; the account limit also holds when the address is spoofed.
    ip_key = (r.client.host if r.client else '', username.lower())
    user_key = ('*', username.lower())
    ip_fails, user_fails = _recent_failures(ip_key), _recent_failures(user_key)
    if len(ip_fails) >= LOGIN_MAX_PER_IP or len(user_fails) >= LOGIN_MAX_PER_USER:
        raise HTTPException(429, 'too_many_attempts')
    with db() as c:
        u = c.execute('SELECT * FROM users WHERE username=? AND enabled=1', (username,)).fetchone()
    # the hash is computed also for unknown users so response times do not reveal valid usernames
    valid = check_password(str(d.get('password', '')), u['password_hash'] if u else _DUMMY_HASH)
    if not u or not valid:
        _login_failures[ip_key] = ip_fails + [time.time()]
        _login_failures[user_key] = user_fails + [time.time()]
        audit(None, 'user.login_failed', username, {'ip': ip_key[0]})
        raise HTTPException(401, 'invalid_credentials')
    _login_failures.pop(ip_key, None)
    _login_failures.pop(user_key, None)
    u = dict(u)
    if u['password_hash'].startswith('legacy$'):  # upgrade legacy hash on successful login
        u['password_hash'] = hash_password(str(d['password']))
        with db() as c:
            c.execute('UPDATE users SET password_hash=? WHERE id=?', (u['password_hash'], u['id']))
    audit(u, 'user.login', u['username'])
    return {'token': make_session(u), 'user': public_user(u)}


@app.get('/api/me')
def me(r: Request):
    u = current_user(r)
    return {**public_user(u), 'hub_version': HUB_VERSION, 'agent_version': AGENT_VERSION}


@app.patch('/api/me')
async def edit_me(r: Request):
    u = current_user(r)
    d = await body(r)
    token = None
    with db() as c:
        if d.get('language') in LANGUAGES:
            c.execute('UPDATE users SET language=? WHERE id=?', (d['language'], u['id']))
        if d.get('new_password'):
            if not check_password(str(d.get('current_password', '')), u['password_hash']):
                raise HTTPException(400, 'wrong_password')
            if len(str(d['new_password'])) < 10:
                raise HTTPException(400, 'password_too_short')
            u['password_hash'] = hash_password(str(d['new_password']))
            c.execute('UPDATE users SET password_hash=? WHERE id=?', (u['password_hash'], u['id']))
            token = make_session(u)
    if d.get('new_password'):
        audit(u, 'user.password', u['username'])
    return {'ok': True, 'token': token}


# ---------------------------------------------------------------- devices

def latest_versions():
    return {'docker': images.latest_version(), 'host': releases.latest_version()}


@app.get('/api/devices')
def list_devices(r: Request):
    current_user(r)
    with db() as c:
        sweep_commands(c)
        failed = failed_counts(c)
        rows = c.execute('SELECT * FROM devices ORDER BY name COLLATE NOCASE').fetchall()
        pending = {x['device_id']: x['n'] for x in c.execute(
            "SELECT device_id, COUNT(*) n FROM commands WHERE state IN ('queued','delivered') GROUP BY device_id")}
    latest = latest_versions()
    devices = []
    for row in rows:
        d = build(row, failed.get(row['id'], 0), latest_caracal=latest)
        d['pending_commands'] = pending.get(row['id'], 0)
        devices.append(d)
    return {'devices': devices, 'attention_count': sum(1 for d in devices if d['needs_attention']),
            'agent_version': AGENT_VERSION, 'hub_version': HUB_VERSION, 'server_time': time.time()}


@app.get('/api/devices/{did}')
def device_detail(did: str, r: Request):
    current_user(r)
    with db() as c:
        sweep_commands(c)
        row = get_device_row(c, did)
        failed = failed_counts(c)
        cmds = [dict(x) for x in c.execute(
            'SELECT id, action, payload_json, state, result, created, updated, username FROM commands '
            'WHERE device_id=? ORDER BY id DESC LIMIT 30', (did,))]
    for x in cmds:
        x['payload_json'] = redact_json(x['action'], x['payload_json'])
    d = build(row, failed.get(did, 0), full=True, latest_caracal=latest_versions())
    d['commands'] = cmds
    d['pending_commands'] = sum(1 for x in cmds if x['state'] in ('queued', 'delivered'))
    return d


@app.patch('/api/devices/{did}')
async def edit_device(did: str, r: Request):
    u = current_user(r, 'manage')
    d = await body(r)
    with db() as c:
        row = dict(get_device_row(c, did))
        name = text(d.get('name', row['name']), 120) or did
        group = text(d.get('group', row['device_group']), 120)
        location = text(d.get('location', row['location']), 120)
        notes = text(d.get('notes', row['notes']), 2000)
        c.execute('UPDATE devices SET name=?, device_group=?, location=?, notes=? WHERE id=?',
                  (name, group, location, notes, did))
        _ensure_org(c, group, location)
    audit(u, 'device.edit', did, {'name': name, 'group': group, 'location': location})
    return {'ok': True}


@app.delete('/api/devices/{did}')
def delete_device(did: str, r: Request):
    u = current_user(r, 'manage')
    with db() as c:
        row = get_device_row(c, did)
        c.execute('DELETE FROM commands WHERE device_id=?', (did,))
        c.execute('DELETE FROM devices WHERE id=?', (did,))
    audit(u, 'device.delete', did, {'name': row['name']})
    return {'ok': True}


@app.post('/api/devices/assign')
async def assign_devices(r: Request):
    """Bulk assignment of group and/or location. Missing key = unchanged, empty string = cleared."""
    u = current_user(r, 'manage')
    d = await body(r)
    ids = [str(x) for x in d.get('device_ids') or []]
    with db() as c:
        for key, col in (('group', 'device_group'), ('location', 'location')):
            if key in d:
                c.executemany(f'UPDATE devices SET {col}=? WHERE id=?', [(text(d[key], 120), i) for i in ids])
        _ensure_org(c, text(d.get('group'), 120), text(d.get('location'), 120))
    audit(u, 'device.assign', ','.join(ids), {k: d[k] for k in ('group', 'location') if k in d})
    return {'ok': True}


def _ensure_org(c, group, location):
    now = time.time()
    if group:
        c.execute('INSERT OR IGNORE INTO device_groups(name, created) VALUES(?,?)', (group, now))
    if location:
        c.execute('INSERT OR IGNORE INTO device_locations(name, created) VALUES(?,?)', (location, now))


# ---------------------------------------------------------------- commands

def validate_command(c, row, action, payload):
    if action not in ACTIONS or action in INTERNAL_ACTIONS:
        raise HTTPException(400, 'unknown_action')
    payload = dict(payload or {})
    online = time.time() - (row['last_seen'] or 0) < 35
    if action in LIVE_ACTIONS and not online:
        raise HTTPException(409, 'device_offline')
    status = json.loads(row['status_json'] or '{}')
    if action in ('show', 'freeze'):
        if payload.get('item_id') in (None, ''):
            raise HTTPException(400, 'item_required')
        known = [str(a.get('id')) for a in status.get('assets') or []]
        if known and str(payload['item_id']) not in known:
            raise HTTPException(409, 'item_not_found')
    if action in ('show_collection', 'freeze_collection'):
        if payload.get('collection_id') in (None, ''):
            raise HTTPException(400, 'item_required')
        known = [str(x.get('id')) for x in collections_of(status)]
        if known and str(payload['collection_id']) not in known:
            raise HTTPException(409, 'item_not_found')
    if action in ('freeze', 'freeze_collection'):
        minutes = int(payload.get('minutes') or 0)
        if not 0 <= minutes <= 1440:
            raise HTTPException(400, 'invalid_minutes')
        payload['minutes'] = minutes
    if action == 'add_web':
        src = text(payload.get('source'), 4000)
        if not src.lower().startswith(('http://', 'https://')):
            raise HTTPException(400, 'invalid_url')
        payload['source'] = src
        payload['name'] = text(payload.get('name'), 200) or src
    if action in ('add_web', 'add_media', 'update_asset', 'add_collection', 'update_collection'):
        payload.update(validate_timing(payload))
    known_profiles = [str(p.get('id')) for p in status.get('profiles') or []] if 'profiles' in status else None
    if action in ('add_web', 'update_asset') and 'auth_profile_id' in payload:
        payload['auth_profile_id'] = validate_profile_ref(payload['auth_profile_id'], known_profiles)
    if action in ('add_profile', 'update_profile'):
        payload = {**({'id': payload['id']} if payload.get('id') not in (None, '') else {}),
                   **validate_profile(payload, required=action == 'add_profile')}
    if action in ('update_profile', 'delete_profile'):
        if payload.get('id') in (None, ''):
            raise HTTPException(400, 'item_required')
        validate_profile_ref(payload['id'], known_profiles)
    if action == 'add_media':
        f = c.execute('SELECT * FROM files WHERE id=?', (str(payload.get('file_id')),)).fetchone()
        if not f:
            raise HTTPException(400, 'file_not_found')
        payload.update(sha256=f['sha256'], filename=f['name'], size=f['size'])
        payload['kind'] = payload.get('kind') or f['kind']
        payload['name'] = text(payload.get('name'), 200) or f['name']
    if action in ('update_asset', 'delete_asset', 'update_collection', 'delete_collection') \
            and payload.get('id') in (None, ''):
        raise HTTPException(400, 'item_required')
    if action in ('add_collection', 'update_collection'):
        payload.update(validate_grafana(payload, required=action == 'add_collection'))
    if action == 'reorder':
        order = payload.get('order')
        if not isinstance(order, list) or not order:
            raise HTTPException(400, 'invalid_order')
    if action == 'update_caracal' and payload.get('release_id') is not None:   # classic node, release archive
        rel = c.execute('SELECT * FROM node_releases WHERE id=?', (str(payload.get('release_id')),)).fetchone()
        if not rel:
            raise HTTPException(400, 'release_not_found')
        if not online:
            raise HTTPException(409, 'device_offline')
        payload = {'release_id': rel['id'], 'version': rel['version'], 'file_id': rel['file_id'],
                   'sha256': rel['sha256']}
    elif action in ('update_caracal', 'convert_to_docker'):   # Docker image version
        version = text(payload.get('version'), 64)
        if not images.VERSION_RE.match(version):
            raise HTTPException(400, 'version_required')
        if not images.node_image():
            raise HTTPException(400, 'node_image_missing')
        if not online:
            raise HTTPException(409, 'device_offline')
        rt = build(row)['runtime']
        if action == 'update_caracal' and rt != 'docker':
            raise HTTPException(409, 'not_docker')
        if action == 'convert_to_docker' and rt == 'docker':
            raise HTTPException(409, 'already_docker')
        payload = {'version': version, 'image': images.node_image()}
    if action == 'set_hub':
        hub = text(payload.get('hub'), 500).rstrip('/')
        if not hub.lower().startswith(('https://', 'http://')):
            raise HTTPException(400, 'invalid_url')
        payload = {'hub': hub}
    return payload


def validate_timing(d):
    """CARACAL shows every item (videos loop) for at least 5 s; scale is 0.5 to 3.0 like in the node UI."""
    out = {}
    try:
        if 'duration' in d:
            out['duration'] = int(float(d['duration']))
            if out['duration'] < 5:
                raise ValueError
        if 'scale' in d:
            out['scale'] = float(str(d['scale']).replace(',', '.'))
    except (TypeError, ValueError):
        raise HTTPException(400, 'invalid_duration')
    if 'scale' in out and not 0.5 <= out['scale'] <= 3:
        raise HTTPException(400, 'invalid_scale')
    return out


PROFILE_SELECTORS = ('user_selector', 'pass_selector', 'submit_selector')


def validate_profile_ref(value, known):
    """A login profile of the node: None/''/0 = without login. 'known' is None for agents that do not report them."""
    if value in (None, '', 0, '0'):
        return None
    try:
        ident = int(value)
    except (TypeError, ValueError):
        raise HTTPException(400, 'invalid_value')
    if known is not None and str(ident) not in known:
        raise HTTPException(409, 'profile_not_found')
    return ident


def validate_profile(d, required=True):
    """Login profile of a web page (CARACAL fills the login form, then opens the target page).
    On edits an empty username or password keeps the stored one; empty selectors keep theirs."""
    out = {}
    if required or 'name' in d:
        out['name'] = text(d.get('name'), 200)
        if not out['name']:
            raise HTTPException(400, 'name_required')
    for key in ('login_url', 'target_url'):
        if required or key in d:
            out[key] = text(d.get(key), 4000)
            if not out[key].lower().startswith(('http://', 'https://')):
                raise HTTPException(400, 'invalid_url')
    for key in PROFILE_SELECTORS:
        if text(d.get(key), 1000):
            out[key] = text(d.get(key), 1000)
    username, password = str(d.get('username') or '').strip()[:500], str(d.get('password') or '')[:500]
    if required and (not username or not password):
        raise HTTPException(400, 'credentials_required')
    if username:
        out['username'] = username
    if password:
        out['password'] = password
    return out


def validate_grafana(d, required=True):
    out = {}
    if required or 'name' in d:
        out['name'] = text(d.get('name'), 200)
        if not out['name']:
            raise HTTPException(400, 'name_required')
    if required or 'grafana_url' in d:
        out['grafana_url'] = text(d.get('grafana_url'), 1000).rstrip('/')
        if not out['grafana_url'].lower().startswith(('http://', 'https://')):
            raise HTTPException(400, 'invalid_url')
    if required or 'tag' in d:
        out['tag'] = text(d.get('tag'), 200)
        if not out['tag']:
            raise HTTPException(400, 'tag_required')
    if 'kiosk' in d or required:
        out['kiosk'] = bool(d.get('kiosk', True))
    return out


@app.post('/api/devices/{did}/commands')
async def device_command(did: str, r: Request):
    d = await body(r)
    action = str(d.get('action', ''))
    u = current_user(r, ACTIONS.get(action, ('admin',))[0])
    with db() as c:
        row = get_device_row(c, did)
        payload = validate_command(c, row, action, d.get('payload'))
        cid = queue_command(c, did, action, payload, u['username'])
    audit(u, 'command.' + action, did, redact(action, payload))
    return {'id': cid}


@app.post('/api/bulk/commands')
async def bulk_command(r: Request):
    d = await body(r)
    action = str(d.get('action', ''))
    u = current_user(r, ACTIONS.get(action, ('admin',))[0])
    ids = list(dict.fromkeys(str(x) for x in d.get('device_ids') or []))
    if not ids:
        raise HTTPException(400, 'no_devices')
    batch, queued, skipped = new_batch(), [], []
    with db() as c:
        for did in ids:
            try:
                row = get_device_row(c, did)
                payload = validate_command(c, row, action, d.get('payload'))
                queued.append(queue_command(c, did, action, payload, u['username'], batch))
            except HTTPException as e:
                if e.detail == 'unknown_action':
                    raise
                skipped.append({'device_id': did, 'reason': e.detail})
    audit(u, 'bulk.' + action, ','.join(ids), {'payload': redact(action, d.get('payload') or {}), 'skipped': skipped})
    return {'command_ids': queued, 'skipped': skipped, 'batch': batch}


@app.get('/api/commands')
def list_commands(r: Request, device_id: str = '', state: str = '', limit: int = 300):
    current_user(r)
    q, args = 'SELECT c.*, d.name device_name FROM commands c LEFT JOIN devices d ON d.id=c.device_id WHERE 1=1', []
    if device_id:
        q += ' AND c.device_id=?'
        args.append(device_id)
    if state:
        q += ' AND c.state=?'
        args.append(state)
    q += ' ORDER BY c.id DESC LIMIT ?'
    args.append(max(1, min(limit, 2000)))
    with db() as c:
        sweep_commands(c)
        rows = [dict(x) for x in c.execute(q, args)]
    for x in rows:
        x['payload_json'] = redact_json(x['action'], x['payload_json'])
    return rows


@app.post('/api/commands/{cid}/cancel')
def cancel_command(cid: int, r: Request):
    u = current_user(r, 'control')
    with db() as c:
        n = c.execute("UPDATE commands SET state='cancelled', updated=? WHERE id=? AND state='queued'",
                      (time.time(), cid)).rowcount
        scrub_secrets(c)
    if not n:
        raise HTTPException(409, 'not_cancellable')
    audit(u, 'command.cancel', str(cid))
    return {'ok': True}


# ---------------------------------------------------------------- media files and copying

@app.post('/api/files')
async def upload_file(r: Request):
    u = current_user(r, 'content')
    name = unquote(r.headers.get('X-File-Name', 'file'))[:200] or 'file'
    ctype = r.headers.get('Content-Type', '')
    kind = 'video' if ctype.startswith('video/') else 'image' if ctype.startswith('image/') else ''
    if not kind:
        raise HTTPException(400, 'unsupported_file')
    fid = await _store_upload(r, name, kind, 'ui:' + u['username'])
    audit(u, 'file.upload', fid, {'name': name, 'kind': kind})
    return {'id': fid, 'name': name, 'kind': kind}


async def _store_upload(r, name, kind, origin):
    fid = secrets.token_hex(16)
    path, tmp = FILES / fid, FILES / (fid + '.part')
    h, size = hashlib.sha256(), 0
    try:
        with tmp.open('wb') as f:
            async for chunk in r.stream():
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise HTTPException(413, 'file_too_large')
                h.update(chunk)
                f.write(chunk)
        if not size:
            raise HTTPException(400, 'empty_file')
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    with db() as c:
        c.execute('INSERT INTO files(id, name, kind, size, sha256, origin, created) VALUES(?,?,?,?,?,?,?)',
                  (fid, name, kind, size, h.hexdigest(), origin, time.time()))
    await run_in_threadpool(cleanup_files)
    return fid


@app.post('/api/copy')
async def copy_content(r: Request):
    """Copy playlist items and/or Grafana collections from one node to others."""
    u = current_user(r, 'content')
    d = await body(r)
    src = str(d.get('source_id', ''))
    targets = [str(t) for t in dict.fromkeys(d.get('target_ids') or []) if str(t) != src]
    mode = 'replace' if d.get('mode') == 'replace' else 'append'
    if not targets:
        raise HTTPException(400, 'no_devices')
    with db() as c:
        source = build(get_device_row(c, src), full=True)
        for t in targets:
            get_device_row(c, t)
    asset_ids, col_ids = d.get('asset_ids'), d.get('collection_ids')
    is_col = lambda a: a['kind'] == COLLECTION_KIND
    if asset_ids is None and col_ids is None:
        assets = [a for a in source['assets'] if d.get('include_collections') or not is_col(a)]
    else:
        wanted = {str(x) for x in (asset_ids or []) + (col_ids or [])}
        assets = [a for a in source['assets'] if str(a.get('id')) in wanted]
    skipped = []
    if source['capabilities'].get('asset_file') is False:
        skipped = [a['name'] for a in assets if a['kind'] in MEDIA_KINDS]
        assets = [a for a in assets if a['kind'] not in MEDIA_KINDS]
    if not assets:
        raise HTTPException(400, 'nothing_to_copy')
    # web pages behind a login profile are copied, but the (encrypted) login stays on the source node
    no_login = [a['name'] for a in assets if a.get('auth_profile_id') and not is_col(a)]
    cols = [a for a in assets if is_col(a)]
    items = []
    for a in assets:  # playlist order is kept
        if is_col(a):
            items.append({'type': 'collection', 'kind': COLLECTION_KIND, 'name': a['name'],
                          'duration': a.get('duration'), 'scale': a.get('scale') or 1, **grafana_config(a['source'])})
            continue
        item = {k: v for k, v in a.items() if k not in ('id', 'position', 'order', 'created', 'updated',
                                                        'auth_profile_id', 'parent_id')}
        item.update(type='asset', source_asset_id=a.get('id'))
        if a['kind'] in MEDIA_KINDS:
            item.pop('source', None)
        items.append(item)
    media_ids = [a.get('id') for a in assets if a['kind'] in MEDIA_KINDS]
    batch = new_batch()
    with db() as c:
        if media_ids:
            if not source['online']:
                raise HTTPException(409, 'source_offline')
            queue_command(c, src, 'export_assets', {'asset_ids': media_ids}, u['username'], batch,
                          followup={'action': 'import_playlist', 'targets': targets, 'items': items, 'mode': mode,
                                    'replace_collections': bool(cols) and mode == 'replace'})
        else:
            for t in targets:
                queue_command(c, t, 'import_playlist', {'items': items, 'mode': mode,
                                                        'replace_collections': bool(cols) and mode == 'replace'},
                              u['username'], batch)
    audit(u, 'content.copy', src, {'targets': targets, 'mode': mode, 'items': len(items), 'collections': len(cols)})
    return {'ok': True, 'batch': batch, 'items': len(items), 'skipped': skipped, 'without_login': no_login}


# ---------------------------------------------------------------- groups and locations

@app.get('/api/org')
def list_org(r: Request):
    current_user(r)
    now = time.time()
    out = {}
    with db() as c:
        for kind, (table, col) in ORG.items():
            stats = {x[col]: x for x in c.execute(
                f'SELECT {col}, COUNT(*) total, SUM(CASE WHEN last_seen>? THEN 1 ELSE 0 END) online '
                f'FROM devices GROUP BY {col}', (now - 35,))}
            out[kind] = [{**dict(x), 'total': stats[x['name']]['total'] if x['name'] in stats else 0,
                          'online': (stats[x['name']]['online'] or 0) if x['name'] in stats else 0}
                         for x in c.execute(f'SELECT * FROM {table} ORDER BY name COLLATE NOCASE')]
    return out


def _org(kind):
    if kind not in ORG:
        raise HTTPException(404, 'not_found')
    return ORG[kind]


@app.post('/api/org/{kind}')
async def create_org(kind: str, r: Request):
    u = current_user(r, 'manage')
    table, _ = _org(kind)
    d = await body(r)
    name = text(d.get('name'), 120)
    if not name:
        raise HTTPException(400, 'name_required')
    with db() as c:
        if c.execute(f'SELECT 1 FROM {table} WHERE name=?', (name,)).fetchone():
            raise HTTPException(409, 'already_exists')
        if kind == 'locations':
            c.execute(f'INSERT INTO {table}(name, description, address, created) VALUES(?,?,?,?)',
                      (name, text(d.get('description'), 1000), text(d.get('address'), 500), time.time()))
        else:
            c.execute(f'INSERT INTO {table}(name, description, created) VALUES(?,?,?)',
                      (name, text(d.get('description'), 1000), time.time()))
    audit(u, f'{kind}.create', name)
    return {'ok': True}


@app.patch('/api/org/{kind}/{name}')
async def edit_org(kind: str, name: str, r: Request):
    u = current_user(r, 'manage')
    table, col = _org(kind)
    d = await body(r)
    new = text(d.get('name', name), 120)
    if not new:
        raise HTTPException(400, 'name_required')
    with db() as c:
        if not c.execute(f'SELECT 1 FROM {table} WHERE name=?', (name,)).fetchone():
            raise HTTPException(404, 'not_found')
        if new != name and c.execute(f'SELECT 1 FROM {table} WHERE name=?', (new,)).fetchone():
            raise HTTPException(409, 'already_exists')
        c.execute(f'UPDATE {table} SET name=?, description=? WHERE name=?', (new, text(d.get('description'), 1000), name))
        if kind == 'locations':
            c.execute(f'UPDATE {table} SET address=? WHERE name=?', (text(d.get('address'), 500), new))
        c.execute(f'UPDATE devices SET {col}=? WHERE {col}=?', (new, name))
    audit(u, f'{kind}.edit', name, {'name': new})
    return {'ok': True}


@app.delete('/api/org/{kind}/{name}')
def delete_org(kind: str, name: str, r: Request):
    u = current_user(r, 'manage')
    table, col = _org(kind)
    with db() as c:
        c.execute(f'DELETE FROM {table} WHERE name=?', (name,))
        c.execute(f"UPDATE devices SET {col}='' WHERE {col}=?", (name,))
    audit(u, f'{kind}.delete', name)
    return {'ok': True}


# ---------------------------------------------------------------- users, audit, settings

@app.get('/api/users')
def list_users(r: Request):
    current_user(r, 'admin')
    with db() as c:
        return [dict(x) for x in c.execute(
            'SELECT id, username, role, language, enabled, created FROM users ORDER BY username COLLATE NOCASE')]


@app.post('/api/users')
async def create_user(r: Request):
    u = current_user(r, 'admin')
    d = await body(r)
    username, password = text(d.get('username'), 64), str(d.get('password', ''))
    role, language = d.get('role', 'viewer'), d.get('language', 'cs')
    if not USERNAME_RE.match(username):
        raise HTTPException(400, 'invalid_username')
    if role not in ROLES or language not in LANGUAGES:
        raise HTTPException(400, 'invalid_role')
    if len(password) < 10:
        raise HTTPException(400, 'password_too_short')
    with db() as c:
        if c.execute('SELECT 1 FROM users WHERE username=?', (username,)).fetchone():
            raise HTTPException(409, 'already_exists')
        c.execute('INSERT INTO users(username, password_hash, role, language, enabled, created) VALUES(?,?,?,?,1,?)',
                  (username, hash_password(password), role, language, time.time()))
    audit(u, 'user.create', username, {'role': role})
    return {'ok': True}


def _other_admins(c, uid):
    return c.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND enabled=1 AND id!=?", (uid,)).fetchone()[0]


@app.patch('/api/users/{uid}')
async def edit_user(uid: int, r: Request):
    u = current_user(r, 'admin')
    d = await body(r)
    with db() as c:
        target = c.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
        if not target:
            raise HTTPException(404, 'not_found')
        role = d.get('role', target['role'])
        language = d.get('language', target['language'])
        enabled = 1 if d.get('enabled', bool(target['enabled'])) else 0
        if role not in ROLES or language not in LANGUAGES:
            raise HTTPException(400, 'invalid_role')
        if (role != 'admin' or not enabled) and target['role'] == 'admin' and not _other_admins(c, uid):
            raise HTTPException(409, 'last_admin')
        c.execute('UPDATE users SET role=?, language=?, enabled=? WHERE id=?', (role, language, enabled, uid))
        if d.get('password'):
            if len(str(d['password'])) < 10:
                raise HTTPException(400, 'password_too_short')
            c.execute('UPDATE users SET password_hash=? WHERE id=?', (hash_password(str(d['password'])), uid))
    audit(u, 'user.edit', target['username'],
          {'role': role, 'language': language, 'enabled': bool(enabled), 'password_reset': bool(d.get('password'))})
    return {'ok': True}


@app.delete('/api/users/{uid}')
def delete_user(uid: int, r: Request):
    u = current_user(r, 'admin')
    with db() as c:
        target = c.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
        if not target:
            raise HTTPException(404, 'not_found')
        if target['id'] == u['id']:
            raise HTTPException(409, 'cannot_delete_self')
        if target['role'] == 'admin' and not _other_admins(c, uid):
            raise HTTPException(409, 'last_admin')
        c.execute('DELETE FROM users WHERE id=?', (uid,))
    audit(u, 'user.delete', target['username'])
    return {'ok': True}


@app.get('/api/audit')
def list_audit(r: Request, q: str = '', limit: int = 500):
    current_user(r, 'manage')
    sql, args = 'SELECT * FROM audit', []
    if q:
        sql += ' WHERE username LIKE ? OR action LIKE ? OR target LIKE ? OR detail LIKE ?'
        args = [f'%{q}%'] * 4
    sql += ' ORDER BY id DESC LIMIT ?'
    args.append(max(1, min(limit, 5000)))
    with db() as c:
        return [dict(x) for x in c.execute(sql, args)]


@app.get('/api/settings')
def settings(r: Request):
    current_user(r, 'admin')
    return {'enroll_token': cfg()['enroll_token'], 'hub_version': HUB_VERSION, 'agent_version': AGENT_VERSION,
            'roles': {role: sorted(p for p, rs in PERMS.items() if role in rs) for role in ROLES}}


# ---------------------------------------------------------------- SSH provisioning jobs

@app.post('/api/provision')
async def provision(r: Request):
    u = current_user(r, 'manage')
    d = await body(r)
    params = {k: text(d.get(k), 8000) for k in ('host', 'username', 'password', 'private_key', 'passphrase', 'name',
                                                 'hub_url', 'group', 'location')}
    params['port'] = int(d.get('port') or 22)
    params['reenroll'] = bool(d.get('reenroll'))
    params['forget_host_key'] = bool(d.get('forget_host_key'))
    params['password'] = str(d.get('password') or '')  # keep exact password (no trimming)
    params['mode'] = 'node' if d.get('mode') == 'node' else 'agent'
    if not params['host'] or not params['username'] or not params['hub_url']:
        raise HTTPException(400, 'missing_fields')
    if not params['password'] and not params['private_key']:
        raise HTTPException(400, 'missing_fields')
    if params['mode'] == 'node':   # full CARACAL node on Docker: needs the image and a version
        params['image'] = images.node_image()
        if not params['image']:
            raise HTTPException(400, 'node_image_missing')
        params['version'] = text(d.get('version'), 64) or images.latest_version() or 'latest'
        if not images.VERSION_RE.match(params['version']):
            raise HTTPException(400, 'version_required')
    job_id = provisioning.create_job('node_install' if params['mode'] == 'node' else 'agent_install',
                                     params['host'], u['username'])
    provisioning.start(job_id, params, u)
    audit(u, 'provision.start', params['host'], {'job': job_id, 'name': params['name']})
    return {'job_id': job_id}


@app.post('/api/discover')
async def discover(r: Request):
    """Scan a local network for devices with SSH (new Raspberry Pis to install)."""
    u = current_user(r, 'manage')
    d = await body(r)
    try:
        hosts = provisioning.discovery_hosts(text(d.get('cidr'), 64))
    except ValueError:
        raise HTTPException(400, 'invalid_network')
    port = int(d.get('port') or 22)
    job_id = provisioning.create_job('discover', text(d.get('cidr'), 64), u['username'])
    provisioning.start_discovery(job_id, hosts, port)
    audit(u, 'discover.start', text(d.get('cidr'), 64))
    return {'job_id': job_id}


@app.get('/api/jobs')
def list_jobs(r: Request, limit: int = 100):
    current_user(r)
    with db() as c:
        return [dict(x) for x in c.execute('SELECT * FROM jobs ORDER BY id DESC LIMIT ?', (max(1, min(limit, 1000)),))]


@app.get('/api/jobs/{jid}')
def get_job(jid: int, r: Request):
    current_user(r)
    with db() as c:
        row = c.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone()
    if not row:
        raise HTTPException(404, 'not_found')
    return dict(row)


# ---------------------------------------------------------------- agent bootstrap (public, contains no secrets)

@app.get('/api/bootstrap/{name}', response_class=PlainTextResponse)
def bootstrap_file(name: str):
    if name not in provisioning.AGENT_FILES + provisioning.NODE_FILES + ('caracal-firstboot.sh',):
        raise HTTPException(404, 'not_found')
    return (BOOT / name).read_text(encoding='utf-8')


# ---------------------------------------------------------------- device (agent) API

@app.post('/api/device/enroll')
async def enroll(r: Request):
    d = await body(r)
    if not secrets.compare_digest(str(d.get('enroll_token', '')), cfg()['enroll_token']):
        raise HTTPException(401, 'invalid_enroll_token')
    fingerprint = str(d.get('fingerprint') or secrets.token_hex(8))
    did = 'CRCL-' + hashlib.sha256(fingerprint.encode()).hexdigest()[:8].upper()
    existing_token = str(d.get('device_token') or '')
    name = text(d.get('name'), 120) or did
    now = time.time()
    with db() as c:
        row = c.execute('SELECT * FROM devices WHERE id=?', (did,)).fetchone()
        if row and existing_token and secrets.compare_digest(row['token_hash'] or '', token_hash(existing_token)):
            tok = existing_token  # re-enrollment keeps the working token
        else:
            tok = secrets.token_urlsafe(32)
        if row:
            c.execute('UPDATE devices SET token_hash=?, last_seen=? WHERE id=?', (token_hash(tok), now, did))
        else:
            c.execute('INSERT INTO devices(id, token_hash, name, ip, version, last_seen, status_json, created) '
                      'VALUES(?,?,?,?,?,?,?,?)', (did, token_hash(tok), name, '', '', now, '{}', now))
    audit(None, 'device.enroll', did, {'name': name, 'new': not row, 'token_kept': tok == existing_token})
    return {'device_id': did, 'device_token': tok}


def _client_ip(r: Request):
    fwd = r.headers.get('X-Forwarded-For', '')
    return fwd.split(',')[0].strip() if fwd else (r.client.host if r.client else '')


@app.get('/api/device/{did}/ping')
def device_ping(did: str, r: Request):
    device_auth(r, did)
    return {'ok': True}


@app.post('/api/device/{did}/heartbeat')
async def heartbeat(did: str, r: Request):
    device_auth(r, did)
    d = await body(r)
    with db() as c:
        c.execute('UPDATE devices SET ip=?, version=?, last_seen=?, status_json=? WHERE id=?',
                  (text(d.get('ip'), 64) or _client_ip(r), text(d.get('version'), 32), time.time(),
                   json.dumps(d, ensure_ascii=False), did))
    return {'ok': True, 'agent_version': AGENT_VERSION, 'server_time': time.time()}


@app.get('/api/device/{did}/commands')
def pull_commands(did: str, r: Request):
    device_auth(r, did)
    with db() as c:
        sweep_commands(c)
        rows = c.execute("SELECT * FROM commands WHERE device_id=? AND state='queued' ORDER BY id", (did,)).fetchall()
        now = time.time()
        for x in rows:
            # credentials of login profiles are not kept once the agent has them
            c.execute("UPDATE commands SET state='delivered', updated=?, payload_json=? WHERE id=?",
                      (now, redact_json(x['action'], x['payload_json']), x['id']))
    return [{'id': x['id'], 'action': x['action'], 'payload': json.loads(x['payload_json'] or '{}')} for x in rows]


@app.post('/api/device/{did}/commands/{cid}/result')
async def command_result(did: str, cid: int, r: Request):
    device_auth(r, did)
    d = await body(r)
    ok = bool(d.get('ok'))
    result = d.get('result', '')
    if not isinstance(result, str):
        result = json.dumps(result, ensure_ascii=False)
    with db() as c:
        row = c.execute('SELECT * FROM commands WHERE id=? AND device_id=?', (cid, did)).fetchone()
        if not row:
            raise HTTPException(404, 'not_found')
        c.execute('UPDATE commands SET state=?, result=?, updated=? WHERE id=?',
                  ('completed' if ok else 'failed', result[-16000:], time.time(), cid))
        if ok and row['followup_json']:
            _run_followup(c, row, json.loads(row['followup_json']), result)
    return {'ok': True}


def _run_followup(c, row, followup, result):
    if followup.get('action') != 'import_playlist':
        return
    try:
        files = json.loads(result).get('files', {})
    except (ValueError, AttributeError):
        files = {}
    items = []
    for it in followup['items']:
        if it.get('type') == 'asset' and it.get('kind') in MEDIA_KINDS:
            fid = files.get(str(it.get('source_asset_id')))
            if not fid:
                continue
            it = {**it, 'file_id': fid}
            f = c.execute('SELECT sha256, name FROM files WHERE id=?', (fid,)).fetchone()
            if f:
                it.update(sha256=f['sha256'], filename=f['name'])
        items.append(it)
    for t in followup['targets']:
        queue_command(c, t, 'import_playlist', {'items': items, 'mode': followup.get('mode', 'append'),
                                                'replace_collections': followup.get('replace_collections', False)},
                      row['username'], row['batch'])


@app.get('/api/device/{did}/files/{fid}')
def device_download(did: str, fid: str, r: Request):
    device_auth(r, did)
    with db() as c:
        f = c.execute('SELECT * FROM files WHERE id=?', (fid,)).fetchone()
    if not f or not (FILES / fid).exists():
        raise HTTPException(404, 'file_not_found')
    return FileResponse(FILES / fid, filename=f['name'], headers={'X-Sha256': f['sha256']})


@app.post('/api/device/{did}/files')
async def device_upload(did: str, r: Request):
    device_auth(r, did)
    # Devices may upload only while they execute an export (copying media between nodes).
    with db() as c:
        if not c.execute("SELECT 1 FROM commands WHERE device_id=? AND action='export_assets' AND state='delivered'",
                         (did,)).fetchone():
            raise HTTPException(403, 'forbidden')
    name = unquote(r.headers.get('X-File-Name', 'file'))[:200] or 'file'
    kind = r.headers.get('X-File-Kind', '')
    if kind not in MEDIA_KINDS:
        raise HTTPException(400, 'unsupported_file')
    return {'id': await _store_upload(r, name, kind, 'device:' + did)}


@app.get('/api/device/{did}/agent')
def agent_package(did: str, r: Request):
    device_auth(r, did)
    code = (BOOT / 'agent.py').read_bytes()
    return {'version': AGENT_VERSION, 'sha256': hashlib.sha256(code).hexdigest(),
            'code': base64.b64encode(code).decode()}


app.mount('/static', StaticFiles(directory=STATIC), name='static')
