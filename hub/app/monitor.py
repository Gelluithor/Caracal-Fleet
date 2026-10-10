"""Monitoring of the fleet: metric history, a timeline of events and alerts for administrators.

Every heartbeat stores the node's metrics in a five-minute bucket (one row per device and bucket, kept for eight
days); a bucket without a row means the node was not reporting. A background check compares the problems of every
device with the previous check and writes an event when a problem starts or ends. A problem that lasts longer than
the configured delay goes to the alert channels (e-mail, Slack, Microsoft Teams, Discord, ntfy or any webhook),
together with every other problem found in the same check, so a network outage is one message, not fifty. When an
alerted problem ends, that is sent too. A device can be muted for a while (maintenance); its events are still kept.
"""
import json
import smtplib
import ssl
import threading
import time
from email.message import EmailMessage
from urllib.parse import urlsplit

import requests
from fastapi import APIRouter, HTTPException, Request

from .core import REDACTED, audit, cfg, current_user, db, save_cfg
from .devices import build

router = APIRouter()
BUCKET = 300                   # seconds per metric sample
METRIC_DAYS = 8
EVENT_DAYS = 90
CHECK_EVERY = 15
START_GRACE = 60               # after the hub starts, nodes need a moment to report again
LEVELS = ('critical', 'warning')
CHANNEL_TYPES = ('email', 'slack', 'teams', 'discord', 'ntfy', 'webhook')
SECURITY = ('starttls', 'ssl', 'none')
DEFAULTS = {'enabled': False, 'delay': 5, 'levels': ['critical'], 'recovery': True, 'language': 'cs',
            'public_url': '', 'channels': [],
            'smtp': {'host': '', 'port': 587, 'security': 'starttls', 'username': '', 'password': '', 'sender': ''}}
_started = time.time()
_results = {}                  # channel id -> {ok, error, ts} of the last delivery
_lock = threading.Lock()

TEXT = {
    'cs': {
        'offline': 'Offline', 'local_api': 'Lokální API neodpovídá', 'player': 'Player neběží',
        'temperature': 'Vysoká teplota ({v} °C)', 'disk': 'Plný disk ({v} %)', 'ram': 'Málo paměti ({v} %)',
        'cpu': 'Vysoké vytížení CPU ({v} %)', 'admin_missing': 'Webová administrace nemá administrátora',
        'commands_failed': 'Selhané příkazy ({v})',
        'problems': 'CARACAL Fleet: {n} {word}', 'word1': 'problém', 'word2': 'problémy', 'word5': 'problémů',
        'resolved_title': 'CARACAL Fleet: vyřešeno {n}', 'since': 'od {t}', 'resolved': 'vyřešeno po {d}',
        'test_title': 'CARACAL Fleet: zkušební upozornění', 'test_body': 'Tento kanál funguje.',
        'open': 'Otevřít ve Fleetu',
    },
    'en': {
        'offline': 'Offline', 'local_api': 'Local API not responding', 'player': 'Player not running',
        'temperature': 'High temperature ({v} °C)', 'disk': 'Disk full ({v} %)', 'ram': 'Low memory ({v} %)',
        'cpu': 'High CPU load ({v} %)', 'admin_missing': 'The web administration has no administrator',
        'commands_failed': 'Failed commands ({v})',
        'problems': 'CARACAL Fleet: {n} {word}', 'word1': 'problem', 'word2': 'problems', 'word5': 'problems',
        'resolved_title': 'CARACAL Fleet: {n} resolved', 'since': 'since {t}', 'resolved': 'resolved after {d}',
        'test_title': 'CARACAL Fleet: test alert', 'test_body': 'This channel works.',
        'open': 'Open in Fleet',
    },
}


# ---------------------------------------------------------------- metrics and events

