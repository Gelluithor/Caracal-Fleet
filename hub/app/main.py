"""CARACAL Fleet Controller - central management of CARACAL signage nodes."""
import asyncio
import base64
import hashlib
import json
import mimetypes
import re
import secrets
import time
from contextlib import asynccontextmanager
from urllib.parse import unquote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import access, approvals, console, images, library, monitor, notify, offsite, plays, playlists, provisioning, proxy, releases, rollout, schedule, sdcard, security, system, takeover, templates, timeline, widgets
from .core import (ACTIONS, AGENT_VERSION, BOOT, DEVICE_PERMISSIONS, FILES, HUB_VERSION, LANGUAGES, ONLINE_TIMEOUT, APP_DIR, require_device,
                   audit, cfg, check_password, cleanup_files, current_user, db, device_auth, hash_password, init,
                   make_session, new_batch, public_user, queue_command, redact, redact_json, scrub_secrets,
                   sweep_commands, token_hash, validate_admin, version_tuple, DARK_MODES, COMMAND_SEQ)
from .devices import COLLECTION_KIND, MEDIA_KINDS, SCREENSHOTS, build, collections_of, dashboards_of, grafana_config, notifications_of

@asynccontextmanager
async def lifespan(_app):
    monitor.start()          # the background check of the fleet (events and alerts)
    schedule.start()         # schedules and scheduled actions
    yield


app = FastAPI(title='CARACAL Fleet Controller', version=HUB_VERSION, docs_url=None, redoc_url=None,
              openapi_url=None, lifespan=lifespan)
init()
STATIC = APP_DIR / 'static'
app.include_router(system.router)
app.include_router(playlists.router)
app.include_router(releases.router)
app.include_router(images.router)
app.include_router(sdcard.router)       # before /api/bootstrap/{name} (node-config)
app.include_router(console.router)      # SSH web console (WebSocket)
app.include_router(notify.router)       # notification API for other apps and its tokens
app.include_router(proxy.router)        # Fleet as the download source of nodes without internet access
app.include_router(monitor.router)      # metric history, events and alerts for administrators
app.include_router(templates.router)    # node templates
app.include_router(schedule.router)     # schedules and scheduled actions
app.include_router(rollout.router)      # staged updates of Docker nodes
app.include_router(security.router)     # two-factor sign-in and single sign-on
app.include_router(access.router)       # users, roles and user groups
app.include_router(offsite.router)      # backups outside the server
app.include_router(approvals.router)    # content changes waiting for an approval
app.include_router(takeover.router)     # emergency messages on the whole screen
app.include_router(plays.router)        # proof of play
app.include_router(timeline.router)     # what plays when
app.include_router(library.router)      # the media library
app.include_router(widgets.router)      # feeds for the widget preview


