"""Emergency messages, approvals, versions of global playlists, what a deployment changes, the media library, proof
of play, what plays when, widgets, device labels and tables."""
import time

from conftest import load_agent
from test_maintenance import CAPS, delivered, enroll

NEW = {**CAPS, 'takeover': True, 'widgets': True, 'plays': True, 'add_camera': True, 'import_camera': True}


def login(client, username, password='long-password-1'):
    return {'Authorization': 'Bearer ' + client.post('/api/login', json={'username': username, 'password': password}).json()['token']}


def playlist(client, h, name, *items):
    pid = client.post('/api/global-playlists', headers=h, json={'name': name}).json()['id']
    for it in items:
        r = client.post(f'/api/global-playlists/{pid}/items', headers=h, json=it)
        assert r.status_code == 200, r.text
    return pid


def test_takeover(client, admin_headers):
    h = admin_headers
    new = enroll(client, 'to-new', version='4.17.0', caps=NEW)
    old = enroll(client, 'to-old', caps=CAPS)
    client.post('/api/devices/assign', headers=h, json={'device_ids': [new['device_id'], old['device_id']], 'location': 'TO Hala'})
    assert client.post('/api/takeovers', headers=h, json={'targets': {'all': True}}).json()['detail'] == 'notification_text_required'
    r = client.post('/api/takeovers', headers=h, json={'title': 'Evakuace', 'message': 'Ven', 'level': 'critical', 'minutes': 30,
                                                       'targets': {'locations': ['TO Hala']}}).json()
    assert {(d['id'], d['how']) for d in r['devices']} == {(new['device_id'], 'takeover'), (old['device_id'], 'notify')}
    cmd = delivered(client, new)[0]
    assert cmd['action'] == 'takeover' and cmd['payload']['title'] == 'Evakuace' and cmd['payload']['until'] > time.time() + 1700
    assert delivered(client, old)[0]['action'] == 'notify'
    live = client.get('/api/devices', headers=h).json()['takeovers']
    assert [x['id'] for x in live] == [r['id']]
    # it cannot be sent as an ordinary command
    assert client.post(f"/api/devices/{new['device_id']}/commands", headers=h, json={'action': 'takeover', 'payload': {}}).status_code == 400
    assert client.post(f"/api/takeovers/{r['id']}/end", headers=h).status_code == 200
    assert [c['action'] for c in delivered(client, new)] == ['takeover_end']
    assert client.get('/api/devices', headers=h).json()['takeovers'] == []
    # a viewer may not start one
    client.post('/api/users', headers=h, json={'username': 'to-viewer', 'password': 'long-password-1', 'role': 'viewer'})
    assert client.post('/api/takeovers', headers=login(client, 'to-viewer'), json={'title': 'x', 'targets': {'all': True}}).status_code == 403


