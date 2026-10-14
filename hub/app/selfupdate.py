"""Updates of CARACAL Fleet itself.

Checking: the hub reads the versions of its own image from the registry (ghcr.io) and the GitHub notes of the newer
releases; the result is cached and refreshed once a day. Administrators see a newer version on the overview and in
Settings -> Fleet update.

Updating from the web UI needs the Docker socket in the hub container (/var/run/docker.sock, opt-in: whoever controls
the socket controls the server). The hub makes a backup, pulls the new image and starts a short-lived helper
container from it ("python -m app.selfupdate helper"). The helper stops the hub, creates a new container with the
same configuration, volumes and networks and the new image, waits until it is healthy and removes the old one; when
the new one does not become healthy, the old hub is started again. On Docker Swarm the service is updated instead.
Without the socket the UI shows the commands for the server.
"""
import http.client
import json
import os
import re
import socket
import threading
import time
from urllib.parse import quote, urlsplit

DOCKER_SOCKET = os.getenv('CARACAL_DOCKER_SOCKET', '/var/run/docker.sock')
DEFAULT_IMAGE = 'ghcr.io/gelluithor/caracal-fleet'
CHECK_EVERY = 24 * 3600
HEALTH_WAIT = 180
STATUS_FILE = 'update-status.json'


# ---------------------------------------------------------------- Docker Engine API (unix socket or http for tests)

class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path, timeout=60):
        super().__init__('localhost', timeout=timeout)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


class Docker:
    def __init__(self, where=None):
        self.where = where or os.getenv('CARACAL_DOCKER_HOST') or DOCKER_SOCKET

    def available(self):
        if self.where.startswith('http'):
            return True
        return os.path.exists(self.where)

    def _conn(self, timeout):
        if self.where.startswith('http'):
            u = urlsplit(self.where)
            return http.client.HTTPConnection(u.hostname, u.port, timeout=timeout)
        return _UnixConnection(self.where, timeout)

    def call(self, method, path, body=None, timeout=60, ok=(200, 201, 204, 304)):
        conn = self._conn(timeout)
        try:
            conn.request(method, path, body=json.dumps(body) if body is not None else None,
                         headers={'Content-Type': 'application/json', 'Host': 'docker'})
            r = conn.getresponse()
            data = r.read()
        finally:
            conn.close()
        if r.status not in ok:
            try:
                message = json.loads(data).get('message', '')
            except ValueError:
                message = data[:200].decode(errors='replace')
            raise RuntimeError(f'Docker {method} {path.split("?")[0]}: HTTP {r.status} {message}')
        if not data:
            return None
        try:
            return json.loads(data)
        except ValueError:   # streamed answers (image pull): JSON lines
            lines = [json.loads(x) for x in data.decode(errors='replace').splitlines() if x.strip().startswith('{')]
            errors = [x.get('error') for x in lines if x.get('error')]
            if errors:
                raise RuntimeError(f'Docker {path.split("?")[0]}: {errors[-1]}')
            return lines

    def inspect(self, ident):
        return self.call('GET', f'/containers/{quote(ident, safe="")}/json')

    def pull(self, image, tag):
        return self.call('POST', f'/images/create?fromImage={quote(image, safe="")}&tag={quote(tag, safe="")}', timeout=1800)


def split_ref(ref):
    """'ghcr.io/o/caracal-fleet:4.16.1' -> ('ghcr.io/o/caracal-fleet', '4.16.1')."""
    ref = ref.split('@', 1)[0]
    name, sep, tag = ref.rpartition(':')
    if not sep or '/' in tag:
        return ref, 'latest'
    return name, tag


# ---------------------------------------------------------------- helper: replaces the hub container

def _user_part(values, image_values):
    """What was set for the container itself (compose, docker run), not inherited from its image."""
    image_values = image_values or []
    return [v for v in values or [] if v not in image_values]


def new_container_config(old, old_image, new_ref):
    """The create request of the new hub container: the old configuration with the new image. Settings that came
    from the old image (environment, labels, command, health check) are left out so the new image brings its own."""
    cfg, img = dict(old['Config']), (old_image or {}).get('Config') or {}
    out = {k: v for k, v in cfg.items() if k not in ('Hostname', 'Image', 'Env', 'Labels', 'Cmd', 'Entrypoint',
                                                     'Healthcheck', 'WorkingDir', 'User')}
    if cfg.get('Hostname') and not old['Id'].startswith(cfg['Hostname']):
        out['Hostname'] = cfg['Hostname']                 # a name set on purpose (not the container id)
    out['Image'] = new_ref
    out['Env'] = _user_part(cfg.get('Env'), img.get('Env'))
    labels = {k: v for k, v in (cfg.get('Labels') or {}).items() if (img.get('Labels') or {}).get(k) != v}
    out['Labels'] = labels
    for key in ('Cmd', 'Entrypoint', 'Healthcheck', 'WorkingDir', 'User'):
        if cfg.get(key) and cfg.get(key) != img.get(key):
            out[key] = cfg[key]
    out['HostConfig'] = old['HostConfig']
    networks = (old.get('NetworkSettings') or {}).get('Networks') or {}
    endpoint = lambda n: {'Aliases': [a for a in (n.get('Aliases') or []) if not old['Id'].startswith(a)],
                          **({'IPAMConfig': n['IPAMConfig']} if n.get('IPAMConfig') else {})}
    names = list(networks)
    mode = (old['HostConfig'] or {}).get('NetworkMode', '')
    first = mode if mode in networks else (names[0] if names else None)
    if first:
        out['NetworkingConfig'] = {'EndpointsConfig': {first: endpoint(networks[first])}}
    extra = {n: endpoint(v) for n, v in networks.items() if n != first}
    return out, extra


