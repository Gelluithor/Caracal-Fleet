"""CARACAL on Docker: image versions from a registry, updates with rollback, conversion and network discovery."""
import json
import os
import socket
import threading
import time

import pytest
import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from conftest import ROOT, TMP, load_agent, serve
from dev.mock_node import create_app

PW = 'admin-password-123'
NODE_DIR = TMP / 'opt' / 'caracal-node'
TAGS = ['latest', '2026.9.30', '2026.10.1', 'edge', '2026.10.10', 'sha-abc']


def fake_registry():
    """Docker registry v2 with anonymous bearer tokens, like ghcr.io."""
    app = FastAPI()

    @app.get('/token')
    def token(scope: str = ''):
        return {'token': 'anon-' + scope}

    @app.get('/v2/owner/caracal-node/tags/list')
    def tags(r: Request):
        if not r.headers.get('Authorization', '').startswith('Bearer anon-repository:owner/caracal-node:pull'):
            host = r.headers['host']
            return JSONResponse({'errors': []}, status_code=401, headers={
                'WWW-Authenticate': f'Bearer realm="http://{host}/token",service="reg",'
                                    f'scope="repository:owner/caracal-node:pull"'})
        return {'name': 'owner/caracal-node', 'tags': TAGS}
    return app


def ssh_banner_server():
    srv = socket.socket()
    srv.bind(('127.0.0.1', 0))
    srv.listen(8)

    def loop():
        while True:
            conn, _ = srv.accept()
            conn.sendall(b'SSH-2.0-OpenSSH_9.2p1 Raspbian-2+deb12u3\r\n')
            conn.close()
    threading.Thread(target=loop, daemon=True).start()
    return srv.getsockname()[1]


@pytest.fixture(scope='module')
def env(hub_app):
    from app import images
    reg_url, _ = serve(fake_registry())
    images.REGISTRY_SCHEME = 'http'
    NODE_DIR.mkdir(parents=True, exist_ok=True)
    (NODE_DIR / 'compose.yml').write_text('services: {}\n')
    (NODE_DIR / '.env').write_text('CARACAL_IMAGE=old/image\nCARACAL_VERSION=2026.9.30\nCARACAL_UID=1000\n')
    os.environ['CARACAL_NODE_DIR'] = str(NODE_DIR)
    try:
        mod = load_agent()   # reads CARACAL_NODE_DIR at import: this agent sees a Docker node
    finally:
        os.environ.pop('CARACAL_NODE_DIR')
    hub_url, _ = serve(hub_app)
    h = {'Authorization': 'Bearer ' + requests.post(hub_url + '/api/login',
                                                    json={'username': 'admin', 'password': PW}).json()['token']}
    enrolled = requests.post(hub_url + '/api/device/enroll', json={
        'enroll_token': 'enroll-test-token', 'fingerprint': 'docker-node', 'name': 'Docker node'}).json()
    node_url, _ = serve(create_app(enrolled['device_token']))
    agent = mod.Agent({'hub': hub_url, 'local_api': node_url, **enrolled})
    agent.service_active = lambda name: True
    agent.heartbeat()
    e = {'hub': hub_url, 'h': h, 'id': enrolled['device_id'], 'agent': agent, 'mod': mod,
         'image': reg_url.replace('http://', '') + '/owner/caracal-node'}
    yield e
    images.REGISTRY_SCHEME = 'https'


def api(env, method, path, ok=True, **kw):
    r = requests.request(method, env['hub'] + path, headers=env['h'], timeout=30, **kw)
    if ok:
        assert r.status_code < 400, r.text
    return r


def command_result(env, cid):
    return next(x for x in api(env, 'GET', f"/api/commands?device_id={env['id']}").json() if x['id'] == cid)


def test_image_required_before_install(env):
    api(env, 'PUT', '/api/node-image', json={'image': ''})
    r = api(env, 'POST', '/api/provision', ok=False, json={'mode': 'node', 'host': '127.0.0.1', 'username': 'pi',
                                                            'password': 'x', 'hub_url': env['hub']})
    assert r.json()['detail'] == 'node_image_missing'
    assert api(env, 'PUT', '/api/node-image', ok=False, json={'image': 'Not An Image!'}).json()['detail'] == 'invalid_image'