def test_approval_versions_and_preview(client, admin_headers):
    h = admin_headers
    dev = enroll(client, 'ap-dev', version='4.17.0', caps=NEW)
    pid = playlist(client, h, 'AP', {'kind': 'web', 'name': 'A', 'source': 'https://a.example'})
    # what a deployment changes: the node has Intranet; replace removes it and adds A
    client.post(f"/api/device/{dev['device_id']}/heartbeat", headers={'X-Device-Token': dev['device_token']},
                json={'version': '4.17.0', 'api_ok': True, 'capabilities': NEW,
                      'assets': [{'id': 1, 'name': 'Intranet', 'kind': 'web', 'source': 'https://i'},
                                 {'id': 2, 'name': 'A', 'kind': 'web', 'source': 'https://a.example'},
                                 {'id': 3, 'name': 'Graf', 'kind': 'grafana-tag', 'source': '{}'}]})
    p = client.post(f'/api/global-playlists/{pid}/preview', headers=h, json={'target_ids': [dev['device_id']], 'mode': 'replace'}).json()
    x = p['devices'][0]
    assert x['same'] == ['A'] and x['removed'] == ['Intranet'] and x['kept'] == ['Graf'] and x['added'] == []
    x = client.post(f'/api/global-playlists/{pid}/preview', headers=h, json={'target_ids': [dev['device_id']], 'mode': 'append'}).json()['devices'][0]
    assert x['added'] == ['A'] and x['duplicates'] == ['A']

    client.put('/api/approvals/settings', headers=h, json={'enabled': True})
    client.post('/api/users', headers=h, json={'username': 'ap-op', 'password': 'long-password-1', 'role': 'operator'})
    op = login(client, 'ap-op')
    r = client.post(f'/api/global-playlists/{pid}/deploy', headers=op, json={'target_ids': [dev['device_id']], 'mode': 'append', 'note': 'nové'}).json()
    aid = r['pending_approval']
    assert delivered(client, dev) == []
    # the playlist changes after the request: the approved version is the requested one
    client.post(f'/api/global-playlists/{pid}/items', headers=h, json={'kind': 'web', 'name': 'B', 'source': 'https://b.example'})
    assert client.get('/api/devices', headers=h).json()['approvals_pending'] == 1
    assert client.post(f'/api/approvals/{aid}/approve', headers=op, json={}).status_code == 403
    a = client.get('/api/approvals', headers=h).json()['approvals'][0]
    assert a['can_decide'] and a['note'] == 'nové' and [i['name'] for i in a['items']] == ['A']
    assert client.post(f'/api/approvals/{aid}/approve', headers=h, json={'comment': 'ok'}).status_code == 200
    assert [i['name'] for i in delivered(client, dev)[0]['payload']['items']] == ['A']
    assert client.post(f'/api/approvals/{aid}/approve', headers=h, json={}).json()['detail'] == 'already_decided'
    # an administrator deploys right away
    assert 'batch' in client.post(f'/api/global-playlists/{pid}/deploy', headers=h, json={'target_ids': [dev['device_id']]}).json()
    rid = client.post(f'/api/global-playlists/{pid}/deploy', headers=op, json={'target_ids': [dev['device_id']]}).json()['pending_approval']
    assert client.post(f'/api/approvals/{rid}/reject', headers=h, json={'comment': 'ne'}).status_code == 200
    done = client.get('/api/approvals?state=done', headers=h).json()['approvals']
    assert {x['state'] for x in done} >= {'approved', 'rejected'}
    client.put('/api/approvals/settings', headers=h, json={'enabled': False})

    # versions: every change is kept, an earlier one comes back
    versions = client.get(f'/api/global-playlists/{pid}/versions', headers=h).json()
    assert versions[0]['current'] and versions[0]['names'] == ['A', 'B']
    first = next(v for v in versions if v['names'] == ['A'])
    assert client.post(f"/api/global-playlists/{pid}/versions/{first['id']}/restore", headers=h).status_code == 200
    assert [i['name'] for i in client.get(f'/api/global-playlists/{pid}', headers=h).json()['items']] == ['A']
    assert client.get(f'/api/global-playlists/{pid}/versions', headers=h).json()[0]['current']


def test_library(client, admin_headers):
    h = admin_headers
    up = lambda name: client.post('/api/files', headers={**h, 'Content-Type': 'image/png', 'X-File-Name': name}, content=b'\x89PNG' + name.encode()).json()['id']
    old, new = up('stare.png'), up('nove.png')
    assert client.post(f'/api/library/{old}', headers=h, json={}).status_code == 200
    dev = enroll(client, 'lib-dev', version='4.17.0', caps=NEW)
    pid = playlist(client, h, 'LIB', {'kind': 'image', 'name': 'stare.png', 'file_id': old, 'duration': 10})
    client.post(f'/api/global-playlists/{pid}/deploy', headers=h, json={'target_ids': [dev['device_id']], 'mode': 'replace'})
    lib = {x['id']: x for x in client.get('/api/library', headers=h).json()}
    assert lib[old]['usage'][0]['name'] == 'LIB' and new not in lib
    assert client.delete(f'/api/library/{old}', headers=h).json()['detail'] == 'file_in_use'
    r = client.post(f'/api/library/{old}/replace', headers=h, json={'file_id': new}).json()
    assert r['playlists'][0]['last_deployment']['targets'] == [dev['device_id']]
    item = client.get(f'/api/global-playlists/{pid}', headers=h).json()['items'][0]
    assert item['file_id'] == new and item['name'] == 'nove.png'
    lib = {x['id']: x for x in client.get('/api/library', headers=h).json()}
    assert lib[new]['library'] and old not in lib
    assert client.get(f'/api/library/{new}/file', headers=h).content == b'\x89PNGnove.png'


