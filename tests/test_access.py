"""Roles, user groups and scopes: what a user may do with which devices (hub/app/access.py, core.Access)."""
from app import core


def enroll(client, fp, **org):
    dev = client.post('/api/device/enroll', json={'enroll_token': 'enroll-test-token', 'fingerprint': fp}).json()
    did = dev['device_id']
    h = {'X-Device-Token': dev['device_token']}
    client.post(f'/api/device/{did}/heartbeat', headers=h, json={'version': core.AGENT_VERSION, 'api_ok': True})
    return did


def login(client, username, password='long-password-1'):
    r = client.post('/api/login', json={'username': username, 'password': password})
    assert r.status_code == 200, r.text
    return {'Authorization': 'Bearer ' + r.json()['token']}


def user(client, admin_headers, username, **fields):
    r = client.post('/api/users', headers=admin_headers, json={'username': username, 'password': 'long-password-1', **fields})
    assert r.status_code == 200, r.text
    return r.json()['id']


def test_location_scope(client, admin_headers):
    brno = enroll(client, 'fp-acc-brno')
    praha = enroll(client, 'fp-acc-praha')
    client.post('/api/devices/assign', headers=admin_headers, json={'device_ids': [brno], 'location': 'Acc Brno'})
    client.post('/api/devices/assign', headers=admin_headers, json={'device_ids': [praha], 'location': 'Acc Praha'})
    user(client, admin_headers, 'acc-brno', role='operator', scope={'locations': ['Acc Brno']})
    h = login(client, 'acc-brno')
    me = client.get('/api/me', headers=h).json()
    assert me['scoped'] and me['permissions'] == ['view', 'control', 'content'] and me['everywhere'] == []
    seen = [d['id'] for d in client.get('/api/devices', headers=h).json()['devices']]
    assert brno in seen and praha not in seen
    assert client.get(f'/api/devices/{praha}', headers=h).status_code == 404
    assert client.get(f'/api/devices/{brno}', headers=h).json()['can'] == ['view', 'control', 'content']
    # commands only on the device in the scope; bulk commands skip the others
    assert client.post(f'/api/devices/{brno}/commands', headers=h, json={'action': 'next'}).status_code in (200, 409)
    assert client.post(f'/api/devices/{praha}/commands', headers=h, json={'action': 'next'}).status_code == 404
    bulk = client.post('/api/bulk/commands', headers=h, json={'device_ids': [brno, praha], 'action': 'restart_player'}).json()
    assert {'device_id': praha, 'reason': 'device_not_found'} in bulk['skipped']
    # no device settings, no system
    assert client.patch(f'/api/devices/{brno}', headers=h, json={'name': 'x'}).status_code == 403
    for path in ('/api/users', '/api/audit', '/api/alerts', '/api/backups', '/api/settings'):
        assert client.get(path, headers=h).status_code == 403, path
    # groups and locations: only those of the scope
    org = client.get('/api/org', headers=h).json()
    assert [x['name'] for x in org['locations']] == ['Acc Brno']
    # the timeline and the command list show only the scope
    assert all(c['device_id'] == brno for c in client.get('/api/commands', headers=h).json())
    assert all(e['device_id'] == brno for e in client.get('/api/events', headers=h).json())


def test_manager_of_a_location_cannot_move_devices_out(client, admin_headers):
    a = enroll(client, 'fp-acc-a')
    client.post('/api/devices/assign', headers=admin_headers, json={'device_ids': [a], 'location': 'Ostrava', 'group': 'Kiosky'})
    client.post('/api/org/locations', headers=admin_headers, json={'name': 'Plzeň'})
    user(client, admin_headers, 'acc-ostrava', role='manager', scope={'locations': ['Ostrava']})
    h = login(client, 'acc-ostrava')
    assert client.patch(f'/api/devices/{a}', headers=h, json={'name': 'Recepce'}).status_code == 200
    assert client.patch(f'/api/devices/{a}', headers=h, json={'location': 'Plzeň'}).json()['detail'] == 'move_outside_scope'
    assert client.patch(f'/api/devices/{a}', headers=h, json={'group': 'Nová skupina'}).json()['detail'] == 'new_group_forbidden'
    assert client.post('/api/org/locations', headers=h, json={'name': 'Jinde'}).status_code == 403
    # staged updates and system settings need the whole fleet
    assert client.post('/api/rollouts', headers=h, json={}).status_code == 403
    assert client.post('/api/provision', headers=h, json={}).status_code == 403   # install is system-wide
    # schedules only for the scope
    pl = client.post('/api/global-playlists', headers=admin_headers, json={'name': 'acc'}).json()['id']
    body = {'name': 'Ostrava', 'default_playlist_id': pl, 'slots': [], 'targets': {'locations': ['Ostrava']}}
    ok = client.post('/api/schedules', headers=h, json=body)
    assert ok.status_code == 200, ok.text
    assert client.post('/api/schedules', headers=h, json={**body, 'targets': {'locations': ['Plzeň']}}).json()['detail'] == 'targets_outside_scope'
    mine = [s['name'] for s in client.get('/api/schedules', headers=h).json()['schedules']]
    assert mine == ['Ostrava']
    client.delete(f"/api/schedules/{ok.json()['id']}", headers=admin_headers)


