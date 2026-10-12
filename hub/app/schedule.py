"""Schedules: what the screens show at which time, when they are on, and actions at a given time.

- A *schedule* applies to devices, groups and locations. Its time slots name a global playlist for days of the week
  and a time range (a range may go past midnight); outside the slots the default playlist (if any) is shown. The hub
  deploys a playlist once when it becomes the current one (or when an offline device comes back), so changes made
  on a device in between stay until the next slot. Its *screen hours* say when the screens are on; outside them the
  agent switches the screen off (DPMS and HDMI-CEC). A device follows the first schedule (lowest id) that names it.
- A *scheduled action* runs once at a time on days of the week or on a date: restart the player, reboot, update the
  agent or CARACAL, switch the screens on or off, deploy a playlist, apply a template or send a notification.

Times are in the hub's time zone (setting 'timezone', IANA name such as Europe/Prague).
"""
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Request

from .core import audit, cfg, current_user, db, new_batch, queue_command, save_cfg, version_tuple

router = APIRouter()
TIME_RE = re.compile(r'([01]\d|2[0-3]):[0-5]\d')
DATE_RE = re.compile(r'\d{4}-\d{2}-\d{2}')
TASK_ACTIONS = ('restart_player', 'reboot', 'update_agent', 'update_caracal', 'screen_on', 'screen_off',
                'deploy_playlist', 'apply_template', 'notify')
SCREEN_AGENT = (4, 14, 0)     # agents that switch the screen
CHECK_EVERY = 30
TASK_WINDOW = 600             # a task is still run when the hub was down at its time, up to 10 minutes later
SCREEN_RETRY = 180            # seconds before the same screen command is sent again
_screen_sent = {}             # device id -> (state, time) of the last screen command
_lock = threading.Lock()


# ---------------------------------------------------------------- time

def timezone_name():
    return cfg().get('timezone') or os.environ.get('TZ') or 'UTC'


def zone():
    try:
        return ZoneInfo(timezone_name())
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo('UTC')


def local_now(ts=None):
    return datetime.fromtimestamp(ts if ts is not None else time.time(), zone())


def _minutes(hhmm):
    h, m = hhmm.split(':')
    return int(h) * 60 + int(m)


def covers(window, now):
    """Is the local time in the window {days, start, end}? end <= start means the range goes past midnight (it
    belongs to the day it starts on); 00:00 as the end is midnight."""
    t, day = now.hour * 60 + now.minute, now.weekday()
    start, end = _minutes(window['start']), _minutes(window['end'])
    if start < end:
        return day in window['days'] and start <= t < end
    return (day in window['days'] and t >= start) or ((day - 1) % 7 in window['days'] and t < end)


# ---------------------------------------------------------------- validation

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


def _days(v):
    if not isinstance(v, list) or not v or any(not isinstance(x, int) or isinstance(x, bool) or not 0 <= x <= 6 for x in v):
        raise HTTPException(400, 'invalid_days')
    return sorted(set(v))


def _time(v):
    v = _text(v, 5)
    if not TIME_RE.fullmatch(v):
        raise HTTPException(400, 'invalid_time')
    return v


def _window(w):
    if not isinstance(w, dict):
        raise HTTPException(400, 'invalid_value')
    out = {'days': _days(w.get('days')), 'start': _time(w.get('start')), 'end': _time(w.get('end'))}
    if out['start'] == out['end']:
        raise HTTPException(400, 'invalid_time')
    return out


def _playlist(c, v, required=True):
    if v in (None, '', 0) and not required:
        return None
    try:
        pid = int(v)
    except (TypeError, ValueError):
        raise HTTPException(400, 'playlist_not_found')
    if not c.execute('SELECT 1 FROM global_playlists WHERE id=?', (pid,)).fetchone():
        raise HTTPException(400, 'playlist_not_found')
    return pid


def _targets(v):
    if not isinstance(v, dict):
        raise HTTPException(400, 'no_devices')
    out = {k: sorted({_text(x, 120) for x in v.get(k) or [] if _text(x, 120)}) for k in ('devices', 'groups', 'locations')}
    if not any(out.values()):
        raise HTTPException(400, 'no_devices')
    return out


