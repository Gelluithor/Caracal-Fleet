"""Reinstalling CARACAL on a node, the diagnostics report and websites in dark mode (the node's setting, a choice per
website, global playlists and templates)."""
import json
import os

from conftest import load_agent

CAPS = {'notify': True, 'notify_settings': True, 'overlay': True, 'upload': True, 'add_grafana_tag': True,
        'player_settings': True}


def enroll(client, name, version='4.13.0', caps=CAPS, **extra):
    dev = client.post('/api/device/enroll', json={'enroll_token': 'enroll-test-token', 'fingerprint': 'mnt-' + name,
                                                  'name': name}).json()
    beat(client, dev, version=version, caps=caps, **extra)
    return dev


def beat(client, dev, version='4.13.0', caps=CAPS, **extra):
    r = client.post(f"/api/device/{dev['device_id']}/heartbeat", headers={'X-Device-Token': dev['device_token']},
                    json={'version': version, 'api_ok': True, 'capabilities': caps, 'runtime': 'docker', **extra})
    assert r.status_code == 200, r.text


def command(client, h, dev, action, payload=None):
    return client.post(f"/api/devices/{dev['device_id']}/commands", headers=h, json={'action': action, 'payload': payload or {}})


def delivered(client, dev):
    return client.get(f"/api/device/{dev['device_id']}/commands", headers={'X-Device-Token': dev['device_token']}).json()


def finish(client, dev, cid, result, ok=True):
    r = client.post(f"/api/device/{dev['device_id']}/commands/{cid}/result", headers={'X-Device-Token': dev['device_token']},
                    json={'ok': ok, 'result': result})
    assert r.status_code == 200, r.text


def pending_template(did):
    from app.core import db
    with db() as c:
        return c.execute('SELECT pending_template FROM devices WHERE id=?', (did,)).fetchone()[0]


def test_reinstall_needs_a_new_agent_and_keeps_the_template_for_later(client, admin_headers):
    h = admin_headers
    assert client.put('/api/node-image', headers=h, json={'image': 'ghcr.io/owner/caracal-node'}).status_code == 200
    old = enroll(client, 'old-agent', version='4.12.0')
    assert command(client, h, old, 'reinstall_node', {'version': '2026.10.12'}).json()['detail'] == 'agent_update_required'
    assert command(client, h, old, 'diagnostics').json()['detail'] == 'agent_update_required'

    dev = enroll(client, 'reinstall')
    assert command(client, h, dev, 'reinstall_node', {}).json()['detail'] == 'version_required'
    assert command(client, h, dev, 'reinstall_node', {'version': '2026.10.12', 'template': 987654}).json()['detail'] == 'template_not_found'
    assert command(client, h, dev, 'reinstall_node', {'version': '2026.10.12', 'wipe_data': True, 'username': 'spravce',
                                                      'password': 'short'}).json()['detail'] == 'admin_password_short'
    tid = client.post('/api/templates', headers=h, json={'name': 'After reinstall', 'overlay': {'enabled': True, 'size': 24}}).json()['id']
    r = command(client, h, dev, 'reinstall_node', {'version': '2026.10.12', 'wipe_data': True, 'username': 'spravce',
                                                   'password': 'reinstall-pass-1', 'template': tid})
    assert r.status_code == 200, r.text
    cid = r.json()['id']
    got = delivered(client, dev)[0]
    assert got['payload'] == {'version': '2026.10.12', 'image': 'ghcr.io/owner/caracal-node', 'wipe_data': True,
                              'username': 'spravce', 'password': 'reinstall-pass-1', 'template': tid}
    # the old installation still reports: the template must wait until the reinstallation is done
    beat(client, dev)
    assert pending_template(dev['device_id']) is None
    finish(client, dev, cid, {'version': '2026.10.12', 'runtime': 'docker'})
    assert pending_template(dev['device_id']) == tid
    listed = next(x for x in client.get(f"/api/commands?device_id={dev['device_id']}", headers=h).json() if x['id'] == cid)
    assert json.loads(listed['payload_json'])['password'] == '•••'
    beat(client, dev)   # the node is back: the template is applied
    assert pending_template(dev['device_id']) is None
    assert [c['action'] for c in delivered(client, dev)] == ['overlay_settings']

    # a failed reinstallation applies no template
    r = command(client, h, dev, 'reinstall_node', {'version': '2026.10.12', 'template': tid})
    delivered(client, dev)
    finish(client, dev, r.json()['id'], 'install-node.sh failed (exit 4)', ok=False)
    assert pending_template(dev['device_id']) is None


