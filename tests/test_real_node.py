"""Hub + agent against the REAL CARACAL node application (caracal repository, app/main.py).

Runs when the node repository is available next to this one (../caracal) or at CARACAL_NODE_REPO and its
Python dependencies are installed; skipped otherwise (e.g. in CI). Grafana discovery is replaced by a stub.
"""
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import pytest
import requests

from conftest import TMP, load_agent, serve

NODE_REPO = Path(os.getenv('CARACAL_NODE_REPO', Path(__file__).resolve().parents[2] / 'caracal'))
PW = 'admin-password-123'


def load_node():
    if not (NODE_REPO / 'app' / 'main.py').exists():
        pytest.skip('CARACAL node repository not found')
    for module in ('jwt', 'passlib', 'cryptography', 'multipart'):
        pytest.importorskip(module)
    data = TMP / 'real-node'
    os.environ['CARACAL_DATA'] = str(data)
    os.environ['CARACAL_FLEET_KEY_FILE'] = str(data / 'fleet-key')
    spec = importlib.util.spec_from_file_location('caracal_node_main', NODE_REPO / 'app' / 'main.py')
    mod = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True   # do not leave __pycache__ in the node repository
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = False
    mod._grafana_discover = lambda url, tag: [{'title': f'{tag} {i}', 'url': f'{url}/d/{tag}{i}'} for i in range(2)]
    return mod, data


@pytest.fixture(scope='module')
def env(hub_app):
    node, data = load_node()
    hub_url, _ = serve(hub_app)
    node_url, _ = serve(node.app)
    h = {'Authorization': 'Bearer ' + requests.post(hub_url + '/api/login',
                                                    json={'username': 'admin', 'password': PW}).json()['token']}
    enrolled = requests.post(hub_url + '/api/device/enroll', json={
        'enroll_token': 'enroll-test-token', 'fingerprint': 'real-caracal', 'name': 'Real CARACAL'}).json()
    (data / 'fleet-key').write_text(enrolled['device_token'])   # what the agent writes to /etc/caracal-fleet-key
    agent = load_agent().Agent({'hub': hub_url, 'local_api': node_url, **enrolled})
    agent.service_active = lambda name: None
    e = {'hub': hub_url, 'node': node_url, 'h': h, 'id': enrolled['device_id'], 'agent': agent, 'mod': node,
         'data': data, 'key': enrolled['device_token']}
    beat(e)
    return e


def beat(env, **state):
    """What the CARACAL player sends every second (/api/v2/player/heartbeat, localhost only)."""
    body = {'current_id': None, 'current_name': '', 'frozen': False, 'collection_frozen': False, 'remaining': 10,
            'duration': 30, **state}
    requests.post(env['node'] + '/api/v2/player/heartbeat', json=body).raise_for_status()
    env['agent'].heartbeat()


def run(env, action, payload=None):
    r = requests.post(f"{env['hub']}/api/devices/{env['id']}/commands", headers=env['h'],
                      json={'action': action, 'payload': payload or {}})
    assert r.status_code == 200, r.text
    env['agent'].run_commands()
    env['agent'].heartbeat()
    row = next(x for x in requests.get(f"{env['hub']}/api/commands?device_id={env['id']}", headers=env['h']).json()
               if x['id'] == r.json()['id'])
    assert row['state'] == 'completed', row
    return row


def node_assets(env):
    return env['mod'].rows('SELECT * FROM assets ORDER BY position,id')


def device(env):
    return requests.get(f"{env['hub']}/api/devices/{env['id']}", headers=env['h']).json()


def test_capabilities_and_live_state(env):
    d = device(env)
    assert d['api_ok'] and d['player_online']
    caps = d['capabilities']
    assert all(caps[k] for k in ('snapshot', 'control', 'add_web', 'upload', 'asset_file', 'add_grafana_tag',
                                 'update_asset', 'delete_asset', 'reorder'))


def test_web_pages(env):
    run(env, 'add_web', {'name': 'Intranet', 'source': 'https://intranet.example', 'duration': 20, 'scale': 1.5})
    a = next(x for x in node_assets(env) if x['name'] == 'Intranet')
    assert a['kind'] == 'web' and a['duration'] == 20 and a['scale'] == 1.5
    run(env, 'update_asset', {'id': a['id'], 'name': 'Intranet 2', 'source': 'https://intranet2.example'})
    a = next(x for x in node_assets(env) if x['id'] == a['id'])
    assert a['name'] == 'Intranet 2' and a['source'] == 'https://intranet2.example'