def test_versions_from_registry(env):
    data = api(env, 'PUT', '/api/node-image', json={'image': env['image']}).json()
    assert data['image'] == env['image'] and not data['error']
    # newest real version first, digests left out, moving tags at the end
    assert data['versions'] == ['2026.10.10', '2026.10.1', '2026.9.30', 'latest', 'edge']


def test_node_reports_docker_runtime_and_outdated(env):
    env['agent'].heartbeat()
    d = api(env, 'GET', f"/api/devices/{env['id']}").json()
    assert d['runtime'] == 'docker' and d['caracal_version'] == '2026.9.30' and d['caracal_image'] == 'old/image'
    assert 'caracal_outdated' in [a['code'] for a in d['attention']]


def test_docker_update_and_rollback(env):
    agent, calls = env['agent'], []
    agent.run_with_heartbeats = lambda cmd, cwd=None: (calls.append(cmd) or (0, 'pulled'))
    agent.compose = lambda *args, timeout=600: (calls.append(args) or (0, 'up'))
    r = api(env, 'POST', '/api/node-image/deploy', json={'version': '2026.10.10', 'device_ids': [env['id'], 'CRCL-X']}).json()
    assert len(r['command_ids']) == 1 and r['skipped'][0]['reason'] == 'device_not_found'
    agent.run_commands()
    assert command_result(env, r['command_ids'][0])['state'] == 'completed'
    envfile = (NODE_DIR / '.env').read_text()
    assert 'CARACAL_VERSION=2026.10.10' in envfile and f"CARACAL_IMAGE={env['image']}" in envfile
    assert 'CARACAL_UID=1000' in envfile and any('pull' in c for c in calls)
    # the update brings the hub's compose file (sound for the overlay) and the audio group
    hub_compose = (ROOT / 'hub' / 'bootstrap' / 'caracal-compose.yml').read_text()
    assert (NODE_DIR / 'compose.yml').read_text() == hub_compose and '/dev/snd' in hub_compose
    assert 'CARACAL_AUDIO_GID=' in envfile
    agent.heartbeat()
    d = api(env, 'GET', f"/api/devices/{env['id']}").json()
    assert d['caracal_version'] == '2026.10.10' and 'caracal_outdated' not in [a['code'] for a in d['attention']]

    # the new containers do not start -> previous version restored
    agent.compose = lambda *args, timeout=600: (1, 'container exited') if args[:1] == ('up',) else (0, '')
    r = api(env, 'POST', '/api/node-image/deploy', json={'version': '2026.10.1', 'device_ids': [env['id']]}).json()
    (NODE_DIR / 'compose.yml').write_text('services: {old: {}}\n')
    agent.run_commands()
    row = command_result(env, r['command_ids'][0])
    assert row['state'] == 'failed' and 'previous version restored' in row['result']
    assert 'CARACAL_VERSION=2026.10.10' in (NODE_DIR / '.env').read_text()
    assert (NODE_DIR / 'compose.yml').read_text() == 'services: {old: {}}\n'

    # the image cannot be downloaded -> nothing changes
    agent.run_with_heartbeats = lambda cmd, cwd=None: (1, 'manifest unknown')
    r = api(env, 'POST', '/api/node-image/deploy', json={'version': '9.9.9', 'device_ids': [env['id']]}).json()
    agent.run_commands()
    assert 'nothing changed' in command_result(env, r['command_ids'][0])['result']
    assert 'CARACAL_VERSION=2026.10.10' in (NODE_DIR / '.env').read_text()
    assert (NODE_DIR / 'compose.yml').read_text() == 'services: {old: {}}\n'

    # download source "fleet": the image comes from the hub (docker load), nothing is pulled from the registry
    calls.clear()
    agent.run_with_heartbeats = lambda cmd, cwd=None: (calls.append(cmd) or (0, 'pulled'))
    agent.compose = lambda *args, timeout=600: (calls.append(args) or (0, 'up'))
    agent.load_image_from_hub = lambda version: (calls.append(('hub', version)) or (0, 'Loaded image'))
    agent.conf['download_source'] = 'fleet'
    try:
        r = api(env, 'POST', '/api/node-image/deploy', json={'version': '2026.10.1', 'device_ids': [env['id']]}).json()
        agent.run_commands()
        assert command_result(env, r['command_ids'][0])['state'] == 'completed'
        assert ('hub', '2026.10.1') in calls and not any('pull' in c for c in calls)
    finally:
        agent.conf['download_source'] = 'internet'


