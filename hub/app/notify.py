"""Notification API for other apps: one request to the hub shows a notification on many CARACAL screens.

An app sends a notification to POST /api/notify with a token created in Settings. The token decides which
screens it may reach (all, groups, locations or chosen devices); the request may narrow that down with
?group=, ?location= or ?device=. The hub queues one 'notify' command per screen and the agents pass it to their
node, which keeps its own queue (one notification at a time, more important first, limits against floods).

The body is forwarded unchanged, so everything the node understands works here as well: JSON with title,
message, level, duration, key and sound; Grafana / Alertmanager and Uptime Kuma webhooks; plain text with the
Title and X-Level headers.
"""
import base64
import json
import secrets
import time
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request

from .core import audit, current_user, db, new_batch, queue_command, token_hash
from .devices import build

router = APIRouter()
BODY_MAX = 65536
TOKEN_PREFIX = 'cft_'
FAIL_WINDOW, FAIL_MAX = 300, 20
_fails = {}
_hits = {}
_fail_logged = {}


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


def _scope(d):
    """{"all": true} or any of {"groups": [...], "locations": [...], "devices": [...]}."""
    if not isinstance(d, dict):
        raise HTTPException(400, 'invalid_scope')
    if d.get('all'):
        return {'all': True}
    out = {k: sorted({_text(x, 120) for x in d.get(k) or [] if _text(x, 120)}) for k in ('groups', 'locations', 'devices')}
    out = {k: v for k, v in out.items() if v}
    if not out:
        raise HTTPException(400, 'invalid_scope')
    return out


def _public(row):
    t = dict(row)
    t.pop('token_hash', None)
    t['scope'] = json.loads(t.pop('scope_json') or '{}')
    return t


# ---------------------------------------------------------------- tokens (Settings, manager and admin)

@router.get('/api/notify-tokens')
def list_tokens(r: Request):
    current_user(r, 'manage')
    with db() as c:
        return [_public(x) for x in c.execute('SELECT * FROM notify_tokens ORDER BY name COLLATE NOCASE, id')]


@router.post('/api/notify-tokens')
async def create_token(r: Request):
    u = current_user(r, 'manage')
    d = await _json(r)
    name = _text(d.get('name'), 60)
    if not name:
        raise HTTPException(400, 'name_required')
    scope = _scope(d.get('scope'))
    rate = _rate_value(d.get('rate_per_min', 30))
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    with db() as c:
        cur = c.execute('INSERT INTO notify_tokens(name, token_hash, prefix, scope_json, rate_per_min, enabled, '
                        'created, created_by) VALUES(?,?,?,?,?,1,?,?)',
                        (name, token_hash(token), token[:10], json.dumps(scope), rate, time.time(), u['username']))
    audit(u, 'notify_token.create', name, {'scope': scope, 'rate_per_min': rate})
    # shown once; the hub keeps only its hash
    return {'id': cur.lastrowid, 'token': token}


@router.patch('/api/notify-tokens/{tid}')
async def edit_token(tid: int, r: Request):
    u = current_user(r, 'manage')
    d = await _json(r)
    with db() as c:
        row = c.execute('SELECT * FROM notify_tokens WHERE id=?', (tid,)).fetchone()
        if not row:
            raise HTTPException(404, 'not_found')
        name = _text(d.get('name'), 60) or row['name']
        scope = _scope(d['scope']) if 'scope' in d else json.loads(row['scope_json'] or '{}')
        rate = _rate_value(d.get('rate_per_min', row['rate_per_min']))
        enabled = 1 if d.get('enabled', bool(row['enabled'])) else 0
        c.execute('UPDATE notify_tokens SET name=?, scope_json=?, rate_per_min=?, enabled=? WHERE id=?',
                  (name, json.dumps(scope), rate, enabled, tid))
    audit(u, 'notify_token.edit', name, {'scope': scope, 'rate_per_min': rate, 'enabled': bool(enabled)})
    return {'ok': True}


@router.delete('/api/notify-tokens/{tid}')
def delete_token(tid: int, r: Request):
    u = current_user(r, 'manage')
    with db() as c:
        row = c.execute('SELECT name FROM notify_tokens WHERE id=?', (tid,)).fetchone()
        if not row:
            raise HTTPException(404, 'not_found')
        c.execute('DELETE FROM notify_tokens WHERE id=?', (tid,))
    audit(u, 'notify_token.delete', row['name'])
    return {'ok': True}


def _rate_value(v):
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise HTTPException(400, 'invalid_value')
    if not 1 <= n <= 600:
        raise HTTPException(400, 'invalid_value')
    return n


# ---------------------------------------------------------------- the API for apps

def _client_ip(r: Request):
    fwd = r.headers.get('X-Forwarded-For', '')
    return fwd.split(',')[0].strip() if fwd else (r.client.host if r.client else '')


