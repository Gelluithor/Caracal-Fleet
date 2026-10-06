"""Hub + agent against nodes with the old hand-applied Fleet API patch (v1, 'real', 'real2') and with the
current CARACAL Fleet API (v2, 'full') - see dev/mock_node.py."""
import time

import pytest
import requests

from conftest import load_agent, serve
from dev.mock_node import create_app

PW = 'admin-password-123'


@pytest.fixture(scope='module')
def env(hub_app):
    hub_url, _ = serve(hub_app)
    mod = load_agent()
    h = {'Authorization': 'Bearer ' + requests.post(hub_url + '/api/login',
                                                    json={'username': 'admin', 'password': PW}).json()['token']}
    nodes = {}
    for name, api_version in (('real', 'v1'), ('real2', 'v1'), ('full', 'v2')):
        enrolled = requests.post(hub_url + '/api/device/enroll', json={
            'enroll_token': 'enroll-test-token', 'fingerprint': 'caracal-' + name, 'name': name}).json()
        mock = create_app(enrolled['device_token'], api=api_version)
        node_url, _ = serve(mock)
        agent = mod.Agent({'hub': hub_url, 'local_api': node_url, **enrolled})
        # systemd state of caracal-player.service, as seen by the agent on a real node
        agent.service_active = lambda name, st=mock.state: not st.player.get('stopped')
        agent.heartbeat()
        nodes[name] = {'id': enrolled['device_id'], 'agent': agent, 'mock': mock.state}
    return {'hub': hub_url, 'h': h, **nodes}


def api(env, method, path, **kw):
    r = requests.request(method, env['hub'] + path, headers=env['h'], timeout=30, **kw)
    assert r.status_code < 400, r.text
    return r.json()


def run(env, node, action, payload=None):
    res = api(env, 'POST', f"/api/devices/{node['id']}/commands", json={'action': action, 'payload': payload or {}})
    node['agent'].run_commands()
    node['agent'].heartbeat()
    return next(x for x in api(env, 'GET', f"/api/commands?device_id={node['id']}") if x['id'] == res['id'])


def test_capabilities_and_collections(env):
    d = api(env, 'GET', f"/api/devices/{env['real']['id']}")
    caps = d['capabilities']
    assert caps['snapshot'] and caps['reorder'] and caps['upload'] is False and caps['add_grafana_tag'] is False
    # login profiles are not collections, grafana-tag assets are
    assert [c['name'] for c in d['collections']] == ['Výroba dashboardy']
    assert d['collections'][0]['tag'] == 'vyroba' and d['supports_enabled'] is False
    full = api(env, 'GET', f"/api/devices/{env['full']['id']}")
    assert full['capabilities']['upload'] is True and full['capabilities']['add_grafana_tag'] is True


def test_show_collection_from_tag_asset(env):
    node = env['real']
    assert run(env, node, 'freeze_collection', {'collection_id': 3})['state'] == 'completed'
    assert node['mock'].player['collection_frozen'] and node['mock'].player['collection_id'] == 3
    run(env, node, 'unfreeze')


def test_reorder_uses_ids(env):
    node = env['real']
    order = [x['id'] for x in node['mock'].assets][::-1]
    assert run(env, node, 'reorder', {'order': order})['state'] == 'completed'
    assert [x['id'] for x in node['mock'].assets] == order


def test_player_liveness_from_updated(env):
    node = env['real']
    node['mock'].player.update(stopped=True, updated=time.time() - 60)   # player service stopped
    try:
        node['agent'].heartbeat()
        d = api(env, 'GET', f"/api/devices/{node['id']}")
        assert d['player_online'] is False and 'player' in [a['code'] for a in d['attention']]
        row = run(env, node, 'show', {'item_id': 1})
        assert row['state'] == 'failed' and 'Player' in row['result']
    finally:
        node['mock'].player['stopped'] = False
        node['agent'].heartbeat()


