"""Proof of play: what the screens showed and for how long, reported by the agents (CARACAL 2026.10.23.1, agent 4.17).
A report sums the plays by item, device or day for a time range; the plays can be downloaded as CSV.
"""
import csv
import io
import json
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from .core import current_user, db

router = APIRouter()
KEEP = 400 * 86400          # plays are kept for a bit more than a year
MAX_PER_REPORT = 2000


def record(c, did, plays):
    """Stores the plays of a heartbeat (each once, also when the agent sends them again)."""
    now, n = time.time(), 0
    for p in plays[:MAX_PER_REPORT]:
        if not isinstance(p, dict):
            continue
        try:
            started, ended = float(p['started']), float(p['ended'])
            node_id = int(p['id'])
        except (KeyError, TypeError, ValueError):
            continue
        if ended < started or started > now + 86400:
            continue
        cur = c.execute('INSERT OR IGNORE INTO plays(device_id, node_id, asset_id, name, kind, started, ended) '
                        'VALUES(?,?,?,?,?,?,?)', (did, node_id, p.get('asset_id') if isinstance(p.get('asset_id'), int) else None,
                                                  str(p.get('name') or '')[:200], str(p.get('kind') or '')[:20],
                                                  started, min(ended, started + 7 * 86400)))
        n += cur.rowcount
    if n and int(now) % 50 == 0:
        c.execute('DELETE FROM plays WHERE started<?', (now - KEEP,))
    return n


def _range(r):
    """(from, to) as Unix time from ?from=YYYY-MM-DD&to=YYYY-MM-DD in the hub's time zone (to is inclusive)."""
    from .schedule import zone
    tz = zone()

    def day(v, default):
        try:
            return datetime.strptime(v, '%Y-%m-%d').replace(tzinfo=tz) if v else default
        except ValueError:
            raise HTTPException(400, 'invalid_value')
    today = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    start = day(r.query_params.get('from'), today - timedelta(days=6))
    end = day(r.query_params.get('to'), today) + timedelta(days=1)
    if end <= start:
        raise HTTPException(400, 'invalid_value')
    return start.timestamp(), end.timestamp(), tz


def _rows(c, u, r):
    start, end, tz = _range(r)
    q = r.query_params
    devices = {x['id']: x for x in c.execute('SELECT * FROM devices').fetchall() if u['access'].on('view', x)}
    if q.get('device'):
        devices = {k: v for k, v in devices.items() if k == q['device']}
    if q.get('location'):
        devices = {k: v for k, v in devices.items() if v['location'] == q['location']}
    if q.get('group'):
        devices = {k: v for k, v in devices.items() if v['device_group'] == q['group']}
    if q.get('tag'):
        devices = {k: v for k, v in devices.items() if q['tag'] in json.loads(v['tags_json'] or '[]')}
    if not devices:
        return [], devices, start, end, tz
    marks = ','.join('?' * len(devices))
    sql = (f'SELECT * FROM plays WHERE device_id IN ({marks}) AND started<? AND ended>? ' +
           ('AND name LIKE ? ' if q.get('q') else '') + ('AND kind=? ' if q.get('kind') else '') + 'ORDER BY started')
    args = [*devices, end, start] + ([f"%{q['q']}%"] if q.get('q') else []) + ([q['kind']] if q.get('kind') else [])
    out = []
    for p in c.execute(sql, args):
        p = dict(p)
        p['seconds'] = max(0.0, min(p['ended'], end) - max(p['started'], start))   # only the part inside the range
        out.append(p)
    return out, devices, start, end, tz


@router.get('/api/plays/report')
def report(r: Request):
    u = current_user(r)
    by = r.query_params.get('by', 'item')
    if by not in ('item', 'device', 'day'):
        raise HTTPException(400, 'invalid_value')
    with db() as c:
        plays, devices, start, end, tz = _rows(c, u, r)
    groups, days = {}, {}
    for p in plays:
        d = datetime.fromtimestamp(p['started'], tz).strftime('%Y-%m-%d')
        key = (p['name'], p['kind']) if by == 'item' else p['device_id'] if by == 'device' else d
        g = groups.setdefault(key, {'count': 0, 'seconds': 0.0, 'devices': set(), 'items': set(), 'first': p['started'], 'last': p['ended']})
        g['count'] += 1
        g['seconds'] += p['seconds']
        g['devices'].add(p['device_id'])
        g['items'].add(p['name'])
        g['last'] = max(g['last'], p['ended'])
        x = days.setdefault(d, {'count': 0, 'seconds': 0.0})
        x['count'] += 1
        x['seconds'] += p['seconds']
    rows = []
    for key, g in groups.items():
        row = {'count': g['count'], 'seconds': round(g['seconds']), 'devices': len(g['devices']), 'items': len(g['items']),
               'first': g['first'], 'last': g['last']}
        if by == 'item':
            row.update(name=key[0], kind=key[1])
        elif by == 'device':
            dev = devices.get(key)
            row.update(device_id=key, name=dev['name'] if dev else key, location=dev['location'] if dev else '')
        else:
            row.update(day=key)
        rows.append(row)
    rows.sort(key=lambda x: x['day'] if by == 'day' else -x['seconds'])
    # every day of the range, also the ones without plays (for the chart)
    chart, t = [], datetime.fromtimestamp(start, tz)
    while t.timestamp() < end and len(chart) < 400:
        k = t.strftime('%Y-%m-%d')
        chart.append({'day': k, **{x: round(y) for x, y in days.get(k, {'count': 0, 'seconds': 0}).items()}})
        t += timedelta(days=1)
    return {'from': start, 'to': end, 'by': by, 'rows': rows, 'days': chart,
            'total': {'count': len(plays), 'seconds': round(sum(p['seconds'] for p in plays)),
                      'items': len({(p['name'], p['kind']) for p in plays}), 'devices': len({p['device_id'] for p in plays})}}


@router.get('/api/plays/export.csv')
def export(r: Request):
    u = current_user(r)
    with db() as c:
        plays, devices, _start, _end, tz = _rows(c, u, r)
    out = io.StringIO()
    w = csv.writer(out, delimiter=';')
    w.writerow(['device_id', 'device', 'location', 'group', 'item', 'kind', 'start', 'end', 'seconds'])
    stamp = lambda ts: datetime.fromtimestamp(ts, tz).strftime('%Y-%m-%d %H:%M:%S')
    for p in plays:
        dev = devices.get(p['device_id'])
        w.writerow([p['device_id'], dev['name'] if dev else '', dev['location'] if dev else '', dev['device_group'] if dev else '',
                    p['name'], p['kind'], stamp(p['started']), stamp(p['ended']), round(p['seconds'])])
    return Response('﻿' + out.getvalue(), media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition': 'attachment; filename="caracal-plays.csv"'})
