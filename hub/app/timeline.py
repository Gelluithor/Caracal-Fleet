"""What plays when: a day (or days) of a device, or of all devices of a location or group, from the schedules (which
global playlist, screens on/off), the scheduled actions, emergency messages and items with a validity.
Times are minutes of the day in the hub's time zone.
"""
import json
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request

from .core import current_user, db
from .devices import build
from .schedule import _resolve, _task, current, device_schedules, timezone_name, zone

router = APIRouter()


def _segments(values):
    """[(minute, value)] for every minute -> [{start, end, value}] of the runs of the same value."""
    out = []
    for m, v in values:
        if out and out[-1]['value'] == v:
            out[-1]['end'] = m + 1
        else:
            out.append({'start': m, 'end': m + 1, 'value': v})
    return out


def _day_of(sched, day, names):
    """Playlist and screen segments of a schedule on a day."""
    plays, screen = [], []
    for m in range(1440):
        pid, _slot, scr = current(sched, day + timedelta(minutes=m))
        plays.append((m, pid))
        screen.append((m, scr))
    return ([{'start': s['start'], 'end': s['end'], 'playlist_id': s['value'], 'name': names.get(s['value'], '')}
             for s in _segments(plays) if s['value']],
            [{'start': s['start'], 'end': s['end']} for s in _segments(screen) if s['value'] == 'off'])


def _window(day, a, b):
    """Minutes of the day covered by the Unix time range a..b (None = open), or None."""
    start, end = day.timestamp(), (day + timedelta(days=1)).timestamp()
    a, b = a or start, b or end
    if b <= start or a >= end:
        return None
    return int((max(a, start) - start) // 60), int((min(b, end) - start + 59) // 60)


@router.get('/api/timeline')
def timeline(r: Request, device: str = '', location: str = '', group: str = '', date: str = '', days: int = 1):
    u = current_user(r)
    tz = zone()
    try:
        first = datetime.strptime(date, '%Y-%m-%d').replace(tzinfo=tz) if date else \
            datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    except ValueError:
        raise HTTPException(400, 'invalid_value')
    days = max(1, min(14, days))
    with db() as c:
        rows = [x for x in c.execute('SELECT * FROM devices ORDER BY name COLLATE NOCASE').fetchall() if u['access'].on('view', x)]
        if device:
            rows = [x for x in rows if x['id'] == device]
        if location:
            rows = [x for x in rows if x['location'] == location]
        if group:
            rows = [x for x in rows if x['device_group'] == group]
        follows = device_schedules(c)
        names = {x['id']: x['name'] for x in c.execute('SELECT id, name FROM global_playlists')}
        playlist_items = {}
        for x in c.execute('SELECT playlist_id, name, kind, valid_from, valid_until FROM global_playlist_items '
                           'WHERE valid_from IS NOT NULL OR valid_until IS NOT NULL'):
            playlist_items.setdefault(x['playlist_id'], []).append(dict(x))
        tasks = [_task(x) for x in c.execute('SELECT * FROM scheduled_tasks WHERE enabled=1').fetchall()]
        task_devices = {t['id']: {d['id'] for d in _resolve(c, t['targets'])} for t in tasks}
        takeovers = [dict(x) for x in c.execute('SELECT * FROM takeovers ORDER BY id')]
    cache, out = {}, []
    for i in range(days):
        day = first + timedelta(days=i)
        key = day.strftime('%Y-%m-%d')
        devices = []
        for row in rows:
            sched = follows.get(row['id'])
            if sched and (sched['id'], key) not in cache:
                cache[(sched['id'], key)] = _day_of(sched, day, names)
            playlists, screen_off = cache[(sched['id'], key)] if sched else ([], [])
            items = []
            # the device's own items with a validity, and those of the playlists the schedule puts on today
            for a in build(row, full=True)['assets']:
                w = _window(day, a.get('valid_from'), a.get('valid_until')) if a.get('valid_from') or a.get('valid_until') else None
                if w:
                    items.append({'name': a['name'], 'kind': a['kind'], 'start': w[0], 'end': w[1], 'from': 'device'})
            for pid in {p['playlist_id'] for p in playlists}:
                for it in playlist_items.get(pid, []):
                    w = _window(day, it['valid_from'], it['valid_until'])
                    if w:
                        items.append({'name': it['name'], 'kind': it['kind'], 'start': w[0], 'end': w[1], 'from': names.get(pid, '')})
            day_tasks = [{'minute': int(t['time'][:2]) * 60 + int(t['time'][3:]), 'name': t['name'], 'action': t['action']}
                         for t in tasks if row['id'] in task_devices[t['id']]
                         and ((t['date'] and t['date'] == key) or (not t['date'] and day.weekday() in t['days']))]
            alerts = []
            for x in takeovers:
                if row['id'] not in {d['id'] for d in json.loads(x['devices_json'] or '[]')}:
                    continue
                w = _window(day, x['created'], x['ended'] or x['until'] or (x['created'] + 86400 * 7))
                if w:
                    alerts.append({'title': x['title'], 'start': w[0], 'end': w[1], 'level': x['level']})
            devices.append({'id': row['id'], 'name': row['name'], 'location': row['location'], 'group': row['device_group'],
                            'schedule': {'id': sched['id'], 'name': sched['name']} if sched else None,
                            'playlists': playlists, 'screen_off': screen_off, 'tasks': sorted(day_tasks, key=lambda x: x['minute']),
                            'items': items, 'takeovers': alerts})
        out.append({'date': key, 'weekday': day.weekday(), 'devices': devices})
    return {'timezone': timezone_name(), 'now': datetime.now(tz).strftime('%Y-%m-%d %H:%M'), 'days': out}
