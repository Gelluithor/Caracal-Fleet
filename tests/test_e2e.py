"""Hub <-> agent <-> local CARACAL Fleet API v2 (mock), all running over real HTTP."""
import json

import pytest
import requests

from conftest import load_agent, serve
from dev.mock_node import create_app

PW = 'admin-password-123'


@pytest.fixture(scope='module')
def env(hub_app):
    hub_url, _ = serve(hub_app)
    agent_mod = load_agent()
    h = {'Authorization': 'Bearer ' + requests.post(hub_url + '/api/login',
                                                    json={'username': 'admin', 'password': PW}).json()['token']}
    nodes = []
    for fp in ('node-a', 'node-b'):
        enrolled = requests.post(hub_url + '/api/device/enroll',
                                 json={'enroll_token': 'enroll-test-token', 'fingerprint': fp, 'name': fp}).json()
        mock = create_app(enrolled['device_token'])
        node_url, _ = serve(mock)
        agent = agent_mod.Agent({'hub': hub_url, 'local_api': node_url, **enrolled})
        agent.heartbeat()
        nodes.append({'id': enrolled['device_id'], 'agent': agent, 'mock': mock.state})
    return {'hub': hub_url, 'h': h, 'a': nodes[0], 'b': nodes[1], 'mod': agent_mod}


def api(env, method, path, **kw):
    r = requests.request(method, env['hub'] + path, headers=env['h'], timeout=30, **kw)
    assert r.status_code < 400, r.text
    return r.json()


def cmd(env, node, action, payload=None):
    res = api(env, 'POST', f"/api/devices/{node['id']}/commands", json={'action': action, 'payload': payload or {}})
    node['agent'].run_commands()
    node['agent'].heartbeat()
    rows = api(env, 'GET', f"/api/commands?device_id={node['id']}")
    row = next(x for x in rows if x['id'] == res['id'])
    assert row['state'] == 'completed', row
    return row


def by_name(mock, name):
    return next(a for a in mock.assets if a['name'] == name)


def test_device_reports_state(env):
    d = api(env, 'GET', f"/api/devices/{env['a']['id']}")
    assert d['online'] and d['api_ok'] and d['player_online']
    assert d['current_name'] == 'Intranet' and len(d['assets']) == 3
    assert [(c['name'], c['tag'], c['grafana_url']) for c in d['collections']] == \
        [('Výroba dashboardy', 'vyroba', 'https://grafana.example')]
    caps = d['capabilities']
    assert caps['upload'] and caps['asset_file'] and caps['add_grafana_tag']
    assert d['version'] == env['mod'].VERSION
    # resource warnings (disk, RAM) depend on the machine running the tests
    assert not {x['code'] for x in d['attention']} & {'offline', 'local_api', 'player', 'agent_outdated'}


def test_show_and_timed_freeze(env):
    a = env['a']
    cmd(env, a, 'show', {'item_id': 2})
    assert a['mock'].player['current_id'] == 2 and not a['mock'].player['frozen']
    cmd(env, a, 'freeze', {'item_id': 1, 'minutes': 5})
    assert a['mock'].player['current_id'] == 1 and a['mock'].player['frozen']
    assert api(env, 'GET', f"/api/devices/{a['id']}")['frozen'] is True
    assert a['agent'].state['unfreeze_at']
    a['agent'].state['unfreeze_at'] = 1  # pretend the time is up
    a['agent'].auto_unfreeze()
    assert not a['mock'].player['frozen']
    cmd(env, a, 'next')
    assert a['mock'].player['current_id'] == 2


def test_collection_playback(env):
    a = env['a']
    cmd(env, a, 'freeze_collection', {'collection_id': 3})
    d = api(env, 'GET', f"/api/devices/{a['id']}")
    # dashboards of a collection play as <collection id> * 100000 + index
    assert d['frozen'] and d['current_asset_id'] == 3 and d['current_kind'] == 'grafana-tag'
    cmd(env, a, 'unfreeze')
    assert not api(env, 'GET', f"/api/devices/{a['id']}")['frozen']


def test_show_unknown_item_is_rejected(env):
    r = requests.post(f"{env['hub']}/api/devices/{env['a']['id']}/commands", headers=env['h'],
                      json={'action': 'freeze', 'payload': {'item_id': 999}})
    assert r.status_code == 409


def test_player_down_blocks_show(env):
    a = env['a']
    a['mock'].player.update(stopped=True, updated=0)
    try:
        a['agent'].heartbeat()
        res = api(env, 'POST', f"/api/devices/{a['id']}/commands", json={'action': 'show', 'payload': {'item_id': 1}})
        a['agent'].run_commands()
        row = next(x for x in api(env, 'GET', '/api/commands') if x['id'] == res['id'])
        assert row['state'] == 'failed' and 'Player' in row['result']
        d = api(env, 'GET', f"/api/devices/{a['id']}")
        codes = [x['code'] for x in d['attention']]
        assert 'player' in codes and 'commands_failed' in codes and d['needs_attention']
    finally:
        a['mock'].player['stopped'] = False
        a['agent'].heartbeat()