SECURITY_HEADERS = {
    # no inline scripts, no third-party resources; inline styles are used for progress bars
    'Content-Security-Policy': "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
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
    if request.url.path == '/static/widgets.html':   # the widget preview runs in a frame of Fleet's own page
        response.headers['Content-Security-Policy'] = SECURITY_HEADERS['Content-Security-Policy'].replace(
            "frame-ancestors 'none'", "frame-ancestors 'self'").replace("img-src 'self' data: blob:", "img-src 'self' data: blob: https:")
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    for k, v in SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    if request.url.path.startswith('/api/'):
        response.headers.setdefault('Cache-Control', 'no-store')
    return response

LIVE_ACTIONS = {'next', 'unfreeze', 'show', 'freeze', 'show_collection', 'freeze_collection', 'restart_player',
                'reboot'}
INTERNAL_ACTIONS = {'import_playlist', 'export_assets', 'takeover', 'takeover_end'}
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

def static_version():
    """The hub version plus a fingerprint of the UI files: browsers fetch changed scripts and styles even when the
    version stayed the same (a fix between releases)."""
    h = hashlib.sha256()
    for name in ('app.js', 'i18n.js', 'style.css', 'notify-style.js'):
        try:
            h.update((STATIC / name).read_bytes())
        except OSError:
            pass
    return f'{HUB_VERSION}-{h.hexdigest()[:8]}'


@app.get('/', response_class=HTMLResponse)
def home():
    html = (STATIC / 'index.html').read_text(encoding='utf-8').replace('{{VERSION}}', static_version())
    return HTMLResponse(html, headers={'Cache-Control': 'no-cache'})


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
    if u['totp_secret']:
        # the second factor: an authenticator code or a recovery code (wrong codes count as failed attempts)
        if not str(d.get('code') or '').strip():
            raise HTTPException(401, 'totp_required')
        with db() as c:
            ok = security.check_second_factor(c, u, d.get('code'))
        if not ok:
            _login_failures[ip_key] = ip_fails + [time.time()]
            _login_failures[user_key] = user_fails + [time.time()]
            audit(dict(u), 'user.login_failed', username, {'ip': ip_key[0], 'totp': True})
            raise HTTPException(401, 'invalid_totp')
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
    u = current_user(r, None)
    return {**public_user(u), 'hub_version': HUB_VERSION, 'agent_version': AGENT_VERSION}


@app.patch('/api/me')
async def edit_me(r: Request):
    u = current_user(r, None)
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
            if check_password(str(d['new_password']), u['password_hash']):
                raise HTTPException(400, 'password_same')
            u['password_hash'] = hash_password(str(d['new_password']))
            c.execute('UPDATE users SET password_hash=?, must_change_password=0 WHERE id=?', (u['password_hash'], u['id']))
            token = make_session(u)
    if d.get('new_password'):
        audit(u, 'user.password', u['username'])
    return {'ok': True, 'token': token}


# ---------------------------------------------------------------- devices

def latest_versions():
    return {'docker': images.latest_version(), 'host': releases.latest_version()}


@app.get('/api/devices')
def list_devices(r: Request):
    u = current_user(r)
    with db() as c:
        sweep_commands(c)
        failed = failed_counts(c)
        rows = [x for x in c.execute('SELECT * FROM devices ORDER BY name COLLATE NOCASE').fetchall()
                if u['access'].on('view', x)]
        pending = {x['device_id']: x['n'] for x in c.execute(
            "SELECT device_id, COUNT(*) n FROM commands WHERE state IN ('queued','delivered') GROUP BY device_id")}
    latest = latest_versions()
    mutes = monitor.config()['mutes']
    devices = []
    for row in rows:
        d = build(row, failed.get(row['id'], 0), latest_caracal=latest)
        d['pending_commands'] = pending.get(row['id'], 0)
        d['muted_until'] = mutes.get(row['id'])
        d['can'] = [p for p in DEVICE_PERMISSIONS if u['access'].on(p, row)]
        devices.append(d)
    with db() as c:
        waiting = approvals.pending_count(c, u)
        live = [{'id': x['id'], 'title': x['title'] or x['message'][:60], 'level': x['level'], 'until': x['until'],
                 'devices': len(json.loads(x['devices_json'] or '[]'))} for x in takeover.active(c)
                if any(d['id'] in {y['id'] for y in devices} for d in json.loads(x['devices_json'] or '[]'))]
    return {'devices': devices, 'attention_count': sum(1 for d in devices if d['needs_attention']),
            'agent_version': AGENT_VERSION, 'hub_version': HUB_VERSION, 'server_time': time.time(),
            'approvals_pending': waiting, 'approval_enabled': bool(cfg().get('require_approval')), 'takeovers': live, 'tags': sorted({t for d in devices for t in d['tags']}, key=str.lower)}


CSV_COLUMNS = ('id', 'name', 'hostname', 'ip', 'location', 'group', 'tags', 'notes', 'version', 'caracal_version', 'online', 'last_seen')


@app.get('/api/devices/export.csv')
def export_devices(r: Request):
    import csv
    import io
    u = current_user(r)
    with db() as c:
        rows = [x for x in c.execute('SELECT * FROM devices ORDER BY name COLLATE NOCASE').fetchall() if u['access'].on('view', x)]
    out = io.StringIO()
    w = csv.writer(out, delimiter=';')
    w.writerow(CSV_COLUMNS)
    for row in rows:
        d = build(row)
        w.writerow([d['id'], d['name'], d['hostname'], d['ip'], d['location'], d['group'], ', '.join(d['tags']), d['notes'],
                    d['version'], d['caracal_version'], 'yes' if d['online'] else 'no',
                    time.strftime('%Y-%m-%d %H:%M', time.localtime(d['last_seen'])) if d['last_seen'] else ''])
    return PlainTextResponse('\ufeff' + out.getvalue(), media_type='text/csv; charset=utf-8',
                             headers={'Content-Disposition': 'attachment; filename="caracal-devices.csv"'})


@app.get('/api/devices/{did}')
def device_detail(did: str, r: Request):
    u = current_user(r)
    with db() as c:
        sweep_commands(c)
        row = get_device_row(c, did)
        require_device(u, 'view', row)
        failed = failed_counts(c)
        cmds = [dict(x) for x in c.execute(
            'SELECT id, action, payload_json, state, result, created, updated, username FROM commands '
            'WHERE device_id=? ORDER BY id DESC LIMIT 30', (did,))]
    for x in cmds:
        x['payload_json'] = redact_json(x['action'], x['payload_json'])
    d = build(row, failed.get(did, 0), full=True, latest_caracal=latest_versions())
    d['commands'] = cmds
    d['pending_commands'] = sum(1 for x in cmds if x['state'] in ('queued', 'delivered'))
    d['muted_until'] = monitor.muted_until(did)
    d['can'] = [p for p in DEVICE_PERMISSIONS if u['access'].on(p, row)]   # what this user may do with it
    return d


@app.patch('/api/devices/{did}')
async def edit_device(did: str, r: Request):
    u = current_user(r, 'devices')
    d = await body(r)
    with db() as c:
        row = dict(get_device_row(c, did))
        require_device(u, 'devices', row)
        name = text(d.get('name', row['name']), 120) or did
        group = text(d.get('group', row['device_group']), 120)
        location = text(d.get('location', row['location']), 120)
        notes = text(d.get('notes', row['notes']), 2000)
        tags = clean_tags(d['tags']) if 'tags' in d else json.loads(row['tags_json'] or '[]')
        _check_move(c, u, [row], group, location)
        c.execute('UPDATE devices SET name=?, device_group=?, location=?, notes=?, tags_json=? WHERE id=?',
                  (name, group, location, notes, json.dumps(tags, ensure_ascii=False), did))
        _ensure_org(c, group, location)
    audit(u, 'device.edit', did, {'name': name, 'group': group, 'location': location})
    return {'ok': True}


def clean_tags(v):
    """Labels of a device: a list or a comma separated text; trimmed, without duplicates, at most 20."""
    items = v.split(',') if isinstance(v, str) else v if isinstance(v, list) else []
    out = []
    for t in items:
        t = text(t, 40)
        if t and t.lower() not in [x.lower() for x in out]:
            out.append(t)
    return out[:20]


@app.post('/api/devices/tags')
async def bulk_tags(r: Request):
    """Adds and removes labels on many devices at once."""
    u = current_user(r, 'devices')
    d = await body(r)
    add, remove = clean_tags(d.get('add') or []), {t.lower() for t in clean_tags(d.get('remove') or [])}
    ids = [str(x) for x in d.get('device_ids') or []]
    with db() as c:
        for did in ids:
            row = get_device_row(c, did)
            require_device(u, 'devices', row)
            tags = [t for t in json.loads(row['tags_json'] or '[]') if t.lower() not in remove]
            c.execute('UPDATE devices SET tags_json=? WHERE id=?', (json.dumps(clean_tags(tags + add), ensure_ascii=False), did))
    audit(u, 'device.tags', ','.join(ids), {'add': add, 'remove': sorted(remove)})
    return {'ok': True}


@app.post('/api/devices/import')
async def import_devices(r: Request):
    """Names, locations, groups, labels and notes of many devices from a table (CSV with ; or ,). A row finds its
    device by the column id, else hostname, else name. A column that is not in the table stays as it is; an empty cell
    clears (except the name). With apply=false only the changes are listed."""
    import csv
    import io
    u = current_user(r, 'devices')
    d = await body(r)
    raw = str(d.get('csv') or '').lstrip('\ufeff')
    if not raw.strip():
        raise HTTPException(400, 'empty_file')
    first = raw.splitlines()[0]
    reader = csv.DictReader(io.StringIO(raw), delimiter=';' if first.count(';') >= first.count(',') else ',')
    fields = {f.strip().lower(): f for f in reader.fieldnames or []}
    alias = {'název': 'name', 'nazev': 'name', 'lokalita': 'location', 'skupina': 'group', 'štítky': 'tags', 'stitky': 'tags',
             'poznámka': 'notes', 'poznamka': 'notes', 'device_id': 'id'}
    fields = {alias.get(k, k): v for k, v in fields.items()}
    if not {'id', 'hostname', 'name'} & set(fields):
        raise HTTPException(400, 'csv_no_key')
    results, apply = [], bool(d.get('apply'))
    with db() as c:
        rows = [x for x in c.execute('SELECT * FROM devices').fetchall() if u['access'].on('view', x)]
        hosts = {}
        for x in rows:
            hosts.setdefault((json.loads(x['status_json'] or '{}').get('hostname') or '').lower(), []).append(x)
        for n, line in enumerate(reader, start=2):
            cell = lambda k: (line.get(fields[k]) or '').strip() if k in fields else None
            match = []
            if cell('id'):
                match = [x for x in rows if x['id'] == cell('id')]
            elif cell('hostname'):
                match = hosts.get(cell('hostname').lower(), [])
            elif cell('name'):
                match = [x for x in rows if (x['name'] or '').lower() == cell('name').lower()]
            label = cell('id') or cell('hostname') or cell('name') or ''
            if len(match) != 1:
                results.append({'line': n, 'key': label, 'error': 'csv_ambiguous' if match else 'device_not_found'})
                continue
            row = dict(match[0])
            # the name changes only when the device was found by its id or host name (else the name found it)
            new = {'name': (cell('name') or None) if cell('id') or cell('hostname') else None,
                   'location': cell('location'), 'group': cell('group'), 'notes': cell('notes'),
                   'tags': clean_tags(cell('tags')) if 'tags' in fields else None}
            old = {'name': row['name'], 'location': row['location'] or '', 'group': row['device_group'] or '',
                   'notes': row['notes'] or '', 'tags': json.loads(row['tags_json'] or '[]')}
            changes = {k: {'from': old[k], 'to': v} for k, v in new.items() if v is not None and v != old[k]}
            entry = {'line': n, 'key': label, 'device_id': row['id'], 'device': row['name'], 'changes': changes}
            try:
                if changes and not u['access'].on('devices', row):
                    raise HTTPException(403, 'forbidden')
                if 'location' in changes or 'group' in changes:
                    _check_move(c, u, [row], new['group'] if 'group' in changes else None,
                                new['location'] if 'location' in changes else None)
            except HTTPException as e:
                entry['error'] = str(e.detail)
                results.append(entry)
                continue
            if apply and changes:
                v = {k: (changes[k]['to'] if k in changes else old[k]) for k in old}
                c.execute('UPDATE devices SET name=?, location=?, device_group=?, notes=?, tags_json=? WHERE id=?',
                          (text(v['name'], 120) or row['id'], text(v['location'], 120), text(v['group'], 120),
                           text(v['notes'], 2000), json.dumps(v['tags'], ensure_ascii=False), row['id']))
                _ensure_org(c, text(v['group'], 120), text(v['location'], 120))
            results.append(entry)
    if apply:
        audit(u, 'device.import', '', {'rows': len(results), 'changed': sum(1 for x in results if x.get('changes') and not x.get('error'))})
    return {'applied': apply, 'rows': results}


@app.delete('/api/devices/{did}')
def delete_device(did: str, r: Request):
    u = current_user(r, 'devices')
    with db() as c:
        row = get_device_row(c, did)
        require_device(u, 'devices', row)
        c.execute('DELETE FROM commands WHERE device_id=?', (did,))
        c.execute('DELETE FROM devices WHERE id=?', (did,))
        (SCREENSHOTS / f'{did}.jpg').unlink(missing_ok=True)
        for table in ('metrics', 'issues', 'events'):
            c.execute(f'DELETE FROM {table} WHERE device_id=?', (did,))
    audit(u, 'device.delete', did, {'name': row['name']})
    return {'ok': True}


@app.post('/api/devices/assign')
async def assign_devices(r: Request):
    """Bulk assignment of group and/or location. Missing key = unchanged, empty string = cleared."""
    u = current_user(r, 'devices')
    d = await body(r)
    ids = [str(x) for x in d.get('device_ids') or []]
    with db() as c:
        rows = [get_device_row(c, i) for i in ids]
        for row in rows:
            require_device(u, 'devices', row)
        _check_move(c, u, [dict(x) for x in rows], text(d['group'], 120) if 'group' in d else None,
                    text(d['location'], 120) if 'location' in d else None)
        for key, col in (('group', 'device_group'), ('location', 'location')):
            if key in d:
                c.executemany(f'UPDATE devices SET {col}=? WHERE id=?', [(text(d[key], 120), i) for i in ids])
        _ensure_org(c, text(d.get('group'), 120), text(d.get('location'), 120))
    audit(u, 'device.assign', ','.join(ids), {k: d[k] for k in ('group', 'location') if k in d})
    return {'ok': True}


def _check_move(c, u, rows, group, location):
    """A user with a scope may only move devices within it and only to groups and locations that exist.
    group/location None = unchanged."""
    if u['access'].everywhere('devices'):
        return
    for kind, table, value in (('group', 'device_groups', group), ('location', 'device_locations', location)):
        if value and not c.execute(f'SELECT 1 FROM {table} WHERE name=?', (value,)).fetchone():
            raise HTTPException(403, f'new_{kind}_forbidden')
    for row in rows:
        moved = {**row, **({'device_group': group} if group is not None else {}),
                 **({'location': location} if location is not None else {})}
        if not u['access'].on('devices', moved):
            raise HTTPException(403, 'move_outside_scope')


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
        known = [str(a.get('id')) for a in status.get('assets') or [] if isinstance(a, dict)]
        known += [str(x['id']) for x in dashboards_of(status)]   # a single dashboard of a collection
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
    caps = status.get('capabilities') if isinstance(status.get('capabilities'), dict) else {}
    if action in ('add_web', 'update_asset') and str(payload.get('source') or '').startswith(playlists.WIDGET_PREFIX) \
            and caps.get('widgets') is not True:
        raise HTTPException(409, 'update_required')   # widgets: CARACAL 2026.10.23.1 with agent 4.17
    if action == 'add_web':
        src = text(payload.get('source'), 4000)
        if not src.lower().startswith(('http://', 'https://', playlists.WIDGET_PREFIX)):
            raise HTTPException(400, 'invalid_url')
        payload['source'] = src
        payload['name'] = text(payload.get('name'), 200) or src
    if action in ('add_web', 'add_media', 'update_asset', 'add_collection', 'update_collection'):
        payload.update(validate_timing(payload))
    if action in ('add_web', 'update_asset', 'add_collection', 'update_collection') and 'dark' in payload:
        payload['dark'] = validate_dark(payload['dark'])
    if action in AGENT_FEATURES and version_tuple(row['version']) < AGENT_FEATURES[action]:
        raise HTTPException(409, 'agent_update_required')
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
        if payload['kind'] not in MEDIA_KINDS:
            raise HTTPException(400, 'unsupported_file')
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
        if not images.VERSION_RE.fullmatch(version):
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
        admin = validate_admin(payload) if action == 'convert_to_docker' and payload.get('username') else {}
        payload = {'version': version, 'image': images.node_image(), **admin}
    elif action == 'reinstall_node':
        # CARACAL installed again (any runtime; a classic node becomes a Docker node), optionally without its data:
        # then optionally with a new web administrator and a template applied once the node is back
        version = text(payload.get('version'), 64)
        if not images.VERSION_RE.fullmatch(version):
            raise HTTPException(400, 'version_required')
        if not images.node_image():
            raise HTTPException(400, 'node_image_missing')
        if not online:
            raise HTTPException(409, 'device_offline')
        wipe = flag(payload.get('wipe_data'))
        admin = validate_admin(payload) if wipe and payload.get('username') else {}
        template = None
        if payload.get('template') not in (None, '', 0, '0'):
            try:
                template = int(payload['template'])
            except (TypeError, ValueError):
                raise HTTPException(400, 'template_not_found')
            if not c.execute('SELECT 1 FROM node_templates WHERE id=?', (template,)).fetchone():
                raise HTTPException(400, 'template_not_found')
        payload = {'version': version, 'image': images.node_image(), 'wipe_data': wipe, **admin,
                   **({'template': template} if template else {})}
    if action in NOTIFY_CAPABILITY and (status.get('capabilities') or {}).get(NOTIFY_CAPABILITY[action]) is False:
        raise HTTPException(409, 'notifications_unsupported')
    # newer than the agent or the node: only when the agent reports that the node has the endpoint
    if action in NEW_CAPABILITY and (status.get('capabilities') or {}).get(NEW_CAPABILITY[action]) is not True:
        raise HTTPException(409, 'update_required')
    if action == 'notify_image':
        payload = validate_notify_image(c, payload)
    if action == 'screenshot':
        payload = {}
    if action == 'notify':
        payload = validate_notification(payload)
    if action == 'notify_settings':
        payload = validate_notify_settings(payload)
    if action in ('add_watcher', 'update_watcher'):
        payload = {**({'id': payload['id']} if payload.get('id') not in (None, '') else {}),
                   **validate_watcher(payload, required=action == 'add_watcher')}
    if action == 'notify_sound':
        payload = validate_notify_sound(c, payload)
    if action in ('update_watcher', 'delete_watcher', 'check_watcher'):
        if payload.get('id') in (None, ''):
            raise HTTPException(400, 'item_required')
        watchers = (status.get('notifications') or {}).get('watchers')
        if watchers is not None and str(payload['id']) not in [str(w.get('id')) for w in watchers]:
            raise HTTPException(409, 'watcher_not_found')
    if action == 'preview_watcher':
        # tries a watcher configuration without saving it (secrets only travel to the node, like for add_watcher)
        payload = {**({'id': payload['id']} if payload.get('id') not in (None, '') else {}),
                   **validate_watcher(payload, required=payload.get('id') in (None, ''))}
    if action == 'set_admin':
        payload = validate_admin(payload)
    if action == 'overlay_settings':
        payload = validate_overlay(payload)
    if action == 'player_settings':
        if payload.get('web_dark') not in DARK_MODES:
            raise HTTPException(400, 'invalid_value')
        payload = {'web_dark': payload['web_dark']}
    if action == 'diagnostics':
        payload = {}
    if action in ('add_camera', 'update_camera'):
        payload = validate_camera(payload, action == 'update_camera')
    if action == 'camera_onvif':
        host = text(payload.get('host'), 255)
        if not re.fullmatch(r'[A-Za-z0-9.\-:\[\]]{1,255}', host):
            raise HTTPException(400, 'invalid_value')
        payload = {'host': host, 'port': int(payload.get('port') or 80) if str(payload.get('port') or '80').isdigit() else 80,
                   'username': text(payload.get('username'), 200), 'password': str(payload.get('password') or '')[:200],
                   'xaddr': text(payload.get('xaddr'), 500)}
    if action == 'camera_discover':
        payload = {}
    if action == 'camera_preview':   # one picture of a stream: an address with credentials, or a stored camera's stream
        if payload.get('id') not in (None, ''):
            try:
                payload = {'id': int(payload['id']), 'index': max(0, min(int(payload.get('index') or 0), CAMERA_MAX - 1))}
            except (TypeError, ValueError):
                raise HTTPException(400, 'item_required')
        else:
            url = text(payload.get('url'), 2000)
            if not url.lower().startswith(CAMERA_SCHEMES) or any(ch.isspace() for ch in url):
                raise HTTPException(400, 'invalid_stream_url')
            payload = {'url': url, 'username': text(payload.get('username'), 200), 'password': str(payload.get('password') or '')[:200]}
    if action == 'screen':
        if payload.get('state') not in ('on', 'off'):
            raise HTTPException(400, 'invalid_value')
        payload = {'state': payload['state']}
    if action in ('notify_remove', 'update_notify_token', 'delete_notify_token'):
        try:
            payload['id'] = int(payload.get('id'))
        except (TypeError, ValueError):
            raise HTTPException(400, 'item_required')
    if action == 'update_notify_token':
        payload = validate_notify_token(payload)
    if action == 'grafana_discover':
        payload = validate_grafana({k: payload.get(k) for k in ('grafana_url', 'tag')} | {'name': 'x'})
        payload.pop('name', None)
        payload.pop('kiosk', None)
    if action == 'set_download_source':
        # where the node downloads CARACAL images and system packages: the internet or this hub
        if payload.get('source') not in ('internet', 'fleet'):
            raise HTTPException(400, 'invalid_value')
        payload = {'source': payload['source']}
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
    out.update(validate_validity(d))
    return out


def validate_validity(d):
    """An item shown only from / until a time (Unix time; None = without a limit). CARACAL 2026.10.14 and newer."""
    out = {}
    for key in ('valid_from', 'valid_until'):
        if key not in d:
            continue
        if d[key] in (None, ''):
            out[key] = None
            continue
        try:
            out[key] = float(d[key])
        except (TypeError, ValueError):
            raise HTTPException(400, 'invalid_validity')
        if not 946684800 <= out[key] <= 4102444800:
            raise HTTPException(400, 'invalid_validity')
    if out.get('valid_from') and out.get('valid_until') and out['valid_until'] <= out['valid_from']:
        raise HTTPException(400, 'invalid_validity')
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
    """Login profile of a web page: 'form' = CARACAL fills the login form, then opens the target page;
    'http' = HTTP Basic/Digest (the browser's pop-up), only the target address and the credentials matter.
    On edits an empty username or password keeps the stored one; empty selectors keep theirs."""
    out = {}
    if 'auth_type' in d:
        out['auth_type'] = text(d.get('auth_type'), 10) or 'form'
        if out['auth_type'] not in PROFILE_TYPES:
            raise HTTPException(400, 'invalid_value')
    if required or 'name' in d:
        out['name'] = text(d.get('name'), 200)
        if not out['name']:
            raise HTTPException(400, 'name_required')
    for key in ('login_url', 'target_url'):
        if key == 'login_url' and out.get('auth_type') == 'http' and not text(d.get(key), 4000):
            continue   # the node uses the target address
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


PROFILE_TYPES = ('form', 'http')
NOTIFY_LEVELS = ('info', 'success', 'warning', 'critical')
NOTIFY_POSITIONS = ('top-right', 'top-left', 'top', 'bottom-right', 'bottom-left', 'bottom', 'center')
NOTIFY_SOUNDS = ('off', 'critical', 'warning', 'all')
NOTIFY_LIMITS = {'duration': (3, 120), 'max_queue': (1, 200), 'scale': (50, 300), 'volume': (0, 100),
                 'history_max': (50, 5000), 'history_days': (1, 90)}
WATCHER_AUTH = ('none', 'bearer', 'basic', 'header', 'oauth2')
WATCHER_TEXT = {'name': 60, 'url': 4000, 'auth_header': 100, 'list_path': 200, 'id_field': 200,
                'title_template': 500, 'message_template': 1000, 'level_field': 200, 'oauth_token_url': 4000,
                'oauth_client_id': 500, 'oauth_scope': 1000, 'oauth_extra': 1000}
WATCHER_CHOICES = {'auth_type': WATCHER_AUTH, 'oauth_grant': ('client_credentials', 'password', 'refresh_token'),
                   'oauth_client_auth': ('body', 'basic'), 'level': NOTIFY_LEVELS}
WATCHER_SECRETS = ('username', 'secret', 'client_secret', 'refresh_token')
# command -> local endpoint of the node (capability reported by the agent)
NOTIFY_CAPABILITY = {'notify': 'notify', 'notify_clear': 'notify_clear', 'notify_settings': 'notify_settings',
                     'add_watcher': 'add_watcher', 'update_watcher': 'update_watcher',
                     'delete_watcher': 'delete_watcher', 'check_watcher': 'check_watcher',
                     'notify_sound': 'notify_sound', 'notify_skip': 'notify_skip', 'notify_remove': 'notify_remove',
                     'notify_log': 'notify_log', 'notify_history_clear': 'notify_history_clear',
                     'notify_audit_clear': 'notify_audit_clear', 'update_notify_token': 'notify_token_update',
                     'delete_notify_token': 'notify_token_delete', 'preview_watcher': 'preview_watcher'}
SOUND_MAX = 5 * 1024 ** 2
# command -> local endpoint of the node, for commands that old agents do not know at all
NEW_CAPABILITY = {'notify_image': 'notify_image', 'screenshot': 'screenshot', 'player_settings': 'player_settings',
                  'add_camera': 'add_camera', 'update_camera': 'update_camera', 'camera_onvif': 'camera_onvif',
                  'camera_discover': 'camera_discover', 'camera_preview': 'camera_preview'}
CAMERA_SCHEMES, CAMERA_LAYOUTS, CAMERA_MAX = ('rtsp://', 'rtsps://', 'http://', 'https://'), ('auto', '2', '2x2', '3x3'), 9
# what only newer agents can do (they report their version in every heartbeat)
AGENT_FEATURES = {'reinstall_node': (4, 13, 0), 'diagnostics': (4, 13, 0), 'player_settings': (4, 13, 0),
                  'screen': (4, 14, 0)}
IMAGE_MAX = 5 * 1024 ** 2
NOTIFY_IMAGE_PLACES = ('none', 'left', 'right', 'top', 'bottom', 'background')
SCREENSHOT_MAX = 8 * 1024 ** 2


def flag(v):
    return v not in (False, 0, '0', 'false', 'off', 'no', '', None)


def validate_camera(d, edit=False):
    """A camera item: name, display time, layout and 1-9 streams (rtsp/rtsps/http/https) with optional credentials."""
    out = {'name': text(d.get('name'), 200), 'layout': d.get('layout') if d.get('layout') in CAMERA_LAYOUTS else 'auto',
           'audio': bool(d.get('audio'))}
    if edit:
        if d.get('id') in (None, ''):
            raise HTTPException(400, 'item_required')
        out['id'] = d['id']
    if not out['name']:
        raise HTTPException(400, 'name_required')
    if d.get('duration') not in (None, ''):
        out.update(validate_timing({'duration': d['duration']}))
    streams = d.get('streams')
    if not isinstance(streams, list) or not 1 <= len(streams) <= CAMERA_MAX:
        raise HTTPException(400, 'invalid_streams')
    out['streams'] = []
    for st in streams:
        st = st if isinstance(st, dict) else {}
        url = text(st.get('url'), 2000)
        if not url.lower().startswith(CAMERA_SCHEMES) or any(ch.isspace() for ch in url):
            raise HTTPException(400, 'invalid_stream_url')
        out['streams'].append({'name': text(st.get('name'), 80), 'url': url, 'username': text(st.get('username'), 200),
                               'password': str(st.get('password') or '')[:200]})
    out.update(validate_validity(d))
    return out


def validate_dark(v):
    """Dark mode of a website or Grafana collection: '' follows the node's setting."""
    v = '' if v is None else str(v).strip().lower()
    if v and v not in DARK_MODES:
        raise HTTPException(400, 'invalid_value')
    return v


def validate_overlay(d):
    """The countdown bar of the current page on the TV (enabled, height in px)."""
    out = {}
    if 'enabled' in d:
        out['enabled'] = flag(d['enabled'])
    if 'size' in d:
        try:
            out['size'] = int(d['size'])
        except (TypeError, ValueError):
            raise HTTPException(400, 'invalid_value')
        if not 4 <= out['size'] <= 200:
            raise HTTPException(400, 'invalid_value')
    if not out:
        raise HTTPException(400, 'nothing_to_change')
    return out


def validate_notify_token(d):
    """Name, limit and enabled of a notification token of the node (new tokens are created on the node)."""
    out = {'id': d['id']}
    if 'name' in d:
        out['name'] = text(d.get('name'), 60)
        if not out['name']:
            raise HTTPException(400, 'name_required')
    if 'rate_per_min' in d:
        try:
            out['rate_per_min'] = int(d['rate_per_min'])
        except (TypeError, ValueError):
            raise HTTPException(400, 'invalid_value')
        if not 1 <= out['rate_per_min'] <= 600:
            raise HTTPException(400, 'invalid_value')
    if 'enabled' in d:
        out['enabled'] = flag(d['enabled'])
    if len(out) == 1:
        raise HTTPException(400, 'nothing_to_change')
    return out


def bounded(v, lo, hi):
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        raise HTTPException(400, 'invalid_value')
    if not lo <= n <= hi:
        raise HTTPException(400, 'invalid_value')
    return n


def validate_notification(d):
    """A notification typed in Fleet (the same fields as the node's notification API)."""
    out = {'title': text(d.get('title'), 120), 'message': text(d.get('message'), 600)}
    if not out['title'] and not out['message']:
        raise HTTPException(400, 'notification_text_required')
    out = {k: v for k, v in out.items() if v}
    out['level'] = text(d.get('level'), 20) or 'info'
    if out['level'] not in NOTIFY_LEVELS:
        raise HTTPException(400, 'invalid_value')
    if d.get('duration') not in (None, ''):
        out['duration'] = bounded(d['duration'], 3, 120)
    if d.get('sound') not in (None, ''):
        out['sound'] = flag(d['sound'])
    if text(d.get('key'), 200):
        out['key'] = text(d.get('key'), 200)
    # the picture of this notification: where (or none) and how big; empty = as the look says (CARACAL 2026.10.10.4)
    if d.get('image') not in (None, ''):
        if d['image'] not in NOTIFY_IMAGE_PLACES:
            raise HTTPException(400, 'invalid_value')
        out['image'] = d['image']
    if d.get('image_size') not in (None, ''):
        out['image_size'] = bounded(d['image_size'], 10, 60)
    return out


def validate_notify_settings(d):
    """Notification settings of a node; keys that are not sent stay as they are on the node."""
    out = {}
    if 'enabled' in d:
        out['enabled'] = flag(d['enabled'])
    for key, choices in (('position', NOTIFY_POSITIONS), ('sound', NOTIFY_SOUNDS)):
        if key in d:
            if d[key] not in choices:
                raise HTTPException(400, 'invalid_value')
            out[key] = d[key]
    for key, (lo, hi) in NOTIFY_LIMITS.items():
        if d.get(key) not in (None, ''):
            out[key] = bounded(d[key], lo, hi)
    if 'sound_device' in d:
        out['sound_device'] = text(d['sound_device'], 100)
        if not re.fullmatch(r'[A-Za-z0-9:=,._-]*', out['sound_device']):
            raise HTTPException(400, 'invalid_value')
    if 'style' in d:
        # the look of the notifications; the node checks every value (colours, choices, ranges, icons)
        if not isinstance(d['style'], dict) or len(json.dumps(d['style'], ensure_ascii=False)) > 4000:
            raise HTTPException(400, 'invalid_value')
        out['style'] = d['style']
    if not out:
        raise HTTPException(400, 'nothing_to_change')
    return out


def validate_notify_sound(c, d):
    """Custom sound of a notification level: an uploaded MP3 (file_id) or {"reset": true} for the default chime."""
    level = text(d.get('level'), 20)
    if level not in NOTIFY_LEVELS:
        raise HTTPException(400, 'invalid_value')
    if flag(d.get('reset')):
        return {'level': level, 'reset': True}
    f = c.execute('SELECT * FROM files WHERE id=?', (str(d.get('file_id') or ''),)).fetchone()
    if not f:
        raise HTTPException(400, 'file_not_found')
    if f['kind'] != 'audio':
        raise HTTPException(400, 'unsupported_file')
    if f['size'] > SOUND_MAX:
        raise HTTPException(413, 'file_too_large')
    return {'level': level, 'file_id': f['id'], 'sha256': f['sha256'], 'filename': f['name'], 'size': f['size']}


def validate_notify_image(c, d):
    """The picture of the notification look: an uploaded image (file_id) or {"reset": true} to remove it."""
    if flag(d.get('reset')):
        return {'reset': True}
    f = c.execute('SELECT * FROM files WHERE id=?', (str(d.get('file_id') or ''),)).fetchone()
    if not f:
        raise HTTPException(400, 'file_not_found')
    if f['kind'] != 'image':
        raise HTTPException(400, 'unsupported_file')
    if f['size'] > IMAGE_MAX:
        raise HTTPException(413, 'file_too_large')
    return {'file_id': f['id'], 'sha256': f['sha256'], 'filename': f['name'], 'size': f['size']}


def validate_watcher(d, required=True):
    """A watcher: the node asks another app's JSON API and announces new items. Credentials only reach the node;
    on edits empty credentials keep the stored ones. The node checks the rest (e.g. OAuth2 fields)."""
    out = {k: text(d[k], limit) for k, limit in WATCHER_TEXT.items() if k in d}
    if (required or 'name' in out) and not out.get('name'):
        raise HTTPException(400, 'name_required')
    for key in ('url', 'oauth_token_url'):
        if (out.get(key) or (required and key == 'url')) and not out.get(key, '').lower().startswith(('http://', 'https://')):
            raise HTTPException(400, 'invalid_url')
    for key, choices in WATCHER_CHOICES.items():
        if key in d:
            if d[key] not in choices:
                raise HTTPException(400, 'invalid_value')
            out[key] = d[key]
    if d.get('interval') not in (None, ''):
        out['interval'] = bounded(d['interval'], 15, 86400)
    for key in ('verify_tls', 'enabled'):
        if key in d:
            out[key] = flag(d[key])
    for key in WATCHER_SECRETS:
        value = str(d.get(key) or '').strip()[:4000]
        if value:
            out[key] = value
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
    u = current_user(r, ACTIONS.get(action, ('settings',))[0])
    with db() as c:
        row = get_device_row(c, did)
        require_device(u, ACTIONS.get(action, ('settings',))[0], row)
        payload = validate_command(c, row, action, d.get('payload'))
        cid = queue_command(c, did, action, payload, u['username'])
    audit(u, 'command.' + action, did, redact(action, payload))
    return {'id': cid}


@app.post('/api/bulk/commands')
async def bulk_command(r: Request):
    d = await body(r)
    action = str(d.get('action', ''))
    perm = ACTIONS.get(action, ('settings',))[0]
    u = current_user(r, perm)
    ids = list(dict.fromkeys(str(x) for x in d.get('device_ids') or []))
    if not ids:
        raise HTTPException(400, 'no_devices')
    batch, queued, skipped = new_batch(), [], []
    with db() as c:
        for did in ids:
            try:
                row = get_device_row(c, did)
                require_device(u, perm, row)
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
    u = current_user(r)
    q, args = ('SELECT c.*, d.name device_name, d.device_group, d.location FROM commands c '
               'LEFT JOIN devices d ON d.id=c.device_id WHERE 1=1', [])
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
    rows = [x for x in rows if u['access'].on('view', {'id': x['device_id'], 'device_group': x.pop('device_group'),
                                                         'location': x.pop('location')})]
    for x in rows:
        x['payload_json'] = redact_json(x['action'], x['payload_json'])
    return rows


@app.get('/api/commands/{cid}')
def get_command(cid: int, r: Request):
    u = current_user(r)
    with db() as c:
        sweep_commands(c)
        row = c.execute('SELECT c.*, d.name device_name, d.device_group, d.location FROM commands c '
                        'LEFT JOIN devices d ON d.id=c.device_id WHERE c.id=?', (cid,)).fetchone()
    if not row or not u['access'].on('view', {'id': row['device_id'], 'device_group': row['device_group'], 'location': row['location']}):
        raise HTTPException(404, 'not_found')
    x = dict(row)
    x.pop('device_group')
    x.pop('location')
    x['payload_json'] = redact_json(x['action'], x['payload_json'])
    return x


@app.post('/api/commands/{cid}/cancel')
def cancel_command(cid: int, r: Request):
    u = current_user(r, 'control')
    with db() as c:
        row = c.execute('SELECT d.* FROM commands c JOIN devices d ON d.id=c.device_id WHERE c.id=?', (cid,)).fetchone()
        if row:
            require_device(u, 'control', row)
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
    # audio: MP3 sounds of notifications (never playlist items)
    kind = 'video' if ctype.startswith('video/') else 'image' if ctype.startswith('image/') else \
        'audio' if ctype in ('audio/mpeg', 'audio/mp3') or name.lower().endswith('.mp3') else ''
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
        src_row = get_device_row(c, src)
        require_device(u, 'view', src_row)
        source = build(src_row, full=True)
        for t in targets:
            require_device(u, 'content', get_device_row(c, t))
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
    u = current_user(r)
    now = time.time()
    out = {}
    with db() as c:
        devices = [x for x in c.execute('SELECT * FROM devices') if u['access'].on('view', x)]
        everything = u['access'].everywhere('view')
        # a user with a scope sees the groups and locations of the scope and of the devices the user sees
        scopes = [s for _, s in u['access'].grants if s is not None]
        for kind, (table, col) in ORG.items():
            stats = {}
            for x in devices:
                st = stats.setdefault(x[col], {'total': 0, 'online': 0})
                st['total'] += 1
                st['online'] += 1 if (x['last_seen'] or 0) > now - 35 else 0
            named = {n for s in scopes for n in s[kind]}
            out[kind] = [{**dict(x), 'total': stats.get(x['name'], {}).get('total', 0), 'online': stats.get(x['name'], {}).get('online', 0)}
                         for x in c.execute(f'SELECT * FROM {table} ORDER BY name COLLATE NOCASE')
                         if everything or x['name'] in stats or x['name'] in named]
    return out


def _org(kind):
    if kind not in ORG:
        raise HTTPException(404, 'not_found')
    return ORG[kind]


@app.post('/api/org/{kind}')
async def create_org(kind: str, r: Request):
    u = current_user(r, 'devices')
    if not u['access'].everywhere('devices'):
        raise HTTPException(403, 'forbidden')
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
    u = current_user(r, 'devices')
    if not u['access'].everywhere('devices'):
        raise HTTPException(403, 'forbidden')
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
        if new != name:
            access.rename_in_scopes(c, kind, name, new)
    audit(u, f'{kind}.edit', name, {'name': new})
    return {'ok': True}


@app.delete('/api/org/{kind}/{name}')
def delete_org(kind: str, name: str, r: Request):
    u = current_user(r, 'devices')
    if not u['access'].everywhere('devices'):
        raise HTTPException(403, 'forbidden')
    table, col = _org(kind)
    with db() as c:
        c.execute(f'DELETE FROM {table} WHERE name=?', (name,))
        c.execute(f"UPDATE devices SET {col}='' WHERE {col}=?", (name,))
        access.rename_in_scopes(c, kind, name)
    audit(u, f'{kind}.delete', name)
    return {'ok': True}


# ---------------------------------------------------------------- users, audit, settings

@app.get('/api/audit')
def list_audit(r: Request, q: str = '', limit: int = 500):
    current_user(r, 'audit')
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
    current_user(r, 'settings')   # the enrollment token
    return {'enroll_token': cfg()['enroll_token'], 'hub_version': HUB_VERSION, 'agent_version': AGENT_VERSION}


# ---------------------------------------------------------------- SSH provisioning jobs

@app.post('/api/provision')
async def provision(r: Request):
    u = current_user(r, 'install')
    d = await body(r)
    params = {k: text(d.get(k), 8000) for k in ('host', 'username', 'password', 'private_key', 'passphrase', 'name',
                                                 'hub_url', 'group', 'location')}
    params['port'] = int(d.get('port') or 22)
    params['reenroll'] = bool(d.get('reenroll'))
    params['forget_host_key'] = bool(d.get('forget_host_key'))
    params['via_fleet'] = bool(d.get('via_fleet'))
    params['password'] = str(d.get('password') or '')  # keep exact password (no trimming)
    params['mode'] = 'node' if d.get('mode') == 'node' else 'agent'
    params['template'] = templates.template_id(d.get('template'))   # applied once the node reports
    # optional: CARACAL's web administrator (created, or its password set on an existing node); kept in memory only
    params['admin'] = validate_admin({'username': d.get('admin_username'), 'password': d.get('admin_password')}) \
        if d.get('admin_username') or d.get('admin_password') else None
    if not params['host'] or not params['username'] or not params['hub_url']:
        raise HTTPException(400, 'missing_fields')
    if not params['password'] and not params['private_key']:
        raise HTTPException(400, 'missing_fields')
    if params['mode'] == 'node':   # full CARACAL node on Docker: needs the image and a version
        params['image'] = images.node_image()
        if not params['image']:
            raise HTTPException(400, 'node_image_missing')
        params['version'] = text(d.get('version'), 64) or images.latest_version() or 'latest'
        if not images.VERSION_RE.fullmatch(params['version']):
            raise HTTPException(400, 'version_required')
    job_id = provisioning.create_job('node_install' if params['mode'] == 'node' else 'agent_install',
                                     params['host'], u['username'])
    provisioning.start(job_id, params, u)
    audit(u, 'provision.start', params['host'], {'job': job_id, 'name': params['name']})
    return {'job_id': job_id}


@app.post('/api/discover')
async def discover(r: Request):
    """Scan a local network for devices with SSH (new Raspberry Pis to install)."""
    u = current_user(r, 'install')
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
    current_user(r, 'install')
    with db() as c:
        return [dict(x) for x in c.execute('SELECT * FROM jobs ORDER BY id DESC LIMIT ?', (max(1, min(limit, 1000)),))]


@app.get('/api/jobs/{jid}')
def get_job(jid: int, r: Request):
    current_user(r, 'install')
    with db() as c:
        row = c.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone()
    if not row:
        raise HTTPException(404, 'not_found')
    return dict(row)


# ---------------------------------------------------------------- agent bootstrap (public, contains no secrets)

@app.get('/api/bootstrap/{name}', response_class=PlainTextResponse)
def bootstrap_file(name: str):
    if name not in provisioning.AGENT_FILES + provisioning.NODE_FILES + ('caracal-firstboot.sh', 'caracal-alpine.sh',
                                                                         'caracal-install.sh'):
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
    # the node template chosen for the installation (zero-touch card, one-command installation); an unknown one
    # (deleted meanwhile) does not stop the enrollment
    try:
        template = templates.template_id(d.get('template'))
    except HTTPException:
        template = None
    now = time.time()
    with db() as c:
        row = c.execute('SELECT * FROM devices WHERE id=?', (did,)).fetchone()
        if row and existing_token and secrets.compare_digest(row['token_hash'] or '', token_hash(existing_token)):
            tok = existing_token  # re-enrollment keeps the working token
        elif row and now - (row['last_seen'] or 0) < ONLINE_TIMEOUT * 3:
            # Someone with the enrollment token and the same fingerprint (a cloned SD card, a guessed hostname)
            # must not take over a device that is reporting right now with its own token: the impostor would get
            # its commands, including login credentials. A reinstalled device enrolls once the old one is offline.
            audit(None, 'device.enroll_refused', did, {'name': name, 'reason': 'device online with its own token'})
            raise HTTPException(409, 'device_online')
        else:
            tok = secrets.token_urlsafe(32)
        if row:
            c.execute('UPDATE devices SET token_hash=?, last_seen=? WHERE id=?', (token_hash(tok), now, did))
        else:
            c.execute('INSERT INTO devices(id, token_hash, name, ip, version, last_seen, status_json, created) '
                      'VALUES(?,?,?,?,?,?,?,?)', (did, token_hash(tok), name, '', '', now, '{}', now))
        if template:
            c.execute('UPDATE devices SET pending_template=? WHERE id=?', (template, did))
    audit(None, 'device.enroll', did, {'name': name, 'new': not row, 'token_kept': tok == existing_token,
                                       **({'template': template} if template else {})})
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
    reported = d.pop('plays', None)   # proof of play (agents 4.17): stored on its own, not in the status
    with db() as c:
        if isinstance(reported, list):
            plays.record(c, did, reported)
        c.execute('UPDATE devices SET ip=?, version=?, last_seen=?, status_json=? WHERE id=?',
                  (text(d.get('ip'), 64) or _client_ip(r), text(d.get('version'), 32), time.time(),
                   json.dumps(d, ensure_ascii=False), did))
        monitor.record_metrics(c, did, d)
        templates.apply_pending(c, did)
    # the trusted mirrors of the hub: agents (4.15) point their apt/apk sources to them through the hub as well
    return {'ok': True, 'agent_version': AGENT_VERSION, 'server_time': time.time(), 'mirrors': [f"{m['scheme']}://{m['host']}" for m in proxy.mirrors()]}


LONG_POLL_MAX = 25


@app.get('/api/device/{did}/commands')
async def pull_commands(did: str, r: Request, wait: int = 0):
    """The commands queued for a device. With ?wait=N (agents from 4.15) the request waits up to N seconds for a
    command, so the agent gets it within a fraction of a second instead of at its next poll."""
    device_auth(r, did)
    deadline = time.time() + max(0, min(wait, LONG_POLL_MAX))
    seq, recheck_until = COMMAND_SEQ.get(did, 0), 0.0
    while True:
        out = await run_in_threadpool(_take_commands, did)
        if out or time.time() >= deadline:
            return JSONResponse(out, headers={'X-Long-Poll': '1'})
        while time.time() < deadline:
            await asyncio.sleep(0.25)
            if COMMAND_SEQ.get(did, 0) != seq:
                # a new command; its transaction may still be open, so the queue is read again for a moment
                seq, recheck_until = COMMAND_SEQ.get(did, 0), time.time() + 3
                break
            if time.time() < recheck_until:
                break


def _take_commands(did):
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
        # a finished command keeps its result: a device cannot rewrite history or run follow-ups (imports) again;
        # a late result after a timeout is still taken (long installations)
        if row['state'] not in ('delivered', 'timeout'):
            return {'ok': True, 'ignored': True}
        # a diagnostics report is longer than other results (it is JSON, so it must not be cut)
        # a diagnostics report and a camera picture are longer than other results (JSON, so they must not be cut)
        limit = 200000 if row['action'] in ('diagnostics', 'camera_preview') else 16000
        c.execute('UPDATE commands SET state=?, result=?, updated=? WHERE id=?',
                  ('completed' if ok else 'failed', result[-limit:], time.time(), cid))
        if ok and row['action'] == 'reinstall_node':
            # the template chosen for a reinstalled node: applied once the node reports again (not earlier, when
            # the old installation was still running)
            template = json.loads(row['payload_json'] or '{}').get('template')
            if template:
                c.execute('UPDATE devices SET pending_template=? WHERE id=?', (template, did))
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


@app.post('/api/device/{did}/screenshot')
async def device_screenshot(did: str, r: Request):
    """The agent uploads a picture of the screen, only while it carries out a screenshot command."""
    device_auth(r, did)
    with db() as c:
        if not c.execute("SELECT 1 FROM commands WHERE device_id=? AND action='screenshot' AND state='delivered'",
                         (did,)).fetchone():
            raise HTTPException(403, 'forbidden')
    data = await r.body()
    if len(data) > SCREENSHOT_MAX:
        raise HTTPException(413, 'file_too_large')
    if not data.startswith(b'\xff\xd8\xff'):
        raise HTTPException(400, 'unsupported_file')
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    tmp = SCREENSHOTS / f'{did}.part'
    tmp.write_bytes(data)
    tmp.replace(SCREENSHOTS / f'{did}.jpg')
    return {'ok': True}


@app.get('/api/devices/{did}/screenshot')
def device_screenshot_file(did: str, r: Request):
    u = current_user(r)
    with db() as c:
        require_device(u, 'view', get_device_row(c, did))
    path = SCREENSHOTS / f'{did}.jpg'
    if not path.exists():
        raise HTTPException(404, 'not_found')
    return FileResponse(path, media_type='image/jpeg', headers={'Cache-Control': 'no-store'})


@app.get('/api/devices/{did}/notify-image')
def device_notify_image(did: str, r: Request):
    """The picture of the device's notification look, for the preview in the look editor: the hub's copy of the
    file the device reports (by checksum), so only pictures sent through Fleet can be shown."""
    u = current_user(r)
    with db() as c:
        row = get_device_row(c, did)
        require_device(u, 'view', row)
        image = (notifications_of(json.loads(row['status_json'] or '{}')) or {}).get('image') or {}
        f = c.execute("SELECT * FROM files WHERE sha256=? AND kind='image' ORDER BY rowid DESC LIMIT 1",
                      (str(image.get('sha256') or ''),)).fetchone() if image.get('sha256') else None
    if not f or not (FILES / f['id']).exists():
        raise HTTPException(404, 'not_found')
    media = mimetypes.guess_type(f['name'] or '')[0] or 'application/octet-stream'
    return FileResponse(FILES / f['id'], media_type=media if media.startswith('image/') else 'application/octet-stream',
                        headers={'Cache-Control': 'no-store'})


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