def test_copy_skips_what_the_node_cannot_transfer(env):
    src, dst = env['real'], env['real2']
    before = [a['id'] for a in dst['mock'].assets if a['kind'] == 'grafana-tag']
    res = api(env, 'POST', '/api/copy', json={'source_id': src['id'], 'target_ids': [dst['id']], 'mode': 'replace'})
    assert res['skipped'] == ['Logo']   # the v1 node cannot export media
    dst['agent'].run_commands()
    names = [a['name'] for a in dst['mock'].assets]
    assert names.count('Intranet') == 1 and 'Logo' not in names
    # the target's own grafana-tag collection survives replace mode
    assert [a['id'] for a in dst['mock'].assets if a['kind'] == 'grafana-tag'] == before


def test_import_skips_media_on_node_without_upload(env, tmp_path):
    full, real = env['full'], env['real']
    res = api(env, 'POST', '/api/copy', json={'source_id': full['id'], 'target_ids': [real['id']], 'mode': 'append',
                                              'include_collections': True})
    full['agent'].run_commands()
    real['agent'].run_commands()
    row = next(x for x in api(env, 'GET', '/api/commands') if x['batch'] == res['batch'] and x['action'] == 'import_playlist')
    assert row['state'] == 'completed'
    assert 'Logo' in row['result'] and 'Výroba' in row['result']   # reported as skipped


def test_collections_from_old_agent_are_ignored(env):
    """Agent 4.0.0 sent the node's login profiles as 'collections'; they must not appear as Grafana collections."""
    node = env['real']
    h = {'X-Device-Token': node['agent'].conf['device_token']}
    old = {'version': '4.0.0', 'api_ok': True, 'player': {'player_online': True},
           'assets': list(node['mock'].assets), 'collections': [{'id': 1, 'name': 'Grafana login'}]}
    requests.post(f"{env['hub']}/api/device/{node['id']}/heartbeat", json=old, headers=h).raise_for_status()
    d = api(env, 'GET', f"/api/devices/{node['id']}")
    assert [c['name'] for c in d['collections']] == ['Výroba dashboardy']
    assert 'agent_outdated' in [a['code'] for a in d['attention']]
    node['agent'].heartbeat()


def test_timed_freeze_ends_although_node_reports_not_frozen(env):
    node = env['real']
    assert run(env, node, 'freeze', {'item_id': 1, 'minutes': 5})['state'] == 'completed'
    assert node['mock'].player['frozen']
    assert api(env, 'GET', f"/api/devices/{node['id']}")['frozen'] is True   # known from the agent
    node['agent'].state['unfreeze_at'] = 1
    node['agent'].auto_unfreeze()
    assert not node['mock'].player['frozen']
    node['agent'].heartbeat()
    assert api(env, 'GET', f"/api/devices/{node['id']}")['frozen'] is False


def test_duration_limits(env):
    node = env['real']
    url = f"{env['hub']}/api/devices/{node['id']}/commands"
    bad = [('add_web', {'name': 'x', 'source': 'https://x.test', 'duration': 3}),
           ('update_asset', {'id': 1, 'duration': 0})]
    for action, payload in bad:
        r = requests.post(url, headers=env['h'], json={'action': action, 'payload': payload})
        assert r.status_code == 400 and r.json()['detail'] == 'invalid_duration'


def test_copy_reports_pages_without_login(env):
    src, dst = env['real'], env['real2']
    page = next(a for a in src['mock'].assets if a['kind'] == 'web')
    page['auth_profile_id'] = 1
    src['agent'].heartbeat()
    try:
        res = api(env, 'POST', '/api/copy', json={'source_id': src['id'], 'target_ids': [dst['id']],
                                                  'asset_ids': [page['id']]})
        assert res['without_login'] == [page['name']]
        dst['agent'].run_commands()
    finally:
        page['auth_profile_id'] = None


def test_player_counts_as_running_when_service_is_active(env, monkeypatch):
    """v2 players do not refresh the v1 state file; a stale timestamp with an active service is not an outage."""
    node = env['real']
    node['mock'].player.update(stopped=True, updated=time.time() - 3600)
    monkeypatch.setattr(node['agent'], 'service_active', lambda name: True)
    try:
        node['agent'].heartbeat()
        d = api(env, 'GET', f"/api/devices/{node['id']}")
        assert d['player_online'] is True and 'player' not in [a['code'] for a in d['attention']]
        assert run(env, node, 'show', {'item_id': 1})['state'] == 'completed'
        monkeypatch.setattr(node['agent'], 'service_active', lambda name: False)
        node['agent'].heartbeat()
        assert api(env, 'GET', f"/api/devices/{node['id']}")['player_online'] is False
    finally:
        node['mock'].player['stopped'] = False
        monkeypatch.undo()
        node['agent'].heartbeat()