def test_convert_only_classic_nodes(env):
    classic = requests.post(env['hub'] + '/api/device/enroll', json={
        'enroll_token': 'enroll-test-token', 'fingerprint': 'classic-node', 'name': 'Classic'}).json()
    requests.post(f"{env['hub']}/api/device/{classic['device_id']}/heartbeat", headers={
        'X-Device-Token': classic['device_token']}, json={'version': '4.5.0', 'runtime': 'host',
                                                        'caracal_version': '2026.10.06', 'api_ok': True})
    r = api(env, 'POST', '/api/node-image/convert', json={'version': '2026.10.10',
                                                         'device_ids': [classic['device_id'], env['id']]}).json()
    assert len(r['command_ids']) == 1 and r['skipped'] == [{'device_id': env['id'], 'reason': 'already_docker'}]
    r = api(env, 'POST', '/api/node-image/deploy', json={'version': '2026.10.10', 'device_ids': [classic['device_id']]}).json()
    assert r['skipped'][0]['reason'] == 'not_docker'


def test_agent_conversion_runs_install_node(env, tmp_path):
    agent, seen = env['agent'], {}

    def fake_run(cmd, cwd=None):
        seen['cmd'], seen['files'] = cmd, sorted(os.listdir(cwd))
        return 0, 'converted'
    agent.run_with_heartbeats = fake_run
    res = agent.do_convert_to_docker({'image': env['image'], 'version': '2026.10.10'})
    assert seen['files'] == ['caracal-compose.yml', 'install-node.sh']
    assert '--skip-agent' in seen['cmd'] and env['image'] in seen['cmd'] and '2026.10.10' in seen['cmd']
    assert res['runtime'] == 'docker'


def test_reboot_requested_in_node_ui(env):
    agent = env['agent']
    agent.state.pop('reboot_request_seen', None)
    agent.check_node_requests({'requests': {'reboot': 3}})      # first sight: only remembered
    assert not agent.reboot_pending
    agent.check_node_requests({'requests': {'reboot': 4}})
    assert agent.reboot_pending
    agent.reboot_pending = False


def test_network_discovery(env):
    port = ssh_banner_server()
    assert api(env, 'POST', '/api/discover', ok=False, json={'cidr': '8.8.8.0/24'}).json()['detail'] == 'invalid_network'
    assert api(env, 'POST', '/api/discover', ok=False, json={'cidr': '10.0.0.0/16'}).json()['detail'] == 'invalid_network'
    job = api(env, 'POST', '/api/discover', json={'cidr': '127.0.0.1/32', 'port': port}).json()['job_id']
    for _ in range(50):
        j = api(env, 'GET', f'/api/jobs/{job}').json()
        if j['state'] == 'completed':
            break
        time.sleep(0.1)
    found = json.loads(j['result_json'])
    assert found[0]['ip'] == '127.0.0.1' and found[0]['system'] == 'Raspberry Pi OS'
    assert found[0]['device_id']   # agents of the test nodes report 127.0.0.1, so it is recognised as known


def test_node_install_job_and_bootstrap_files(env):
    r = api(env, 'POST', '/api/provision', json={'mode': 'node', 'host': '127.0.0.1', 'port': 1, 'username': 'pi',
                                                  'password': 'x', 'hub_url': env['hub']}).json()
    job = api(env, 'GET', f"/api/jobs/{r['job_id']}").json()
    assert job['kind'] == 'node_install'
    for name in ('install-node.sh', 'caracal-compose.yml'):
        assert requests.get(f"{env['hub']}/api/bootstrap/{name}").status_code == 200