def _write_status(data_dir, status):
    if not data_dir:
        return
    try:
        tmp = os.path.join(data_dir, STATUS_FILE + '.tmp')
        with open(tmp, 'w') as f:
            json.dump({**status, 'at': time.time()}, f)
        os.replace(tmp, os.path.join(data_dir, STATUS_FILE))
    except OSError:
        pass


def wait_healthy(docker, ident, timeout=HEALTH_WAIT, sleep=time.sleep):
    end = time.time() + timeout
    while time.time() < end:
        state = docker.inspect(ident)['State']
        health = (state.get('Health') or {}).get('Status')
        if not state.get('Running') and state.get('Status') in ('exited', 'dead'):
            return False
        if health == 'healthy' or (health is None and state.get('Running') and time.time() > end - timeout + 20):
            return True
        sleep(2)
    return False


def replace_container(docker, old_id, new_ref, data_dir=None, sleep=time.sleep):
    """Stop the old hub, start a new one with new_ref; back to the old one when the new one is not healthy."""
    old = docker.inspect(old_id)
    old_image = docker.call('GET', f'/images/{quote(old["Image"], safe="")}/json', ok=(200,))
    name = old['Name'].lstrip('/')
    config, extra = new_container_config(old, old_image, new_ref)
    _write_status(data_dir, {'state': 'replacing', 'from': old['Config']['Image'], 'to': new_ref})
    docker.call('POST', f'/containers/{old["Id"]}/stop?t=20', timeout=60)
    backup = f'{name}-old-{int(time.time())}'
    docker.call('POST', f'/containers/{old["Id"]}/rename?name={quote(backup)}')
    new_id = None
    try:
        new_id = docker.call('POST', f'/containers/create?name={quote(name)}', config)['Id']
        for net, endpoint in extra.items():
            docker.call('POST', f'/networks/{quote(net, safe="")}/connect', {'Container': new_id, 'EndpointConfig': endpoint})
        docker.call('POST', f'/containers/{new_id}/start')
        if not wait_healthy(docker, new_id, sleep=sleep):
            raise RuntimeError('the new hub did not become healthy')
    except Exception as e:   # noqa: BLE001 - every failure brings the old hub back
        if new_id:
            docker.call('POST', f'/containers/{new_id}/stop?t=10', ok=(204, 304, 404))
            docker.call('DELETE', f'/containers/{new_id}?force=true', ok=(204, 404))
        docker.call('POST', f'/containers/{old["Id"]}/rename?name={quote(name)}')
        docker.call('POST', f'/containers/{old["Id"]}/start', ok=(204, 304))
        _write_status(data_dir, {'state': 'failed', 'from': old['Config']['Image'], 'to': new_ref, 'error': str(e)[:300]})
        return False
    docker.call('DELETE', f'/containers/{old["Id"]}?v=false', ok=(204, 404))
    _write_status(data_dir, {'state': 'done', 'from': old['Config']['Image'], 'to': new_ref})
    return True


def helper_main():
    """python -m app.selfupdate helper (in the helper container, started by the hub)."""
    old_id, new_ref = os.environ['CARACAL_UPDATE_OLD'], os.environ['CARACAL_UPDATE_IMAGE']
    time.sleep(2)   # the hub answers the request that started the update first
    ok = replace_container(Docker(), old_id, new_ref, os.getenv('CARACAL_HUB_DATA'))
    print('CARACAL Fleet update', 'done' if ok else 'failed, previous version started again', flush=True)
    return 0 if ok else 1


# ---------------------------------------------------------------- the hub: check and start

_state = {'checked': 0, 'data': None, 'job': None}
_lock = threading.Lock()


def own_container_id():
    """The id of the container this process runs in: Docker mounts /etc/hostname (and resolv.conf, hosts) from
    /var/lib/docker/containers/<id>/; the host name is the id only without host networking."""
    try:
        with open('/proc/self/mountinfo') as f:
            m = re.search(r'/containers/([0-9a-f]{64})/', f.read())
        if m:
            return m.group(1)
    except OSError:
        pass
    return socket.gethostname()


def own_container(docker):
    """The hub's own container, or None (not in Docker, or no access to the socket)."""
    try:
        return docker.inspect(own_container_id())
    except Exception:   # noqa: BLE001
        return None


