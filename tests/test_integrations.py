"""API tokens, webhooks, customers, screen layouts, documents, monthly reports, content that runs out and the map."""
import hashlib
import hmac
import json
import shutil
import time

import pytest

from test_features import NEW, login
from test_maintenance import CAPS, delivered, enroll


def test_api_token(client, admin_headers):
    h = admin_headers
    dev = enroll(client, 'tok-dev', version='4.17.0', caps=NEW)
    client.post('/api/devices/assign', headers=h, json={'device_ids': [dev['device_id']], 'location': 'Tok Lab'})
    r = client.post('/api/api-tokens', headers=h, json={'name': 'HA', 'role': 'operator', 'scope': {'locations': ['Tok Lab']}}).json()
    tok = {'Authorization': 'Bearer ' + r['token']}
    assert r['token'].startswith('cfa_')
    seen = [d['id'] for d in client.get('/api/devices', headers=tok).json()['devices']]
    assert seen == [dev['device_id']]
    assert client.post('/api/takeovers', headers=tok, json={'title': 'Z HA', 'targets': {'all': True}}).status_code == 200
    assert client.get('/api/users', headers=tok).status_code == 403
    assert any(a['username'] == 'api:HA' for a in client.get('/api/audit', headers=h).json())
    client.patch(f"/api/api-tokens/{r['id']}", headers=h, json={'enabled': False})
    assert client.get('/api/devices', headers=tok).json()['detail'] == 'invalid_token'
    # nobody creates a token with more than they have; a token never manages users
    client.post('/api/users', headers=h, json={'username': 'tok-mgr', 'password': 'long-password-1', 'role': 'viewer'})
    assert client.post('/api/api-tokens', headers=login(client, 'tok-mgr'), json={'name': 'x', 'role': 'admin'}).status_code == 403
    adm = client.post('/api/api-tokens', headers=h, json={'name': 'admin', 'role': 'admin'}).json()
    assert client.get('/api/users', headers={'Authorization': 'Bearer ' + adm['token']}).status_code == 403


def test_webhooks(client, admin_headers, monkeypatch):
    from app import integrations
    h = admin_headers
    assert client.post('/api/webhooks', headers=h, json={'url': 'https://hooks.example/x', 'events': []}).json()['detail'] == 'events_required'
    hook = client.post('/api/webhooks', headers=h, json={'url': 'https://hooks.example/x', 'events': ['takeover.started']}).json()
    sent = []

    class R:
        status_code = 204
    integrations.deliver({'url': 'https://hooks.example/x', 'secret': hook['secret']}, 'test', {'a': 1},
                         send=lambda url, data, timeout, headers: sent.append((data, headers)) or R())
    body, headers = sent[0]
    assert headers['X-Caracal-Signature'] == 'sha256=' + hmac.new(hook['secret'].encode(), body, hashlib.sha256).hexdigest()
    assert json.loads(body)['event'] == 'test'
    queued = []
    monkeypatch.setattr(integrations._queue, 'put_nowait', lambda x: queued.append(x))
    dev = enroll(client, 'wh-dev', version='4.17.0', caps=NEW)
    client.post('/api/takeovers', headers=h, json={'title': 'Hook', 'targets': {'devices': [dev['device_id']]}})
    assert [(x[1], x[2]['title']) for x in queued] == [('takeover.started', 'Hook')]
    assert 'secret' not in client.get('/api/webhooks', headers=h).json()['webhooks'][0]
    client.delete(f"/api/webhooks/{hook['id']}", headers=h)


