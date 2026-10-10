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


def test_login_profiles(env):
    """Logins of web pages: created from Fleet, encrypted on the node, used by the player, never kept in the hub."""
    from app.core import db
    assert device(env)['capabilities']['add_profile'] is True
    zabbix = {'name': 'Zabbix', 'login_url': 'https://zabbix.example/index.php',
              'target_url': 'https://zabbix.example/zabbix.php?action=dashboard.view', 'username': 'monitor',
              'password': 'S3cret "pass"', 'user_selector': '#name', 'pass_selector': '#password',
              'submit_selector': '#enter'}
    row = run(env, 'add_profile', zabbix)
    pid = json.loads(row['result'])['id']
    stored = env['mod'].rows('SELECT * FROM auth_profiles WHERE id=?', (pid,))[0]
    assert b'S3cret' not in stored['password_enc'] and stored['user_selector'] == '#name'
    # the player gets the decrypted login (localhost only)
    player = requests.get(f"{env['node']}/api/player/profile/{pid}").json()
    assert player['username'] == 'monitor' and player['password'] == 'S3cret "pass"'
    # the hub keeps no credentials: history, audit and the stored command
    assert 'S3cret' not in row['payload_json'] and 'monitor' not in row['payload_json']
    with db() as c:
        assert 'S3cret' not in c.execute('SELECT payload_json FROM commands WHERE id=?', (row['id'],)).fetchone()[0]
        assert not c.execute("SELECT 1 FROM audit WHERE detail LIKE '%S3cret%' OR detail LIKE '%monitor%'").fetchone()
    profile = next(p for p in device(env)['profiles'] if p['id'] == pid)
    assert profile['target_url'] == zabbix['target_url'] and 'password' not in profile and 'username' not in profile

    # a web page with the login, then without it
    run(env, 'add_web', {'name': 'Dohled', 'source': zabbix['target_url'], 'duration': 30, 'auth_profile_id': pid})
    page = next(x for x in node_assets(env) if x['name'] == 'Dohled')
    assert page['auth_profile_id'] == pid
    run(env, 'update_asset', {'id': page['id'], 'auth_profile_id': None})
    assert next(x for x in node_assets(env) if x['id'] == page['id'])['auth_profile_id'] is None
    run(env, 'update_asset', {'id': page['id'], 'auth_profile_id': pid, 'name': 'Dohled 2'})
    assert next(x for x in node_assets(env) if x['id'] == page['id'])['auth_profile_id'] == pid

    # editing without credentials keeps them, a new password replaces only the password
    run(env, 'update_profile', {'id': pid, 'name': 'Zabbix NOC', 'login_url': zabbix['login_url'],
                                'target_url': zabbix['target_url'], 'user_selector': '', 'password': ''})
    player = requests.get(f"{env['node']}/api/player/profile/{pid}").json()
    assert player['name'] == 'Zabbix NOC' and player['password'] == 'S3cret "pass"' and player['user_selector'] == '#name'
    run(env, 'update_profile', {'id': pid, 'password': 'new-pass'})
    player = requests.get(f"{env['node']}/api/player/profile/{pid}").json()
    assert player['username'] == 'monitor' and player['password'] == 'new-pass' and player['name'] == 'Zabbix NOC'

    # the hub rejects what the node would reject, before queueing
    url = f"{env['hub']}/api/devices/{env['id']}/commands"
    bad = [('add_profile', {**zabbix, 'password': ''}, 'credentials_required'),
           ('add_profile', {**zabbix, 'login_url': 'ftp://x'}, 'invalid_url'),
           ('add_web', {'name': 'x', 'source': 'https://x.example', 'auth_profile_id': 999}, 'profile_not_found'),
           ('delete_profile', {'id': 999}, 'profile_not_found')]
    for action, payload, code in bad:
        r = requests.post(url, headers=env['h'], json={'action': action, 'payload': payload})
        assert r.json().get('detail') == code, (action, r.text)

    # deleting the login keeps the page, without automatic login
    run(env, 'delete_profile', {'id': pid})
    assert not env['mod'].rows('SELECT id FROM auth_profiles WHERE id=?', (pid,))
    assert next(x for x in node_assets(env) if x['id'] == page['id'])['auth_profile_id'] is None
    assert pid not in [p['id'] for p in device(env)['profiles']]
    run(env, 'delete_asset', {'id': page['id']})