def record_metrics(c, did, status, now=None):
    """Called from the heartbeat: the latest values of the node in its five-minute bucket."""
    now = now or time.time()
    num = lambda k: status.get(k) if isinstance(status.get(k), (int, float)) else None
    c.execute('INSERT OR REPLACE INTO metrics(device_id, ts, cpu, ram, disk, temp) VALUES(?,?,?,?,?,?)',
              (did, int(now // BUCKET * BUCKET), num('cpu'), num('ram'), num('disk'), num('temp')))


@router.get('/api/devices/{did}/metrics')
def device_metrics(did: str, r: Request, hours: int = 24):
    current_user(r)
    hours = max(1, min(hours, METRIC_DAYS * 24))
    now = time.time()
    since = int((now - hours * 3600) // BUCKET * BUCKET)
    with db() as c:
        if not c.execute('SELECT 1 FROM devices WHERE id=?', (did,)).fetchone():
            raise HTTPException(404, 'device_not_found')
        rows = c.execute('SELECT ts, cpu, ram, disk, temp FROM metrics WHERE device_id=? AND ts>=? ORDER BY ts',
                         (did, since)).fetchall()
        created = c.execute('SELECT MIN(ts) m FROM metrics WHERE device_id=?', (did,)).fetchone()['m']
    return {'bucket': BUCKET, 'from': since, 'to': int(now), 'first_sample': created,
            'points': [[x['ts'], x['cpu'], x['ram'], x['disk'], x['temp']] for x in rows]}


@router.get('/api/events')
def list_events(r: Request, device_id: str = '', hours: int = 168, limit: int = 300):
    current_user(r)
    since = time.time() - max(1, min(hours, EVENT_DAYS * 24)) * 3600
    sql = ('SELECT e.*, d.name device_name FROM events e LEFT JOIN devices d ON d.id=e.device_id WHERE e.ts>=?')
    args = [since]
    if device_id:
        sql += ' AND e.device_id=?'
        args.append(device_id)
    sql += ' ORDER BY e.id DESC LIMIT ?'
    args.append(max(1, min(limit, 1000)))
    with db() as c:
        return [dict(x) for x in c.execute(sql, args)]


# ---------------------------------------------------------------- configuration

def config():
    """The alert configuration with defaults filled in."""
    a = cfg().get('alerts') or {}
    out = {**DEFAULTS, **a}
    out['smtp'] = {**DEFAULTS['smtp'], **(a.get('smtp') or {})}
    out['mutes'] = {k: v for k, v in (a.get('mutes') or {}).items() if v and v > time.time()}
    return out


def muted_until(did):
    return config()['mutes'].get(did)


def _public(conf):
    out = json.loads(json.dumps(conf))
    if out['smtp'].get('password'):
        out['smtp']['password'] = REDACTED
    for ch in out['channels']:
        if ch.get('token'):
            ch['token'] = REDACTED
        ch['last'] = _results.get(ch['id'])
    return out


def _text(v, limit=500):
    return str(v if v is not None else '').strip()[:limit]


def _channel(d, old):
    if not isinstance(d, dict) or d.get('type') not in CHANNEL_TYPES:
        raise HTTPException(400, 'invalid_channel')
    ch = {'id': _text(d.get('id'), 40) or format(time.time_ns(), 'x'), 'type': d['type'],
          'name': _text(d.get('name'), 80), 'enabled': d.get('enabled', True) is not False}
    if d['type'] == 'email':
        to = [x.strip() for x in _text(d.get('to'), 1000).replace(';', ',').split(',') if x.strip()]
        if not to or any('@' not in x for x in to):
            raise HTTPException(400, 'invalid_email')
        ch['to'] = ', '.join(to)
    else:
        url = _text(d.get('url'), 2000)
        if urlsplit(url).scheme not in ('http', 'https') or not urlsplit(url).netloc:
            raise HTTPException(400, 'invalid_url')
        ch['url'] = url
        token = _text(d.get('token'), 500)
        ch['token'] = (old or {}).get('token', '') if token == REDACTED else token
    return ch


def _validate(d, old):
    if not isinstance(d, dict):
        raise HTTPException(400, 'invalid_json')
    out = dict(old)
    out['enabled'] = bool(d.get('enabled', old['enabled']))
    try:
        out['delay'] = max(0, min(int(d.get('delay', old['delay'])), 1440))
    except (TypeError, ValueError):
        raise HTTPException(400, 'invalid_value')
    levels = [x for x in d.get('levels', old['levels']) or [] if x in LEVELS]
    out['levels'] = levels or ['critical']
    out['recovery'] = bool(d.get('recovery', old['recovery']))
    out['language'] = d.get('language') if d.get('language') in TEXT else old['language']
    out['public_url'] = _text(d.get('public_url', old['public_url']), 300).rstrip('/')
    olds = {ch['id']: ch for ch in old['channels']}
    if 'channels' in d:
        out['channels'] = [_channel(ch, olds.get(_text(ch.get('id'), 40))) for ch in d['channels'] or []][:20]
    if 'smtp' in d:
        s = d['smtp'] or {}
        smtp = {'host': _text(s.get('host'), 200), 'username': _text(s.get('username'), 200),
                'sender': _text(s.get('sender'), 200), 'security': s.get('security') if s.get('security') in SECURITY else 'starttls'}
        try:
            smtp['port'] = max(1, min(int(s.get('port') or 587), 65535))
        except (TypeError, ValueError):
            raise HTTPException(400, 'invalid_value')
        password = _text(s.get('password'), 500)
        smtp['password'] = old['smtp'].get('password', '') if password == REDACTED else password
        out['smtp'] = smtp
    return out


def _save(conf):
    c = cfg()
    c['alerts'] = {k: v for k, v in conf.items() if k != 'mutes'} | {'mutes': conf.get('mutes', {})}
    save_cfg(c)


@router.get('/api/alerts')
def get_alerts(r: Request):
    current_user(r, 'admin')
    return _public(config())


@router.put('/api/alerts')
async def put_alerts(r: Request):
    u = current_user(r, 'admin')
    try:
        d = await r.json()
    except ValueError:
        raise HTTPException(400, 'invalid_json')
    with _lock:
        conf = _validate(d, config())
        if not conf['public_url']:
            conf['public_url'] = str(r.base_url).rstrip('/')
        _save(conf)
    audit(u, 'alerts.update', '', json.dumps({'enabled': conf['enabled'], 'channels': len(conf['channels'])}))
    return _public(conf)


@router.post('/api/alerts/test')
async def test_alert(r: Request):
    """Sends a test message to one channel as it is in the form (secrets left unchanged come from the saved one)."""
    current_user(r, 'admin')
    try:
        d = await r.json()
    except ValueError:
        raise HTTPException(400, 'invalid_json')
    conf = config()
    if isinstance(d.get('smtp'), dict):
        conf = _validate({'smtp': d['smtp']}, conf)
    old = next((ch for ch in conf['channels'] if ch['id'] == _text((d.get('channel') or {}).get('id'), 40)), None)
    ch = _channel(d.get('channel'), old)
    tx = TEXT[conf['language']]
    try:
        _deliver(ch, conf, tx['test_title'], [tx['test_body']], {'event': 'test'}, 'info')
    except Exception as e:     # any delivery error is shown to the administrator as it is
        return {'ok': False, 'error': _text(e, 400)}
    return {'ok': True}


@router.post('/api/alerts/mute')
async def mute_devices(r: Request):
    """Mute alerts of devices for some minutes (0 = unmute); events are still recorded."""
    u = current_user(r, 'manage')
    try:
        d = await r.json()
    except ValueError:
        raise HTTPException(400, 'invalid_json')
    ids = [_text(x, 120) for x in d.get('devices') or [] if _text(x, 120)]
    try:
        minutes = max(0, min(int(d.get('minutes') or 0), 60 * 24 * 30))
    except (TypeError, ValueError):
        raise HTTPException(400, 'invalid_value')
    if not ids:
        raise HTTPException(400, 'no_targets')
    with _lock:
        conf = config()
        for did in ids:
            if minutes:
                conf['mutes'][did] = time.time() + minutes * 60
            else:
                conf['mutes'].pop(did, None)
        _save(conf)
    audit(u, 'alerts.mute' if minutes else 'alerts.unmute', ', '.join(ids)[:300], json.dumps({'minutes': minutes}))
    return {'ok': True, 'until': time.time() + minutes * 60 if minutes else None}


# ---------------------------------------------------------------- the check

def _issue_text(tx, code, detail):
    s = tx.get(code, code)
    return s.replace('{v}', _text(detail, 40)) if '{v}' in s else s


def _duration(sec):
    sec = int(sec)
    if sec < 3600:
        return f'{max(1, sec // 60)} min'
    if sec < 86400:
        return f'{sec // 3600} h {sec % 3600 // 60} min'
    return f'{sec // 86400} d {sec % 86400 // 3600} h'


def _plural(tx, n):
    return tx['word1'] if n == 1 else tx['word2'] if 2 <= n <= 4 else tx['word5']


def check(now=None, send=None):
    """One pass over all devices: events for problems that started or ended, alerts for the due ones.

    Returns the messages that were sent (for tests); `send(channel, conf, title, lines, payload, level)` replaces
    the real delivery."""
    now = now or time.time()
    conf = config()
    started, ended = [], []
    with db() as c:
        devices = {row['id']: build(row) for row in c.execute('SELECT * FROM devices')}
        known = {(x['device_id'], x['code']): dict(x) for x in c.execute('SELECT * FROM issues')}
        current = {}
        for did, d in devices.items():
            for a in d['attention']:
                if a['level'] in LEVELS:
                    current[(did, a['code'])] = a
        for (did, code), a in current.items():
            issue = known.get((did, code))
            detail = _text(a.get('detail'), 200)
            if not issue:
                c.execute('INSERT INTO issues(device_id, code, level, detail, since, alerted) VALUES(?,?,?,?,?,0)',
                          (did, code, a['level'], detail, now))
                c.execute('INSERT INTO events(device_id, ts, kind, code, level, detail) VALUES(?,?,?,?,?,?)',
                          (did, now, 'start', code, a['level'], detail))
                known[(did, code)] = {'device_id': did, 'code': code, 'level': a['level'], 'detail': detail,
                                      'since': now, 'alerted': 0}
            elif issue['level'] != a['level'] or issue['detail'] != detail:
                # a warning that became critical is alerted again if critical problems are alerted
                realert = issue['level'] == 'warning' and a['level'] == 'critical' and 'warning' not in conf['levels']
                c.execute('UPDATE issues SET level=?, detail=?, alerted=? WHERE device_id=? AND code=?',
                          (a['level'], detail, 0 if realert else issue['alerted'], did, code))
                issue.update(level=a['level'], detail=detail, alerted=0 if realert else issue['alerted'])
        for (did, code), issue in list(known.items()):
            if (did, code) in current:
                continue
            c.execute('DELETE FROM issues WHERE device_id=? AND code=?', (did, code))
            del known[(did, code)]
            if did not in devices:
                continue         # the device was removed from Fleet
            c.execute('INSERT INTO events(device_id, ts, kind, code, level, detail) VALUES(?,?,?,?,?,?)',
                      (did, now, 'end', code, issue['level'], issue['detail']))
            if issue['alerted'] and conf['recovery']:
                ended.append({**issue, 'ended': now})
        channels = [ch for ch in conf['channels'] if ch.get('enabled', True)]
        if conf['enabled'] and channels:
            for (did, code), issue in known.items():
                if issue['alerted'] or issue['level'] not in conf['levels'] or did in conf['mutes']:
                    continue
                if now - issue['since'] >= conf['delay'] * 60:
                    started.append(issue)
                    c.execute('UPDATE issues SET alerted=1 WHERE device_id=? AND code=?', (did, code))
        if now - _last_cleanup[0] > 3600:
            _last_cleanup[0] = now
            c.execute('DELETE FROM metrics WHERE ts<?', (now - METRIC_DAYS * 86400,))
            c.execute('DELETE FROM events WHERE ts<?', (now - EVENT_DAYS * 86400,))
    if not conf['enabled'] or not channels or not (started or ended):
        return []
    return _notify(conf, channels, devices, started, ended, send or _deliver)


_last_cleanup = [0]


def _notify(conf, channels, devices, started, ended, send):
    tx = TEXT[conf['language']]
    link = lambda did: f"{conf['public_url']}/#/device/{did}" if conf['public_url'] else ''
    where = lambda d: ' / '.join(x for x in (d['location'], d['group']) if x)
    clock = lambda ts: time.strftime('%H:%M', time.localtime(ts))
    messages = []

    def item(issue, resolved):
        d = devices.get(issue['device_id']) or {'name': issue['device_id'], 'location': '', 'group': ''}
        return {'device_id': issue['device_id'], 'device': d['name'], 'where': where(d), 'code': issue['code'],
                'level': issue['level'], 'text': _issue_text(tx, issue['code'], issue['detail']),
                'since': issue['since'], 'ended': issue.get('ended'), 'url': link(issue['device_id']),
                'when': tx['resolved'].replace('{d}', _duration(issue['ended'] - issue['since'])) if resolved
                else tx['since'].replace('{t}', clock(issue['since']))}

    batches = []
    if started:
        items = [item(x, False) for x in started]
        title = tx['problems'].replace('{n}', str(len(items))).replace('{word}', _plural(tx, len(items)))
        level = 'critical' if any(x['level'] == 'critical' for x in items) else 'warning'
        batches.append((title, items, 'alert', level))
    if ended:
        items = [item(x, True) for x in ended]
        batches.append((tx['resolved_title'].replace('{n}', str(len(items))), items, 'resolved', 'ok'))
    for title, items, event, level in batches:
        mark = {'critical': '🔴', 'warning': '🟠', 'ok': '✅'}
        lines = [f"{mark['ok' if event == 'resolved' else x['level']]} {x['device']}"
                 f"{' (' + x['where'] + ')' if x['where'] else ''}: {x['text']}, {x['when']}" for x in items]
        payload = {'event': event, 'title': title, 'items': items}
        for ch in channels:
            try:
                send(ch, conf, title, lines, payload, level)
                _results[ch['id']] = {'ok': True, 'error': '', 'ts': time.time()}
            except Exception as e:     # one broken channel must not stop the others
                _results[ch['id']] = {'ok': False, 'error': _text(e, 300), 'ts': time.time()}
        messages.append({'title': title, 'lines': lines, 'event': event})
    return messages


# ---------------------------------------------------------------- delivery

def _deliver(ch, conf, title, lines, payload, level):
    body = '\n'.join(lines)
    items = payload.get('items') or []
    url_of = lambda x: f" {x['url']}" if x.get('url') else ''
    headers = {'Authorization': 'Bearer ' + ch['token']} if ch.get('token') else {}
    t = ch['type']
    if t == 'email':
        s = conf['smtp']
        if not s['host'] or not (s['sender'] or s['username']):
            raise ValueError('smtp_not_set')
        msg = EmailMessage()
        msg['Subject'], msg['From'], msg['To'] = title, s['sender'] or s['username'], ch['to']
        msg.set_content(body + ''.join(f"\n\n{x['device']}: {x['url']}" for x in items if x.get('url')))
        ctx = ssl.create_default_context()
        cls = smtplib.SMTP_SSL if s['security'] == 'ssl' else smtplib.SMTP
        kwargs = {'context': ctx} if s['security'] == 'ssl' else {}
        with cls(s['host'], s['port'], timeout=15, **kwargs) as smtp:
            if s['security'] == 'starttls':
                smtp.starttls(context=ctx)
            if s['username']:
                smtp.login(s['username'], s['password'])
            smtp.send_message(msg)
        return
    if t == 'slack':
        data = {'text': f'*{title}*\n' + '\n'.join(f'{line}{url_of(x)}' for line, x in zip(lines, items))
                if items else f'*{title}*\n{body}'}
    elif t == 'discord':
        data = {'content': (f'**{title}**\n{body}')[:1900]}
    elif t == 'teams':
        # Workflows ("When a Teams webhook request is received") expect an Adaptive Card
        card = {'type': 'AdaptiveCard', 'version': '1.4', '$schema': 'http://adaptivecards.io/schemas/adaptive-card.json',
                'body': [{'type': 'TextBlock', 'text': title, 'weight': 'Bolder', 'size': 'Medium', 'wrap': True}]
                + [{'type': 'TextBlock', 'text': line, 'wrap': True, 'spacing': 'Small'} for line in lines]}
        data = {'type': 'message', 'attachments': [{'contentType': 'application/vnd.microsoft.card.adaptive',
                                                     'content': card}]}
    elif t == 'ntfy':
        parts = urlsplit(ch['url'])
        topic = parts.path.strip('/')
        data = {'topic': topic, 'title': title, 'message': body, 'priority': 5 if level == 'critical' else 3,
                'tags': ['rotating_light' if level == 'critical' else 'warning' if level == 'warning' else 'white_check_mark']}
        if items and items[0].get('url'):
            data['click'] = items[0]['url']
        r = requests.post(f'{parts.scheme}://{parts.netloc}', json=data, headers=headers, timeout=10)
        r.raise_for_status()
        return
    else:
        data = {**payload, 'title': title, 'text': body}
    r = requests.post(ch['url'], json=data, headers=headers, timeout=10)
    r.raise_for_status()


# ---------------------------------------------------------------- background loop

def _loop():
    while True:
        time.sleep(CHECK_EVERY)
        if time.time() - _started < START_GRACE:
            continue
        try:
            check()
        except Exception as e:     # the loop keeps running; the next pass tries again
            print('monitor check failed:', e, flush=True)


def start():
    threading.Thread(target=_loop, daemon=True, name='fleet-monitor').start()