def _schedule_fields(c, d, current=None):
    cur = current or {}
    out = {'name': _text(d.get('name', cur.get('name')), 120)}
    if not out['name']:
        raise HTTPException(400, 'name_required')
    out['enabled'] = bool(d.get('enabled', cur.get('enabled', True)))
    out['targets'] = _targets(d.get('targets', cur.get('targets')))
    slots = d.get('slots', cur.get('slots') or [])
    if not isinstance(slots, list) or len(slots) > 50:
        raise HTTPException(400, 'invalid_value')
    out['slots'] = [{**_window(s), 'playlist_id': _playlist(c, s.get('playlist_id'))} for s in slots]
    out['default_playlist_id'] = _playlist(c, d.get('default_playlist_id', cur.get('default_playlist_id')), False)
    screen = d.get('screen', cur.get('screen') or [])
    if not isinstance(screen, list) or len(screen) > 50:
        raise HTTPException(400, 'invalid_value')
    out['screen'] = [_window(w) for w in screen]
    if not out['slots'] and not out['default_playlist_id'] and not out['screen']:
        raise HTTPException(400, 'schedule_empty')
    return out


def _task_fields(c, d, current=None):
    from .main import validate_notification   # main imports this module
    cur = current or {}
    out = {'name': _text(d.get('name', cur.get('name')), 120)}
    if not out['name']:
        raise HTTPException(400, 'name_required')
    out['enabled'] = bool(d.get('enabled', cur.get('enabled', True)))
    out['targets'] = _targets(d.get('targets', cur.get('targets')))
    out['time'] = _time(d.get('time', cur.get('time')))
    date = _text(d.get('date', cur.get('date')), 10)
    if date:
        if not DATE_RE.fullmatch(date):
            raise HTTPException(400, 'invalid_date')
        try:
            datetime.strptime(date, '%Y-%m-%d')
        except ValueError:
            raise HTTPException(400, 'invalid_date')
        out['date'], out['days'] = date, []
    else:
        out['date'], out['days'] = '', _days(d.get('days', cur.get('days')))
    action = d.get('action', cur.get('action'))
    if action not in TASK_ACTIONS:
        raise HTTPException(400, 'unknown_action')
    out['action'] = action
    p = d.get('payload', cur.get('payload') or {})
    p = p if isinstance(p, dict) else {}
    if action == 'deploy_playlist':
        out['payload'] = {'playlist_id': _playlist(c, p.get('playlist_id')), 'mode': 'append' if p.get('mode') == 'append' else 'replace'}
    elif action == 'apply_template':
        try:
            tid = int(p.get('template_id'))
        except (TypeError, ValueError):
            raise HTTPException(400, 'template_not_found')
        if not c.execute('SELECT 1 FROM node_templates WHERE id=?', (tid,)).fetchone():
            raise HTTPException(400, 'template_not_found')
        out['payload'] = {'template_id': tid}
    elif action == 'notify':
        out['payload'] = validate_notification(p)
    else:
        out['payload'] = {}
    return out


# ---------------------------------------------------------------- storage

def _schedule(row):
    return {'id': row['id'], 'name': row['name'], 'enabled': bool(row['enabled']), **json.loads(row['data_json'] or '{}'),
            'updated': row['updated'], 'updated_by': row['updated_by']}


def _task(row):
    return {'id': row['id'], 'name': row['name'], 'enabled': bool(row['enabled']), **json.loads(row['data_json'] or '{}'),
            'last_run': row['last_run'], 'last_result': json.loads(row['last_result'] or 'null'),
            'updated': row['updated'], 'updated_by': row['updated_by']}


def _resolve(c, targets):
    """Device rows named by the targets: devices, groups and locations."""
    t = targets or {}
    rows = c.execute('SELECT * FROM devices ORDER BY name').fetchall()
    return [r for r in rows if r['id'] in t.get('devices', []) or (r['device_group'] and r['device_group'] in t.get('groups', []))
            or (r['location'] and r['location'] in t.get('locations', []))]


def _online(row, now=None):
    from .core import ONLINE_TIMEOUT
    return (now or time.time()) - (row['last_seen'] or 0) < ONLINE_TIMEOUT


# ---------------------------------------------------------------- what applies now