def test_grafana_collection(env):
    run(env, 'add_collection', {'name': 'Výroba', 'grafana_url': 'https://grafana.example', 'tag': 'vyroba',
                                'duration': 30})
    col = next(x for x in node_assets(env) if x['kind'] == 'grafana-tag')
    assert json.loads(col['source']) == {'grafana_url': 'https://grafana.example', 'tag': 'vyroba', 'kiosk': True}
    run(env, 'update_collection', {'id': col['id'], 'tag': 'linka', 'grafana_url': 'https://grafana.example'})
    assert json.loads(next(x for x in node_assets(env) if x['id'] == col['id'])['source'])['tag'] == 'linka'
    assert [c['tag'] for c in device(env)['collections']] == ['linka']
    run(env, 'freeze_collection', {'collection_id': col['id']})
    # the player reads exactly this command
    command = requests.get(env['node'] + '/api/v6/player/command').json()
    assert command['action'] == 'freeze_collection' and command['collection_id'] == col['id']


def test_show_and_freeze_reach_the_player(env):
    item = next(x for x in node_assets(env) if x['kind'] == 'web')
    run(env, 'freeze', {'item_id': item['id'], 'minutes': 5})
    command = requests.get(env['node'] + '/api/v6/player/command').json()
    assert command['action'] == 'freeze' and command['item_id'] == item['id']
    beat(env, current_id=item['id'], current_name=item['name'], frozen=True)
    d = device(env)
    assert d['frozen'] and d['current_name'] == item['name']
    run(env, 'unfreeze')
    assert requests.get(env['node'] + '/api/v6/player/command').json()['action'] == 'unfreeze'


def test_player_down_is_detected(env):
    env['mod']._cc2_write({**env['mod']._cc2_read(), 'updated': time.time() - 60})
    env['agent'].heartbeat()
    assert device(env)['player_online'] is False
    beat(env)
    assert device(env)['player_online'] is True


def test_media_upload_export_and_delete(env):
    png = b'\x89PNG\r\n\x1a\n real node image'
    f = requests.post(env['hub'] + '/api/files', data=png, headers={**env['h'], 'Content-Type': 'image/png',
                                                                    'X-File-Name': 'banner.png'}).json()
    run(env, 'add_media', {'file_id': f['id'], 'name': 'Banner', 'duration': 12})
    a = next(x for x in node_assets(env) if x['name'] == 'Banner')
    media = env['data'] / 'media' / Path(a['source']).name
    assert a['kind'] == 'image' and a['duration'] == 12 and media.read_bytes() == png
    exported = requests.get(f"{env['node']}/api/fleet/v1/assets/{a['id']}/file", headers={'X-Fleet-Key': env['key']})
    assert exported.content == png
    run(env, 'delete_asset', {'id': a['id']})
    assert not media.exists()   # the node frees the file


def test_reorder(env):
    ids = [x['id'] for x in node_assets(env)][::-1]
    run(env, 'reorder', {'order': ids})
    assert [x['id'] for x in node_assets(env)] == ids


def test_node_security(env):
    from fastapi.testclient import TestClient
    node = env['node']
    assert requests.get(node + '/api/fleet/v1/snapshot').status_code == 401
    assert requests.get(node + '/api/fleet/v1/snapshot', headers={'X-Fleet-Key': 'wrong'}).status_code == 401
    remote = TestClient(env['mod'].app)   # requests from another host than 127.0.0.1
    assert remote.get('/api/player/playlist-expanded').status_code == 403
    assert remote.get('/api/player/playlist').status_code == 403
    assert requests.get(node + '/api/player/playlist-expanded').status_code == 200   # local player
    fails = [remote.post('/api/login', data={'username': 'x', 'password': 'y'}).status_code for _ in range(11)]
    assert fails[:10] == [401] * 10 and fails[10] == 429


def test_docker_runtime_requests(env):
    """In Docker the node UI cannot use sudo/systemd: reboot and player restart become requests."""
    from fastapi.testclient import TestClient
    node = env['mod']
    remote = TestClient(node.app)
    r = remote.post('/api/setup', data={'username': 'admin', 'password': 'node-password-1'})
    assert r.status_code in (200, 409)
    if r.status_code == 409:
        r = remote.post('/api/login', data={'username': 'admin', 'password': 'node-password-1'})
    old = node.RUNTIME
    node.RUNTIME = 'docker'
    try:
        snap = lambda: requests.get(env['node'] + '/api/fleet/v1/snapshot', headers={'X-Fleet-Key': env['key']}).json()
        before = snap()
        assert remote.post('/api/system/reboot', json={'confirm': 'REBOOT'}).status_code == 200
        assert remote.post('/api/v3/player/restart').status_code == 200
        after = snap()
        assert after['requests']['reboot'] == before['requests']['reboot'] + 1
        assert after['requests']['restart_player'] == before['requests']['restart_player'] + 1
        # the player sees the restart request in its command channel
        assert requests.get(env['node'] + '/api/v6/player/command').json()['restart_id'] == after['requests']['restart_player']
    finally:
        node.RUNTIME = old