def check(force=False):
    from . import images
    from .core import HUB_VERSION, version_tuple
    import requests
    with _lock:
        if _state['data'] and not force and time.time() - _state['checked'] < CHECK_EVERY:
            return _state['data']
    docker = Docker()
    me = own_container(docker) if docker.available() else None
    image = split_ref(me['Config']['Image'])[0] if me else os.getenv('CARACAL_HUB_IMAGE', DEFAULT_IMAGE)
    data = {'current': HUB_VERSION, 'image': image, 'latest': None, 'newer': [], 'notes': [], 'error': ''}
    try:
        versions = [t for t in images.sort_versions(images.list_tags(image)) if re.fullmatch(r'\d+\.\d+\.\d+', t)]
        data['latest'] = versions[0] if versions else None
        data['newer'] = [v for v in versions if version_tuple(v) > version_tuple(HUB_VERSION)]
    except Exception as e:   # noqa: BLE001
        data['error'] = f'registry_unavailable: {e}'[:300]
    owner_repo = '/'.join(image.split('/')[1:3]) if image.startswith('ghcr.io/') else ''
    if data['newer'] and owner_repo:
        try:
            releases = requests.get(f'https://api.github.com/repos/{owner_repo}/releases?per_page=20', timeout=15).json()
            wanted = {f'v{v}' for v in data['newer']}
            data['notes'] = [{'version': r['tag_name'].lstrip('v'), 'name': r.get('name') or r['tag_name'], 'body': (r.get('body') or '')[:20000],
                              'url': r.get('html_url'), 'published': r.get('published_at')} for r in releases if isinstance(r, dict) and r.get('tag_name') in wanted]
        except Exception:   # noqa: BLE001 - notes are optional
            pass
    swarm = bool(me and (me['Config'].get('Labels') or {}).get('com.docker.swarm.service.id'))
    data['docker'] = {'available': bool(me), 'swarm': swarm, 'socket': docker.available(),
                      'compose': (me['Config'].get('Labels') or {}).get('com.docker.compose.project') if me else None}
    with _lock:
        _state.update(checked=time.time(), data=data)
    return data


def status():
    from .core import DATA
    try:
        last = json.loads((DATA / STATUS_FILE).read_text())
    except (OSError, ValueError):
        last = None
    return {'job': _state['job'], 'last': last}


def _run(version, username):
    from .core import DATA
    from .system import make_scheduled_backup
    job = _state['job']
    try:
        docker = Docker()
        me = own_container(docker)
        if not me:
            raise RuntimeError('the hub container was not found through the Docker socket')
        image = split_ref(me['Config']['Image'])[0]
        new_ref = f'{image}:{version}'
        job.update(step='backup')
        backup = make_scheduled_backup()
        job.update(step='pull', backup=backup.name)
        docker.pull(image, version)
        labels = me['Config'].get('Labels') or {}
        if labels.get('com.docker.swarm.service.id'):
            sid = labels['com.docker.swarm.service.id']
            svc = docker.call('GET', f'/services/{sid}')
            spec = svc['Spec']
            spec['TaskTemplate']['ContainerSpec']['Image'] = new_ref
            job.update(step='restart')
            docker.call('POST', f"/services/{sid}/update?version={svc['Version']['Index']}", spec)
            return
        job.update(step='restart')
        helper = docker.call('POST', '/containers/create?name=' + quote(f'caracal-fleet-updater-{int(time.time())}'), {
            'Image': new_ref, 'Cmd': ['python', '-m', 'app.selfupdate', 'helper'],
            'Env': [f'CARACAL_UPDATE_OLD={me["Id"]}', f'CARACAL_UPDATE_IMAGE={new_ref}', f'CARACAL_HUB_DATA={DATA}'],
            'Labels': {'caracal-fleet.updater': '1'},
            'HostConfig': {'AutoRemove': True, 'NetworkMode': 'none',
                           'Binds': [f'{DOCKER_SOCKET}:/var/run/docker.sock'],
                           'Mounts': [{'Type': m['Type'], 'Source': m.get('Name') or m['Source'], 'Target': m['Destination']}
                                      for m in me.get('Mounts') or [] if m.get('Destination') == str(DATA)]}})
        docker.call('POST', f"/containers/{helper['Id']}/start")
        # the helper stops this container now; nothing after this line is sure to run
    except Exception as e:   # noqa: BLE001
        job.update(state='failed', error=str(e)[:300])
        from .core import audit
        audit({'username': username}, 'system.update_failed', version, {'error': str(e)[:300]})


def start(version, username):
    from fastapi import HTTPException
    data = check()
    if version not in data['newer'] and version != data.get('latest'):
        raise HTTPException(400, 'version_required')
    if not data['docker']['available']:
        raise HTTPException(409, 'docker_socket_missing')
    with _lock:
        if _state['job'] and _state['job'].get('state') == 'running':
            raise HTTPException(409, 'update_running')
        _state['job'] = {'state': 'running', 'version': version, 'step': 'start', 'started': time.time()}
    threading.Thread(target=_run, args=(version, username), daemon=True).start()


def tick():
    """Called by the schedule loop: the daily check."""
    try:
        check()
    except Exception as e:   # noqa: BLE001
        print('update check failed:', e, flush=True)


if __name__ == '__main__':
    import sys
    if sys.argv[1:] == ['helper']:
        sys.exit(helper_main())