def test_diagnostics_report_is_kept_whole(client, admin_headers):
    h = admin_headers
    dev = enroll(client, 'diagnostics')
    cid = command(client, h, dev, 'diagnostics', {'anything': 1}).json()['id']
    assert delivered(client, dev)[0]['payload'] == {}
    report = {'sections': [{'title': f'Section {i}', 'output': 'x' * 5000} for i in range(12)]}
    finish(client, dev, cid, report)
    row = client.get(f'/api/commands/{cid}', headers=h).json()
    assert json.loads(row['result']) == report   # longer than other results, still valid JSON


def test_dark_websites(client, admin_headers):
    h = admin_headers
    dev = enroll(client, 'dark', player_settings={'web_dark': 'force'})
    assert client.get(f"/api/devices/{dev['device_id']}", headers=h).json()['player_settings'] == {'web_dark': 'force'}
    assert command(client, h, dev, 'player_settings', {'web_dark': 'night'}).json()['detail'] == 'invalid_value'
    assert command(client, h, dev, 'player_settings', {'web_dark': 'dark', 'extra': 1}).status_code == 200
    assert command(client, h, dev, 'add_web', {'name': 'W', 'source': 'https://example.com', 'dark': 'nope'}).json()['detail'] == 'invalid_value'
    assert command(client, h, dev, 'add_web', {'name': 'W', 'source': 'https://example.com', 'dark': 'force'}).status_code == 200
    assert command(client, h, dev, 'add_web', {'name': 'W2', 'source': 'https://example.com', 'dark': None}).status_code == 200
    cmds = {c['payload'].get('name', c['action']): c['payload'] for c in delivered(client, dev)}
    assert cmds['player_settings'] == {'web_dark': 'dark'} and cmds['W']['dark'] == 'force' and cmds['W2']['dark'] == ''
    # a node without the setting (older CARACAL) is not sent it
    older = enroll(client, 'dark-old', caps={**CAPS, 'player_settings': False})
    assert command(client, h, older, 'player_settings', {'web_dark': 'dark'}).json()['detail'] == 'update_required'

    # global playlists keep the choice of a website and send it only when it is set
    pid = client.post('/api/global-playlists', headers=h, json={'name': 'Dark loop'}).json()['id']
    assert client.post(f'/api/global-playlists/{pid}/items', headers=h, json={'kind': 'web', 'name': 'x', 'source': 'https://e.com',
                                                                            'dark': 'black'}).json()['detail'] == 'invalid_value'
    a = client.post(f'/api/global-playlists/{pid}/items', headers=h, json={'kind': 'web', 'name': 'Night', 'source': 'https://e.com', 'dark': 'dark'}).json()['id']
    client.post(f'/api/global-playlists/{pid}/items', headers=h, json={'kind': 'web', 'name': 'Plain', 'source': 'https://e.com/p'})
    assert client.patch(f'/api/global-playlists/{pid}/items/{a}', headers=h, json={'dark': 'force'}).status_code == 200
    items = {x['name']: x for x in client.get(f'/api/global-playlists/{pid}', headers=h).json()['items']}
    assert items['Night']['dark'] == 'force' and items['Plain']['dark'] == ''
    from app.core import db
    from app.playlists import deploy_items
    with db() as c:
        sent = {x['name']: x for x in deploy_items(c, pid)}
    assert sent['Night']['dark'] == 'force' and 'dark' not in sent['Plain']

    # templates carry the node's setting
    assert client.post('/api/templates', headers=h, json={'name': 'x', 'player': {'web_dark': 'dim'}}).json()['detail'] == 'invalid_value'
    tid = client.post('/api/templates', headers=h, json={'name': 'Dark', 'player': {'web_dark': 'dark'}}).json()['id']
    r = client.post(f'/api/templates/{tid}/apply', headers=h, json={'target_ids': [dev['device_id']]}).json()
    assert r['results'][0]['queued'] == ['player_settings']
    copied = client.post('/api/templates/from-device', headers=h, json={'device_id': dev['device_id'], 'name': 'Copy'}).json()['id']
    assert client.get(f'/api/templates/{copied}', headers=h).json()['player'] == {'web_dark': 'force'}


