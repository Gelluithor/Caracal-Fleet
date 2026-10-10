#!/usr/bin/env python3
"""CARACAL Fleet Agent.

Runs on a CARACAL node, reports health and playback state to the Fleet hub and executes queued commands.
All playback and content operations go exclusively through the node's local CARACAL API
(default http://127.0.0.1:8080/api/fleet/v1). The agent never writes player control files and never
reinstalls or modifies the CARACAL installation. Compatible with Python 3.9+.

Usage:
  agent.py run                                    run the agent loop (systemd)
  agent.py enroll --hub URL --token T [--name N] [--reenroll] [--local-api URL]
  agent.py check                                  test local API and hub connectivity
"""
import argparse
import base64
import email.utils
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path
from urllib.parse import quote, urlparse

import psutil
import requests

VERSION = '4.10.1'
CONFIG = Path(os.getenv('CARACAL_AGENT_CONFIG', '/etc/caracal-agent.json'))
KEY_FILE = Path(os.getenv('CARACAL_FLEET_KEY_FILE', '/etc/caracal-fleet-key'))
STATE = Path(os.getenv('CARACAL_AGENT_STATE', '/var/lib/caracal-agent/state.json'))
AGENT_FILE = Path(__file__).resolve()
CARACAL_DIR = Path(os.getenv('CARACAL_DIR', '/opt/caracal'))
# start order used by CARACAL's install.sh: API, X display, then everything that draws on the display
CARACAL_SERVICES = ('caracal.service', 'caracal-display.service', 'caracal-overlay.service', 'caracal-player.service')
X_SOCKET = Path('/tmp/.X11-unix/X0')
# CARACAL on Docker (installed by install-node.sh): compose project in NODE_DIR, data in DATA_DIR
NODE_DIR = Path(os.getenv('CARACAL_NODE_DIR', '/opt/caracal-node'))
DATA_DIR = Path(os.getenv('CARACAL_DATA_DIR', '/var/lib/caracal'))
COMPOSE_SERVICES = {'caracal.service': 'app', 'caracal-player.service': 'player', 'caracal-overlay.service': 'overlay'}
INSTALL_TIMEOUT = 3000   # apt + pip on a Raspberry Pi can take a while
# Download source "fleet": apt and CARACAL images come through the hub (no internet access needed). The apt sources
# of these repositories point to <hub>/apt/<host>/...; keep the list in sync with DEFAULT_APT_HOSTS of the hub.
APT_DIR = Path(os.getenv('CARACAL_APT_DIR', '/etc/apt'))
APT_HOSTS = ('deb.debian.org', 'security.debian.org', 'ftp.debian.org', 'archive.raspberrypi.com',
             'archive.raspberrypi.org', 'raspbian.raspberrypi.com', 'raspbian.raspberrypi.org', 'download.docker.com',
             'dietpi.com')

DEFAULTS = {
    'local_api': 'http://127.0.0.1:8080',
    'player_service': 'caracal-player.service',
    'heartbeat_interval': 10,
    'poll_interval': 3,
}

# Local CARACAL Fleet API contract (see docs/LOCAL-API.md). Can be overridden via "endpoints" in the config.
ENDPOINTS = {
    'snapshot': ('GET', '/api/fleet/v1/snapshot'),
    'control': ('POST', '/api/fleet/v1/control'),
    'add_web': ('POST', '/api/fleet/v1/assets/web'),
    'upload': ('POST', '/api/fleet/v1/assets/upload'),
    'update_asset': ('PUT', '/api/fleet/v1/assets/{id}'),
    'delete_asset': ('DELETE', '/api/fleet/v1/assets/{id}'),
    'asset_file': ('GET', '/api/fleet/v1/assets/{id}/file'),
    'reorder': ('PUT', '/api/fleet/v1/playlist/reorder'),
    'add_grafana_tag': ('POST', '/api/fleet/v1/assets/grafana-tag'),
    'add_profile': ('POST', '/api/fleet/v1/profiles'),
    'update_profile': ('PUT', '/api/fleet/v1/profiles/{id}'),
    'delete_profile': ('DELETE', '/api/fleet/v1/profiles/{id}'),
    # on-screen notifications (CARACAL 2026.10.08 and newer)
    'notify': ('POST', '/api/fleet/v1/notify'),
    'notify_settings': ('PUT', '/api/fleet/v1/notify/settings'),
    'notify_clear': ('POST', '/api/fleet/v1/notify/clear'),
    'add_watcher': ('POST', '/api/fleet/v1/notify/watchers'),
    'update_watcher': ('PUT', '/api/fleet/v1/notify/watchers/{id}'),
    'delete_watcher': ('DELETE', '/api/fleet/v1/notify/watchers/{id}'),
    'check_watcher': ('POST', '/api/fleet/v1/notify/watchers/{id}/check'),
    'notify_sound': ('POST', '/api/fleet/v1/notify/sounds/{level}'),
    'notify_sound_delete': ('DELETE', '/api/fleet/v1/notify/sounds/{level}'),
    # what else the node's web administration offers (CARACAL 2026.10.10 and newer)
    'admin': ('POST', '/api/fleet/v1/admin'),
    'overlay': ('PUT', '/api/fleet/v1/player/overlay'),
    'notify_skip': ('POST', '/api/fleet/v1/notify/skip'),
    'notify_remove': ('DELETE', '/api/fleet/v1/notify/queue/{id}'),
    'notify_log': ('GET', '/api/fleet/v1/notify/log'),
    'notify_history_clear': ('POST', '/api/fleet/v1/notify/history/clear'),
    'notify_audit_clear': ('POST', '/api/fleet/v1/notify/audit/clear'),
    'notify_token_update': ('PUT', '/api/fleet/v1/notify/tokens/{id}'),
    'notify_token_delete': ('DELETE', '/api/fleet/v1/notify/tokens/{id}'),
    'preview_watcher': ('POST', '/api/fleet/v1/notify/watchers/preview'),
    'grafana_discover': ('POST', '/api/fleet/v1/grafana/discover'),
}
# the node's own first-run setup, for CARACAL versions without the Fleet admin endpoint
SETUP_STATUS, SETUP = '/api/setup-status', '/api/setup'
GRAFANA_FIELDS = ('name', 'grafana_url', 'tag', 'kiosk', 'duration', 'scale')
# Login profiles of web pages: the credentials are stored encrypted on the node and only travel to it.
# auth_type: 'form' (log-in form on the page) or 'http' (HTTP Basic/Digest, the browser's pop-up)
PROFILE_FIELDS = ('name', 'login_url', 'target_url', 'username', 'password', 'user_selector', 'pass_selector',
                  'submit_selector', 'auth_type')
PROFILE_PUBLIC = ('id', 'name', 'login_url', 'target_url', 'user_selector', 'pass_selector', 'submit_selector',
                  'auth_type')
# Notification settings and watchers of the node. Watcher credentials (username, secret, client_secret,
# refresh_token) only travel to the node like login credentials; the node never reports them back.
NOTIFY_SETTINGS = ('enabled', 'position', 'duration', 'max_queue', 'scale', 'sound', 'volume', 'sound_device',
                   'history_max', 'history_days', 'style')   # style: the look of the notifications (CARACAL 2026.10.10.2+)
WATCHER_FIELDS = ('name', 'url', 'auth_type', 'auth_header', 'username', 'secret', 'client_secret', 'refresh_token',
                  'list_path', 'id_field', 'title_template', 'message_template', 'level', 'level_field', 'interval',
                  'verify_tls', 'enabled', 'oauth_token_url', 'oauth_grant', 'oauth_client_id', 'oauth_scope',
                  'oauth_extra', 'oauth_client_auth')
WATCHER_PUBLIC = ('id', 'name', 'url', 'auth_type', 'auth_header', 'list_path', 'id_field', 'title_template',
                  'message_template', 'level', 'level_field', 'interval', 'verify_tls', 'enabled', 'initialized',
                  'last_check', 'last_error', 'last_count', 'last_new', 'oauth_token_url', 'oauth_grant',
                  'oauth_client_id', 'oauth_scope', 'oauth_extra', 'oauth_client_auth', 'has_credentials')
