"""CARACAL update through Fleet: release upload, installation by the agent, verification and rollback."""
import io
import os
import shutil
import zipfile

import pytest
import requests

from conftest import TMP, load_agent, serve
from dev.mock_node import create_app

PW = 'admin-password-123'
CARACAL_DIR = TMP / 'opt' / 'caracal'

# Stand-in for CARACAL's install.sh: replaces the application folder (the real one also keeps
# /var/lib/caracal and restarts the services). A FAIL marker simulates a broken installation.
INSTALL_SH = """#!/bin/bash
set -e
BASE=$(cd "$(dirname "$0")" && pwd)
if [ -f "$BASE/FAIL" ]; then rm -rf "$CARACAL_DIR"; echo 'simulated failure'; exit 7; fi
rm -rf "$CARACAL_DIR"; mkdir -p "$CARACAL_DIR"; cp -R "$BASE"/. "$CARACAL_DIR"/
echo "installed $(cat "$BASE/VERSION")"
"""


def release(version, fail=False, folder='caracal-master-abc123/'):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr(folder + 'install.sh', INSTALL_SH)
        z.writestr(folder + 'app/main.py', '# CARACAL ' + version)
        z.writestr(folder + 'VERSION', version + '\n')
        if fail:
            z.writestr(folder + 'FAIL', '')
    return buf.getvalue()


@pytest.fixture(scope='module')
def env(hub_app):
    if not shutil.which('bash'):
        pytest.skip('bash not available')
    os.environ['CARACAL_DIR'] = str(CARACAL_DIR)
    (CARACAL_DIR / 'app').mkdir(parents=True, exist_ok=True)
    (CARACAL_DIR / 'app' / 'main.py').write_text('# CARACAL old')
    (CARACAL_DIR / 'VERSION').write_text('2026.09.1')
    mod = load_agent()
    hub_url, _ = serve(hub_app)
    h = {'Authorization': 'Bearer ' + requests.post(hub_url + '/api/login',
                                                    json={'username': 'admin', 'password': PW}).json()['token']}
    enrolled = requests.post(hub_url + '/api/device/enroll', json={
        'enroll_token': 'enroll-test-token', 'fingerprint': 'update-node', 'name': 'Update node'}).json()
    node_url, _ = serve(create_app(enrolled['device_token']))
    agent = mod.Agent({'hub': hub_url, 'local_api': node_url, **enrolled})
    agent.service_active = lambda name: True
    agent.heartbeat()
    return {'hub': hub_url, 'h': h, 'id': enrolled['device_id'], 'agent': agent, 'mod': mod}


def upload(env, data, **headers):
    return requests.post(env['hub'] + '/api/node-releases', data=data,
                         headers={**env['h'], 'X-File-Name': 'caracal.zip', **headers})


def device(env):
    return requests.get(f"{env['hub']}/api/devices/{env['id']}", headers=env['h']).json()


def deploy_and_run(env, rid):
    r = requests.post(f"{env['hub']}/api/node-releases/{rid}/deploy", headers=env['h'],
                      json={'device_ids': [env['id'], 'CRCL-NONE']})
    assert r.status_code == 200 and len(r.json()['command_ids']) == 1, r.text
    assert r.json()['skipped'][0]['reason'] == 'device_not_found'
    env['agent'].run_commands()
    env['agent'].heartbeat()
    cmds = requests.get(f"{env['hub']}/api/commands?device_id={env['id']}", headers=env['h']).json()
    return next(x for x in cmds if x['id'] == r.json()['command_ids'][0])


def test_release_validation(env):
    assert upload(env, b'not an archive').json()['detail'] == 'invalid_release'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('readme.txt', 'x')
    assert upload(env, buf.getvalue()).json()['detail'] == 'invalid_release'


def test_version_reported_and_outdated(env):
    assert device(env)['caracal_version'] == '2026.09.1'
    assert upload(env, release('2026.10.1')).status_code == 200
    assert upload(env, release('2026.10.1')).status_code == 409   # same version twice
    d = device(env)
    assert 'caracal_outdated' in [a['code'] for a in d['attention']]


def test_update_installs_new_version(env):
    rid = next(r['id'] for r in requests.get(env['hub'] + '/api/node-releases', headers=env['h']).json()
               if r['version'] == '2026.10.1')
    row = deploy_and_run(env, rid)
    assert row['state'] == 'completed', row['result']
    assert (CARACAL_DIR / 'VERSION').read_text().strip() == '2026.10.1'
    d = device(env)
    assert d['caracal_version'] == '2026.10.1' and 'caracal_outdated' not in [a['code'] for a in d['attention']]
    assert not d['maintenance']


def test_failed_update_restores_previous_version(env):
    rid = upload(env, release('2026.11.0', fail=True)).json()['id']
    row = deploy_and_run(env, rid)
    assert row['state'] == 'failed' and 'previous version restored' in row['result']
    assert (CARACAL_DIR / 'VERSION').read_text().strip() == '2026.10.1'
    assert (CARACAL_DIR / 'app' / 'main.py').read_text() == '# CARACAL 2026.10.1'


def test_update_requires_manage_role_and_online_node(env):
    requests.post(env['hub'] + '/api/users', headers=env['h'],
                  json={'username': 'op-upd', 'password': 'long-password-1', 'role': 'operator'})
    op = {'Authorization': 'Bearer ' + requests.post(env['hub'] + '/api/login', json={
        'username': 'op-upd', 'password': 'long-password-1'}).json()['token']}
    rid = requests.get(env['hub'] + '/api/node-releases', headers=env['h']).json()[0]['id']
    assert requests.post(f"{env['hub']}/api/node-releases/{rid}/deploy", headers=op,
                         json={'device_ids': [env['id']]}).status_code == 403


def test_unsafe_archive_is_rejected_by_agent(env, tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('../evil.sh', 'x')
        z.writestr('install.sh', 'x')
        z.writestr('app/main.py', 'x')
    pkg = tmp_path / 'evil.zip'
    pkg.write_bytes(buf.getvalue())
    with pytest.raises(RuntimeError):
        env['mod'].unpack_release(str(pkg), tmp_path / 'out')
