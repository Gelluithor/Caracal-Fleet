"""Integrations: API tokens and webhooks.

An API token works like a signed-in user with a role for a scope (nobody can create one with more than they have; a
token never manages users), so other systems can use the whole REST API of Fleet: start an emergency message, deploy
a global playlist, send a command, apply a template. Webhooks tell other systems what happens in Fleet: devices that go
down or come back, problems, emergency messages, approvals, deployments, content that runs out. Every delivery is signed
(X-Caracal-Signature: sha256=<HMAC of the body with the webhook's secret>).
"""
import hashlib
import hmac
import json
import queue
import secrets
import threading
import time

import requests
from fastapi import APIRouter, HTTPException, Request

from .core import DATA, audit, cfg, current_user, db, parse_scope, role_permissions, save_cfg, token_hash

router = APIRouter()
TOKEN_PREFIX = 'cfa_'
EVENTS = ('device.offline', 'device.online', 'device.problem', 'device.resolved', 'takeover.started', 'takeover.ended',
          'approval.requested', 'approval.decided', 'playlist.deployed', 'content.expiring', 'test')
STATUS = DATA / 'webhook-status.json'
RETRIES = (2, 10, 60)
_queue = queue.Queue(maxsize=1000)
_lock = threading.Lock()


async def _json(r: Request):
    try:
        d = await r.json()
    except ValueError:
        raise HTTPException(400, 'invalid_json')
    return d if isinstance(d, dict) else {}


# ---------------------------------------------------------------- API tokens

def token_user(raw):
    """The pretend user of an API token, or None (unknown, disabled or expired)."""
    from .core import Access
    with db() as c:
        row = c.execute('SELECT * FROM api_tokens WHERE token_hash=? AND enabled=1', (token_hash(raw),)).fetchone()
        if not row or (row['expires'] and row['expires'] < time.time()):
            return None
        perms = role_permissions(c, row['role']) - {'users'}   # a token never manages users (or other tokens)
        c.execute('UPDATE api_tokens SET last_used=? WHERE id=?', (time.time(), row['id']))
        tenant = row['tenant_id'] if 'tenant_id' in row.keys() else None
    u = {'id': -row['id'], 'username': f"api:{row['name']}"[:64], 'role': row['role'], 'scope_json': row['scope_json'],
         'language': 'en', 'password_hash': '', 'prefs_json': '{}', 'enabled': 1, 'token_id': row['id'], 'tenant_id': tenant or None}
    u['access'] = Access([(perms, parse_scope(row['scope_json'] or ''))])
    if tenant:
        from .tenants import restrict
        with db() as c:
            u['access'] = restrict(c, u['access'], tenant)
    return u


def _public_token(row):
    from .access import scope_public
    return {'id': row['id'], 'name': row['name'], 'prefix': row['prefix'], 'role': row['role'], 'scope': scope_public(row['scope_json']),
            'created': row['created'], 'created_by': row['created_by'], 'last_used': row['last_used'], 'expires': row['expires'],
            'enabled': bool(row['enabled'])}


@router.get('/api/api-tokens')
def list_tokens(r: Request):
    current_user(r, 'users')
    with db() as c:
        return [_public_token(x) for x in c.execute('SELECT * FROM api_tokens ORDER BY id DESC')]


@router.post('/api/api-tokens')
async def create_token(r: Request):
    from .access import _check_grant, _role_exists, scope_value
    u = current_user(r, 'users')
    d = await _json(r)
    name = str(d.get('name') or '').strip()[:80]
    if not name:
        raise HTTPException(400, 'name_required')
    role = str(d.get('role') or '')
    try:
        days = max(0, min(3650, int(d.get('days') or 0)))
    except (TypeError, ValueError):
        raise HTTPException(400, 'invalid_value')
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    with db() as c:
        if not _role_exists(c, role):
            raise HTTPException(400, 'unknown_role')
        scope = scope_value(d.get('scope'))
        _check_grant(c, u, role, scope)
        tid = c.execute('INSERT INTO api_tokens(name, token_hash, prefix, role, scope_json, created, created_by, expires, enabled, tenant_id) '
                        'VALUES(?,?,?,?,?,?,?,?,1,?)', (name, token_hash(raw), raw[:10], role, scope, time.time(), u['username'],
                                                        time.time() + days * 86400 if days else None, u.get('tenant_id'))).lastrowid
    audit(u, 'api_token.create', str(tid), {'name': name, 'role': role})
    return {'id': tid, 'token': raw}


@router.patch('/api/api-tokens/{tid}')
async def edit_token(tid: int, r: Request):
    u = current_user(r, 'users')
    d = await _json(r)
    with db() as c:
        if not c.execute('SELECT 1 FROM api_tokens WHERE id=?', (tid,)).fetchone():
            raise HTTPException(404, 'not_found')
        if 'enabled' in d:
            c.execute('UPDATE api_tokens SET enabled=? WHERE id=?', (1 if d['enabled'] else 0, tid))
    audit(u, 'api_token.edit', str(tid), {'enabled': bool(d.get('enabled'))})
    return {'ok': True}


@router.delete('/api/api-tokens/{tid}')
def delete_token(tid: int, r: Request):
    u = current_user(r, 'users')
    with db() as c:
        c.execute('DELETE FROM api_tokens WHERE id=?', (tid,))
    audit(u, 'api_token.delete', str(tid))
    return {'ok': True}