QUEUE_PUBLIC = ('id', 'source', 'title', 'message', 'level', 'priority', 'created')
TOKEN_PUBLIC = ('id', 'name', 'prefix', 'rate_per_min', 'enabled', 'created', 'last_used')   # never the token
MEDIA_KINDS = ('image', 'video')
COLLECTION_KIND = 'grafana-tag'   # Grafana collections are playlist assets of this kind on CARACAL nodes
PLAYER_STALE = 15                 # seconds without a player heartbeat before the player counts as down
FREEZE_GRACE = 15                 # seconds after a freeze before the live player state may cancel it
# Keys never sent back to the node when re-creating an item.
VOLATILE_KEYS = ('id', 'type', 'source_asset_id', 'file_id', 'sha256', 'filename', 'size', 'position', 'order',
                 'created', 'updated', 'path', 'file', 'mimetype', 'urls_text')


class LocalApiError(RuntimeError):
    pass


def log(*a):
    print(time.strftime('%Y-%m-%d %H:%M:%S'), *a, flush=True)


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def write_json(path, data, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, indent=2))
    os.chmod(tmp, mode)
    tmp.replace(path)


def item_id(resp_json):
    """Extract the id of a created item from various response shapes."""
    if isinstance(resp_json, dict):
        for key in ('id', 'asset_id', 'collection_id'):
            if resp_json.get(key) is not None:
                return resp_json[key]
        for key in ('asset', 'item', 'collection', 'data'):
            if isinstance(resp_json.get(key), dict) and resp_json[key].get('id') is not None:
                return resp_json[key]['id']
    return None