def test_live_v2_state_overrides_agent_freeze(env):
    """With the live v2 player state, a resume done in the node UI is picked up (after the grace period)."""
    node = env['full']
    assert run(env, node, 'freeze', {'item_id': 1, 'minutes': 30})['state'] == 'completed'
    assert api(env, 'GET', f"/api/devices/{node['id']}")['frozen'] is True
    node['mock'].player['frozen'] = False          # resumed directly on the node
    node['agent'].heartbeat()                      # still within the grace period: freeze kept
    assert node['agent'].state['unfreeze_at']
    node['agent'].state['frozen_at'] = 0
    node['agent'].heartbeat()
    assert api(env, 'GET', f"/api/devices/{node['id']}")['frozen'] is False
    assert not node['agent'].state.get('unfreeze_at')


def test_global_playlist_deploy(env):
    full, real = env['full'], env['real']
    png = b'\x89PNG\r\n\x1a\n global image'
    f = requests.post(env['hub'] + '/api/files', data=png, headers={**env['h'], 'Content-Type': 'image/png',
                                                                    'X-File-Name': 'banner.png'}).json()
    pid = api(env, 'POST', '/api/global-playlists', json={'name': 'Firemní obsah'})['id']
    api(env, 'POST', f'/api/global-playlists/{pid}/items',
        json={'kind': 'web', 'name': 'Novinky', 'source': 'https://news.example', 'duration': 20})
    api(env, 'POST', f'/api/global-playlists/{pid}/items',
        json={'kind': 'grafana-tag', 'name': 'KPI', 'grafana_url': 'https://grafana.example', 'tag': 'kpi'})
    iid = api(env, 'POST', f'/api/global-playlists/{pid}/items',
              json={'kind': 'image', 'file_id': f['id'], 'name': 'Banner', 'duration': 10})['id']
    bad = requests.post(f"{env['hub']}/api/global-playlists/{pid}/items", headers=env['h'],
                        json={'kind': 'web', 'name': 'x', 'source': 'https://x', 'duration': 2})
    assert bad.status_code == 400
    api(env, 'PUT', f'/api/global-playlists/{pid}/order', json={'order': [iid]})

    res = api(env, 'POST', f'/api/global-playlists/{pid}/deploy',
              json={'target_ids': [full['id'], real['id']], 'mode': 'replace'})
    assert res['media_unsupported'] == ['real']
    tags_before = [a['id'] for a in real['mock'].assets if a['kind'] == 'grafana-tag']
    full['agent'].run_commands()
    real['agent'].run_commands()
    # replace mode keeps the node's own Grafana collections; the new ones follow in playlist order
    assert [(a['name'], a['kind']) for a in full['mock'].assets] == \
        [('Výroba dashboardy', 'grafana-tag'), ('Banner', 'image'), ('Novinky', 'web'), ('KPI', 'grafana-tag')]
    assert full['mock'].files[full['mock'].assets[1]['id']] == png
    # the v1 node gets the web page; its Grafana tag stays, the image and the new collection are skipped
    assert [a['name'] for a in real['mock'].assets if a['kind'] == 'web'] == ['Novinky']
    assert [a['id'] for a in real['mock'].assets if a['kind'] == 'grafana-tag'] == tags_before

    p = api(env, 'GET', f'/api/global-playlists/{pid}')
    states = {x['device_id']: x for x in p['deployments'][0]['results']}
    assert states[full['id']]['state'] == 'completed' and 'Banner' in states[real['id']]['result']
    listing = api(env, 'GET', '/api/global-playlists')
    assert next(x for x in listing if x['id'] == pid)['last_deployment']['targets'] == 2


