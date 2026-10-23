"""Cameras in global playlists: the stream credentials stay out of the API and the command history, older agents are
not sent them, and the agent creates them on the node."""
import json

from conftest import load_agent
from test_maintenance import CAPS, delivered, enroll

CAM_CAPS = {**CAPS, 'add_camera': True, 'camera_onvif': True}


def camera(client, h, pid, **fields):
    body = {'kind': 'camera', 'name': 'Vstup', 'duration': 20, 'layout': '2', 'audio': True,
            'streams': [{'name': 'Brána', 'url': 'rtsp://admin:tajne@192.168.1.20:554/s1'},
                        {'name': 'Dvůr', 'url': 'rtsp://192.168.1.21/s1', 'username': 'viewer', 'password': ''}], **fields}
    return client.post(f'/api/global-playlists/{pid}/items', headers=h, json=body)


def test_global_camera(client, admin_headers):
    h = admin_headers
    pid = client.post('/api/global-playlists', headers=h, json={'name': 'Kamery'}).json()['id']
    assert camera(client, h, pid, streams=[]).json()['detail'] == 'camera_streams'
    assert camera(client, h, pid, streams=[{'url': 'ftp://x'}]).json()['detail'] == 'camera_url'
    iid = camera(client, h, pid).json()['id']
    p = client.get(f'/api/global-playlists/{pid}', headers=h).json()
    it = p['items'][0]
    assert 'tajne' not in json.dumps(p) and 'secret_json' not in it
    assert it['streams'] == [{'name': 'Brána', 'url': 'rtsp://192.168.1.20:554/s1', 'username': 'admin', 'has_password': True},
                             {'name': 'Dvůr', 'url': 'rtsp://192.168.1.21/s1', 'username': 'viewer', 'has_password': False}]
    assert it['layout'] == '2' and it['audio'] is True
    # editing without the passwords keeps them; a new name alone keeps the streams
    streams = [{'name': 'Brána', 'url': 'rtsp://192.168.1.20:554/s1', 'username': 'admin', 'password': ''},
               {'name': 'Dvůr', 'url': 'rtsp://192.168.1.21/s1', 'username': 'viewer', 'password': 'nove'}]
    assert client.patch(f'/api/global-playlists/{pid}/items/{iid}', headers=h, json={'streams': streams, 'layout': 'auto'}).status_code == 200
    assert client.patch(f'/api/global-playlists/{pid}/items/{iid}', headers=h, json={'name': 'Vchod'}).status_code == 200
    it = client.get(f'/api/global-playlists/{pid}', headers=h).json()['items'][0]
    assert it['name'] == 'Vchod' and it['layout'] == 'auto' and [s['has_password'] for s in it['streams']] == [True, True]
    client.post(f'/api/global-playlists/{pid}/items', headers=h, json={'kind': 'web', 'name': 'W', 'source': 'https://e.com'})

    new = enroll(client, 'cam-new', version='4.17.0', caps={**CAM_CAPS, 'import_camera': True})
    old = enroll(client, 'cam-old', version='4.16.0', caps=CAM_CAPS)
    r = client.post(f'/api/global-playlists/{pid}/deploy', headers=h,
                    json={'target_ids': [new['device_id'], old['device_id']]}).json()
    assert r['camera_unsupported'] == ['cam-old']
    got = delivered(client, new)[0]['payload']['items']
    cam = next(x for x in got if x['kind'] == 'camera')
    assert cam['streams'] == [{'name': 'Brána', 'url': 'rtsp://192.168.1.20:554/s1', 'username': 'admin', 'password': 'tajne'},
                              {'name': 'Dvůr', 'url': 'rtsp://192.168.1.21/s1', 'username': 'viewer', 'password': 'nove'}]
    assert cam['layout'] == 'auto' and cam['audio'] is True and cam['duration'] == 20
    assert [x['kind'] for x in delivered(client, old)[0]['payload']['items']] == ['web']
    # once delivered, the passwords are gone from the hub's command history
    from app.core import db, scrub_secrets
    with db() as c:
        scrub_secrets(c)
        stored = [x['payload_json'] for x in c.execute("SELECT payload_json FROM commands WHERE action='import_playlist'")]
    assert stored and not any('tajne' in x or 'nove' in x for x in stored)

    # templates leave the cameras out for older agents too
    tid = client.post('/api/templates', headers=h, json={'name': 'Kamery', 'playlist_id': pid}).json()['id']
    res = client.post(f'/api/templates/{tid}/apply', headers=h, json={'target_ids': [old['device_id']]}).json()['results'][0]
    assert {'action': 'add_camera', 'reason': 'agent_too_old'} in res['skipped'] and 'import_playlist' in res['queued']


def test_scrub_finds_a_password_next_to_an_empty_one(client, admin_headers):
    dev = enroll(client, 'cam-scrub', version='4.16.0', caps=CAM_CAPS)
    payload = {'name': 'C', 'streams': [{'url': 'rtsp://a/1', 'username': '', 'password': ''},
                                        {'url': 'rtsp://a/2', 'username': 'u', 'password': 'heslo'}]}
    client.post(f"/api/devices/{dev['device_id']}/commands", headers=admin_headers, json={'action': 'add_camera', 'payload': payload})
    delivered(client, dev)
    from app.core import db, scrub_secrets
    with db() as c:
        scrub_secrets(c)
        stored = c.execute("SELECT payload_json FROM commands WHERE action='add_camera' AND device_id=?", (dev['device_id'],)).fetchone()[0]
    assert 'heslo' not in stored


def test_agent_imports_a_camera():
    mod = load_agent()
    agent = mod.Agent({'hub': 'http://127.0.0.1:9', 'local_api': 'http://127.0.0.1:9', 'device_id': 'CRCL-T',
                       'device_token': 'token'})
    sent = []

    class Resp:
        status_code = 200

        def __init__(self, ident):
            self.ident = ident

        def json(self):
            return {'ok': True, 'id': self.ident}
    agent.local = lambda name, ids=None, timeout=30, **kw: sent.append((name, kw.get('json'))) or Resp(len(sent))
    agent.snapshot = lambda: {}
    agent.assets_of = lambda snap: []
    agent.capabilities = lambda: {'add_camera': True, 'upload': True}
    item = {'type': 'asset', 'kind': 'camera', 'name': 'Vstup', 'duration': 20, 'scale': 1, 'layout': '2', 'audio': True,
            'streams': [{'name': 'B', 'url': 'rtsp://a/1', 'username': 'u', 'password': 'p'}]}
    res = agent.do_import_playlist({'items': [item, {'type': 'asset', 'kind': 'web', 'name': 'W', 'source': 'https://e.com'}]})
    assert res['created'] == 2 and sent[0][0] == 'add_camera' and sent[1][0] == 'add_web'
    assert sent[0][1]['streams'] == item['streams'] and sent[0][1]['layout'] == '2'
    # an older node without cameras skips it
    sent.clear()
    agent.capabilities = lambda: {'add_camera': False}
    assert agent.do_import_playlist({'items': [item]})['skipped'] == ['Vstup'] and sent == []