def node_notifications(env, where='done=0'):
    return env['mod'].rows(f'SELECT * FROM notifications WHERE {where} ORDER BY id')


def test_notifications_from_fleet(env):
    """Sending, clearing and the settings of on-screen notifications go through the node's Fleet API."""
    d = device(env)
    assert all(d['capabilities'][k] for k in ('notify', 'notify_settings', 'notify_clear', 'add_watcher'))
    assert d['notifications']['settings']['position'] == 'top-right' and d['notifications']['watchers'] == []

    run(env, 'notify', {'title': 'Porada', 'message': 'Za 5 minut v zasedačce', 'level': 'warning', 'sound': True})
    n = node_notifications(env)[-1]
    assert (n['title'], n['level'], n['source'], n['sound']) == ('Porada', 'warning', 'CARACAL Fleet', 1)
    env['agent'].heartbeat()
    assert device(env)['notifications']['waiting'] >= 1

    run(env, 'notify_settings', {'position': 'bottom-left', 'sound': 'critical', 'volume': 40, 'history_max': 100})
    s = env['mod']._ntf_settings()
    assert (s['position'], s['sound'], s['volume'], s['history_max'], s['duration']) == ('bottom-left', 'critical', 40, 100, 8)
    assert device(env)['notifications']['settings']['position'] == 'bottom-left'
    audit = env['mod'].rows("SELECT * FROM notify_audit WHERE action='Nastavení změněno' ORDER BY id DESC LIMIT 1")[0]
    assert audit['actor'] == 'CARACAL Fleet' and 'bottom-left' in audit['detail']

    run(env, 'notify_clear')
    assert node_notifications(env) == []

    # the hub rejects what the node would reject, before queueing
    url = f"{env['hub']}/api/devices/{env['id']}/commands"
    bad = [('notify', {'level': 'info'}, 'notification_text_required'),
           ('notify', {'title': 'x', 'level': 'loud'}, 'invalid_value'),
           ('notify_settings', {'position': 'left'}, 'invalid_value'),
           ('notify_settings', {'volume': 101}, 'invalid_value'),
           ('notify_settings', {'sound_device': 'hw;reboot'}, 'invalid_value'),
           ('notify_settings', {}, 'nothing_to_change')]
    for action, payload, code in bad:
        r = requests.post(url, headers=env['h'], json={'action': action, 'payload': payload})
        assert r.json().get('detail') == code, (action, r.text)
    run(env, 'notify_settings', {'position': 'top-right', 'sound': 'off', 'volume': 70, 'history_max': 500})


def test_notification_look_from_fleet(env):
    """The look of the notifications (colours, shape, a banner...) is edited in Fleet and reaches the node's overlay."""
    d = device(env)
    assert d['notify_style'] and d['notifications']['settings']['style']['fill'] == 'stripe'
    run(env, 'notify_settings', {'style': {'fill': 'solid', 'width': 100, 'align': 'center',
                                           'levels': {'critical': {'color': '#FF0000', 'icon': '★'}}}})
    st = env['mod']._ntf_settings()['style']
    assert (st['fill'], st['width'], st['align'], st['levels']['critical']) == ('solid', 100, 'center', {'color': '#ff0000', 'icon': '★'})
    assert st['bg'] == '#111926' and st['levels']['info']['icon'] == 'ℹ'   # what was not sent keeps its value
    assert device(env)['notifications']['settings']['style']['width'] == 100
    # what the node cannot draw fails on the node with its reason (colour emoji crash Tk on X11)
    r = requests.post(f"{env['hub']}/api/devices/{env['id']}/commands", headers=env['h'],
                      json={'action': 'notify_settings', 'payload': {'style': {'levels': {'info': {'icon': '🔥'}}}}})
    env['agent'].run_commands()
    row = next(x for x in requests.get(f"{env['hub']}/api/commands?device_id={env['id']}", headers=env['h']).json()
               if x['id'] == r.json()['id'])
    assert row['state'] == 'failed' and 'emoji' in row['result']
    assert requests.post(f"{env['hub']}/api/devices/{env['id']}/commands", headers=env['h'],
                         json={'action': 'notify_settings', 'payload': {'style': 'red'}}).json()['detail'] == 'invalid_value'
    run(env, 'notify_settings', {'style': env['mod']._NTF_STYLE})