def current(sched, now):
    """(playlist id or None, slot index or None, screen 'on'/'off' or None) of a schedule at the local time."""
    pid, slot = sched.get('default_playlist_id'), None
    for i, s in enumerate(sched.get('slots') or []):
        if covers(s, now):
            pid, slot = s['playlist_id'], i
            break
    screen = None
    if sched.get('screen'):
        screen = 'on' if any(covers(w, now) for w in sched['screen']) else 'off'
    return pid, slot, screen


def device_schedules(c):
    """device id -> schedule (the first enabled schedule naming the device)."""
    out = {}
    for row in c.execute('SELECT * FROM node_schedules WHERE enabled=1 ORDER BY id'):
        s = _schedule(row)
        for d in _resolve(c, s['targets']):
            out.setdefault(d['id'], s)
    return out


def apply_schedules(c, now_ts=None):
    """Deploy the current playlist of every schedule and switch screens; returns what was queued."""
    from .playlists import deploy_items
    now_ts = now_ts if now_ts is not None else time.time()
    now = local_now(now_ts)
    done = []
    for did, s in device_schedules(c).items():
        row = c.execute('SELECT * FROM devices WHERE id=?', (did,)).fetchone()
        if not row or not _online(row):   # online now (the clock of a test may differ)
            continue
        pid, slot, screen = current(s, now)
        user = f'schedule:{s["name"]}'[:64]
        if pid:
            key = f'{s["id"]}:{pid}'
            if row['schedule_key'] != key:
                items = deploy_items(c, pid, missing_ok=True)
                name = c.execute('SELECT name FROM global_playlists WHERE id=?', (pid,)).fetchone()
                if items and name:
                    queue_command(c, did, 'import_playlist', {
                        'items': items, 'mode': 'replace', 'global_playlist': name['name'],
                        'replace_collections': any(it.get('type') == 'collection' for it in items)}, user, new_batch())
                    done.append((did, 'playlist', pid))
                c.execute('UPDATE devices SET schedule_key=? WHERE id=?', (key, did))
        status = json.loads(row['status_json'] or '{}')
        reported = status.get('screen')
        if screen and reported in ('on', 'off') and reported != screen and version_tuple(row['version']) >= SCREEN_AGENT:
            with _lock:
                last = _screen_sent.get(did)
                if last and last[0] == screen and now_ts - last[1] < SCREEN_RETRY:
                    continue
                _screen_sent[did] = (screen, now_ts)
            queue_command(c, did, 'screen', {'state': screen}, user, new_batch())
            done.append((did, 'screen', screen))
    return done


def run_task(c, task, username):
    """Queue a scheduled action for its devices: {'queued': [device ids], 'skipped': [{device_id, reason}]}."""
    from .main import validate_command
    from .playlists import deploy_items
    from . import images, templates
    queued, skipped = [], []
    batch = new_batch()
    action, payload = task['action'], dict(task.get('payload') or {})
    if action == 'update_caracal':
        payload = {'version': images.latest_version() or ''}
    for row in _resolve(c, task['targets']):
        try:
            if not _online(row):
                raise HTTPException(409, 'device_offline')
            if action == 'apply_template':
                tpl = c.execute('SELECT * FROM node_templates WHERE id=?', (payload['template_id'],)).fetchone()
                if not tpl:
                    raise HTTPException(409, 'template_not_found')
                res = templates.apply_to(c, tpl, row, username, batch)
                if not res['queued'] and res['skipped']:
                    raise HTTPException(409, res['skipped'][0]['reason'])
            elif action == 'deploy_playlist':
                name = c.execute('SELECT name FROM global_playlists WHERE id=?', (payload['playlist_id'],)).fetchone()
                items = deploy_items(c, payload['playlist_id'], missing_ok=True)
                if not name or not items:
                    raise HTTPException(409, 'nothing_to_copy')
                queue_command(c, row['id'], 'import_playlist', {'items': items, 'mode': payload['mode'],
                                                                'global_playlist': name['name']}, username, batch)
            else:
                real = 'screen' if action.startswith('screen_') else action
                p = {'state': action[7:]} if real == 'screen' else payload
                if real == 'update_caracal' and build_runtime(row) != 'docker':
                    raise HTTPException(409, 'not_docker')
                queue_command(c, row['id'], real, validate_command(c, row, real, p), username, batch)
            queued.append(row['id'])
        except HTTPException as e:
            skipped.append({'device_id': row['id'], 'reason': e.detail})
    return {'queued': queued, 'skipped': skipped, 'batch': batch}


