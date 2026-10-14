"""Updates of CARACAL Fleet itself: the check, and the helper that replaces the hub container (fake Docker API)."""
import copy
import json

from app import selfupdate

OLD_ID = 'a1b2c3d4e5f6' + '0' * 52
IMAGE_ENV = ['PATH=/usr/local/bin:/usr/bin', 'CARACAL_HUB_DATA=/var/lib/caracal-hub', 'CARACAL_NODE_IMAGE=ghcr.io/o/caracal-node']


class FakeDocker(selfupdate.Docker):
    """Containers, images and networks of a Docker engine, as far as the update uses them."""

    def __init__(self, healthy=True):
        super().__init__('http://fake')
        self.healthy = healthy
        self.calls = []
        self.containers = {OLD_ID: {
            'Id': OLD_ID, 'Name': '/caracal-fleet-hub-1', 'Image': 'sha256:old', 'State': {'Running': True, 'Status': 'running'},
            'Config': {'Hostname': OLD_ID[:12], 'Image': 'ghcr.io/o/caracal-fleet:4.16.1',
                       'Env': IMAGE_ENV + ['CARACAL_HUB_ADMIN_PASSWORD=secret', 'CARACAL_HUB_PROXY_CACHE_GB=20'],
                       'Labels': {'com.docker.compose.project': 'caracal-fleet', 'com.docker.compose.service': 'hub',
                                  'org.opencontainers.image.source': 'https://github.com/o/caracal-fleet'},
                       'Cmd': ['uvicorn', 'app.main:app'], 'ExposedPorts': {'8090/tcp': {}}},
            'HostConfig': {'NetworkMode': 'caracal-fleet_default', 'RestartPolicy': {'Name': 'unless-stopped'},
                           'PortBindings': {'8090/tcp': [{'HostPort': '8090'}]},
                           'Mounts': [{'Type': 'volume', 'Source': 'caracal-fleet_hub-data', 'Target': '/var/lib/caracal-hub'}]},
            'NetworkSettings': {'Networks': {'caracal-fleet_default': {'Aliases': ['caracal-fleet-hub-1', 'hub', OLD_ID[:12]]},
                                             'monitoring': {'Aliases': ['fleet']}}},
            'Mounts': [{'Type': 'volume', 'Name': 'caracal-fleet_hub-data', 'Destination': '/var/lib/caracal-hub'}]}}
        self.images = {'sha256:old': {'Config': {'Env': IMAGE_ENV, 'Cmd': ['uvicorn', 'app.main:app'],
                                                 'Labels': {'org.opencontainers.image.source': 'https://github.com/o/caracal-fleet'}}}}

    def call(self, method, path, body=None, timeout=60, ok=(200, 201, 204, 304)):
        self.calls.append((method, path.split('?')[0], copy.deepcopy(body)))
        parts = path.split('?')[0].strip('/').split('/')
        query = dict(x.split('=', 1) for x in path.split('?', 1)[1].split('&')) if '?' in path else {}
        if parts[0] == 'images':
            return self.images.get(parts[1].replace('%3A', ':'), {'Config': {}})
        if parts[0] == 'containers' and parts[1] == 'create':
            new_id = f'new{len(self.containers)}'.ljust(64, '0')
            self.containers[new_id] = {'Id': new_id, 'Name': '/' + query['name'], 'Config': body, 'State': {'Running': False, 'Status': 'created'}}
            return {'Id': new_id}
        if parts[0] == 'networks':
            return None
        c = self.containers[parts[1]]
        action = parts[2] if len(parts) > 2 else ''
        if method == 'GET' and action == 'json':
            return c
        if action == 'start':
            c['State'] = {'Running': True, 'Status': 'running', 'Health': {'Status': 'healthy' if (self.healthy or c['Id'] == OLD_ID) else 'unhealthy'}}
            if not self.healthy and c['Id'] != OLD_ID:
                c['State'] = {'Running': False, 'Status': 'exited'}
        elif action == 'stop':
            c['State'] = {'Running': False, 'Status': 'exited'}
        elif action == 'rename':
            c['Name'] = '/' + query['name']
        elif method == 'DELETE':
            del self.containers[parts[1]]
        return None

    def inspect(self, ident):
        return self.containers[ident]