def test_playlist_management(env):
    a = env['a']
    cmd(env, a, 'add_web', {'name': 'Novinky', 'source': 'https://g.example', 'duration': 15, 'scale': 1.25})
    new = by_name(a['mock'], 'Novinky')
    assert new['duration'] == 15 and new['scale'] == 1.25
    cmd(env, a, 'update_asset', {'id': new['id'], 'name': 'Renamed', 'duration': 20, 'source': 'https://h.example'})
    assert new['name'] == 'Renamed' and new['source'] == 'https://h.example'
    order = [x['id'] for x in a['mock'].assets][::-1]
    cmd(env, a, 'reorder', {'order': order})
    assert [x['id'] for x in a['mock'].assets] == order
    # an outdated order (missing the newest item, containing a deleted one) is completed by the agent
    cmd(env, a, 'reorder', {'order': [str(order[-1]), '99999']})
    assert [x['id'] for x in a['mock'].assets] == [order[-1]] + order[:-1]
    cmd(env, a, 'delete_asset', {'id': new['id']})
    assert new['id'] not in [x['id'] for x in a['mock'].assets]


def test_duration_and_scale_follow_caracal_rules(env):
    url = f"{env['hub']}/api/devices/{env['a']['id']}/commands"
    for payload in ({'name': 'x', 'source': 'https://x.test', 'duration': 0},
                    {'name': 'x', 'source': 'https://x.test', 'duration': 10, 'scale': 4}):
        assert requests.post(url, headers=env['h'], json={'action': 'add_web', 'payload': payload}).status_code == 400


def test_media_upload(env):
    a = env['a']
    data = b'\x89PNG\r\n fake image'
    f = requests.post(env['hub'] + '/api/files', data=data, timeout=30,
                      headers={**env['h'], 'Content-Type': 'image/png', 'X-File-Name': 'banner%20A.png'}).json()
    assert f['kind'] == 'image' and f['name'] == 'banner A.png'
    cmd(env, a, 'add_media', {'file_id': f['id'], 'name': 'Banner', 'duration': 12})
    asset = by_name(a['mock'], 'Banner')
    assert asset['kind'] == 'image' and asset['duration'] == 12 and a['mock'].files[asset['id']] == data


def test_grafana_collections(env):
    a = env['a']
    cmd(env, a, 'add_collection', {'name': 'Linka 1', 'grafana_url': 'https://grafana.example/', 'tag': 'linka1',
                                   'duration': 30, 'scale': 1.5})
    col = by_name(a['mock'], 'Linka 1')
    assert col['kind'] == 'grafana-tag' and col['scale'] == 1.5
    assert json.loads(col['source']) == {'grafana_url': 'https://grafana.example', 'tag': 'linka1', 'kiosk': True}
    cmd(env, a, 'update_collection', {'id': col['id'], 'name': 'Linka 1b', 'tag': 'linka1b', 'kiosk': False,
                                      'grafana_url': 'https://grafana.example'})
    assert col['name'] == 'Linka 1b' and json.loads(col['source'])['tag'] == 'linka1b'
    cmd(env, a, 'delete_collection', {'id': col['id']})
    assert col not in a['mock'].assets
    bad = requests.post(f"{env['hub']}/api/devices/{a['id']}/commands", headers=env['h'],
                        json={'action': 'add_collection', 'payload': {'name': 'x', 'grafana_url': 'https://g'}})
    assert bad.status_code == 400 and bad.json()['detail'] == 'tag_required'


def test_copy_playlist_with_media_and_collections(env):
    a, b = env['a'], env['b']
    res = api(env, 'POST', '/api/copy', json={'source_id': a['id'], 'target_ids': [b['id']], 'mode': 'replace',
                                              'include_collections': True})
    assert res['items'] == len(a['mock'].assets) and not res['skipped']
    a['agent'].run_commands()       # export media to the hub
    b['agent'].run_commands()       # import on the target
    b['agent'].heartbeat()
    summary = lambda m: [(x['name'], x['kind'], x['source'] if x['kind'] != 'image' else '') for x in m.assets]
    assert summary(b['mock']) == summary(a['mock'])
    assert b['mock'].files[by_name(b['mock'], 'Logo')['id']] == a['mock'].files[2]
    states = {x['action']: x['state'] for x in api(env, 'GET', '/api/commands') if x['batch'] == res['batch']}
    assert states == {'export_assets': 'completed', 'import_playlist': 'completed'}


def test_copy_single_collection(env):
    a, b = env['a'], env['b']
    before = len(b['mock'].assets)
    api(env, 'POST', '/api/copy', json={'source_id': a['id'], 'target_ids': [b['id']], 'collection_ids': [3]})
    b['agent'].run_commands()
    assert len(b['mock'].assets) == before + 1 and b['mock'].assets[-1]['kind'] == 'grafana-tag'


def test_bulk_command_reports_skipped(env):
    res = api(env, 'POST', '/api/bulk/commands', json={'device_ids': [env['a']['id'], env['b']['id'], 'CRCL-NONE'],
                                                       'action': 'next'})
    assert len(res['command_ids']) == 2 and res['skipped'][0]['reason'] == 'device_not_found'
    env['a']['agent'].run_commands()
    env['b']['agent'].run_commands()


def test_agent_self_update(env, tmp_path, monkeypatch):
    a = env['a']
    target = tmp_path / 'agent.py'
    target.write_text('old')
    monkeypatch.setattr(env['mod'], 'AGENT_FILE', target)
    result = a['agent'].do_update_agent({})
    a['agent'].after = None  # do not exit the test process
    assert 'VERSION' in target.read_text() and env['mod'].VERSION in result


def test_audit_and_history(env):
    audit = api(env, 'GET', '/api/audit')
    actions = {x['action'] for x in audit}
    assert {'command.show', 'content.copy', 'file.upload'} <= actions
    detail = json.loads(next(x for x in audit if x['action'] == 'command.show')['detail'])
    assert 'item_id' in detail