def test_watchers_from_fleet(env):
    """Watchers are created from Fleet; their credentials reach only the node, encrypted, like login profiles."""
    from fastapi import FastAPI
    from app.core import db
    api = FastAPI()
    tickets = [{'id': 1, 'subject': 'Tiskárna'}]
    api.get('/tickets')(lambda: {'items': tickets})
    api_url, _ = serve(api)

    row = run(env, 'add_watcher', {'name': 'Helpdesk', 'url': api_url + '/tickets', 'auth_type': 'bearer',
                                   'secret': 'T0ken-secret', 'list_path': 'items', 'id_field': 'id',
                                   'title_template': 'Ticket #{id}: {subject}', 'interval': 600,
                                   # off: the node's background loop would check a new watcher at once and race
                                   # with the checks below; "check now" works for watchers that are off as well
                                   'enabled': False})
    wid = json.loads(row['result'])['id']
    stored = env['mod'].rows('SELECT * FROM notify_watchers WHERE id=?', (wid,))[0]
    assert b'T0ken-secret' not in stored['credentials_enc'] and env['mod']._wch_credentials(stored)['secret'] == 'T0ken-secret'
    # the hub keeps no credentials: history, audit, the stored command and the reported state
    assert 'T0ken' not in row['payload_json']
    with db() as c:
        assert 'T0ken' not in c.execute('SELECT payload_json FROM commands WHERE id=?', (row['id'],)).fetchone()[0]
        assert not c.execute("SELECT 1 FROM audit WHERE detail LIKE '%T0ken%'").fetchone()
        assert 'T0ken' not in c.execute('SELECT status_json FROM devices WHERE id=?', (env['id'],)).fetchone()[0]
    w = next(x for x in device(env)['notifications']['watchers'] if x['id'] == wid)
    assert w['name'] == 'Helpdesk' and w['has_credentials'] and 'secret' not in w and 'seen' not in w

    # the first check remembers, the next one announces a new ticket
    assert json.loads(run(env, 'check_watcher', {'id': wid})['result'])['first'] is True
    tickets.append({'id': 2, 'subject': 'Wi-Fi'})
    assert json.loads(run(env, 'check_watcher', {'id': wid})['result'])['new'] == 1
    assert node_notifications(env)[-1]['title'] == 'Ticket #2: Wi-Fi'

    # edits without credentials keep them; turning on and off, deleting
    run(env, 'update_watcher', {'id': wid, 'name': 'Helpdesk 2', 'secret': ''})
    stored = env['mod'].rows('SELECT * FROM notify_watchers WHERE id=?', (wid,))[0]
    assert stored['name'] == 'Helpdesk 2' and env['mod']._wch_credentials(stored)['secret'] == 'T0ken-secret'
    run(env, 'update_watcher', {'id': wid, 'enabled': True})
    assert env['mod'].rows('SELECT enabled FROM notify_watchers WHERE id=?', (wid,))[0]['enabled']
    run(env, 'update_watcher', {'id': wid, 'enabled': False})
    assert not env['mod'].rows('SELECT enabled FROM notify_watchers WHERE id=?', (wid,))[0]['enabled']
    url = f"{env['hub']}/api/devices/{env['id']}/commands"
    for action, payload, code in [('add_watcher', {'name': 'x', 'url': 'ftp://x'}, 'invalid_url'),
                                  ('add_watcher', {'url': 'https://x.example'}, 'name_required'),
                                  ('add_watcher', {'name': 'x', 'url': 'https://x.example', 'interval': 5}, 'invalid_value'),
                                  ('delete_watcher', {'id': 999}, 'watcher_not_found')]:
        r = requests.post(url, headers=env['h'], json={'action': action, 'payload': payload})
        assert r.json().get('detail') == code, (action, r.text)
    run(env, 'delete_watcher', {'id': wid})
    assert not env['mod'].rows('SELECT id FROM notify_watchers WHERE id=?', (wid,))
    run(env, 'notify_clear')