class Agent:
    def __init__(self, conf):
        self.conf = {**DEFAULTS, **conf}
        self.endpoints = {**ENDPOINTS, **{k: tuple(v) for k, v in (self.conf.get('endpoints') or {}).items()}}
        self.hub_url = self.conf['hub'].rstrip('/')
        self.base = f"{self.hub_url}/api/device/{self.conf['device_id']}"
        self.session = requests.Session()
        self.session.headers['X-Device-Token'] = self.conf['device_token']
        self.local_session = requests.Session()
        self.local_session.headers['X-Fleet-Key'] = self.conf['device_token']
        self.state = read_json(STATE, {})
        self.after = None          # action executed after the result was reported (reboot, self-update)
        self.last_snapshot = {}
        self.last_error = ''
        self._caps, self._caps_at = None, 0
        self.maintenance = ''      # e.g. 'caracal_update' while CARACAL is being updated
        self.reboot_pending = False

    # ------------------------------------------------------------ transport

    def hub(self, method, path, **kw):
        kw.setdefault('timeout', 20)
        r = self.session.request(method, self.base + path, **kw)
        r.raise_for_status()
        return r

    def local(self, name, ids=None, timeout=30, **kw):
        method, path = self.endpoints[name]
        url = self.conf['local_api'].rstrip('/') + path.format(**{k: quote(str(v), safe='') for k, v in
                                                                  (ids or {}).items()})
        try:
            r = self.local_session.request(method, url, timeout=timeout, **kw)
        except requests.RequestException as e:
            raise LocalApiError(f'Local CARACAL API unreachable: {e}')
        if r.status_code == 404 and not ids:
            raise LocalApiError(f'Local CARACAL API does not support "{name}" ({method} {path})')
        if r.status_code in (401, 403):
            raise LocalApiError(f'Local CARACAL API rejected the fleet key (HTTP {r.status_code})')
        if r.status_code >= 400:
            raise LocalApiError(f'Local CARACAL API {method} {path}: HTTP {r.status_code} {r.text[:300]}')
        return r

    @staticmethod
    def body(r):
        try:
            return r.json()
        except ValueError:
            return {'text': r.text[:500]}

    # ------------------------------------------------------------ state

    def snapshot(self):
        snap = self.body(self.local('snapshot', timeout=10))
        if not isinstance(snap, dict):
            raise LocalApiError('Invalid snapshot response')
        self.last_snapshot = snap
        return snap

    @staticmethod
    def assets_of(snap):
        return snap.get('assets') or snap.get('playlist') or []

    @staticmethod
    def profiles_of(snap):
        # never more than the node shows (no credentials), also if a node version sent other columns
        return [{k: p.get(k) for k in PROFILE_PUBLIC if k in p} for p in snap.get('profiles') or []
                if isinstance(p, dict)]

    @staticmethod
    def notifications_of(snap):
        # None for nodes without notifications, so the hub can tell them apart from an empty configuration
        n = snap.get('notifications')
        if not isinstance(n, dict):
            return None
        settings = n.get('settings') if isinstance(n.get('settings'), dict) else {}
        return {'settings': {k: settings[k] for k in NOTIFY_SETTINGS if k in settings},
                'waiting': n.get('waiting'), 'current': n.get('current'), 'tokens': n.get('tokens'),
                # custom MP3 per level: only its name, size and checksum
                'sounds': {level: {k: v.get(k) for k in ('name', 'size', 'sha256', 'uploaded')}
                           for level, v in (n.get('sounds') or {}).items() if isinstance(v, dict)},
                'watchers': [{k: w.get(k) for k in WATCHER_PUBLIC if k in w} for w in n.get('watchers') or []
                             if isinstance(w, dict)],
                **({'queue': [{k: x.get(k) for k in QUEUE_PUBLIC} for x in n['queue'] if isinstance(x, dict)][:30]}
                   if isinstance(n.get('queue'), list) else {}),
                **({'token_list': [{k: x.get(k) for k in TOKEN_PUBLIC} for x in n['token_list'] if isinstance(x, dict)]}
                   if isinstance(n.get('token_list'), list) else {}),
                **{k: n[k] for k in ('history_count', 'audit_count') if isinstance(n.get(k), int)}}

    @staticmethod
    def admin_of(snap):
        # whether the node's web administrator exists and its name; never more
        a = snap.get('admin')
        return {'configured': bool(a.get('configured')), 'username': str(a.get('username') or '')[:64]} \
            if isinstance(a, dict) else None

    @classmethod
    def collections_of(cls, snap):
        # Grafana collections are playlist assets of kind grafana-tag ('profiles' are login profiles).
        return [a for a in cls.assets_of(snap) if str(a.get('kind') or a.get('type')) == COLLECTION_KIND]

    def player_state(self, snap):
        """Is the player running?

        The node's state file always says player_online=true, so its 'updated' timestamp is checked instead.
        Newer players send their heartbeat to /api/v2/player/heartbeat, which does not touch that file, so a
        stale timestamp alone is no proof of a dead player: the systemd state of the player service decides.
        """
        player = dict(snap.get('player') or {})
        fresh = None
        if player.get('updated'):
            try:
                fresh = time.time() - float(player['updated']) < PLAYER_STALE
            except (TypeError, ValueError):
                fresh = None
        if fresh:
            player['player_online'] = True
        else:
            active = self.service_active(self.conf['player_service'])
            if active is not None:
                player['player_online'] = active
            elif fresh is False:
                player['player_online'] = False
        player['state_stale'] = fresh is False
        return player

    def service_active(self, name):
        """True/False from Docker (CARACAL containers) or systemd, None when neither is available."""
        if is_docker() and name in COMPOSE_SERVICES:
            running = self.running_services()
            return None if running is None else COMPOSE_SERVICES[name] in running
        return systemd_active(name)

    def compose(self, *args, timeout=600):
        """Run "docker compose" for the CARACAL project; returns (exit code, output)."""
        try:
            r = subprocess.run(['docker', 'compose', '--project-directory', str(NODE_DIR), *args], timeout=timeout,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        except (OSError, subprocess.SubprocessError) as e:
            return 1, str(e)
        return r.returncode, r.stdout

    def running_services(self):
        code, out = self.compose('ps', '--services', '--status', 'running', timeout=30)
        return set(out.split()) if code == 0 else None

    def capabilities(self):
        """Which optional local endpoints exist, read from the node's FastAPI OpenAPI schema.
        Returns {} when unknown (schema not available); callers then simply try the endpoint."""
        if self._caps is not None and time.time() - self._caps_at < 600:
            return self._caps
        caps = {}
        try:
            r = self.local_session.get(self.conf['local_api'].rstrip('/') + '/openapi.json', timeout=10)
            if r.ok:
                norm = lambda path: re.sub(r'\{[^}]+\}', '{}', path)
                paths = {norm(k): {m.lower() for m in v} for k, v in (r.json().get('paths') or {}).items()}
                caps = {name: method.lower() in paths.get(norm(path), set())
                        for name, (method, path) in self.endpoints.items()}
        except (requests.RequestException, ValueError, AttributeError):
            caps = {}
        self._caps, self._caps_at = caps, time.time()
        return caps

    def save_state(self):
        try:
            write_json(STATE, self.state, 0o600)
        except OSError as e:
            log('state save failed:', e)

    def local_ip(self):
        try:
            host = urlparse(self.hub_url).hostname
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((socket.gethostbyname(host), 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except OSError:
            return ''

    @staticmethod
    def temperature():
        try:
            return round(float(Path('/sys/class/thermal/thermal_zone0/temp').read_text()) / 1000, 1)
        except (OSError, ValueError):
            return None

    @staticmethod
    def model():
        try:
            return Path('/proc/device-tree/model').read_text().strip('\x00\n ')
        except OSError:
            return ''

    def check_node_requests(self, snap):
        """CARACAL in Docker cannot reboot the host: its admin UI leaves a request counter in the snapshot."""
        if 'requests' not in snap:
            return
        wanted = int((snap.get('requests') or {}).get('reboot') or 0)
        seen = self.state.get('reboot_request_seen')
        if seen is not None and wanted > seen:
            log('reboot requested in the CARACAL admin UI')
            self.reboot_pending = True
        if seen != wanted:
            self.state['reboot_request_seen'] = wanted
            self.save_state()

    def heartbeat(self):
        snap, api_ok, api_error = {}, True, ''
        try:
            snap = self.snapshot()
        except Exception as e:  # noqa: BLE001
            api_ok, api_error = False, str(e)[:500]
        self.check_node_requests(snap)
        player = self.player_state(snap)
        if player.get('state_stale') or not player.get('updated'):
            # The node reports a stale (v1) state that does not reflect Fleet freezes: use what the agent knows.
            player['frozen'] = bool(player.get('frozen')) or bool(self.state.get('frozen'))
        else:
            # live v2 state: a frozen collection counts as frozen as well
            player['frozen'] = bool(player.get('frozen')) or bool(player.get('collection_frozen'))
        # the player picks up commands about once per second, so give it time before trusting its state
        if player.get('updated') and not player.get('state_stale') and not player['frozen'] \
                and self.state.get('frozen') and time.time() - self.state.get('frozen_at', 0) > FREEZE_GRACE:
            # Live player state says playback runs (e.g. resumed in the node UI): forget the agent's freeze.
            self.state['frozen'] = False
            self.state.pop('unfreeze_at', None)
            self.save_state()
        disk = psutil.disk_usage('/')
        data = {
            'version': VERSION, 'hostname': socket.gethostname(), 'model': self.model(), 'ip': self.local_ip(),
            'cpu': round(psutil.cpu_percent(0.2), 1), 'ram': round(psutil.virtual_memory().percent, 1),
            'disk': round(disk.percent, 1), 'disk_free': disk.free, 'temp': self.temperature(),
            'uptime': int(time.time() - psutil.boot_time()), 'load': [round(x, 2) for x in psutil.getloadavg()],
            'api_ok': api_ok, 'api_error': api_error, 'player': player,
            'assets': self.assets_of(snap), 'collections': snap.get('collections') or [],
            'profiles': self.profiles_of(snap), 'notifications': self.notifications_of(snap),
            'admin': self.admin_of(snap), 'overlay': snap.get('overlay') if isinstance(snap.get('overlay'), dict) else None,
            'frozen_until': self.state.get('unfreeze_at'), 'last_error': self.last_error,
            'capabilities': self.capabilities() if api_ok else {},
            'caracal_version': caracal_version(), 'maintenance': self.maintenance, 'runtime': runtime(),
            'caracal_image': read_env().get('CARACAL_IMAGE', '') if is_docker() else '',
            'download_source': self.download_source(), 'arch': machine_arch(),
        }
        r = self.hub('POST', '/heartbeat', json=data)
        try:   # a device without a time source (no internet, no clock battery) follows the hub's clock
            set_clock(float(r.json()['server_time']))
        except (ValueError, KeyError, TypeError):
            pass

    # ------------------------------------------------------------ commands

    def run_commands(self):
        commands = self.hub('GET', '/commands').json()
        for cmd in commands:
            try:
                result, ok = self.execute(cmd['action'], dict(cmd.get('payload') or {})), True
            except Exception as e:  # noqa: BLE001 - reported to the hub
                result, ok = str(e), False
                self.last_error = f"{cmd['action']}: {e}"[:500]
            log('command', cmd['id'], cmd['action'], 'ok' if ok else 'failed', str(result)[:200])
            try:
                self.hub('POST', f"/commands/{cmd['id']}/result", json={'ok': ok, 'result': result})
            except requests.RequestException as e:
                log('result upload failed:', e)
            if self.after:
                action, self.after = self.after, None
                action()
        return len(commands)

    def execute(self, action, p):
        handler = getattr(self, 'do_' + action, None)
        if not handler:
            raise RuntimeError(f'Unsupported action {action}')
        return handler(p)

    def control(self, action, **extra):
        return self.body(self.local('control', json={'action': action, **extra}))

    def require_player(self):
        snap = self.snapshot()
        if self.player_state(snap).get('player_online') is False:
            raise RuntimeError('Player is not running; command was not sent to keep the player consistent')
        return snap

    def _check_item(self, snap, key, value):
        items = self.assets_of(snap) if key == 'item_id' else self.collections_of(snap)
        match = next((x for x in items if str(x.get('id')) == str(value)), None)
        if match is None:
            raise RuntimeError(f'Item {value} does not exist on this node')
        if match.get('enabled') in (False, 0):
            raise RuntimeError(f'Item {value} is disabled')
        return match

    def _show(self, action, key, p):
        snap = self.require_player()
        value = p[key]
        match = self._check_item(snap, key, value)
        ident = int(value) if str(value).isdigit() else value
        res = self.control(action, **{key: ident})
        minutes = int(p.get('minutes') or 0)
        # The node's state file does not reflect freezes sent through the Fleet API (they go to the player's
        # command channel), so the agent remembers them itself.
        self.state['frozen'] = action.startswith('freeze')
        self.state['frozen_at'] = time.time()
        if action.startswith('freeze') and minutes:
            self.state['unfreeze_at'] = time.time() + minutes * 60
            self.state['unfreeze_item'] = str(value)
        else:
            self.state.pop('unfreeze_at', None)
        self.save_state()
        return {'item': match.get('name'), 'response': res}

    def do_show(self, p):
        return self._show('show', 'item_id', p)

    def do_freeze(self, p):
        return self._show('freeze', 'item_id', p)

    def do_show_collection(self, p):
        return self._show('show_collection', 'collection_id', p)

    def do_freeze_collection(self, p):
        return self._show('freeze_collection', 'collection_id', p)

    def do_next(self, p):
        self.require_player()
        self.state.pop('unfreeze_at', None)
        self.state['frozen'] = False
        self.save_state()
        return self.control('next')

    def do_unfreeze(self, p):
        self.state.pop('unfreeze_at', None)
        self.state['frozen'] = False
        self.save_state()
        return self.control('unfreeze')

    def do_snapshot(self, p):
        snap = self.snapshot()
        return {'assets': len(self.assets_of(snap)), 'player': snap.get('player')}

    def do_restart_player(self, p):
        if is_docker():
            code, out = self.compose('restart', 'player', timeout=120)
            if code:
                raise RuntimeError(f'docker compose restart player failed: {out[-500:]}')
            return 'player container restarted'
        service = self.conf['player_service']
        subprocess.run(['systemctl', 'restart', service], check=True, timeout=60)
        return f'{service} restarted'

    def do_reboot(self, p):
        self.after = lambda: subprocess.Popen(['systemctl', 'reboot'])
        return 'reboot scheduled'

    # content ------------------------------------------------------

    @staticmethod
    def clean(item):
        return {k: v for k, v in item.items() if k not in VOLATILE_KEYS and v is not None}

    def with_profile(self, p):
        # None in auth_profile_id removes the login from a page, so it is kept although clean() drops None values
        body = self.clean(p)
        if 'auth_profile_id' in p:
            body['auth_profile_id'] = p['auth_profile_id']
        return body

    def do_add_web(self, p):
        return self.body(self.local('add_web', json=self.with_profile(p)))

    def do_update_asset(self, p):
        ident = p.pop('id')
        return self.body(self.local('update_asset', {'id': ident}, json=self.with_profile(p)))

    @staticmethod
    def profile_fields(p):
        return {k: p[k] for k in PROFILE_FIELDS if p.get(k) is not None}

    def do_add_profile(self, p):
        res = self.body(self.local('add_profile', json=self.profile_fields(p)))
        return {'id': item_id(res), 'name': p.get('name')}

    def do_update_profile(self, p):
        self.local('update_profile', {'id': p['id']}, json=self.profile_fields(p))
        return {'id': p['id'], 'name': p.get('name')}

    def do_delete_profile(self, p):
        return self.body(self.local('delete_profile', {'id': p['id']}))

    # notifications ------------------------------------------------

    def do_notify(self, p):
        # the payload is what the node accepts on /api/notify/v1: {title, message, level, ...} or a webhook body
        return self.body(self.local('notify', json=p))

    def do_notify_settings(self, p):
        return self.body(self.local('notify_settings', json={k: p[k] for k in NOTIFY_SETTINGS if k in p}))

    def do_notify_clear(self, p):
        return self.body(self.local('notify_clear'))

    @staticmethod
    def watcher_fields(p):
        return {k: p[k] for k in WATCHER_FIELDS if p.get(k) is not None}

    def do_add_watcher(self, p):
        res = self.body(self.local('add_watcher', json=self.watcher_fields(p)))
        return {'id': item_id(res), 'name': p.get('name')}

    def do_update_watcher(self, p):
        res = self.body(self.local('update_watcher', {'id': p['id']}, json=self.watcher_fields(p)))
        return {'id': p['id'], 'name': p.get('name'), 'reset': bool(isinstance(res, dict) and res.get('reset'))}

    def do_delete_watcher(self, p):
        return self.body(self.local('delete_watcher', {'id': p['id']}))

    def do_notify_sound(self, p):
        """Custom MP3 of a notification level (downloaded from the hub and checked), or the default chime again."""
        ids = {'level': p['level']}
        if p.get('reset'):
            return self.body(self.local('notify_sound_delete', ids))
        path = self.download_file(p['file_id'], p.get('sha256'))
        try:
            with open(path, 'rb') as f:
                return self.body(self.local('notify_sound', ids, files={'file': (p.get('filename') or 'sound.mp3', f,
                                                                               'audio/mpeg')}, timeout=120))
        finally:
            os.unlink(path)

    def do_check_watcher(self, p):
        return self.body(self.local('check_watcher', {'id': p['id']}, timeout=60))

    def do_preview_watcher(self, p):
        return self.body(self.local('preview_watcher', json={**self.watcher_fields(p), **(
            {'id': p['id']} if p.get('id') is not None else {})}, timeout=60))

    def do_notify_skip(self, p):
        return self.body(self.local('notify_skip'))

    def do_notify_remove(self, p):
        return self.body(self.local('notify_remove', {'id': p['id']}))

    def do_notify_log(self, p):
        """The latest history and audit entries, shortened to fit the hub's command result (16 kB)."""
        res = self.body(self.local('notify_log', params={'limit': 60}))
        if not isinstance(res, dict):
            raise LocalApiError('Invalid notification log response')
        cut = lambda v, n: v[:n] + '…' if isinstance(v, str) and len(v) > n else v
        out = {'history_count': res.get('history_count'), 'audit_count': res.get('audit_count'),
               'history': [{k: cut(x.get(k), 160) for k in ('id', 'source', 'title', 'message', 'level', 'created',
                                                            'shown_at', 'done')} for x in res.get('history') or []],
               'audit': [{k: cut(x.get(k), 160) for k in ('ts', 'actor', 'ip', 'action', 'detail')}
                         for x in res.get('audit') or []]}
        while len(json.dumps(out, ensure_ascii=False)) > 15000 and (out['history'] or out['audit']):
            longer = 'history' if len(out['history']) >= len(out['audit']) else 'audit'
            out[longer].pop()
        return out

    def do_notify_history_clear(self, p):
        return self.body(self.local('notify_history_clear'))

    def do_notify_audit_clear(self, p):
        return self.body(self.local('notify_audit_clear'))

    def do_update_notify_token(self, p):
        return self.body(self.local('notify_token_update', {'id': p['id']},
                                    json={k: p[k] for k in ('name', 'rate_per_min', 'enabled') if k in p}))

    def do_delete_notify_token(self, p):
        return self.body(self.local('notify_token_delete', {'id': p['id']}))

    def do_overlay_settings(self, p):
        return self.body(self.local('overlay', json={k: p[k] for k in ('enabled', 'size') if k in p}))

    def do_grafana_discover(self, p):
        return self.body(self.local('grafana_discover', json={'grafana_url': p['grafana_url'], 'tag': p['tag']},
                                    timeout=60))

    def set_admin_waiting(self, creds, wait=300):
        """do_set_admin, waiting for a CARACAL that is just starting."""
        deadline = time.time() + wait
        while True:
            try:
                return self.do_set_admin(creds)
            except LocalApiError as e:
                if time.time() > deadline or 'unreachable' not in str(e):
                    raise
                time.sleep(5)

    def do_set_admin(self, p):
        """The web administrator of the node: created (the node's first-run setup), or a new name and password of
        the existing one (its old sessions end)."""
        creds = {'username': p['username'], 'password': p['password']}
        base = self.conf['local_api'].rstrip('/')
        try:
            needed = self.local_session.get(base + SETUP_STATUS, timeout=10).json().get('needed')
        except (requests.RequestException, ValueError, AttributeError) as e:
            raise LocalApiError(f'Local CARACAL API unreachable: {e}')
        if needed:
            r = self.local_session.post(base + SETUP, data=creds, timeout=30)
            if r.status_code >= 400 and r.status_code != 409:   # 409: created meanwhile, change it below
                raise LocalApiError(f'CARACAL setup: HTTP {r.status_code} {r.text[:200]}')
            if r.status_code < 400:
                log('web administrator created', p['username'])
                return {'created': True, 'username': p['username']}
        try:
            self.local('admin', json=creds)
        except LocalApiError as e:
            if 'does not support' in str(e):
                raise LocalApiError('This CARACAL version cannot change its administrator; update CARACAL first')
            raise
        log('web administrator changed', p['username'])
        return {'created': False, 'username': p['username']}

    def do_delete_asset(self, p):
        return self.body(self.local('delete_asset', {'id': p['id']}))

    def do_reorder(self, p):
        # The playlist may have changed since the order was chosen: drop ids that no longer exist and keep
        # items that were added meanwhile at the end, so the node always receives a complete order.
        current = [a.get('id') for a in self.assets_of(self.snapshot())]
        by_key = {str(x): x for x in current}
        order = [by_key[str(x)] for x in dict.fromkeys(str(x) for x in p['order']) if str(x) in by_key]
        order += [x for x in current if x not in order]
        # CARACAL nodes read 'ids'; 'order' is kept for other patch versions
        return self.body(self.local('reorder', json={'ids': order, 'order': order}))

    @staticmethod
    def grafana_fields(p):
        """Grafana collection fields; the configuration may also come as the node's JSON 'source'."""
        out = {k: p[k] for k in GRAFANA_FIELDS if p.get(k) is not None}
        if 'tag' not in out and p.get('source'):
            try:
                out = {**json.loads(p['source']), **out}
            except (TypeError, ValueError):
                pass
        return {k: out[k] for k in GRAFANA_FIELDS if k in out}

    def do_add_collection(self, p):
        return self.body(self.local('add_grafana_tag', json=self.grafana_fields(p)))

    def do_update_collection(self, p):
        ident = p.pop('id')
        return self.body(self.local('update_asset', {'id': ident}, json=self.grafana_fields(p)))

    def do_delete_collection(self, p):
        return self.do_delete_asset(p)

    def download_file(self, file_id, sha256=None):
        """Download a hub file to a temporary file and verify its checksum."""
        fd, path = tempfile.mkstemp(prefix='caracal-fleet-', dir=self.tmp_dir())
        h = hashlib.sha256()
        try:
            with self.session.get(f'{self.base}/files/{file_id}', stream=True, timeout=60) as r, \
                    os.fdopen(fd, 'wb') as f:
                r.raise_for_status()
                for chunk in r.iter_content(1024 * 256):
                    h.update(chunk)
                    f.write(chunk)
            if sha256 and h.hexdigest() != sha256:
                raise RuntimeError('Downloaded file checksum mismatch')
            return path
        except Exception:
            os.unlink(path)
            raise

    @staticmethod
    def tmp_dir():
        for d in ('/var/tmp', tempfile.gettempdir()):
            if os.path.isdir(d) and os.access(d, os.W_OK):
                return d
        return None

    def do_add_media(self, p):
        path = self.download_file(p['file_id'], p.get('sha256'))
        try:
            fields = {k: str(v) for k, v in self.clean(p).items() if not isinstance(v, (dict, list))}
            fields.setdefault('kind', p.get('kind', 'image'))
            with open(path, 'rb') as f:
                r = self.local('upload', data=fields, files={'file': (p.get('filename') or p.get('name') or 'file', f)},
                               timeout=600)
            return self.body(r)
        finally:
            os.unlink(path)

    def do_export_assets(self, p):
        """Upload media of the given local assets to the hub (used for copying content between nodes)."""
        snap = self.snapshot()
        assets = {str(a.get('id')): a for a in self.assets_of(snap)}
        files = {}
        for ident in p.get('asset_ids') or []:
            asset = assets.get(str(ident))
            if not asset:
                raise RuntimeError(f'Asset {ident} not found')
            kind = str(asset.get('kind') or asset.get('type') or 'image')
            kind = kind.split('/', 1)[0] if '/' in kind else kind
            fd, path = tempfile.mkstemp(prefix='caracal-export-', dir=self.tmp_dir())
            try:
                with self.local('asset_file', {'id': ident}, stream=True, timeout=600) as r, os.fdopen(fd, 'wb') as f:
                    for chunk in r.iter_content(1024 * 256):
                        f.write(chunk)
                # keep the extension: CARACAL decides between image and video by it
                ext = Path(str(asset.get('source') or '')).suffix
                name = asset.get('filename') or asset.get('name') or f'asset-{ident}'
                if ext and not str(name).lower().endswith(ext.lower()):
                    name = f'{name}{ext}'
                with open(path, 'rb') as f:
                    resp = self.hub('POST', '/files', data=f, timeout=600,
                                    headers={'X-File-Name': quote(str(name)), 'X-File-Kind': kind,
                                             'Content-Type': 'application/octet-stream'})
                files[str(ident)] = resp.json()['id']
            finally:
                os.unlink(path)
        return {'files': files}

    def do_import_playlist(self, p):
        """Create the given items. In replace mode the old items are removed only after everything was added;
        Grafana collections of the node are removed only when collections are imported as well.
        Items this node cannot create (missing local endpoint) are skipped and reported."""
        items, mode = p.get('items') or [], p.get('mode', 'append')
        caps = self.capabilities()
        snap = self.snapshot()
        tag_asset = lambda a: str(a.get('kind') or a.get('type')) == COLLECTION_KIND
        old_assets = [a.get('id') for a in self.assets_of(snap)]
        removable = [a.get('id') for a in self.assets_of(snap) if p.get('replace_collections') or not tag_asset(a)]
        created, skipped, unknown_ids = [], [], False
        try:
            for it in items:
                kind = str(it.get('kind') or 'web')
                collection = it.get('type') == 'collection' or kind == COLLECTION_KIND
                if (collection and caps.get('add_grafana_tag') is False) or \
                        (it.get('file_id') and caps.get('upload') is False):
                    skipped.append(it.get('name'))
                    continue
                if collection:
                    res = self.do_add_collection(dict(it))
                elif it.get('file_id'):
                    res = self.do_add_media(dict(it))
                else:
                    res = self.do_add_web(dict(it))
                ident = item_id(res)
                unknown_ids = unknown_ids or ident is None
                created.append(ident)
                log('imported', kind, it.get('name'))
        except Exception:
            for ident in created:
                if ident is not None:
                    try:
                        self.local('delete_asset', {'id': ident})
                    except LocalApiError:
                        pass
            raise
        if mode == 'replace':
            for ident in removable:
                self.local('delete_asset', {'id': ident})
        if created and not unknown_ids:
            kept = [x for x in old_assets if mode != 'replace' or x not in removable]
            try:
                self.do_reorder({'order': kept + created})
            except LocalApiError as e:
                log('reorder after import failed:', e)
        return {'created': len(created), 'skipped': skipped, 'mode': mode}

    def do_set_hub(self, p):
        """Move the agent to another hub (server migration). Switches only when the new hub already knows
        this device and accepts its token, i.e. after the backup was restored there."""
        new = str(p['hub']).rstrip('/')
        try:
            r = requests.get(f"{new}/api/device/{self.conf['device_id']}/ping", timeout=20,
                             headers={'X-Device-Token': self.conf['device_token']})
        except requests.RequestException as e:
            raise RuntimeError(f'New hub {new} is not reachable: {e}')
        if r.status_code != 200:
            raise RuntimeError(f'New hub {new} does not accept this device (HTTP {r.status_code}); '
                               'restore the backup on the new hub first')
        conf = read_json(CONFIG, {})
        conf['hub'] = new
        write_json(CONFIG, conf, 0o600)
        self.after = lambda: os._exit(0)  # systemd restarts the agent with the new hub
        return f'hub changed to {new}'

    # ------------------------------------------------------------ CARACAL update

    def do_update_caracal(self, p):
        """Update CARACAL: a new image version on Docker nodes, a release archive on classic nodes."""
        if is_docker():
            return self.update_docker(p)
        if not p.get('file_id'):
            raise RuntimeError('This node runs classic CARACAL: convert it to Docker or use a release archive')
        return self.update_classic(p)

    def update_docker(self, p):
        """Pull the new image and recreate the containers; the previous version is restored on failure."""
        version = str(p.get('version') or '').strip()
        if not re.fullmatch(r'[A-Za-z0-9._+-]{1,64}', version):
            raise RuntimeError('Invalid version')
        image = str(p.get('image') or '')
        # both end up in the compose .env: a newline would add settings of its own
        if image and not re.fullmatch(r'[a-z0-9]+([._-][a-z0-9]+)*(:[0-9]+)?(/[a-z0-9]+([._-][a-z0-9]+)*)+', image):
            raise RuntimeError('Invalid image')
        env = read_env()
        previous = dict(env)
        env['CARACAL_VERSION'] = version
        if image:
            env['CARACAL_IMAGE'] = image
        # the compose file of the hub comes with every update, so existing nodes get new container settings too
        # (e.g. the sound device of the overlay); it is restored with the previous version on failure
        compose_file = NODE_DIR / 'compose.yml'
        old_compose = compose_file.read_text() if compose_file.is_file() else None
        new_compose = self.hub_compose()
        env.setdefault('CARACAL_AUDIO_GID', group_id('audio') or '29')

        def restore():
            write_env(previous)
            if old_compose is not None:
                write_text_file(compose_file, old_compose)

        log('CARACAL update', previous.get('CARACAL_VERSION', '?'), '->', version)
        self.maintenance = 'caracal_update'
        try:
            write_env(env)
            if new_compose and new_compose != old_compose:
                write_text_file(compose_file, new_compose)
            if self.download_source() == 'fleet':
                code, out = self.load_image_from_hub(version)
            else:
                code, out = self.run_with_heartbeats(['docker', 'compose', '--project-directory', str(NODE_DIR), 'pull'])
            if code != 0:
                restore()
                raise RuntimeError(f'Image download failed, nothing changed:\n{out[-3000:]}')
            code, up = self.compose('up', '-d', '--remove-orphans')
            if code != 0 or not self.wait_for_caracal():
                restore()
                self.compose('up', '-d', '--remove-orphans')
                raise RuntimeError(f'CARACAL {version} did not start, previous version restored:\n{up[-3000:]}')
            try:  # free untagged layers; tagged previous versions stay available for a manual rollback
                subprocess.run(['docker', 'image', 'prune', '-f'], timeout=300, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
            except (OSError, subprocess.SubprocessError):
                pass
            return {'version': version, 'image': env.get('CARACAL_IMAGE')}
        finally:
            self.maintenance = ''

    def load_image_from_hub(self, version):
        """CARACAL image through the hub (download source "fleet"): the hub prepares it from its registry, the agent
        checks its SHA-256 and loads it into Docker. Returns (exit code, output) like run_with_heartbeats."""
        params = {'version': version, 'arch': machine_arch()}
        auth = (self.conf['device_id'], self.conf['device_token'])
        fd, path = tempfile.mkstemp(prefix='caracal-image-', suffix='.tar', dir=self.tmp_dir())
        os.close(fd)
        deadline = time.time() + INSTALL_TIMEOUT
        try:
            while True:
                r = requests.get(f'{self.hub_url}/api/proxy/image', params=params, auth=auth, stream=True,
                                 timeout=(20, 600))
                if r.status_code == 202:   # the hub is still downloading the image from its registry
                    r.close()
                    if time.time() > deadline:
                        return 1, 'The hub did not prepare the image in time'
                    self.quiet_heartbeat()
                    time.sleep(10)
                    continue
                if r.status_code != 200:
                    return 1, f'Image download from the hub failed: HTTP {r.status_code} {r.text[:300]}'
                h = hashlib.sha256()
                with r, open(path, 'wb') as f:
                    for chunk in r.iter_content(1024 * 1024):
                        h.update(chunk)
                        f.write(chunk)
                if h.hexdigest() != r.headers.get('X-Sha256'):
                    return 1, 'Image from the hub: checksum mismatch'
                break
            log('image from the hub downloaded, loading it into Docker')
            return self.run_with_heartbeats(['docker', 'load', '-i', path])
        except requests.RequestException as e:
            return 1, f'Image download from the hub failed: {e}'
        finally:
            Path(path).unlink(missing_ok=True)

    def clock_from_hub(self):
        """Read the time from the hub's Date header without checking its certificate (the certificate cannot be
        checked with a wrong clock; nothing but the time is taken from this answer)."""
        try:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            r = requests.head(self.hub_url + '/api/health', timeout=15, verify=False)
            return set_clock(email.utils.parsedate_to_datetime(r.headers['Date']).timestamp())
        except (requests.RequestException, KeyError, TypeError, ValueError) as e:
            log('clock from the hub:', e)
            return False

    def quiet_heartbeat(self):
        try:
            self.heartbeat()
        except Exception as e:  # noqa: BLE001 - the local API may be down during an update
            log('heartbeat during update:', e)

    # download source -------------------------------------------------

    def download_source(self):
        return 'fleet' if self.conf.get('download_source') == 'fleet' else 'internet'

    def apply_download_source(self):
        """Point apt to the hub (fleet) or back to the repositories (internet); returns the changed source files."""
        fleet = self.download_source() == 'fleet'
        return apt_use(self.hub_url if fleet else None, self.conf['device_id'], self.conf['device_token'])

    def do_set_download_source(self, p):
        source = 'fleet' if p.get('source') == 'fleet' else 'internet'
        conf = read_json(CONFIG, {})
        conf['download_source'] = self.conf['download_source'] = source
        write_json(CONFIG, conf, 0o600)
        changed = self.apply_download_source()
        log('download source:', source, 'apt sources changed:', ', '.join(changed) or 'none')
        return {'source': source, 'apt_sources_changed': changed}

    def hub_compose(self):
        """The current compose file of the CARACAL node from the hub; None keeps the node's own."""
        try:
            r = self.session.get(f'{self.hub_url}/api/bootstrap/caracal-compose.yml', timeout=30)
            r.raise_for_status()
        except requests.RequestException as e:
            log('compose file not downloaded, the current one is kept:', e)
            return None
        return r.text if 'services:' in r.text else None

    def do_convert_to_docker(self, p):
        """Convert a classic CARACAL node (or a bare system) to CARACAL on Docker with install-node.sh.
        Data in /var/lib/caracal are kept. The agent itself stays installed and enrolled."""
        work = Path(tempfile.mkdtemp(prefix='caracal-node-', dir=self.tmp_dir()))
        try:
            for name in ('install-node.sh', 'caracal-compose.yml'):
                r = self.session.get(f'{self.hub_url}/api/bootstrap/{name}', timeout=60)
                r.raise_for_status()
                (work / name).write_bytes(r.content)
            self.maintenance = 'caracal_update'
            cmd = [shutil.which('bash') or 'bash', 'install-node.sh', '--hub', self.hub_url, '--skip-agent',
                   '--image', str(p['image']), '--version', str(p.get('version') or 'latest')]
            if self.download_source() == 'fleet':
                # the device's credentials in a file, not on the command line (visible in the process list)
                auth = work / 'fleet-auth'
                auth.write_text(f"{self.conf['device_id']}:{self.conf['device_token']}")
                os.chmod(auth, 0o600)
                cmd += ['--via-fleet', '--fleet-auth-file', str(auth)]
            code, out = self.run_with_heartbeats(cmd, cwd=work)
            write_key_file(self.conf['device_token'])
            if code == 5:   # the graphics driver was enabled (e.g. DietPi); nothing was converted yet
                self.after = lambda: subprocess.Popen(['systemctl', 'reboot'])
                raise RuntimeError('The graphics driver was enabled and the node reboots now. Run "Convert to Docker" '
                                   f'again when it is back online.\n{out[-3000:]}')
            if code != 0:
                raise RuntimeError(f'install-node.sh failed (exit {code}):\n{out[-6000:]}')
            result = {'version': caracal_version(), 'runtime': runtime(), 'log': out[-3000:]}
            if p.get('username') and p.get('password'):   # optional: the web administrator of the converted node
                try:
                    result['admin'] = self.set_admin_waiting({'username': p['username'], 'password': p['password']})
                except LocalApiError as e:
                    result['admin_error'] = str(e)[:300]
            return result
        finally:
            self.maintenance = ''
            shutil.rmtree(work, ignore_errors=True)

    def update_classic(self, p):
        """Install a CARACAL release with its own install.sh; roll back to the previous version on failure.

        install.sh replaces /opt/caracal and keeps /var/lib/caracal (playlists, media, database) as well as
        /etc/caracal-fleet-key. The previous /opt/caracal is archived first so that it can be restored.
        """
        if not (CARACAL_DIR / 'app' / 'main.py').exists():
            raise RuntimeError(f'CARACAL was not found in {CARACAL_DIR}')
        package = self.download_file(p['file_id'], p.get('sha256'))
        work = Path(tempfile.mkdtemp(prefix='caracal-update-', dir=self.tmp_dir()))
        backup = STATE.parent / 'caracal-backup.tar.gz'
        try:
            source = unpack_release(package, work)
            log('CARACAL update', caracal_version() or '?', '->', p.get('version') or '?')
            backup.parent.mkdir(parents=True, exist_ok=True)
            with tarfile.open(backup, 'w:gz') as tar:
                tar.add(str(CARACAL_DIR), arcname=CARACAL_DIR.name)
            self.maintenance = 'caracal_update'
            code, output = self.run_with_heartbeats([shutil.which('bash') or 'bash', 'install.sh'], cwd=source)
            if code != 0:
                self.rollback_caracal(backup)
                raise RuntimeError(f'install.sh failed (exit {code}), previous version restored:\n{output[-6000:]}')
            if not self.wait_for_caracal():
                self.rollback_caracal(backup)
                raise RuntimeError('CARACAL did not start after the update, previous version restored:\n'
                                   + output[-6000:])
            return {'version': caracal_version(), 'log': output[-3000:]}
        finally:
            self.maintenance = ''
            shutil.rmtree(work, ignore_errors=True)
            os.unlink(package)

    def run_with_heartbeats(self, cmd, cwd=None):
        """Run a long command (installer, image download) while still reporting to the hub, so the node shows
        'update running' instead of an outage."""
        out = tempfile.TemporaryFile(mode='w+', encoding='utf-8', errors='replace')
        proc = subprocess.Popen(cmd, cwd=str(cwd) if cwd else None, stdout=out, stderr=subprocess.STDOUT)
        started = time.time()
        while proc.poll() is None:
            if time.time() - started > INSTALL_TIMEOUT:
                proc.kill()
            try:
                self.heartbeat()
            except Exception as e:  # noqa: BLE001 - the local API is down during the installation
                log('heartbeat during update:', e)
            time.sleep(10 if proc.poll() is None else 0)
        out.seek(0)
        return proc.returncode, out.read()

    def wait_for_caracal(self, timeout=120):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                self.snapshot()
                return all(self.service_active(s) is not False for s in CARACAL_SERVICES)
            except Exception:  # noqa: BLE001
                time.sleep(3)
        return False

    def rollback_caracal(self, backup):
        """Restore the previous /opt/caracal, its systemd units and start every CARACAL service again."""
        log('CARACAL update failed, restoring the previous version')
        for svc in reversed(CARACAL_SERVICES):
            systemctl('stop', svc)
        shutil.rmtree(CARACAL_DIR, ignore_errors=True)
        with tarfile.open(backup) as tar:
            extract(tar, CARACAL_DIR.parent, 'fully_trusted')   # our own backup (venv has absolute links)
        try:
            subprocess.run(['chown', '-R', 'caracal:caracal', str(CARACAL_DIR)], check=False, timeout=120)
        except OSError:
            pass
        # install.sh may already have installed the new unit files
        units = CARACAL_DIR / 'systemd'
        if units.is_dir() and Path('/etc/systemd/system').is_dir():
            for unit in units.glob('caracal*.service'):
                try:
                    shutil.copyfile(unit, Path('/etc/systemd/system') / unit.name)
                except OSError as e:
                    log('unit restore failed:', unit.name, e)
        systemctl('daemon-reload')
        start_caracal_services()

    def do_update_agent(self, p):
        pkg = self.hub('GET', '/agent').json()
        code = base64.b64decode(pkg['code'])
        if hashlib.sha256(code).hexdigest() != pkg['sha256']:
            raise RuntimeError('Agent package checksum mismatch')
        compile(code, 'agent.py', 'exec')
        tmp = AGENT_FILE.with_suffix('.new')
        tmp.write_bytes(code)
        os.chmod(tmp, 0o755)
        tmp.replace(AGENT_FILE)
        self.after = lambda: os._exit(0)  # systemd (Restart=always) starts the new version
        return f"agent {VERSION} -> {pkg['version']}"

    # ------------------------------------------------------------ main loop

    def auto_unfreeze(self):
        until = self.state.get('unfreeze_at')
        if not until or time.time() < until:
            return
        try:
            # Unfreeze unconditionally: the node's own 'frozen' flag does not cover Fleet freezes.
            self.control('unfreeze')
            log('timed freeze finished, playback resumed')
            self.state.pop('unfreeze_at', None)
            self.state['frozen'] = False
            self.save_state()
        except Exception as e:  # noqa: BLE001
            log('auto unfreeze failed:', e)

    def run(self):
        log(f'CARACAL Fleet Agent {VERSION} started, device {self.conf["device_id"]}, hub {self.hub_url}')
        try:
            write_key_file(self.conf['device_token'])   # also provides the key to CARACAL containers
        except OSError as e:
            log('fleet key file:', e)
        if self.download_source() == 'fleet':
            try:
                self.apply_download_source()
            except OSError as e:
                log('apt sources:', e)
        next_hb = 0
        while True:
            try:
                self.auto_unfreeze()
                if time.time() >= next_hb:
                    self.heartbeat()
                    next_hb = time.time() + self.conf['heartbeat_interval']
                if self.reboot_pending:
                    self.reboot_pending = False
                    subprocess.Popen(['systemctl', 'reboot'])
                if self.run_commands():
                    next_hb = 0  # report the new state immediately after changes
            except requests.exceptions.SSLError as e:
                # a clock far behind makes the hub's certificate look "not yet valid"
                log('hub TLS error:', e)
                self.clock_from_hub()
            except requests.HTTPError as e:
                code = e.response.status_code if e.response is not None else 0
                log('hub error:', e)
                if code == 401:
                    log('device token rejected by hub; re-enroll with install-agent.sh --reenroll')
                    time.sleep(60)
            except Exception as e:  # noqa: BLE001 - the agent must keep running
                log('agent error:', e)
            time.sleep(self.conf['poll_interval'])


def is_docker():
    return (NODE_DIR / 'compose.yml').is_file()


def runtime():
    """'docker' (CARACAL in containers), 'host' (classic installation in /opt/caracal) or ''."""
    if is_docker():
        return 'docker'
    return 'host' if (CARACAL_DIR / 'app' / 'main.py').exists() else ''


def read_env():
    """KEY=value settings of the CARACAL compose project (.env next to compose.yml)."""
    out = {}
    try:
        for line in (NODE_DIR / '.env').read_text().splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                k, v = line.split('=', 1)
                out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


CLOCK_TOLERANCE = 120   # seconds


def ntp_synchronized():
    """True when a time server synchronises the clock (or when this cannot be told: then the clock is left alone)."""
    try:
        r = subprocess.run(['timedatectl', 'show', '-p', 'NTPSynchronized', '--value'], capture_output=True, text=True,
                           timeout=10)
    except (OSError, subprocess.SubprocessError):
        return True
    return r.returncode != 0 or r.stdout.strip() == 'yes'


def set_clock(ts):
    """Set the system clock to the hub's time when it is more than CLOCK_TOLERANCE off and no time server is used."""
    if abs(ts - time.time()) <= CLOCK_TOLERANCE or ntp_synchronized():
        return False
    off = int(ts - time.time())
    r = subprocess.run(['date', '-s', f'@{int(ts)}'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if r.returncode == 0:
        log(f'clock set from the hub ({off:+d} s)')
    return r.returncode == 0


def machine_arch():
    """Docker platform architecture of this device (arm64 on a 64-bit Raspberry Pi OS)."""
    m = platform.machine().lower()
    return {'aarch64': 'arm64', 'arm64': 'arm64', 'x86_64': 'amd64', 'amd64': 'amd64'}.get(m, m)


def apt_source_files():
    d = APT_DIR / 'sources.list.d'
    files = [APT_DIR / 'sources.list'] + sorted(d.glob('*.list')) + sorted(d.glob('*.sources'))
    return [f for f in files if f.is_file()]


def apt_rewrite(text, hub=None):
    """apt source URIs of the known repositories: through the hub (<hub>/apt/<host>/...) or, with hub=None,
    directly again. Addresses of an earlier hub are replaced too."""
    hosts = '|'.join(re.escape(h) for h in APT_HOSTS)
    text = re.sub(rf'https?://[^\s/]+(?:/[^\s]*?)?/apt/({hosts})(?=[/\s]|$)', r'https://\1', text)
    if hub:
        text = re.sub(rf'https?://({hosts})(?=[/\s]|$)', lambda m: f'{hub}/apt/{m.group(1)}', text)
    return text


def apt_use(hub=None, user='', password=''):
    """Point apt to the hub (credentials in auth.conf.d, readable by root only) or back to the repositories."""
    changed = []
    for f in apt_source_files():
        old = f.read_text()
        new = apt_rewrite(old, hub)
        if new != old:
            write_text_file(f, new)
            changed.append(f.name)
    auth = APT_DIR / 'auth.conf.d' / 'caracal-fleet.conf'
    if hub:
        scheme, _, rest = hub.partition('://')
        machine = rest if scheme == 'https' else hub   # apt uses entries without a protocol for https only
        auth.parent.mkdir(parents=True, exist_ok=True)
        tmp = auth.with_suffix('.tmp')
        tmp.write_text(f'machine {machine}/apt login {user} password {password}\n')
        os.chmod(tmp, 0o600)
        tmp.replace(auth)
    else:
        auth.unlink(missing_ok=True)
    return changed


def group_id(name):
    """Host group id as text (e.g. audio), None when it does not exist."""
    try:
        import grp
        return str(grp.getgrnam(name).gr_gid)
    except (ImportError, KeyError):
        return None


def write_text_file(path, text):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(text)
    os.chmod(tmp, 0o644)
    tmp.replace(path)


def write_env(values):
    path = NODE_DIR / '.env'
    tmp = path.with_suffix('.tmp')
    tmp.write_text(''.join(f'{k}={v}\n' for k, v in values.items()))
    os.chmod(tmp, 0o644)
    tmp.replace(path)


def caracal_version():
    if is_docker():
        return read_env().get('CARACAL_VERSION', '')[:64]
    try:
        return (CARACAL_DIR / 'VERSION').read_text().strip()[:40]
    except OSError:
        return ''


def systemd_active(name):
    """True/False from systemd, None when systemd is not available."""
    try:
        r = subprocess.run(['systemctl', 'is-active', '--quiet', name], timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.returncode == 0


def systemctl(*args):
    try:
        subprocess.run(['systemctl', *args], check=False, timeout=120)
    except (OSError, subprocess.SubprocessError):
        pass


def extract(tar, path, trust):
    """tarfile.extractall with an explicit filter where supported (Python 3.12+ changes the default)."""
    if hasattr(tarfile, 'data_filter'):
        tar.extractall(str(path), filter=trust)
    else:
        tar.extractall(str(path))


def start_caracal_services():
    """Start the CARACAL services like install.sh does: the overlay and the player need the X display."""
    if not shutil.which('systemctl'):
        return
    systemctl('reset-failed', *CARACAL_SERVICES)
    systemctl('restart', 'caracal.service')
    systemctl('restart', 'caracal-display.service')
    deadline = time.time() + 60
    while not X_SOCKET.exists() and time.time() < deadline:
        time.sleep(2)
    for svc in CARACAL_SERVICES[2:]:
        systemctl('restart', svc)


def unpack_release(package, target):
    """Unpack a CARACAL release (zip or tar.gz, e.g. a GitHub/GitLab source archive) and return the folder with
    install.sh. Entries leaving the target folder are rejected."""
    target = Path(target).resolve()

    def check(name):
        dest = (target / name).resolve()
        if dest != target and target not in dest.parents:
            raise RuntimeError(f'Unsafe path in the release archive: {name}')

    if zipfile.is_zipfile(package):
        with zipfile.ZipFile(package) as z:
            for n in z.namelist():
                check(n)
            z.extractall(str(target))
    else:
        with tarfile.open(package) as tar:
            for m in tar.getmembers():
                check(m.name)
                if m.issym() or m.islnk():
                    check(os.path.join(os.path.dirname(m.name), m.linkname))
            extract(tar, target, 'tar')
    for root in [target] + sorted(x for x in target.iterdir() if x.is_dir()):
        if (root / 'install.sh').is_file() and (root / 'app' / 'main.py').is_file():
            return root
    raise RuntimeError('The archive is not a CARACAL release (install.sh and app/main.py missing)')


# ---------------------------------------------------------------- CLI

def fingerprint():
    for p in ('/etc/machine-id', '/var/lib/dbus/machine-id'):
        try:
            return hashlib.sha256(Path(p).read_bytes()).hexdigest()
        except OSError:
            continue
    return hashlib.sha256(socket.gethostname().encode()).hexdigest()


def enrollment_valid(conf):
    """True when the hub still accepts the stored device token (False on 401, None when unreachable)."""
    try:
        r = requests.get(f"{conf['hub']}/api/device/{conf['device_id']}/ping", timeout=20,
                         headers={'X-Device-Token': conf['device_token']})
    except requests.RequestException as e:
        log('hub unreachable, cannot verify enrollment:', e)
        return None
    if r.status_code == 401:
        return False
    r.raise_for_status()
    return True


def write_key_file(token):
    """The local CARACAL Fleet API authenticates the agent with this shared key. CARACAL runs as the
    unprivileged user "caracal", so the file is readable for that group. Containers read a copy in the data
    folder (/var/lib/caracal/.fleet-key)."""
    targets = [KEY_FILE] + ([DATA_DIR / '.fleet-key'] if DATA_DIR.is_dir() else [])
    for path in targets:
        if not path.exists() or path.read_text().strip() != token:
            path.write_text(token)
        mode = 0o600
        try:
            import grp
            os.chown(path, 0, grp.getgrnam('caracal').gr_gid)
            mode = 0o640
        except (ImportError, KeyError, OSError):
            pass
        os.chmod(path, mode)


def enroll(hub, token, name, reenroll=False, local_api=None):
    hub = hub.rstrip('/')
    conf = read_json(CONFIG, {})
    keep = bool(conf.get('device_id') and conf.get('device_token')) and \
        conf.get('hub', '').rstrip('/') == hub and not reenroll
    if keep and enrollment_valid({**conf, 'hub': hub}) is False:
        log('stored device token was rejected by the hub, enrolling again')
        keep = False
    if keep:
        log('existing enrollment kept:', conf['device_id'])
    else:
        if not token:
            raise SystemExit('Enrollment token is required for a new enrollment')
        r = requests.post(hub + '/api/device/enroll', timeout=30, json={
            'enroll_token': token, 'fingerprint': fingerprint(), 'name': name or socket.gethostname(),
            'device_token': conf.get('device_token')})
        r.raise_for_status()
        x = r.json()
        conf.update(hub=hub, device_id=x['device_id'], device_token=x['device_token'])
        log('enrolled as', x['device_id'])
    if local_api:
        conf['local_api'] = local_api
    write_json(CONFIG, conf, 0o600)
    write_key_file(conf['device_token'])
    print('DEVICE_ID=' + conf['device_id'], flush=True)
    return conf


def check():
    agent = Agent(read_json(CONFIG, {}))
    ok = True
    try:
        snap = agent.snapshot()
        print(f"local API OK, {len(agent.assets_of(snap))} playlist items, player: {snap.get('player')}")
    except Exception as e:  # noqa: BLE001
        ok = False
        print('local API ERROR:', e)
        if '503' in str(e) or 'not configured' in str(e):
            st = KEY_FILE.stat() if KEY_FILE.exists() else None
            print(f'  hint: the node Fleet API does not see the key {KEY_FILE} '
                  f'(exists={bool(st)}, mode={oct(st.st_mode & 0o777) if st else "-"}, gid={st.st_gid if st else "-"}). '
                  'It may run as another user or read the key only at start-up.')
    try:
        agent.heartbeat()
        print('hub OK')
    except Exception as e:  # noqa: BLE001
        ok = False
        print('hub ERROR:', e)
    return 0 if ok else 1


def set_admin_from_file(path, wait=300):
    """Installers pass the web administrator in a file readable by root only (never on the command line)."""
    try:
        creds = json.loads(Path(path).read_text(encoding='utf-8'))
    finally:
        try:
            Path(path).unlink()
        except OSError:
            pass
    conf = read_json(CONFIG, None)
    if not conf:
        raise SystemExit(f'{CONFIG} missing - run "agent.py enroll" first')
    try:   # a freshly started CARACAL container needs a moment
        result = Agent(conf).set_admin_waiting({'username': str(creds.get('username') or ''),
                                                'password': str(creds.get('password') or '')}, wait)
    except LocalApiError as e:
        print(f'The web administrator could not be set: {e}', file=sys.stderr)
        return 1
    print(f"Web administrator {result['username']} {'created' if result['created'] else 'changed'}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description='CARACAL Fleet Agent ' + VERSION)
    sub = ap.add_subparsers(dest='cmd')
    e = sub.add_parser('enroll')
    e.add_argument('--hub', required=True)
    e.add_argument('--token', default='')
    e.add_argument('--name', default='')
    e.add_argument('--reenroll', action='store_true')
    e.add_argument('--local-api', default='')
    ds = sub.add_parser('download-source', help='where CARACAL and system packages are downloaded from')
    ds.add_argument('source', choices=('internet', 'fleet'))
    sub.add_parser('run')
    adm = sub.add_parser('set-admin', help="create the node's web administrator or set its password")
    adm.add_argument('file', help='JSON file {"username", "password"}; removed afterwards')
    adm.add_argument('--wait', type=int, default=300, help='seconds to wait for the local CARACAL API')
    sub.add_parser('check')
    sub.add_parser('version')
    a = ap.parse_args(argv)
    if a.cmd == 'enroll':
        enroll(a.hub, a.token, a.name, a.reenroll, a.local_api or None)
    elif a.cmd == 'download-source':
        conf = read_json(CONFIG, None)
        if not conf:
            raise SystemExit(f'{CONFIG} missing - run "agent.py enroll" first')
        print(Agent(conf).do_set_download_source({'source': a.source}))
        # a running agent reads its configuration at start
        subprocess.run(['systemctl', 'try-restart', 'caracal-agent.service'], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    elif a.cmd == 'set-admin':
        return set_admin_from_file(a.file, a.wait)
    elif a.cmd == 'check':
        return check()
    elif a.cmd == 'version':
        print(VERSION)
    else:
        conf = read_json(CONFIG, None)
        if not conf:
            raise SystemExit(f'{CONFIG} missing - run "agent.py enroll" first')
        Agent(conf).run()
    return 0


if __name__ == '__main__':
    sys.exit(main())