def test_custom_role_and_user_group(client, admin_headers):
    d = enroll(client, 'fp-acc-g')
    client.post('/api/devices/assign', headers=admin_headers, json={'device_ids': [d], 'group': 'Jídelna'})
    role = client.post('/api/roles', headers=admin_headers, json={'name': 'Obsah a plán', 'permissions': ['content', 'schedule']}).json()['key']
    roles = {r['key']: r for r in client.get('/api/roles', headers=admin_headers).json()['roles']}
    assert roles[role]['permissions'] == ['view', 'content', 'schedule'] and roles['admin']['builtin']
    # a user without an own role gets everything from the group
    uid = user(client, admin_headers, 'acc-member', role='')
    h = login(client, 'acc-member')
    assert client.get('/api/devices', headers=h).status_code == 403   # nothing at all yet
    assert client.get('/api/me', headers=h).json()['permissions'] == []
    gid = client.post('/api/user-groups', headers=admin_headers, json={'name': 'Jídelna', 'role': role, 'scope': {'groups': ['Jídelna']},
                                                                       'members': [uid]}).json()['id']
    assert [x['id'] for x in client.get('/api/devices', headers=h).json()['devices']] == [d]
    me = client.get('/api/me', headers=h).json()
    assert me['permissions'] == ['view', 'content', 'schedule'] and me['role'] == ''
    users = {u['username']: u for u in client.get('/api/users', headers=admin_headers).json()}
    assert users['acc-member']['groups'] == [gid]
    # renaming the device group keeps the scope
    client.patch('/api/org/groups/Jídelna', headers=admin_headers, json={'name': 'Jídelna 2'})
    assert [x['id'] for x in client.get('/api/devices', headers=h).json()['devices']] == [d]
    assert client.get('/api/user-groups', headers=admin_headers).json()[0]['scope'] == {'locations': [], 'groups': ['Jídelna 2'], 'devices': []}
    # a role in use cannot be deleted; built-in roles cannot be changed
    assert client.delete(f'/api/roles/{role}', headers=admin_headers).json()['detail'] == 'role_in_use'
    assert client.patch('/api/roles/admin', headers=admin_headers, json={'name': 'x', 'permissions': ['view']}).json()['detail'] == 'builtin_role'
    client.delete(f'/api/user-groups/{gid}', headers=admin_headers)
    assert client.get('/api/devices', headers=h).status_code == 403
    assert client.delete(f'/api/roles/{role}', headers=admin_headers).status_code == 200


def test_no_one_grants_more_than_they_have(client, admin_headers):
    role = client.post('/api/roles', headers=admin_headers, json={'name': 'Správce uživatelů', 'permissions': ['users']}).json()['key']
    user(client, admin_headers, 'acc-usermgr', role=role)
    h = login(client, 'acc-usermgr')
    assert client.get('/api/users', headers=h).status_code == 200
    r = client.post('/api/users', headers=h, json={'username': 'acc-evil', 'password': 'long-password-1', 'role': 'admin'})
    assert r.status_code == 403 and r.json()['detail'].startswith('cannot_grant:')
    assert client.post('/api/users', headers=h, json={'username': 'acc-viewer', 'password': 'long-password-1', 'role': 'viewer'}).status_code == 200
    assert client.post('/api/roles', headers=h, json={'name': 'x', 'permissions': ['settings']}).status_code == 403
    me = next(u for u in client.get('/api/users', headers=h).json() if u['username'] == 'acc-usermgr')
    assert client.patch(f"/api/users/{me['id']}", headers=h, json={'role': 'admin'}).status_code == 403
    # the last full administrator stays
    admin = next(u for u in client.get('/api/users', headers=admin_headers).json() if u['username'] == 'admin')
    others = [u for u in client.get('/api/users', headers=admin_headers).json() if u['role'] == 'admin' and u['username'] != 'admin']
    if not others:
        assert client.patch(f"/api/users/{admin['id']}", headers=admin_headers, json={'scope': {'locations': ['Acc Brno']}}).json()['detail'] == 'last_admin'


def test_scoped_console_and_screenshot(client, admin_headers):
    d = enroll(client, 'fp-acc-c')
    other = enroll(client, 'fp-acc-c2')
    client.post('/api/devices/assign', headers=admin_headers, json={'device_ids': [d], 'location': 'Konzole'})
    role = client.post('/api/roles', headers=admin_headers, json={'name': 'Technik', 'permissions': ['console', 'devices']}).json()['key']
    user(client, admin_headers, 'acc-tech', role=role, scope={'devices': [d]})
    h = login(client, 'acc-tech')
    assert client.get(f'/api/devices/{d}', headers=h).json()['can'] == ['view', 'devices', 'console']
    assert client.get(f'/api/devices/{other}/screenshot', headers=h).status_code == 404
    assert client.get(f'/api/devices/{d}/metrics', headers=h).status_code == 200
    assert client.get(f'/api/devices/{other}/metrics', headers=h).status_code == 404
