"""Turns raw heartbeat data into the device model shown in the UI, including attention reasons."""
import json
import time

from .core import AGENT_VERSION, ONLINE_TIMEOUT, version_tuple

MEDIA_KINDS = ('image', 'video')
COLLECTION_KIND = 'grafana-tag'   # Grafana collections kept as playlist assets on CARACAL nodes


def normalize_asset(a):
    a = dict(a or {})
    a['kind'] = str(a.get('kind') or a.get('type') or a.get('mimetype') or 'web').lower()
    if '/' in a['kind']:  # mimetype such as image/png
        a['kind'] = a['kind'].split('/', 1)[0]
    a['source'] = a.get('source') or a.get('url') or a.get('uri') or ''
    a['name'] = a.get('name') or a['source'] or f"#{a.get('id')}"
    a['enabled'] = a.get('enabled', a.get('is_enabled', True)) not in (False, 0, '0', 'false')
    if a['kind'] == COLLECTION_KIND:
        a.update(grafana_config(a['source']))
    return a


def grafana_config(source):
    """A CARACAL Grafana collection stores {"grafana_url", "tag", "kiosk"} as JSON in 'source'."""
    try:
        cfg = json.loads(source or '{}')
    except (TypeError, ValueError):
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    return {'grafana_url': str(cfg.get('grafana_url') or ''), 'tag': str(cfg.get('tag') or ''),
            'kiosk': bool(cfg.get('kiosk', True))}


def collections_of(status):
    """Grafana collections are the playlist assets of kind grafana-tag ('profiles' are login profiles)."""
    return [normalize_asset(a) for a in status.get('assets') or []
            if str(a.get('kind') or a.get('type')) == COLLECTION_KIND]


def profiles_of(status):
    """Login profiles of web pages on the node (agents from 4.6 report them; never with credentials)."""
    out = []
    for p in status.get('profiles') or []:
        if isinstance(p, dict) and p.get('id') is not None:
            out.append({'id': p['id'], **{k: p.get(k) or '' for k in ('name', 'login_url', 'target_url',
                                                                      'user_selector', 'pass_selector',
                                                                      'submit_selector')},
                        'auth_type': 'http' if p.get('auth_type') == 'http' else 'form'})
    return out


def notifications_of(status):
    """On-screen notifications of the node (agents from 4.7 on CARACAL with notifications); None = not reported."""
    n = status.get('notifications')
    if not isinstance(n, dict):
        return None
    return {'settings': n.get('settings') if isinstance(n.get('settings'), dict) else {},
            'waiting': n.get('waiting') or 0, 'current': n.get('current'), 'tokens': n.get('tokens') or 0,
            'sounds': n.get('sounds') if isinstance(n.get('sounds'), dict) else {},
            'watchers': [w for w in n.get('watchers') or [] if isinstance(w, dict)],
            # agents from 4.9: the queue, the size of the history and the audit log, the node's own tokens
            'queue': [x for x in n.get('queue') or [] if isinstance(x, dict)] if 'queue' in n else None,
            'history_count': n.get('history_count'), 'audit_count': n.get('audit_count'),
            'token_list': [x for x in n.get('token_list') or [] if isinstance(x, dict)] if 'token_list' in n else None}


def admin_of(status):
    """The node's web administrator: {'configured', 'username'}; None when the node does not report it."""
    a = status.get('admin')
    return {'configured': bool(a.get('configured')), 'username': str(a.get('username') or '')} \
        if isinstance(a, dict) else None


def overlay_of(status):
    """The countdown bar on the TV: {'enabled', 'size'}; None when the node does not report it."""
    o = status.get('overlay')
    return {'enabled': bool(o.get('enabled', True)), 'size': int(o.get('size') or 16)} if isinstance(o, dict) else None


def player_of(status):
    """Agent 4 sends a nested player object, agent 3 sent flat keys."""
    p = status.get('player')
    if isinstance(p, dict):
        return p
    return {k: status.get(k) for k in ('current_id', 'current_name', 'remaining', 'duration', 'frozen',
                                       'player_online')}


def attention(d, failed_commands=0):
    """List of {code, level, detail}; level is critical, warning or info."""
    out = []
    add = lambda code, level, detail='': out.append({'code': code, 'level': level, 'detail': detail})
    if not d['online']:
        add('offline', 'critical', d['last_seen'])
        return out
    if d.get('maintenance'):
        # CARACAL is being updated: its API and player are down on purpose
        add('maintenance', 'info', d['maintenance'])
        return out
    if d.get('api_ok') is False:
        add('local_api', 'critical', d.get('api_error') or '')
    elif d.get('player_online') is False:
        add('player', 'critical', d.get('player_error') or '')
    temp = d.get('temp')
    if temp is not None:
        if temp >= 80:
            add('temperature', 'critical', temp)
        elif temp >= 70:
            add('temperature', 'warning', temp)
    disk = d.get('disk')
    if disk is not None:
        if disk >= 95:
            add('disk', 'critical', disk)
        elif disk >= 85:
            add('disk', 'warning', disk)
    if (d.get('ram') or 0) >= 90:
        add('ram', 'warning', d['ram'])
    if (d.get('cpu') or 0) >= 95:
        add('cpu', 'warning', d['cpu'])
    if d.get('admin') and not d['admin']['configured']:
        # anyone who opens the node's web administration first could create the administrator
        add('admin_missing', 'warning')
    if failed_commands:
        add('commands_failed', 'warning', failed_commands)
    if version_tuple(d.get('version')) < version_tuple(AGENT_VERSION):
        add('agent_outdated', 'info', d.get('version') or '?')
    return out