def test_http_login_profile(env):
    """HTTP Basic/Digest log-ins (the browser pop-up) need only the server address and the credentials."""
    row = run(env, 'add_profile', {'auth_type': 'http', 'name': 'Router', 'target_url': 'https://router.example/',
                                   'username': 'admin', 'password': 'R0uter-pw'})
    pid = json.loads(row['result'])['id']
    player = requests.get(f"{env['node']}/api/player/profile/{pid}").json()
    assert player['auth_type'] == 'http' and player['login_url'] == 'https://router.example/' and player['password'] == 'R0uter-pw'
    assert next(p for p in device(env)['profiles'] if p['id'] == pid)['auth_type'] == 'http'
    assert 'R0uter' not in row['payload_json']
    run(env, 'update_profile', {'id': pid, 'name': 'Router 2'})
    assert requests.get(f"{env['node']}/api/player/profile/{pid}").json()['auth_type'] == 'http'
    run(env, 'delete_profile', {'id': pid})


def test_notification_api_for_apps(env):
    """An app sends one request to Fleet; Fleet queues it for the screens of the token's scope."""
    from app import notify
    h, hub = env['h'], env['hub']
    created = requests.post(hub + '/api/notify-tokens', headers=h, json={'name': 'Grafana', 'scope': {'all': True}, 'rate_per_min': 3}).json()
    token, tid = created['token'], created['id']
    listed = requests.get(hub + '/api/notify-tokens', headers=h).json()
    assert [x['name'] for x in listed] == ['Grafana'] and 'token_hash' not in listed[0] and listed[0]['prefix'] == token[:10]

    send = lambda body=None, **kw: requests.post(hub + '/api/notify', json=body, **kw)
    r = send({'title': 'Deploy', 'message': 'Verze 2.4', 'level': 'warning'}, headers={'Authorization': 'Bearer ' + token})
    # other tests' devices share the hub: the ones without notifications are skipped, not failed
    assert r.status_code == 200 and env['id'] in r.json()['devices'], r.text
    assert all(x['reason'] == 'notifications_unsupported' for x in r.json()['skipped'])
    requests.patch(f'{hub}/api/notify-tokens/{tid}', headers=h, json={'scope': {'devices': [env['id']]}})
    env['agent'].run_commands()
    assert node_notifications(env)[-1]['title'] == 'Deploy'
    cmd = requests.get(f"{hub}/api/commands?device_id={env['id']}", headers=h).json()[0]
    assert cmd['action'] == 'notify' and cmd['username'] == 'api:Grafana'

    # Grafana webhook body, forwarded unchanged and parsed by the node; Basic auth with the token as password
    grafana = {'status': 'firing', 'alerts': [{'status': 'firing', 'labels': {'alertname': 'CPU', 'severity': 'critical'},
                                               'annotations': {'summary': 'CPU 99 %'}, 'fingerprint': 'f1'}]}
    assert send(grafana, auth=('grafana', token)).status_code == 200
    # plain text with headers, token in X-Caracal-Token
    r = requests.post(hub + '/api/notify', data='Záloha hotová'.encode(), headers={'X-Caracal-Token': token, 'Title': 'Záloha', 'X-Level': 'success', 'Content-Type': 'text/plain; charset=utf-8'})
    assert r.status_code == 200, r.text
    env['agent'].run_commands()
    last = node_notifications(env)[-2:]
    assert (last[0]['title'], last[0]['level']) == ('CPU', 'critical') and (last[1]['title'], last[1]['level']) == ('Záloha', 'success')

    # rate limit, wrong tokens, scope and narrowing
    assert send({'title': 'x'}, headers={'Authorization': 'Bearer ' + token}).json()['detail'] == 'rate_limited'
    notify._hits.clear()
    assert send({'title': 'x'}, headers={'Authorization': 'Bearer cft_wrong'}).status_code == 401
    assert send({'title': 'x'}).status_code == 401
    assert send({'level': 'info'}, headers={'Authorization': 'Bearer ' + token}).json()['detail'] == 'notification_text_required'
    assert send({'title': 'x'}, headers={'Authorization': 'Bearer ' + token}, params={'group': 'Nikde'}).json()['detail'] == 'no_devices'
    requests.patch(f'{hub}/api/notify-tokens/{tid}', headers=h, json={'scope': {'groups': ['Recepce']}})
    assert send({'title': 'x'}, headers={'Authorization': 'Bearer ' + token}).json()['detail'] == 'no_devices'
    requests.patch(f'{hub}/api/notify-tokens/{tid}', headers=h, json={'scope': {'devices': [env['id']]}, 'enabled': False})
    assert send({'title': 'x'}, headers={'Authorization': 'Bearer ' + token}).status_code == 401
    assert requests.post(hub + '/api/notify-tokens', headers=h, json={'name': 'x', 'scope': {}}).json()['detail'] == 'invalid_scope'
    assert requests.delete(f'{hub}/api/notify-tokens/{tid}', headers=h).status_code == 200
    from app.core import db
    with db() as c:
        assert not c.execute('SELECT 1 FROM audit WHERE detail LIKE ?', ('%' + token + '%',)).fetchone()
        actions = [x[0] for x in c.execute("SELECT action FROM audit WHERE action LIKE 'notify%'")]
    assert {'notify_token.create', 'notify_token.edit', 'notify_token.delete', 'notify.token_rejected'} <= set(actions)
    env['agent'].run_commands()
    run(env, 'notify_clear')