def test_global_playlist_from_device(env):
    res = api(env, 'POST', '/api/global-playlists/from-device', json={'device_id': env['real']['id']})
    p = api(env, 'GET', f"/api/global-playlists/{res['id']}")
    assert {it['kind'] for it in p['items']} <= {'web', 'grafana-tag'} and p['items']
    assert 'Logo' in res['skipped'] and 'Výroba dashboardy' not in res['skipped']
    tag = next(it for it in p['items'] if it['kind'] == 'grafana-tag')
    assert tag['tag'] == 'vyroba' and tag['grafana_url'] == 'https://grafana.example'


def test_agent_moves_to_another_hub_only_when_known(env):
    agent = env['full']['agent']
    with pytest.raises(RuntimeError):
        agent.do_set_hub({'hub': 'http://127.0.0.1:9'})   # unreachable / unknown hub
    result = agent.do_set_hub({'hub': env['hub'] + '/'})   # the hub knows this device
    agent.after = None   # do not exit the test process
    assert env['hub'] in result


def test_login_profiles_on_many_nodes(env):
    full, real = env['full'], env['real']
    assert api(env, 'GET', f"/api/devices/{full['id']}")['capabilities']['add_profile'] is True
    old = api(env, 'GET', f"/api/devices/{real['id']}")
    assert old['capabilities']['add_profile'] is False                  # v1 patch: no login endpoints
    assert [p['name'] for p in old['profiles']] == ['Grafana login']     # but its logins are listed

    login = {'name': 'Intranet', 'login_url': 'https://intra.example/login', 'target_url': 'https://intra.example/',
             'username': 'tv', 'password': 'tv-secret-1'}
    res = api(env, 'POST', '/api/bulk/commands', json={'device_ids': [full['id'], real['id']], 'action': 'add_profile',
                                                       'payload': login})
    assert len(res['command_ids']) == 2
    # while queued the credentials are hidden from the UI, the agent still receives them
    queued = [x for x in api(env, 'GET', '/api/commands') if x['batch'] == res['batch']]
    assert all('tv-secret-1' not in x['payload_json'] and '•••' in x['payload_json'] for x in queued)
    full['agent'].run_commands()
    real['agent'].run_commands()
    states = {x['device_id']: x for x in api(env, 'GET', '/api/commands') if x['batch'] == res['batch']}
    assert states[full['id']]['state'] == 'completed'
    assert states[real['id']]['state'] == 'failed' and 'does not support "add_profile"' in states[real['id']]['result']
    created = next(p for p in full['mock'].profiles if p['name'] == 'Intranet')
    assert full['mock'].secrets[created['id']] == ('tv', 'tv-secret-1')
    full['agent'].heartbeat()
    d = api(env, 'GET', f"/api/devices/{full['id']}")
    assert any(p['id'] == created['id'] for p in d['profiles']) and d['profile_count'] == len(full['mock'].profiles)
    assert 'tv-secret-1' not in str(d)

    # a page with the login of another node is refused before it is queued
    r = requests.post(f"{env['hub']}/api/devices/{full['id']}/commands", headers=env['h'], json={
        'action': 'add_web', 'payload': {'name': 'x', 'source': 'https://x.example', 'auth_profile_id': 777}})
    assert r.status_code == 409 and r.json()['detail'] == 'profile_not_found'
    row = run(env, full, 'add_web', {'name': 'Intranet TV', 'source': 'https://intra.example/', 'duration': 20,
                                     'auth_profile_id': created['id']})
    assert row['state'] == 'completed'
    page = next(a for a in full['mock'].assets if a['name'] == 'Intranet TV')
    assert page['auth_profile_id'] == created['id']
    assert run(env, full, 'delete_profile', {'id': created['id']})['state'] == 'completed'
    assert page['auth_profile_id'] is None and created not in full['mock'].profiles


def test_cancelled_login_command_forgets_credentials(env):
    from app.core import db
    full = env['full']
    cid = api(env, 'POST', f"/api/devices/{full['id']}/commands", json={'action': 'add_profile', 'payload': {
        'name': 'X', 'login_url': 'https://x.example/', 'target_url': 'https://x.example/', 'username': 'u',
        'password': 'cancel-me-123'}})['id']
    api(env, 'POST', f'/api/commands/{cid}/cancel')
    with db() as c:
        assert 'cancel-me-123' not in c.execute('SELECT payload_json FROM commands WHERE id=?', (cid,)).fetchone()[0]