def _token(r: Request):
    """Bearer token, X-Caracal-Token, the password of Basic auth (any user name) or ?token=."""
    ip, now = _client_ip(r), time.time()
    if len(_fails) > 10000:   # the address can be spoofed behind a proxy, keep the table bounded
        for k in [k for k, v in _fails.items() if not v or v[-1] < now - FAIL_WINDOW]:
            _fails.pop(k, None)
    recent = [x for x in _fails.get(ip, []) if x > now - FAIL_WINDOW]
    if len(recent) >= FAIL_MAX:
        raise HTTPException(429, 'too_many_attempts')
    header, raw = r.headers.get('Authorization', ''), ''
    if header.lower().startswith('bearer '):
        raw = header[7:].strip()
    elif header.lower().startswith('basic '):
        try:
            raw = base64.b64decode(header[6:].strip()).decode().partition(':')[2].strip()
        except ValueError:
            raw = ''
    raw = raw or r.headers.get('X-Caracal-Token', '').strip() or r.query_params.get('token', '').strip()
    row = None
    if raw:
        with db() as c:
            row = c.execute('SELECT * FROM notify_tokens WHERE token_hash=?', (token_hash(raw),)).fetchone()
    if not row or not row['enabled']:
        _fails[ip] = recent + [now]
        # audited once per address and window, not for every attempt
        if now - _fail_logged.get(ip, 0) > FAIL_WINDOW:
            _fail_logged[ip] = now
            audit(None, 'notify.token_rejected', ip, {'missing': not raw})
        raise HTTPException(401, 'invalid_notify_token')
    _fails.pop(ip, None)
    return dict(row)


def _rate(tok):
    now = time.time()
    hits = [x for x in _hits.get(tok['id'], []) if x > now - 60]
    if len(hits) >= int(tok['rate_per_min'] or 30):
        _hits[tok['id']] = hits
        raise HTTPException(429, 'rate_limited')
    _hits[tok['id']] = hits + [now]


TEXT_KEYS = ('title', 'subject', 'summary', 'message', 'text', 'body', 'msg', 'description', 'content')


def _has_text(d):
    """Something the node can show: a text field (the same aliases as the node) or a webhook it understands."""
    if isinstance(d.get('alerts'), list) or isinstance(d.get('heartbeat'), dict):   # Grafana/Alertmanager, Uptime Kuma
        return True
    if isinstance(d.get('notifications'), list):
        return bool(d['notifications']) and all(isinstance(x, dict) and _has_text(x) for x in d['notifications'])
    return any(_text(d.get(k)) for k in TEXT_KEYS)


async def _payload(r: Request):
    """The notification as the node accepts it; a list of notifications becomes {"notifications": [...]}."""
    try:
        length = int(r.headers.get('content-length') or 0)
    except ValueError:
        length = 0
    raw = await r.body() if length <= BODY_MAX else b''
    if length > BODY_MAX or len(raw) > BODY_MAX:
        raise HTTPException(413, 'notification_too_large')
    ctype = r.headers.get('content-type', '').lower()
    body = raw.decode('utf-8', errors='replace').strip()
    if 'json' in ctype or body[:1] in ('{', '['):
        try:
            data = json.loads(body)
        except ValueError:
            raise HTTPException(400, 'invalid_json')
        if isinstance(data, list):
            data = {'notifications': data}
        if not isinstance(data, dict) or not data:
            raise HTTPException(400, 'invalid_json')
        if not _has_text(data):
            raise HTTPException(400, 'notification_text_required')
        return data
    if 'x-www-form-urlencoded' in ctype:
        data = {k: v[-1] for k, v in parse_qs(body).items() if k != 'token'}
    else:   # plain text: the body is the message, the rest comes from headers or the query string
        data = {k: v for k, v in r.query_params.items() if k not in ('token', 'group', 'location', 'device')}
        data['message'] = body or data.get('message', '')
        for key, names in (('title', ('Title', 'X-Title')), ('level', ('X-Level', 'Priority', 'X-Priority')),
                           ('duration', ('X-Duration',)), ('key', ('X-Key',)), ('sound', ('X-Sound',))):
            value = next((r.headers[n] for n in names if r.headers.get(n)), None)
            if value and key not in data:
                data[key] = value
    if not _text(data.get('title')) and not _text(data.get('message')):
        raise HTTPException(400, 'notification_text_required')
    return data


def _targets(c, scope, query):
    """Devices of the token's scope, narrowed by ?group=, ?location= and ?device= (each may repeat)."""
    rows = c.execute('SELECT * FROM devices').fetchall()
    allowed = [x for x in rows if scope.get('all') or x['id'] in scope.get('devices', [])
               or (x['device_group'] or '') in scope.get('groups', []) or (x['location'] or '') in scope.get('locations', [])]
    for key, col in (('group', 'device_group'), ('location', 'location'), ('device', 'id')):
        wanted = [v for v in query.getlist(key) if v]
        if wanted:
            allowed = [x for x in allowed if (x[col] or '') in wanted]
    return allowed


@router.post('/api/notify')
async def send(r: Request):
    tok = _token(r)
    _rate(tok)
    payload = await _payload(r)
    scope = json.loads(tok['scope_json'] or '{}')
    batch, queued, skipped = new_batch(), [], []
    with db() as c:
        for row in _targets(c, scope, r.query_params):
            # nodes whose CARACAL has no notifications would only fail the command
            if (build(row)['capabilities'] or {}).get('notify') is False:
                skipped.append({'device_id': row['id'], 'reason': 'notifications_unsupported'})
                continue
            queue_command(c, row['id'], 'notify', payload, 'api:' + tok['name'], batch)
            queued.append(row['id'])
        c.execute('UPDATE notify_tokens SET last_used=? WHERE id=?', (time.time(), tok['id']))
    if not queued and not skipped:
        raise HTTPException(409, 'no_devices')
    return {'ok': True, 'queued': len(queued), 'devices': queued, 'skipped': skipped, 'batch': batch}