def test_plays_report_and_export(client, admin_headers):
    h = admin_headers
    dev = enroll(client, 'pl-dev', version='4.17.0', caps=NEW)
    now = time.time()
    plays = [{'id': i + 1, 'asset_id': 1, 'name': 'Akce' if i % 2 else 'Logo', 'kind': 'image', 'started': now - 3600 + i * 20, 'ended': now - 3600 + i * 20 + 15}
             for i in range(10)]
    beat = lambda: client.post(f"/api/device/{dev['device_id']}/heartbeat", headers={'X-Device-Token': dev['device_token']},
                               json={'version': '4.17.0', 'api_ok': True, 'capabilities': NEW, 'plays': plays})
    beat()
    beat()   # sent again: stored once
    r = client.get(f"/api/plays/report?device={dev['device_id']}", headers=h).json()
    assert r['total']['count'] == 10 and r['total']['seconds'] == 150
    assert {x['name']: x['count'] for x in r['rows']} == {'Akce': 5, 'Logo': 5}
    assert sum(d['count'] for d in r['days']) == 10
    by_day = client.get(f"/api/plays/report?device={dev['device_id']}&by=day", headers=h).json()['rows']
    assert len(by_day) in (1, 2)
    csv = client.get(f"/api/plays/export.csv?device={dev['device_id']}&q=Akce", headers=h).text
    assert csv.count('\n') == 6 and 'Akce' in csv and 'Logo' not in csv


def test_timeline(client, admin_headers):
    h = admin_headers
    dev = enroll(client, 'tl-dev', version='4.17.0', caps=NEW)
    a = playlist(client, h, 'TL den', {'kind': 'web', 'name': 'X', 'source': 'https://x', 'valid_until': time.time() + 86400 * 30})
    b = playlist(client, h, 'TL noc', {'kind': 'web', 'name': 'Y', 'source': 'https://y'})
    s = client.post('/api/schedules', headers=h, json={'name': 'TL', 'default_playlist_id': b, 'targets': {'devices': [dev['device_id']]},
                                                       'slots': [{'days': [0, 1, 2, 3, 4, 5, 6], 'start': '08:00', 'end': '16:30', 'playlist_id': a}],
                                                       'screen': [{'days': [0, 1, 2, 3, 4, 5, 6], 'start': '06:00', 'end': '22:00'}]})
    assert s.status_code == 200, s.text
    client.post('/api/scheduled-tasks', headers=h, json={'name': 'Restart', 'action': 'restart_player', 'time': '05:30', 'days': [0, 1, 2, 3, 4, 5, 6],
                                                         'targets': {'devices': [dev['device_id']]}})
    r = client.get(f"/api/timeline?device={dev['device_id']}&days=2", headers=h).json()
    day = r['days'][0]['devices'][0]
    assert [(p['start'], p['end'], p['name']) for p in day['playlists']] == [(0, 480, 'TL noc'), (480, 990, 'TL den'), (990, 1440, 'TL noc')]
    assert [(x['start'], x['end']) for x in day['screen_off']] == [(0, 360), (1320, 1440)]
    assert [(x['minute'], x['action']) for x in day['tasks']] == [(330, 'restart_player')]
    assert any(i['name'] == 'X' for i in day['items'])
    assert len(r['days']) == 2
    client.delete(f"/api/schedules/{s.json()['id']}", headers=h)


def test_widgets(client, admin_headers):
    h = admin_headers
    new = enroll(client, 'w-new', version='4.17.0', caps=NEW)
    old = enroll(client, 'w-old', caps=CAPS)
    src = 'caracal-widget:type=clock&lang=cs'
    send = lambda d: client.post(f"/api/devices/{d['device_id']}/commands", headers=h, json={'action': 'add_web', 'payload': {'name': 'Hodiny', 'source': src, 'duration': 20}})
    assert send(new).status_code == 200
    assert send(old).json()['detail'] == 'update_required'
    pid = playlist(client, h, 'W', {'kind': 'web', 'name': 'Hodiny', 'source': src}, {'kind': 'web', 'name': 'Web', 'source': 'https://e.com'})
    r = client.post(f'/api/global-playlists/{pid}/deploy', headers=h, json={'target_ids': [new['device_id'], old['device_id']]}).json()
    assert r['items_unsupported'] == ['w-old']
    assert [i['name'] for i in delivered(client, old)[-1]['payload']['items']] == ['Web']
    # the agent turns the widget into the node's own page
    mod = load_agent()
    agent = mod.Agent({'hub': 'http://127.0.0.1:9', 'local_api': 'http://127.0.0.1:8080', 'device_id': 'CRCL-W', 'device_token': 't'})
    assert agent.with_profile({'name': 'H', 'source': src})['source'] == 'http://127.0.0.1:8080/widgets?type=clock&lang=cs'
    # the preview reads only public addresses
    assert client.get('/api/widgets/feed?url=http://127.0.0.1:8090/api/health', headers=h).json()['detail'] == 'private_address'
    assert client.get('/static/widgets.html').headers['X-Frame-Options'] == 'SAMEORIGIN'