def build(row, failed_commands=0, full=False, latest_caracal=None):
    status = json.loads(row['status_json'] or '{}')
    player = player_of(status)
    now = time.time()
    online = now - (row['last_seen'] or 0) < ONLINE_TIMEOUT
    assets = [normalize_asset(a) for a in status.get('assets') or []]
    collections = collections_of(status)
    profiles = profiles_of(status)
    notifications = notifications_of(status)
    d = {
        'id': row['id'], 'name': row['name'] or row['id'], 'ip': status.get('ip') or row['ip'] or '',
        'version': row['version'] or '', 'last_seen': row['last_seen'], 'online': online,
        'group': row['device_group'] or '', 'location': row['location'] or '', 'notes': row['notes'] or '',
        'hostname': status.get('hostname', ''), 'model': status.get('model', ''),
        'cpu': status.get('cpu'), 'ram': status.get('ram'), 'disk': status.get('disk'), 'temp': status.get('temp'),
        'uptime': status.get('uptime'), 'load': status.get('load'),
        'api_ok': status.get('api_ok'), 'api_error': status.get('api_error', ''),
        'player_online': player.get('player_online'), 'player_error': player.get('error', ''),
        'current_id': player.get('current_id'), 'current_name': player.get('current_name') or '',
        'current_kind': player.get('current_kind') or '',
        'remaining': player.get('remaining'), 'duration': player.get('duration'),
        'frozen': bool(player.get('frozen')), 'frozen_until': status.get('frozen_until'),
        'playlist_count': len(assets), 'collection_count': len(collections), 'profile_count': len(profiles),
        'notify_waiting': notifications['waiting'] if notifications else None,
        'watcher_count': len(notifications['watchers']) if notifications else 0,
        # the node reports the look of its notifications (and the agent passes it on): the look editor works
        'notify_style': bool(notifications and isinstance(notifications['settings'].get('style'), dict)),
        'status_age': now - (row['last_seen'] or now),
        'capabilities': status.get('capabilities') or {},
        'caracal_version': status.get('caracal_version') or '', 'maintenance': status.get('maintenance') or '',
        # agents before 4.5 do not report the runtime; they only ran on classic installations
        'runtime': status['runtime'] if 'runtime' in status else ('host' if status.get('caracal_version') else ''),
        'caracal_image': status.get('caracal_image') or '',
        # where the node downloads CARACAL and system packages ('' = agent too old to report it)
        'download_source': status.get('download_source') or '', 'arch': status.get('arch') or '',
        'supports_enabled': any('enabled' in a or 'is_enabled' in a for a in status.get('assets') or []),
        'admin': admin_of(status), 'overlay': overlay_of(status),
    }
    d['current_asset_id'] = None
    if d['current_id'] is not None:
        match = next((a for a in assets if str(a.get('id')) == str(d['current_id'])), None)
        if not match and str(d['current_id']).isdigit() and int(d['current_id']) >= 100000:
            # dashboards of a Grafana collection play as <collection id> * 100000 + index
            parent = int(d['current_id']) // 100000
            match = next((a for a in assets if str(a.get('id')) == str(parent) and a['kind'] == COLLECTION_KIND),
                         None)
        if match:
            d['current_asset_id'] = match.get('id')
            d['current_name'] = d['current_name'] or match['name']
            d['current_kind'] = d['current_kind'] or match['kind']
    d['attention'] = attention(d, failed_commands)
    if isinstance(latest_caracal, dict):   # latest version per runtime: Docker image / classic release
        latest_caracal = latest_caracal.get(d['runtime'])
    if latest_caracal and d['online'] and not d['maintenance'] and 'caracal_version' in status \
            and d['caracal_version'] != latest_caracal:
        d['attention'].append({'code': 'caracal_outdated', 'level': 'info', 'detail': d['caracal_version'] or '?'})
    d['needs_attention'] = any(x['level'] in ('critical', 'warning') for x in d['attention'])
    if full:
        d['assets'] = assets
        d['collections'] = collections
        d['profiles'] = profiles
        d['notifications'] = notifications
    return d
