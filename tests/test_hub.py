import json
import sqlite3

from app import core


def login(client, username, password):
    r = client.post('/api/login', json={'username': username, 'password': password})
    assert r.status_code == 200, r.text
    return {'Authorization': 'Bearer ' + r.json()['token']}


def test_login_rejects_bad_password(client):
    assert client.post('/api/login', json={'username': 'admin', 'password': 'nope'}).status_code == 401


def test_me_and_static(client, admin_headers):
    me = client.get('/api/me', headers=admin_headers).json()
    assert me['role'] == 'admin' and 'admin' in me['permissions']
    page = client.get('/')
    assert page.status_code == 200 and core.HUB_VERSION in page.text
    assert client.get('/static/app.js').status_code == 200
    assert client.get('/api/devices').status_code == 401


def test_roles(client, admin_headers):
    for name, role in (('viewer1', 'viewer'), ('operator1', 'operator'), ('manager1', 'manager')):
        r = client.post('/api/users', headers=admin_headers,
                        json={'username': name, 'password': 'long-password-1', 'role': role, 'language': 'en'})
        assert r.status_code == 200, r.text
    viewer = login(client, 'viewer1', 'long-password-1')
    operator = login(client, 'operator1', 'long-password-1')
    manager = login(client, 'manager1', 'long-password-1')
    assert client.get('/api/devices', headers=viewer).status_code == 200
    assert client.get('/api/users', headers=manager).status_code == 403
    assert client.get('/api/audit', headers=manager).status_code == 200
    assert client.get('/api/audit', headers=operator).status_code == 403
    assert client.post('/api/bulk/commands', headers=viewer,
                       json={'device_ids': ['x'], 'action': 'next'}).status_code == 403
    assert client.post('/api/org/groups', headers=operator, json={'name': 'G'}).status_code == 403


def test_last_admin_is_protected(client, admin_headers):
    users = client.get('/api/users', headers=admin_headers).json()
    admin = next(u for u in users if u['username'] == 'admin')
    r = client.patch(f"/api/users/{admin['id']}", headers=admin_headers, json={'role': 'viewer'})
    assert r.status_code == 409
    assert client.delete(f"/api/users/{admin['id']}", headers=admin_headers).status_code == 409


def test_password_change_invalidates_sessions(client, admin_headers):
    client.post('/api/users', headers=admin_headers,
                json={'username': 'pwuser', 'password': 'long-password-1', 'role': 'viewer'})
    h = login(client, 'pwuser', 'long-password-1')
    r = client.patch('/api/me', headers=h, json={'current_password': 'long-password-1',
                                                   'new_password': 'long-password-2'})
    assert r.status_code == 200
    assert client.get('/api/me', headers=h).status_code == 401
    assert client.get('/api/me', headers={'Authorization': 'Bearer ' + r.json()['token']}).status_code == 200


def test_enroll_keeps_existing_token(client):
    body = {'enroll_token': 'enroll-test-token', 'fingerprint': 'fp-keep', 'name': 'Keep'}
    first = client.post('/api/device/enroll', json=body).json()
    again = client.post('/api/device/enroll', json={**body, 'device_token': first['device_token']}).json()
    assert again == first
    assert client.post('/api/device/enroll', json={**body, 'enroll_token': 'bad'}).status_code == 401


def test_groups_and_locations(client, admin_headers):
    dev = client.post('/api/device/enroll', json={'enroll_token': 'enroll-test-token', 'fingerprint': 'fp-org'}).json()
    did = dev['device_id']
    assert client.post('/api/org/groups', headers=admin_headers, json={'name': 'Hala A'}).status_code == 200
    assert client.post('/api/org/locations', headers=admin_headers,
                       json={'name': 'Praha', 'address': 'Main 1'}).status_code == 200
    r = client.post('/api/devices/assign', headers=admin_headers,
                    json={'device_ids': [did], 'group': 'Hala A', 'location': 'Praha'})
    assert r.status_code == 200
    client.patch('/api/org/groups/Hala A', headers=admin_headers, json={'name': 'Hala B'})
    d = client.get(f'/api/devices/{did}', headers=admin_headers).json()
    assert d['group'] == 'Hala B' and d['location'] == 'Praha'
    org = client.get('/api/org', headers=admin_headers).json()
    assert any(g['name'] == 'Hala B' and g['total'] == 1 for g in org['groups'])
    client.delete('/api/org/groups/Hala B', headers=admin_headers)
    assert client.get(f'/api/devices/{did}', headers=admin_headers).json()['group'] == ''