def test_tenants(client, admin_headers):
    h = admin_headers
    a, b = enroll(client, 'tn-a', version='4.17.0', caps=NEW), enroll(client, 'tn-b', version='4.17.0', caps=NEW)
    client.post('/api/devices/assign', headers=h, json={'device_ids': [a['device_id']], 'location': 'Tn Alfa'})
    client.post('/api/devices/assign', headers=h, json={'device_ids': [b['device_id']], 'location': 'Tn Beta'})
    alfa = client.post('/api/tenants', headers=h, json={'name': 'Alfa s.r.o.', 'locations': ['Tn Alfa']}).json()['id']
    assert client.post('/api/tenants', headers=h, json={'name': 'Jiný', 'locations': ['Tn Alfa']}).json()['detail'].startswith('location_taken')
    client.post('/api/users', headers=h, json={'username': 'tn-alfa', 'password': 'long-password-1', 'role': 'manager', 'tenant_id': alfa})
    ah = login(client, 'tn-alfa')
    me = client.get('/api/me', headers=ah).json()
    assert me['tenant'] == 'Alfa s.r.o.' and 'users' not in me['permissions'] and 'settings' not in me['permissions']
    assert [d['id'] for d in client.get('/api/devices', headers=ah).json()['devices']] == [a['device_id']]
    assert client.get(f"/api/devices/{b['device_id']}", headers=ah).status_code == 404
    # content of the operator stays hidden; the customer's own is the customer's
    op_pid = client.post('/api/global-playlists', headers=h, json={'name': 'Operátor'}).json()['id']
    own = client.post('/api/global-playlists', headers=ah, json={'name': 'Alfa obsah'}).json()['id']
    assert [p['name'] for p in client.get('/api/global-playlists', headers=ah).json()] == ['Alfa obsah']
    assert client.get(f'/api/global-playlists/{op_pid}', headers=ah).status_code == 404
    assert 'Alfa obsah' in [p['name'] for p in client.get('/api/global-playlists', headers=h).json()]
    assert client.post(f'/api/global-playlists/{own}/deploy', headers=ah, json={'target_ids': [b['device_id']]}).status_code == 404
    assert client.get('/api/tenants', headers=ah).json()[0]['name'] == 'Alfa s.r.o.'
    assert client.post('/api/tenants', headers=ah, json={'name': 'x'}).status_code == 403
    assert client.get('/api/users', headers=ah).status_code == 403
    # files: an upload of the customer stays the customer's
    fid = client.post('/api/files', headers={**ah, 'Content-Type': 'image/png', 'X-File-Name': 'a.png'}, content=b'\x89PNGalfa').json()['id']
    client.post(f'/api/library/{fid}', headers=ah, json={})
    assert fid in [x['id'] for x in client.get('/api/library', headers=ah).json()]
    other = client.post('/api/files', headers={**h, 'Content-Type': 'image/png', 'X-File-Name': 'b.png'}, content=b'\x89PNGop').json()['id']
    assert client.post(f'/api/global-playlists/{own}/items', headers=ah, json={'kind': 'image', 'file_id': other}).json()['detail'] == 'file_not_found'
    assert client.delete(f'/api/tenants/{alfa}', headers=h).json()['detail'] == 'tenant_in_use'


def test_screen_layout(client, admin_headers):
    h = admin_headers
    dev = enroll(client, 'lay-dev', version='4.17.0', caps={**NEW, 'player_settings': True})
    cmd = lambda p: client.post(f"/api/devices/{dev['device_id']}/commands", headers=h, json={'action': 'player_settings', 'payload': p})
    assert cmd({'layout': {'side': {'width': 30}}}).json()['detail'] == 'zone_widget_required'
    assert cmd({'layout': {'side': {'width': 99, 'widget': 'type=clock'}}}).json()['detail'] == 'invalid_value'
    assert cmd({'layout': {'side': {'width': 30, 'position': 'left', 'widget': 'caracal-widget:type=clock&lang=cs'},
                           'ticker': {'height': 8, 'widget': 'type=ticker&text=Ahoj'}}}).status_code == 200
    payload = delivered(client, dev)[-1]['payload']
    assert payload == {'layout': {'side': {'width': 30, 'widget': 'type=clock&lang=cs', 'position': 'left'}, 'ticker': {'height': 8, 'widget': 'type=ticker&text=Ahoj'}}}
    assert cmd({'layout': {}}).status_code == 200
    tid = client.post('/api/templates', headers=h, json={'name': 'Zóny', 'player': {'layout': {'ticker': {'height': 10, 'widget': 'type=clock'}}}}).json()['id']
    assert client.get(f'/api/templates/{tid}', headers=h).json()['player'] == {'layout': {'ticker': {'height': 10, 'widget': 'type=clock'}}}


@pytest.mark.skipif(not shutil.which('pdftoppm'), reason='poppler is not installed')
def test_document_pages(client, admin_headers):
    pdf = (b'%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R 4 0 R]/Count 2>>endobj '
           b'3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 320 180]>>endobj 4 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 320 180]>>endobj\n'
           b'trailer<</Root 1 0 R>>\n%%EOF')
    r = client.post('/api/documents', headers={**admin_headers, 'X-File-Name': 'Cen%C3%ADk.pdf'}, content=pdf)
    assert r.status_code == 200, r.text
    assert [p['name'] for p in r.json()['pages']] == ['Ceník – 1', 'Ceník – 2']
    lib = {x['id']: x for x in client.get('/api/library', headers=admin_headers).json()}
    assert all(p['id'] in lib for p in r.json()['pages'])
    assert client.post('/api/documents', headers={**admin_headers, 'X-File-Name': 'x.exe'}, content=b'MZ').json()['detail'] == 'unsupported_file'


