"""Emergency messages: one click puts a message on the whole screen of all devices, or of chosen locations, groups
and devices (evacuation, an outage), for a time or until it is ended; then the playlists go on by themselves.
Nodes from CARACAL 2026.10.23.1 with agent 4.17 show it instead of the playlist; older ones get it as a notification.
"""
import json
import time

from fastapi import APIRouter, HTTPException, Request

from .core import audit, current_user, db, new_batch, queue_command
from .devices import build

router = APIRouter()
LEVELS = ('critical', 'warning', 'info')
FALLBACK_LEVEL = {'critical': 'critical', 'warning': 'warning', 'info': 'info'}


async def _json(r: Request):
    try:
        d = await r.json()
    except ValueError:
        raise HTTPException(400, 'invalid_json')
    return d if isinstance(d, dict) else {}


def _targets(c, u, t):
    t = t if isinstance(t, dict) else {}
    rows = c.execute('SELECT * FROM devices ORDER BY name COLLATE NOCASE').fetchall()
    if not t.get('all'):
        rows = [r for r in rows if r['id'] in (t.get('devices') or []) or (r['device_group'] and r['device_group'] in (t.get('groups') or []))
                or (r['location'] and r['location'] in (t.get('locations') or []))]
    return [r for r in rows if u['access'].on('control', r)]


def active(c, now=None):
    now = now or time.time()
    return [dict(x) for x in c.execute('SELECT * FROM takeovers WHERE ended IS NULL AND (until IS NULL OR until>?) '
                                         'ORDER BY id DESC', (now,))]


def _view(x):
    return {**{k: x[k] for k in ('id', 'title', 'message', 'level', 'until', 'created', 'username', 'ended', 'ended_by')},
            'targets': json.loads(x['targets_json'] or '{}'), 'devices': json.loads(x['devices_json'] or '[]'),
            'active': not x['ended'] and (not x['until'] or x['until'] > time.time())}


@router.get('/api/takeovers')
def list_takeovers(r: Request):
    u = current_user(r)
    with db() as c:
        rows = [dict(x) for x in c.execute('SELECT * FROM takeovers ORDER BY id DESC LIMIT 30')]
        visible = {x['id'] for x in c.execute('SELECT id FROM devices').fetchall()}
        names = {x['id']: x['name'] for x in c.execute('SELECT id, name FROM devices')}
        allowed = {x['id'] for x in c.execute('SELECT * FROM devices').fetchall() if u['access'].on('view', x)}
    out = []
    for x in rows:
        v = _view(x)
        if not any(d['id'] in allowed for d in v['devices']):
            continue
        v['devices'] = [{**d, 'name': names.get(d['id'], d['id'])} for d in v['devices'] if d['id'] in visible]
        out.append(v)
    return out


@router.post('/api/takeovers')
async def start(r: Request):
    u = current_user(r, 'control')
    d = await _json(r)
    title, message = str(d.get('title') or '').strip()[:200], str(d.get('message') or '').strip()[:2000]
    if not title and not message:
        raise HTTPException(400, 'notification_text_required')
    level = d.get('level') if d.get('level') in LEVELS else 'critical'
    try:
        minutes = max(0, min(7 * 1440, int(d.get('minutes') or 0)))
    except (TypeError, ValueError):
        raise HTTPException(400, 'invalid_value')
    now = time.time()
    until = now + minutes * 60 if minutes else None
    batch, devices = new_batch(), []
    with db() as c:
        rows = _targets(c, u, d.get('targets'))
        if not rows:
            raise HTTPException(400, 'no_devices')
        tid = c.execute('INSERT INTO takeovers(title, message, level, targets_json, until, created, username) '
                        'VALUES(?,?,?,?,?,?,?)', (title, message, level, json.dumps(d.get('targets') or {}), until, now,
                                                  u['username'])).lastrowid
        for row in rows:
            caps = build(row)['capabilities']
            if caps.get('takeover') is True:
                queue_command(c, row['id'], 'takeover', {'id': str(tid), 'title': title, 'message': message,
                                                         'level': level, 'until': until}, u['username'], batch,
                              expires=until)
                devices.append({'id': row['id'], 'how': 'takeover'})
            elif caps.get('notify') is not False:
                # an older node: the message as a long notification (it cannot be ended early)
                queue_command(c, row['id'], 'notify', {'title': title[:120] or 'CARACAL', 'message': message[:600],
                                                       'level': FALLBACK_LEVEL[level], 'duration': 120},
                              u['username'], batch)
                devices.append({'id': row['id'], 'how': 'notify'})
            else:
                devices.append({'id': row['id'], 'how': 'none'})
        c.execute('UPDATE takeovers SET devices_json=? WHERE id=?', (json.dumps(devices), tid))
    audit(u, 'takeover.start', str(tid), {'title': title, 'devices': len(devices), 'minutes': minutes})
    try:
        from . import monitor
        monitor.takeover_started(title or message[:60], u['username'], len(devices))
    except Exception as e:
        print('takeover alert failed:', e, flush=True)
    return {'id': tid, 'devices': devices}


@router.post('/api/takeovers/{tid}/end')
def end(tid: int, r: Request):
    u = current_user(r, 'control')
    with db() as c:
        row = c.execute('SELECT * FROM takeovers WHERE id=?', (tid,)).fetchone()
        if not row:
            raise HTTPException(404, 'not_found')
        batch = new_batch()
        for x in json.loads(row['devices_json'] or '[]'):
            dev = c.execute('SELECT * FROM devices WHERE id=?', (x['id'],)).fetchone()
            if not dev or not u['access'].on('control', dev):
                continue
            # one still waiting in the queue is not delivered any more
            c.execute("UPDATE commands SET state='cancelled', updated=? WHERE device_id=? AND action='takeover' "
                      "AND state='queued' AND payload_json LIKE ?", (time.time(), x['id'], f'%"id": "{tid}"%'))
            if x['how'] == 'takeover':
                queue_command(c, x['id'], 'takeover_end', {'id': str(tid)}, u['username'], batch)
        c.execute('UPDATE takeovers SET ended=?, ended_by=? WHERE id=?', (time.time(), u['username'], tid))
    audit(u, 'takeover.end', str(tid))
    return {'ok': True}