def test_notification_sounds(env):
    """A custom MP3 per level: uploaded to the hub, sent to the node, served to the overlay, reset to the chime."""
    h, hub = env['h'], env['hub']
    assert device(env)['capabilities']['notify_sound'] is True
    mp3 = b'ID3\x03\x00\x00\x00\x00\x00\x00' + b'\xff\xfb\x90\x00' * 500
    up = requests.post(hub + '/api/files', data=mp3, headers={**h, 'Content-Type': 'audio/mpeg', 'X-File-Name': 'gong.mp3'}).json()
    assert up['kind'] == 'audio'
    run(env, 'notify_sound', {'level': 'critical', 'file_id': up['id']})
    stored = env['data'] / 'notify-sounds' / 'critical.mp3'
    assert stored.read_bytes() == mp3
    assert device(env)['notifications']['sounds']['critical']['name'] == 'gong.mp3'
    # the overlay learns that the level has its own sound and downloads it from the device itself
    overlay = requests.get(env['node'] + '/api/notify/overlay').json()
    assert overlay['sounds']['critical'] and requests.get(env['node'] + '/api/notify/sounds/critical/overlay').content == mp3

    url = f"{hub}/api/devices/{env['id']}/commands"
    for action, payload, code in [('notify_sound', {'level': 'loud', 'file_id': up['id']}, 'invalid_value'),
                                  ('notify_sound', {'level': 'info', 'file_id': 'nope'}, 'file_not_found'),
                                  ('add_media', {'file_id': up['id'], 'duration': 10}, 'unsupported_file')]:
        r = requests.post(url, headers=h, json={'action': action, 'payload': payload})
        assert r.json().get('detail') == code, (action, r.text)

    # a file that is no MP3 is refused by the node, the previous sound stays
    fake = requests.post(hub + '/api/files', data=b'not a sound', headers={**h, 'Content-Type': 'audio/mpeg', 'X-File-Name': 'x.mp3'}).json()
    cid = requests.post(url, headers=h, json={'action': 'notify_sound', 'payload': {'level': 'critical', 'file_id': fake['id']}}).json()['id']
    env['agent'].run_commands()
    row = next(x for x in requests.get(f"{hub}/api/commands?device_id={env['id']}", headers=h).json() if x['id'] == cid)
    assert row['state'] == 'failed' and stored.read_bytes() == mp3

    run(env, 'notify_sound', {'level': 'critical', 'reset': True})
    assert not stored.exists() and 'critical' not in device(env)['notifications']['sounds']
    assert 'critical' not in requests.get(env['node'] + '/api/notify/overlay').json()['sounds']