def build_runtime(row):
    from .devices import build
    return build(row)['runtime']


def due_tasks(c, now_ts=None):
    """Run the scheduled actions whose time has come (once per day)."""
    now_ts = now_ts if now_ts is not None else time.time()
    now = local_now(now_ts)
    today = now.strftime('%Y-%m-%d')
    ran = []
    for row in c.execute('SELECT * FROM scheduled_tasks WHERE enabled=1 ORDER BY id').fetchall():
        t = _task(row)
        if t['date'] and t['date'] != today or not t['date'] and now.weekday() not in t['days']:
            continue
        at = now.replace(hour=int(t['time'][:2]), minute=int(t['time'][3:]), second=0, microsecond=0)
        if not timedelta(0) <= now - at < timedelta(seconds=TASK_WINDOW) or row['last_day'] == today:
            continue
        result = run_task(c, t, f'schedule:{t["name"]}'[:64])
        c.execute('UPDATE scheduled_tasks SET last_run=?, last_day=?, last_result=? WHERE id=?',
                  (now_ts, today, json.dumps(result), t['id']))
        c.execute('INSERT INTO audit(username, action, target, detail, created) VALUES(?,?,?,?,?)',
                  ('schedule', 'schedule.task_run', str(t['id']), json.dumps({'name': t['name'], 'action': t['action'],
                                                                             **{k: result[k] for k in ('queued', 'skipped')}}), now_ts))
        ran.append(t['id'])
    return ran


def tick(now_ts=None):
    from . import rollout
    with db() as c:
        due_tasks(c, now_ts)
        apply_schedules(c, now_ts)
    rollout.tick(now_ts)   # staged updates advance in the same loop
    from .system import backup_tick
    backup_tick(now_ts)    # and the daily backup


def _loop():
    while True:
        time.sleep(CHECK_EVERY)
        try:
            tick()
        except Exception as e:     # the loop keeps running; the next pass tries again
            print('schedule check failed:', e, flush=True)


def start():
    threading.Thread(target=_loop, daemon=True, name='fleet-schedule').start()


# ---------------------------------------------------------------- API

@router.get('/api/schedules')
def list_schedules(r: Request):
    current_user(r)
    now = local_now()
    with db() as c:
        schedules = [_schedule(x) for x in c.execute('SELECT * FROM node_schedules ORDER BY name COLLATE NOCASE')]
        tasks = sorted((_task(x) for x in c.execute('SELECT * FROM scheduled_tasks')), key=lambda t: (t['time'], t['name'].lower()))
        follows = device_schedules(c)
    for s in schedules:
        pid, slot, screen = current(s, now)
        s['now'] = {'playlist_id': pid, 'slot': slot, 'screen': screen}
        s['devices'] = sorted(did for did, x in follows.items() if x['id'] == s['id'])
    return {'timezone': timezone_name(), 'now': now.strftime('%Y-%m-%d %H:%M'), 'weekday': now.weekday(),
            'schedules': schedules, 'tasks': tasks}


@router.put('/api/schedules/settings')
async def schedule_settings(r: Request):
    u = current_user(r, 'manage')
    d = await _json(r)
    name = _text(d.get('timezone'), 64)
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(400, 'invalid_timezone')
    conf = cfg()
    conf['timezone'] = name
    save_cfg(conf)
    audit(u, 'schedule.timezone', name)
    return {'timezone': name}


@router.post('/api/schedules')
async def create_schedule(r: Request):
    u = current_user(r, 'manage')
    d = await _json(r)
    now = time.time()
    with db() as c:
        f = _schedule_fields(c, d)
        sid = c.execute('INSERT INTO node_schedules(name, enabled, data_json, created, updated, updated_by) VALUES(?,?,?,?,?,?)',
                        (f.pop('name'), int(f.pop('enabled')), json.dumps(f), now, now, u['username'])).lastrowid
    audit(u, 'schedule.create', str(sid), {'name': d.get('name')})
    return {'id': sid}