# ---------------------------------------------------------------- webhooks

def hooks():
    return [h for h in cfg().get('webhooks') or [] if isinstance(h, dict)]


def _status():
    try:
        return json.loads(STATUS.read_text())
    except (OSError, ValueError):
        return {}


def _set_status(hid, **values):
    with _lock:
        st = _status()
        st[hid] = {**st.get(hid, {}), **values}
        STATUS.write_text(json.dumps(st))


def emit(event, data=None):
    """Queue the event for every webhook that wants it (never blocks or fails the caller)."""
    try:
        for h in hooks():
            if h.get('enabled', True) and (event in h.get('events', []) or event == 'test'):
                _queue.put_nowait((h, event, data or {}))
    except Exception as e:   # a full queue or broken settings must not break the action that happened
        print('webhook queue:', e, flush=True)


def deliver(h, event, data, send=requests.post):
    body = json.dumps({'event': event, 'time': time.time(), 'data': data}, ensure_ascii=False, default=str).encode()
    sig = hmac.new(str(h.get('secret') or '').encode(), body, hashlib.sha256).hexdigest()
    r = send(h['url'], data=body, timeout=10, headers={'Content-Type': 'application/json', 'User-Agent': 'CARACAL Fleet',
                                                         'X-Caracal-Event': event, 'X-Caracal-Signature': 'sha256=' + sig})
    if r.status_code >= 300:
        raise RuntimeError(f'HTTP {r.status_code}')


def _worker():
    while True:
        h, event, data = _queue.get()
        error = ''
        for wait in (0,) + RETRIES:
            time.sleep(wait)
            try:
                deliver(h, event, data)
                error = ''
                break
            except Exception as e:
                error = str(e)[:300]
        _set_status(h['id'], at=time.time(), event=event, ok=not error, error=error)


def start():
    threading.Thread(target=_worker, daemon=True, name='fleet-webhooks').start()


def _hook(d, old=None):
    old = old or {}
    url = str(d.get('url', old.get('url', '')) or '').strip()[:2000]
    if not url.lower().startswith(('http://', 'https://')):
        raise HTTPException(400, 'invalid_url')
    events = [e for e in d.get('events', old.get('events', [])) or [] if e in EVENTS and e != 'test']
    if not events:
        raise HTTPException(400, 'events_required')
    return {'id': old.get('id') or secrets.token_hex(6), 'name': str(d.get('name', old.get('name', '')) or '').strip()[:80] or url,
            'url': url, 'events': events, 'enabled': bool(d.get('enabled', old.get('enabled', True))),
            'secret': old.get('secret') or secrets.token_urlsafe(24)}


def _public_hook(h):
    st = _status().get(h['id'], {})
    return {**{k: h.get(k) for k in ('id', 'name', 'url', 'events', 'enabled')}, 'last': st or None}


@router.get('/api/webhooks')
def list_hooks(r: Request):
    current_user(r, 'settings')
    return {'webhooks': [_public_hook(h) for h in hooks()], 'events': [e for e in EVENTS if e != 'test']}


@router.post('/api/webhooks')
async def add_hook(r: Request):
    u = current_user(r, 'settings')
    h = _hook(await _json(r))
    conf = cfg()
    conf['webhooks'] = hooks() + [h]
    save_cfg(conf)
    audit(u, 'webhook.create', h['id'], {'url': h['url'], 'events': h['events']})
    return {**_public_hook(h), 'secret': h['secret']}   # the secret is shown once


@router.patch('/api/webhooks/{hid}')
async def edit_hook(hid: str, r: Request):
    u = current_user(r, 'settings')
    d = await _json(r)
    conf = cfg()
    found = next((h for h in hooks() if h['id'] == hid), None)
    if not found:
        raise HTTPException(404, 'not_found')
    new = _hook(d, found)
    if d.get('new_secret'):
        new['secret'] = secrets.token_urlsafe(24)
    conf['webhooks'] = [new if h['id'] == hid else h for h in hooks()]
    save_cfg(conf)
    audit(u, 'webhook.edit', hid)
    return {**_public_hook(new), **({'secret': new['secret']} if d.get('new_secret') else {})}


@router.delete('/api/webhooks/{hid}')
def delete_hook(hid: str, r: Request):
    u = current_user(r, 'settings')
    conf = cfg()
    conf['webhooks'] = [h for h in hooks() if h['id'] != hid]
    save_cfg(conf)
    audit(u, 'webhook.delete', hid)
    return {'ok': True}


@router.post('/api/webhooks/{hid}/test')
def test_hook(hid: str, r: Request):
    current_user(r, 'settings')
    h = next((h for h in hooks() if h['id'] == hid), None)
    if not h:
        raise HTTPException(404, 'not_found')
    try:
        deliver(h, 'test', {'message': 'CARACAL Fleet webhook test'})
    except Exception as e:
        _set_status(hid, at=time.time(), event='test', ok=False, error=str(e)[:300])
        raise HTTPException(502, f'webhook_failed: {e}'[:300])
    _set_status(hid, at=time.time(), event='test', ok=True, error='')
    return {'ok': True}