def test_new_container_keeps_the_settings_but_not_the_old_image_defaults():
    d = FakeDocker()
    old = d.containers[OLD_ID]
    config, extra = selfupdate.new_container_config(old, d.images['sha256:old'], 'ghcr.io/o/caracal-fleet:4.17.0')
    assert config['Image'] == 'ghcr.io/o/caracal-fleet:4.17.0'
    assert config['Env'] == ['CARACAL_HUB_ADMIN_PASSWORD=secret', 'CARACAL_HUB_PROXY_CACHE_GB=20']   # the image's own ENV comes from the new image
    assert 'Cmd' not in config and 'Hostname' not in config
    assert config['Labels'] == {'com.docker.compose.project': 'caracal-fleet', 'com.docker.compose.service': 'hub'}
    assert config['HostConfig']['PortBindings'] == {'8090/tcp': [{'HostPort': '8090'}]}
    assert config['NetworkingConfig'] == {'EndpointsConfig': {'caracal-fleet_default': {'Aliases': ['caracal-fleet-hub-1', 'hub']}}}
    assert extra == {'monitoring': {'Aliases': ['fleet']}}


def test_replace_and_remove_the_old_container(tmp_path):
    d = FakeDocker()
    assert selfupdate.replace_container(d, OLD_ID, 'ghcr.io/o/caracal-fleet:4.17.0', str(tmp_path), sleep=lambda s: None)
    assert OLD_ID not in d.containers
    (new,) = d.containers.values()
    assert new['Name'] == '/caracal-fleet-hub-1' and new['Config']['Image'] == 'ghcr.io/o/caracal-fleet:4.17.0'
    assert ('POST', '/networks/monitoring/connect', {'Container': new['Id'], 'EndpointConfig': {'Aliases': ['fleet']}}) in d.calls
    status = json.loads((tmp_path / 'update-status.json').read_text())
    assert status['state'] == 'done' and status['to'].endswith(':4.17.0')


def test_a_new_version_that_does_not_start_brings_the_old_one_back(tmp_path):
    d = FakeDocker(healthy=False)
    assert not selfupdate.replace_container(d, OLD_ID, 'ghcr.io/o/caracal-fleet:4.17.0', str(tmp_path), sleep=lambda s: None)
    assert list(d.containers) == [OLD_ID]
    old = d.containers[OLD_ID]
    assert old['Name'] == '/caracal-fleet-hub-1' and old['State']['Running']
    assert json.loads((tmp_path / 'update-status.json').read_text())['state'] == 'failed'


def test_check_and_start(client, admin_headers, monkeypatch):
    from app import images
    selfupdate._state.update(checked=0, data=None, job=None)
    monkeypatch.setattr(images, 'list_tags', lambda image: ['latest', '4.16.1', '4.17.0', '99.0.0', 'edge', 'sha-1'])
    monkeypatch.setattr(selfupdate.Docker, 'available', lambda self: False)
    monkeypatch.setenv('CARACAL_HUB_IMAGE', 'ghcr.io/o/caracal-fleet')

    class R:
        def json(self):
            return [{'tag_name': 'v99.0.0', 'name': 'CARACAL Fleet 99.0.0', 'body': '## Highlights\n- **new**', 'html_url': 'https://x'},
                    {'tag_name': 'v4.10.0', 'name': 'old', 'body': ''}]
    import requests
    monkeypatch.setattr(requests, 'get', lambda url, **kw: R())
    from app.core import HUB_VERSION, version_tuple
    r = client.get('/api/system/update?refresh=1', headers=admin_headers).json()
    assert r['current'] == HUB_VERSION and r['latest'] == '99.0.0'
    assert all(version_tuple(v) > version_tuple(HUB_VERSION) for v in r['newer']) and '99.0.0' in r['newer']
    assert [n['version'] for n in r['notes']] == ['99.0.0']
    assert r['docker']['available'] is False
    assert client.post('/api/system/update', headers=admin_headers, json={'version': '99.0.0'}).json()['detail'] == 'docker_socket_missing'
    assert client.post('/api/system/update', headers=admin_headers, json={'version': '9.9.9'}).json()['detail'] == 'version_required'
    selfupdate._state.update(checked=0, data=None, job=None)