def test_offline_device_rejects_live_commands(client, admin_headers):
    dev = client.post('/api/device/enroll', json={'enroll_token': 'enroll-test-token', 'fingerprint': 'fp-off'}).json()
    with core.db() as c:
        c.execute('UPDATE devices SET last_seen=0 WHERE id=?', (dev['device_id'],))
    r = client.post(f"/api/devices/{dev['device_id']}/commands", headers=admin_headers,
                    json={'action': 'show', 'payload': {'item_id': 1}})
    assert r.status_code == 409 and r.json()['detail'] == 'device_offline'
    d = client.get(f"/api/devices/{dev['device_id']}", headers=admin_headers).json()
    assert d['needs_attention'] and d['attention'][0]['code'] == 'offline'
    # content changes are queued for when the node comes back
    r = client.post(f"/api/devices/{dev['device_id']}/commands", headers=admin_headers,
                    json={'action': 'add_web', 'payload': {'name': 'X', 'source': 'https://x.test', 'duration': 5}})
    assert r.status_code == 200


def test_migrates_legacy_database(tmp_path, monkeypatch):
    data = tmp_path / 'legacy'
    data.mkdir()
    (data / 'config.json').write_text(json.dumps({'enroll_token': 'old', 'secret': 's' * 64,
                                                  'admin_hash': 'deadbeef'}))
    c = sqlite3.connect(data / 'hub.db')
    c.executescript("""CREATE TABLE devices(id TEXT PRIMARY KEY, token_hash TEXT, name TEXT, ip TEXT, version TEXT,
        last_seen REAL, status_json TEXT);
        CREATE TABLE commands(id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT, action TEXT, state TEXT,
        result TEXT, created REAL);
        INSERT INTO devices VALUES('CRCL-AAAA1111', 'hash123', 'Recepce', '10.0.0.5', '2.0.0', 1, '{}');""")
    c.commit()
    c.close()
    monkeypatch.setattr(core, 'DATA', data)
    monkeypatch.setattr(core, 'DB_PATH', data / 'hub.db')
    monkeypatch.setattr(core, 'CFG_PATH', data / 'config.json')
    monkeypatch.setattr(core, 'FILES', data / 'files')
    monkeypatch.delenv('CARACAL_HUB_ENROLL_TOKEN')
    monkeypatch.delenv('CARACAL_HUB_ADMIN_PASSWORD')
    core.init()
    with core.db() as c:
        dev = dict(c.execute('SELECT * FROM devices').fetchone())
        admin = c.execute("SELECT password_hash FROM users WHERE username='admin'").fetchone()
        cols = {r['name'] for r in c.execute('PRAGMA table_info(commands)')}
    assert dev['token_hash'] == 'hash123' and dev['name'] == 'Recepce' and dev['notes'] == ''
    assert admin['password_hash'] == 'legacy$deadbeef'
    assert {'payload_json', 'username', 'expires', 'followup_json'} <= cols
    assert core.cfg()['enroll_token'] == 'old' and core.cfg()['secret'] == 's' * 64


def test_enrollment_cannot_take_over_an_online_device(client):
    """The enrollment token and a known fingerprint do not replace the token of a device that is reporting."""
    body = {'enroll_token': 'enroll-test-token', 'fingerprint': 'fp-takeover'}
    first = client.post('/api/device/enroll', json=body).json()
    r = client.post('/api/device/enroll', json=body)          # no token of its own, device seen just now
    assert r.status_code == 409 and r.json()['detail'] == 'device_online'
    assert client.post('/api/device/enroll', json={**body, 'device_token': first['device_token']}).json() == first
    with core.db() as c:                                      # long offline: a reinstalled device gets a new token
        c.execute('UPDATE devices SET last_seen=0 WHERE id=?', (first['device_id'],))
    again = client.post('/api/device/enroll', json=body).json()
    assert again['device_id'] == first['device_id'] and again['device_token'] != first['device_token']


def test_finished_command_keeps_its_result(client, admin_headers):
    """A device cannot rewrite the result of a finished command (nor run its follow-ups again)."""
    dev = client.post('/api/device/enroll', json={'enroll_token': 'enroll-test-token', 'fingerprint': 'fp-replay'}).json()
    h = {'X-Device-Token': dev['device_token']}
    cid = client.post(f"/api/devices/{dev['device_id']}/commands", headers=admin_headers,
                      json={'action': 'add_web', 'payload': {'name': 'X', 'source': 'https://x.test', 'duration': 5}}).json()['id']
    assert client.get(f"/api/device/{dev['device_id']}/commands", headers=h).status_code == 200
    client.post(f"/api/device/{dev['device_id']}/commands/{cid}/result", headers=h, json={'ok': True, 'result': 'done'})
    r = client.post(f"/api/device/{dev['device_id']}/commands/{cid}/result", headers=h, json={'ok': False, 'result': 'rewritten'})
    assert r.json().get('ignored')
    row = client.get(f'/api/commands/{cid}', headers=admin_headers).json()
    assert (row['state'], row['result']) == ('completed', 'done')