def test_monthly_report(client, admin_headers):
    from app import reports
    from app.core import db
    dev = enroll(client, 'rep-dev', version='4.17.0', caps=NEW)
    month = time.strftime('%Y-%m')
    now = time.time()
    with db() as c:
        c.execute("INSERT INTO events(device_id, ts, kind, code, level) VALUES(?,?,'start','offline','critical')", (dev['device_id'], now - 7200))
        c.execute("INSERT INTO events(device_id, ts, kind, code, level) VALUES(?,?,'end','offline','critical')", (dev['device_id'], now - 3600))
        c.execute('UPDATE devices SET created=? WHERE id=?', (now - 10 * 3600, dev['device_id']))
    d = reports.data(month, allowed={dev['device_id']})
    row = d['devices'][0]
    assert row['outages'] == 1 and 85 < row['availability'] < 95
    r = client.get(f'/api/reports/monthly.pdf?month={month}', headers=admin_headers)
    assert r.status_code == 200 and r.content.startswith(b'%PDF')
    sent = []
    client.put('/api/alerts', headers=admin_headers, json={**client.get('/api/alerts', headers=admin_headers).json(),
                                                          'smtp': {'host': 'smtp.example', 'port': 25, 'security': 'none', 'username': '', 'password': '', 'sender': 'fleet@example.com'}})
    client.put('/api/reports/settings', headers=admin_headers, json={'enabled': True, 'recipients': 'a@example.com, b@example.com'})
    assert reports.send(month, send_mail=lambda m: sent.append(m) or month) == month
    assert sent[0]['To'] == 'a@example.com, b@example.com' and sent[0].get_payload()[1].get_filename().endswith('.pdf')


def test_content_running_out(client, admin_headers, monkeypatch):
    from app import monitor
    from app.core import DATA
    (DATA / 'content-notice.json').unlink(missing_ok=True)
    now = time.time()
    pid = client.post('/api/global-playlists', headers=admin_headers, json={'name': 'Končí'}).json()['id']
    client.post(f'/api/global-playlists/{pid}/items', headers=admin_headers, json={'kind': 'web', 'name': 'Akce', 'source': 'https://e.com', 'valid_until': now + 3600})
    from app import schedule
    monkeypatch.setattr(schedule, 'local_now', lambda ts=None: __import__('datetime').datetime(2026, 10, 25, 9, 0))
    soon = monitor.content_notice(now, send=lambda *a: None)
    assert any(x['name'] == 'Akce' for x in soon)
    assert monitor.content_notice(now, send=lambda *a: None) is None   # once a day
    # a node whose items all ran out needs attention
    dev = enroll(client, 'out-dev', version='4.17.0', caps=NEW)
    client.post(f"/api/device/{dev['device_id']}/heartbeat", headers={'X-Device-Token': dev['device_token']},
                json={'version': '4.17.0', 'api_ok': True, 'capabilities': NEW, 'player': {'player_online': True, 'updated': now},
                      'assets': [{'id': 1, 'name': 'Stará akce', 'kind': 'web', 'source': 'https://x', 'valid_until': now - 60}]})
    codes = [a['code'] for a in client.get(f"/api/devices/{dev['device_id']}", headers=admin_headers).json()['attention']]
    assert 'playlist_ran_out' in codes


def test_location_coordinates(client, admin_headers):
    h = admin_headers
    assert client.post('/api/org/locations', headers=h, json={'name': 'Mapa Brno', 'lat': 49.19, 'lon': 16.61}).status_code == 200
    loc = next(x for x in client.get('/api/org', headers=h).json()['locations'] if x['name'] == 'Mapa Brno')
    assert (loc['lat'], loc['lon']) == (49.19, 16.61)
    assert client.post('/api/org/locations', headers=h, json={'name': 'Mapa Zlá', 'lat': 120, 'lon': 0}).json()['detail'] == 'invalid_value'
    assert client.get('/api/widgets/qr?text=https://example.com').headers['content-type'].startswith('image/svg')
