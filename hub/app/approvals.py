"""Approving content: when it is switched on (Settings -> Users), a deployment of a global playlist by a user without
the permission 'approve' for all its devices waits until somebody with it approves it. What is approved is the version
of the playlist at the time of the request. Schedules and templates set up by others are not held back.
"""
import json
import time

from fastapi import APIRouter, HTTPException, Request

from .core import audit, cfg, current_user, db, save_cfg


router = APIRouter()


async def _json(r: Request):
    try:
        d = await r.json()
    except ValueError:
        raise HTTPException(400, 'invalid_json')
    return d if isinstance(d, dict) else {}


def pending_count(c, u):
    """Requests this user can decide (or made), for the badge in the menu."""
    n = 0
    for row in c.execute("SELECT * FROM approvals WHERE state='pending'"):
        targets = json.loads(row['payload_json'] or '{}').get('targets') or []
        devices = [d for d in (c.execute('SELECT * FROM devices WHERE id=?', (t,)).fetchone() for t in targets) if d]
        if devices and all(u['access'].on('approve', d) for d in devices):
            n += 1
    return n


def notify_approvers(playlist, username, count):
    try:
        from . import monitor
        monitor.approval_requested(playlist, username, count)
    except Exception as e:   # an alert channel that does not answer must not stop the request
        print('approval alert failed:', e, flush=True)


def _view(c, row, u):
    p = json.loads(row['payload_json'] or '{}')
    devices = []
    for t in p.get('targets') or []:
        d = c.execute('SELECT * FROM devices WHERE id=?', (t,)).fetchone()
        if d:
            devices.append(d)
    if not any(u['access'].on('view', d) for d in devices) and row['username'] != u['username']:
        return None
    version = c.execute('SELECT items_json, created FROM global_playlist_versions WHERE id=?', (p.get('version_id'),)).fetchone()
    items = json.loads(version['items_json']) if version else []
    current = c.execute('SELECT name FROM global_playlists WHERE id=?', (row['playlist_id'],)).fetchone()
    return {'id': row['id'], 'kind': row['kind'], 'state': row['state'], 'playlist_id': row['playlist_id'],
            'playlist': current['name'] if current else row['summary'], 'mode': p.get('mode'), 'note': p.get('note', ''),
            'targets': [{'id': d['id'], 'name': d['name']} for d in devices], 'version_id': p.get('version_id'),
            'items': [{'name': it.get('name'), 'kind': it.get('kind'), 'duration': it.get('duration')} for it in items],
            'username': row['username'], 'created': row['created'], 'decided_by': row['decided_by'],
            'decided': row['decided'], 'comment': row['comment'],
            'can_decide': bool(devices) and all(u['access'].on('approve', d) for d in devices),
            'mine': row['username'] == u['username']}


@router.get('/api/approvals')
def list_approvals(r: Request, state: str = 'pending'):
    u = current_user(r)
    with db() as c:
        q = "SELECT * FROM approvals WHERE state='pending' ORDER BY id DESC" if state == 'pending' else \
            "SELECT * FROM approvals WHERE state!='pending' ORDER BY id DESC LIMIT 100"
        out = [x for x in (_view(c, row, u) for row in c.execute(q).fetchall()) if x]
    return {'enabled': bool(cfg().get('require_approval')), 'approvals': out}


@router.put('/api/approvals/settings')
async def approval_settings(r: Request):
    u = current_user(r, 'settings')
    d = await _json(r)
    conf = cfg()
    conf['require_approval'] = bool(d.get('enabled'))
    save_cfg(conf)
    audit(u, 'approval.settings', '', {'enabled': conf['require_approval']})
    return {'ok': True, 'enabled': conf['require_approval']}


def _pending(c, aid):
    row = c.execute('SELECT * FROM approvals WHERE id=?', (aid,)).fetchone()
    if not row:
        raise HTTPException(404, 'not_found')
    if row['state'] != 'pending':
        raise HTTPException(409, 'already_decided')
    return row


@router.post('/api/approvals/{aid}/approve')
async def approve(aid: int, r: Request):
    from .playlists import deploy_now
    u = current_user(r, 'approve')
    d = await _json(r)
    with db() as c:
        row = _pending(c, aid)
        p = json.loads(row['payload_json'] or '{}')
        devices = [x for x in (c.execute('SELECT * FROM devices WHERE id=?', (t,)).fetchone() for t in p.get('targets') or []) if x]
        if not devices:
            raise HTTPException(400, 'no_devices')
        if not all(u['access'].on('approve', x) for x in devices):
            raise HTTPException(403, 'forbidden')
        if not c.execute('SELECT 1 FROM global_playlists WHERE id=?', (row['playlist_id'],)).fetchone():
            raise HTTPException(404, 'not_found')
        version = c.execute('SELECT items_json FROM global_playlist_versions WHERE id=?', (p.get('version_id'),)).fetchone()
        raw = json.loads(version['items_json']) if version else None
        res = deploy_now(c, row['playlist_id'], [x['id'] for x in devices], p.get('mode', 'append'),
                         f"{row['username']}/{u['username']}"[:64], raw=raw)
        c.execute("UPDATE approvals SET state='approved', decided_by=?, decided=?, comment=? WHERE id=?",
                  (u['username'], time.time(), str(d.get('comment') or '')[:500], aid))
    audit(u, 'approval.approve', str(aid), {'playlist': res['name'], 'requested_by': row['username']})
    return {'ok': True, **{k: res[k] for k in ('batch', 'media_unsupported', 'items_unsupported')}}


@router.post('/api/approvals/{aid}/reject')
async def reject(aid: int, r: Request):
    u = current_user(r, 'approve')
    d = await _json(r)
    with db() as c:
        row = _pending(c, aid)
        view = _view(c, row, u)
        if not view or not view['can_decide']:
            raise HTTPException(403, 'forbidden')
        c.execute("UPDATE approvals SET state='rejected', decided_by=?, decided=?, comment=? WHERE id=?",
                  (u['username'], time.time(), str(d.get('comment') or '')[:500], aid))
    audit(u, 'approval.reject', str(aid), {'requested_by': row['username']})
    return {'ok': True}


@router.delete('/api/approvals/{aid}')
def cancel(aid: int, r: Request):
    u = current_user(r)
    with db() as c:
        row = _pending(c, aid)
        if row['username'] != u['username']:
            raise HTTPException(403, 'forbidden')
        c.execute("UPDATE approvals SET state='cancelled', decided_by=?, decided=? WHERE id=?",
                  (u['username'], time.time(), aid))
    audit(u, 'approval.cancel', str(aid))
    return {'ok': True}