def test_agent_reinstall_diagnostics_and_dark_mode(tmp_path):
    mod = load_agent()
    agent = mod.Agent({'hub': 'http://127.0.0.1:9', 'local_api': 'http://127.0.0.1:9', 'device_id': 'CRCL-T',
                       'device_token': 'token'})
    seen = {}

    class Resp:
        content = b'#!/bin/bash\n'

        def raise_for_status(self):
            pass
    agent.session.get = lambda url, timeout=60: Resp()

    def fake_run(cmd, cwd=None):
        seen['cmd'], seen['files'] = cmd, sorted(os.listdir(cwd))
        return 0, 'reinstalled'
    agent.run_with_heartbeats = fake_run
    mod.write_key_file = lambda token: None
    agent.do_reinstall_node({'image': 'ghcr.io/o/caracal-node', 'version': '2026.10.12', 'wipe_data': True})
    assert seen['files'] == ['caracal-compose.yml', 'install-node.sh']
    assert seen['cmd'][-2:] == ['--reinstall', '--wipe-data'] and '--skip-agent' in seen['cmd']
    agent.do_reinstall_node({'image': 'ghcr.io/o/caracal-node', 'version': '2026.10.12'})
    assert seen['cmd'][-1] == '--reinstall' and '--wipe-data' not in seen['cmd']
    agent.do_convert_to_docker({'image': 'ghcr.io/o/caracal-node', 'version': '2026.10.12'})
    assert '--reinstall' not in seen['cmd']

    report = agent.do_diagnostics({})
    assert report['version'] == mod.VERSION and report['sections']
    assert all(set(s) == {'title', 'output'} and len(s['output']) <= 6000 for s in report['sections'])
    assert 'Disk' in [s['title'] for s in report['sections']]

    sent = []
    agent.local = lambda name, ids=None, timeout=30, **kw: sent.append((name, kw.get('json'))) or type('R', (), {'json': lambda self: {'ok': True}})()
    agent.do_player_settings({'web_dark': 'dark', 'other': 1})
    agent.do_add_web({'name': 'W', 'source': 'https://e.com', 'dark': ''})
    agent.do_add_collection({'name': 'G', 'grafana_url': 'https://g', 'tag': 't', 'dark': 'dark'})
    assert sent[0] == ('player_settings', {'web_dark': 'dark'})
    assert sent[1][1]['dark'] == '' and sent[2][1]['dark'] == 'dark'


def test_installer_reinstall_options():
    from conftest import ROOT
    src = (ROOT / 'hub' / 'bootstrap' / 'install-node.sh').read_text()
    assert '--reinstall) REINSTALL=1' in src and '--wipe-data) WIPE_DATA=1' in src
    assert "--wipe-data needs --reinstall" in src
    # the containers and images go first, the data only when asked
    assert src.index('docker compose down --remove-orphans --rmi all') < src.index("step '[3/7] User and data folders'")
    assert 'find /var/lib/caracal -mindepth 1 -maxdepth 1 -exec rm -rf {} +' in src