def test_agent_plays_and_takeover():
    mod = load_agent()
    agent = mod.Agent({'hub': 'http://127.0.0.1:9', 'local_api': 'http://127.0.0.1:9', 'device_id': 'CRCL-P', 'device_token': 't'})
    calls = []

    class R:
        def __init__(self, data):
            self.data = data

        def json(self):
            return self.data
    node = {'last_id': 3, 'plays': [{'id': 2, 'name': 'A', 'started': 1, 'ended': 2}, {'id': 3, 'name': 'B', 'started': 2, 'ended': 3}]}
    agent.local = lambda name, ids=None, timeout=30, **kw: calls.append((name, kw)) or R(node if name == 'plays' else {'ok': True})
    agent.capabilities = lambda: {'plays': True}
    agent.state['plays_after'] = 1
    plays, after = agent.collect_plays()
    assert [p['name'] for p in plays] == ['A', 'B'] and after == 3 and calls[0][1]['params'] == {'after': 1}
    # a reinstalled node starts counting again
    agent.state['plays_after'] = 50
    calls.clear()
    agent.collect_plays()
    assert [c[1]['params'] for c in calls] == [{'after': 50}, {'after': 0}]
    agent.do_takeover({'title': 'X'})
    agent.do_takeover_end({})
    assert [c[0] for c in calls[-2:]] == ['takeover', 'takeover_end']


def test_tags_and_tables(client, admin_headers):
    h = admin_headers
    a = enroll(client, 'tag-a')
    b = enroll(client, 'tag-b')
    assert client.patch(f"/api/devices/{a['device_id']}", headers=h, json={'tags': 'lobby, 4K, lobby'}).status_code == 200
    client.post('/api/devices/tags', headers=h, json={'device_ids': [a['device_id'], b['device_id']], 'add': ['venku'], 'remove': ['4k']})
    devs = {d['id']: d for d in client.get('/api/devices', headers=h).json()['devices']}
    assert devs[a['device_id']]['tags'] == ['lobby', 'venku'] and devs[b['device_id']]['tags'] == ['venku']
    assert 'lobby' in client.get('/api/devices', headers=h).json()['tags']
    csv = client.get('/api/devices/export.csv', headers=h).text
    assert csv.splitlines()[0].lstrip('﻿').startswith('id;name;hostname') and 'lobby, venku' in csv
    table = f"id;name;location;tags\n{a['device_id']};Recepce A;Tag Praha;lobby\nCRCL-NONE;x;y;z\n"
    r = client.post('/api/devices/import', headers=h, json={'csv': table}).json()
    assert not r['applied'] and r['rows'][1]['error'] == 'device_not_found'
    assert r['rows'][0]['changes'] == {'name': {'from': 'tag-a', 'to': 'Recepce A'}, 'location': {'from': '', 'to': 'Tag Praha'},
                                       'tags': {'from': ['lobby', 'venku'], 'to': ['lobby']}}
    assert client.get(f"/api/devices/{a['device_id']}", headers=h).json()['name'] == 'tag-a'
    client.post('/api/devices/import', headers=h, json={'csv': table, 'apply': True})
    d = client.get(f"/api/devices/{a['device_id']}", headers=h).json()
    assert (d['name'], d['location'], d['tags']) == ('Recepce A', 'Tag Praha', ['lobby'])
    # by name: the name found the device and does not change
    r = client.post('/api/devices/import', headers=h, json={'csv': 'name,group\ntag-b,Tag Skupina\n', 'apply': True}).json()
    assert r['rows'][0]['changes'] == {'group': {'from': '', 'to': 'Tag Skupina'}}
    assert client.post('/api/devices/import', headers=h, json={'csv': 'foo;bar\n1;2\n'}).json()['detail'] == 'csv_no_key'