@router.patch('/api/schedules/{sid}')
async def edit_schedule(sid: int, r: Request):
    u = current_user(r, 'manage')
    d = await _json(r)
    with db() as c:
        row = c.execute('SELECT * FROM node_schedules WHERE id=?', (sid,)).fetchone()
        if not row:
            raise HTTPException(404, 'not_found')
        f = _schedule_fields(c, d, _schedule(row))
        c.execute('UPDATE node_schedules SET name=?, enabled=?, data_json=?, updated=?, updated_by=? WHERE id=?',
                  (f.pop('name'), int(f.pop('enabled')), json.dumps(f), time.time(), u['username'], sid))
        # the devices get the playlist of the changed schedule at the next check
        c.execute("UPDATE devices SET schedule_key='' WHERE schedule_key LIKE ?", (f'{sid}:%',))
    audit(u, 'schedule.edit', str(sid), {'name': d.get('name')})
    return {'ok': True}


@router.delete('/api/schedules/{sid}')
def delete_schedule(sid: int, r: Request):
    u = current_user(r, 'manage')
    with db() as c:
        row = c.execute('SELECT name FROM node_schedules WHERE id=?', (sid,)).fetchone()
        if not row:
            raise HTTPException(404, 'not_found')
        c.execute('DELETE FROM node_schedules WHERE id=?', (sid,))
        c.execute("UPDATE devices SET schedule_key='' WHERE schedule_key LIKE ?", (f'{sid}:%',))
    audit(u, 'schedule.delete', str(sid), {'name': row['name']})
    return {'ok': True}


@router.post('/api/scheduled-tasks')
async def create_task(r: Request):
    u = current_user(r, 'manage')
    d = await _json(r)
    now = time.time()
    with db() as c:
        f = _task_fields(c, d)
        tid = c.execute('INSERT INTO scheduled_tasks(name, enabled, data_json, created, updated, updated_by) VALUES(?,?,?,?,?,?)',
                        (f.pop('name'), int(f.pop('enabled')), json.dumps(f), now, now, u['username'])).lastrowid
        # a task created after its time today waits for its next day
        if not f['date'] or f['date'] == local_now(now).strftime('%Y-%m-%d'):
            at = local_now(now).replace(hour=int(f['time'][:2]), minute=int(f['time'][3:]), second=0, microsecond=0)
            if local_now(now) > at:
                c.execute('UPDATE scheduled_tasks SET last_day=? WHERE id=?', (local_now(now).strftime('%Y-%m-%d'), tid))
    audit(u, 'schedule.task_create', str(tid), {'name': d.get('name'), 'action': d.get('action')})
    return {'id': tid}


@router.patch('/api/scheduled-tasks/{tid}')
async def edit_task(tid: int, r: Request):
    u = current_user(r, 'manage')
    d = await _json(r)
    with db() as c:
        row = c.execute('SELECT * FROM scheduled_tasks WHERE id=?', (tid,)).fetchone()
        if not row:
            raise HTTPException(404, 'not_found')
        f = _task_fields(c, d, _task(row))
        c.execute('UPDATE scheduled_tasks SET name=?, enabled=?, data_json=?, updated=?, updated_by=? WHERE id=?',
                  (f.pop('name'), int(f.pop('enabled')), json.dumps(f), time.time(), u['username'], tid))
    audit(u, 'schedule.task_edit', str(tid), {'name': d.get('name')})
    return {'ok': True}


@router.delete('/api/scheduled-tasks/{tid}')
def delete_task(tid: int, r: Request):
    u = current_user(r, 'manage')
    with db() as c:
        row = c.execute('SELECT name FROM scheduled_tasks WHERE id=?', (tid,)).fetchone()
        if not row:
            raise HTTPException(404, 'not_found')
        c.execute('DELETE FROM scheduled_tasks WHERE id=?', (tid,))
    audit(u, 'schedule.task_delete', str(tid), {'name': row['name']})
    return {'ok': True}


@router.post('/api/scheduled-tasks/{tid}/run')
def run_task_now(tid: int, r: Request):
    u = current_user(r, 'manage')
    with db() as c:
        row = c.execute('SELECT * FROM scheduled_tasks WHERE id=?', (tid,)).fetchone()
        if not row:
            raise HTTPException(404, 'not_found')
        t = _task(row)
        result = run_task(c, t, u['username'])
        c.execute('UPDATE scheduled_tasks SET last_run=?, last_result=? WHERE id=?', (time.time(), json.dumps(result), tid))
    audit(u, 'schedule.task_run', str(tid), {'name': t['name'], 'action': t['action'], 'queued': result['queued'],
                                              'skipped': result['skipped']})
    return result