def test_web_administrator_from_fleet(env, tmp_path):
    """The node's web administrator: created (first-run setup) or changed from Fleet; the password never stays in Fleet."""
    from fastapi.testclient import TestClient
    node, h, hub = env['mod'], env['h'], env['hub']
    c = node.con()
    c.execute('DELETE FROM users')
    c.commit()
    c.close()
    env['agent'].heartbeat()
    d = device(env)
    assert d['admin'] == {'configured': False, 'username': ''} and d['capabilities']['admin'] is True
    assert any(a['code'] == 'admin_missing' for a in d['attention'])

    row = run(env, 'set_admin', {'username': 'spravce', 'password': 'first-password-1'})
    assert json.loads(row['payload_json']) == {'username': '•••', 'password': '•••'}   # history and storage
    assert 'first-password-1' not in json.dumps(requests.get(f"{hub}/api/audit", headers=h).json())
    assert json.loads(row['result'])['created'] is True
    d = device(env)
    assert d['admin'] == {'configured': True, 'username': 'spravce'} and not any(a['code'] == 'admin_missing' for a in d['attention'])

    node._login_fails.clear()   # failed logins of test_node_security
    browser = TestClient(node.app)
    assert browser.post('/api/login', data={'username': 'spravce', 'password': 'first-password-1'}).status_code == 200
    assert browser.get('/api/me').status_code == 200
    # a new name and password from Fleet: the old password and the old sessions stop working
    row = run(env, 'set_admin', {'username': 'admin', 'password': 'second-password-2'})
    assert json.loads(row['result'])['created'] is False
    assert browser.get('/api/me').status_code == 401
    assert browser.post('/api/login', data={'username': 'spravce', 'password': 'first-password-1'}).status_code == 401
    assert browser.post('/api/login', data={'username': 'admin', 'password': 'second-password-2'}).status_code == 200
    assert node.rows('SELECT COUNT(*) AS n FROM users')[0]['n'] == 1

    url = f"{hub}/api/devices/{env['id']}/commands"
    for payload, code in [({'username': 'a b', 'password': 'long-enough-1'}, 'invalid_admin_user'),
                          ({'username': 'admin', 'password': 'short'}, 'admin_password_short'),
                          ({'password': 'long-enough-1'}, 'invalid_admin_user')]:
        r = requests.post(url, headers=h, json={'action': 'set_admin', 'payload': payload})
        assert r.json().get('detail') == code, r.text
    # the node itself refuses a weak password from a client that skips the hub
    key = {'X-Fleet-Key': env['key']}
    assert requests.post(env['node'] + '/api/fleet/v1/admin', headers=key, json={'username': 'x', 'password': 'short'}).status_code == 400
    assert requests.post(env['node'] + '/api/fleet/v1/admin', json={'username': 'x', 'password': 'long-enough-1'}).status_code == 401

    # installers pass it to the agent in a file, which is removed afterwards
    agent_mod = load_agent()
    creds = tmp_path / 'admin.json'
    creds.write_text(json.dumps({'username': 'instalace', 'password': 'installer-password-3'}))
    old_conf = agent_mod.CONFIG
    agent_mod.CONFIG = tmp_path / 'agent.json'
    agent_mod.CONFIG.write_text(json.dumps(env['agent'].conf))
    try:
        assert agent_mod.set_admin_from_file(str(creds), wait=5) == 0
    finally:
        agent_mod.CONFIG = old_conf
    assert not creds.exists() and node.rows('SELECT username FROM users')[0]['username'] == 'instalace'


def test_countdown_bar_from_fleet(env):
    run(env, 'overlay_settings', {'enabled': False, 'size': 24})
    state = env['mod']._cc2_read()
    assert (state['overlay_enabled'], state['overlay_size']) == (False, 24)
    assert device(env)['overlay'] == {'enabled': False, 'size': 24}
    run(env, 'overlay_settings', {'enabled': True})
    assert device(env)['overlay'] == {'enabled': True, 'size': 24}
    url = f"{env['hub']}/api/devices/{env['id']}/commands"
    for payload, code in [({'size': 500}, 'invalid_value'), ({}, 'nothing_to_change')]:
        assert requests.post(url, headers=env['h'], json={'action': 'overlay_settings', 'payload': payload}).json()['detail'] == code


def test_notification_queue_history_and_audit_from_fleet(env):
    node = env['mod']
    run(env, 'notify_clear')
    for title in ('První', 'Druhé', 'Třetí'):
        run(env, 'notify', {'title': title, 'level': 'info'})
    queue = device(env)['notifications']['queue']
    assert [x['title'] for x in queue] == ['První', 'Druhé', 'Třetí'] and 'done' not in queue[0]
    run(env, 'notify_remove', {'id': queue[1]['id']})
    assert [x['title'] for x in device(env)['notifications']['queue']] == ['První', 'Třetí']
    # the overlay shows the first one; skipping it ends it
    requests.get(env['node'] + '/api/notify/overlay')
    run(env, 'notify_skip')
    assert node.rows('SELECT done FROM notifications WHERE title=?', ('První',))[0]['done'] == 3

    log = json.loads(run(env, 'notify_log')['result'])
    assert {'První', 'Druhé'} <= {x['title'] for x in log['history']} and log['history_count'] >= 2
    assert any(x['action'] == 'Oznámení odebráno z fronty' and x['actor'] == 'CARACAL Fleet' for x in log['audit'])
    run(env, 'notify_history_clear')
    assert node.rows('SELECT COUNT(*) AS n FROM notifications WHERE done>0')[0]['n'] == 0
    run(env, 'notify_audit_clear')
    assert [x['action'] for x in node.rows('SELECT action FROM notify_audit')] == ['Audit log smazán']
    run(env, 'notify_clear')


def test_node_tokens_from_fleet(env):
    """Tokens the node issued itself: listed without the token, disabled and deleted from Fleet."""
    from fastapi.testclient import TestClient
    node = env['mod']
    node._login_fails.clear()   # failed logins of test_node_security
    browser = TestClient(node.app)
    assert browser.post('/api/login', data={'username': 'instalace', 'password': 'installer-password-3'}).status_code == 200
    token = browser.post('/api/notify/tokens', json={'name': 'Zabbix', 'rate_per_min': 10}).json()
    env['agent'].heartbeat()
    listed = device(env)['notifications']['token_list']
    entry = next(x for x in listed if x['id'] == token['id'])
    assert entry['prefix'] == token['token'][:10] and token['token'] not in json.dumps(listed)
    run(env, 'update_notify_token', {'id': token['id'], 'enabled': False})
    assert node.rows('SELECT enabled FROM notify_tokens WHERE id=?', (token['id'],))[0]['enabled'] == 0
    assert requests.post(env['node'] + '/api/notify/v1', headers={'Authorization': 'Bearer ' + token['token']},
                         json={'title': 'x'}).status_code == 401
    run(env, 'delete_notify_token', {'id': token['id']})
    assert not node.rows('SELECT id FROM notify_tokens WHERE id=?', (token['id'],))


def test_try_watcher_and_grafana_tag_from_fleet(env):
    """Trying a watcher before saving it and a Grafana tag: the dialog waits for the command's result."""
    from fastapi import FastAPI
    api = FastAPI()
    api.get('/tickets')(lambda: [{'id': 7, 'subject': 'Tiskárna'}, {'id': 8, 'subject': 'Wi-Fi'}])
    url, _ = serve(api)
    row = run(env, 'preview_watcher', {'name': 'Tickets', 'url': url + '/tickets', 'auth_type': 'bearer', 'secret': 'api-secret',
                                        'id_field': 'id', 'title_template': 'Ticket {subject}', 'level': 'info', 'interval': 60})
    result = json.loads(row['result'])
    assert result['count'] == 2 and result['samples'][0]['title'] == 'Ticket Tiskárna'
    assert json.loads(row['payload_json'])['secret'] == '•••'
    assert not env['mod'].rows("SELECT id FROM notify_watchers WHERE name='Tickets'")   # nothing saved
    found = json.loads(run(env, 'grafana_discover', {'grafana_url': 'https://grafana.example', 'tag': 'tv'})['result'])
    assert found['count'] == 2 and found['dashboards'][0]['title'] == 'tv 0'
    one = requests.get(f"{env['hub']}/api/commands/{row['id']}", headers=env['h']).json()
    assert one['state'] == 'completed' and json.loads(one['payload_json'])['secret'] == '•••'
