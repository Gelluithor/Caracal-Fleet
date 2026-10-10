"""Metric history, events and alerts for administrators (hub/app/monitor.py)."""
import time

from app import core, monitor


def enroll(client, fp):
    dev = client.post('/api/device/enroll', json={'enroll_token': 'enroll-test-token', 'fingerprint': fp}).json()
    return dev['device_id'], {'X-Device-Token': dev['device_token']}


def heartbeat(client, did, headers, **status):
    r = client.post(f'/api/device/{did}/heartbeat', headers=headers,
                    json={'version': core.AGENT_VERSION, 'api_ok': True, 'cpu': 10, 'ram': 20, 'disk': 30, **status})
    assert r.status_code == 200, r.text


def set_alerts(client, admin_headers, **conf):
    body = {'enabled': True, 'delay': 5, 'levels': ['critical'], 'recovery': True, 'language': 'en',
            'channels': [{'type': 'webhook', 'name': 'Hook', 'url': 'https://hooks.test/x', 'token': 'secret-token'}]}
    r = client.put('/api/alerts', headers=admin_headers, json={**body, **conf})
    assert r.status_code == 200, r.text
    return r.json()


def mine(messages, name):
    return [m for m in messages if any(name in line for line in m['lines'])]


def test_heartbeat_stores_metrics_in_buckets(client, admin_headers):
    did, h = enroll(client, 'fp-metrics')
    heartbeat(client, did, h, cpu=12.5, temp=51)
    heartbeat(client, did, h, cpu=14, temp=52)     # same bucket: the latest values win
    m = client.get(f'/api/devices/{did}/metrics?hours=24', headers=admin_headers).json()
    assert m['bucket'] == monitor.BUCKET and len(m['points']) == 1
    assert m['points'][0][1:] == [14, 20, 30, 52]
    assert client.get('/api/devices/nope/metrics', headers=admin_headers).status_code == 404


def test_problems_become_events_and_alerts(client, admin_headers):
    did, h = enroll(client, 'fp-alerts')
    client.patch(f'/api/devices/{did}', headers=admin_headers, json={'name': 'Alert screen', 'location': 'Lobby'})
    heartbeat(client, did, h, player={'player_online': False, 'error': 'crashed'})
    set_alerts(client, admin_headers)
    sent = []
    send = lambda ch, conf, title, lines, payload, level: sent.append((ch['name'], title, lines, payload))
    now = time.time()
    monitor.check(now, send)                        # the problem starts: an event, no alert yet
    events = client.get(f'/api/events?device_id={did}', headers=admin_headers).json()
    assert [(e['kind'], e['code']) for e in events] == [('start', 'player')]
    assert not [s for s in sent if 'Alert screen' in ' '.join(s[2])]
    msgs = monitor.check(now + 6 * 60, send)        # still there after the delay: alerted once
    alert = mine(msgs, 'Alert screen')
    line = next(x for x in alert[0]['lines'] if 'Alert screen' in x) if alert else ''
    assert alert[0]['event'] == 'alert' and 'Player not running' in line and 'Lobby' in line
    assert not mine(monitor.check(now + 7 * 60, send), 'Alert screen')
    heartbeat(client, did, h, player={'player_online': True})
    done = mine(monitor.check(now + 9 * 60, send), 'Alert screen')
    assert done and done[0]['event'] == 'resolved'
    kinds = [e['kind'] for e in client.get(f'/api/events?device_id={did}', headers=admin_headers).json()]
    assert kinds == ['end', 'start']
    payload = next(s for s in sent if s[3]['event'] == 'alert' and 'Alert screen' in ' '.join(s[2]))[3]
    item = next(x for x in payload['items'] if x['device'] == 'Alert screen')
    assert item['device_id'] == did and item['url'].endswith(f'/#/device/{did}')


def test_muted_device_is_not_alerted(client, admin_headers):
    did, h = enroll(client, 'fp-muted')
    client.patch(f'/api/devices/{did}', headers=admin_headers, json={'name': 'Muted screen'})
    heartbeat(client, did, h, api_ok=False)
    set_alerts(client, admin_headers, delay=0)
    assert client.post('/api/alerts/mute', headers=admin_headers, json={'devices': [did], 'minutes': 60}).status_code == 200
    assert client.get(f'/api/devices/{did}', headers=admin_headers).json()['muted_until']
    send = lambda *a: None
    assert not mine(monitor.check(time.time(), send), 'Muted screen')
    client.post('/api/alerts/mute', headers=admin_headers, json={'devices': [did], 'minutes': 0})
    assert mine(monitor.check(time.time(), send), 'Muted screen')


def test_disabled_alerts_still_record_events(client, admin_headers):
    did, h = enroll(client, 'fp-disabled')
    heartbeat(client, did, h, temp=85)
    set_alerts(client, admin_headers, enabled=False, delay=0)
    assert monitor.check(time.time(), lambda *a: None) == []
    events = client.get(f'/api/events?device_id={did}', headers=admin_headers).json()
    assert events[0]['code'] == 'temperature' and events[0]['level'] == 'critical'


def test_alert_settings_hide_secrets_and_keep_them(client, admin_headers):
    conf = set_alerts(client, admin_headers, smtp={'host': 'smtp.test', 'port': 465, 'security': 'ssl',
                                                    'username': 'u', 'password': 'pw', 'sender': 'f@x.test'})
    assert conf['smtp']['password'] == core.REDACTED and conf['channels'][0]['token'] == core.REDACTED
    again = client.put('/api/alerts', headers=admin_headers, json=conf).json()   # saved back unchanged
    stored = monitor.config()
    assert stored['smtp']['password'] == 'pw' and stored['channels'][0]['token'] == 'secret-token'
    assert again['channels'][0]['id'] == conf['channels'][0]['id']
    bad = client.put('/api/alerts', headers=admin_headers, json={'channels': [{'type': 'email', 'to': 'nobody'}]})
    assert bad.status_code == 400
    assert client.put('/api/alerts', headers=admin_headers, json={'channels': [{'type': 'slack', 'url': 'ftp://x'}]}).status_code == 400


def test_alert_settings_need_admin(client, admin_headers):
    client.post('/api/users', headers=admin_headers,
                json={'username': 'mon-manager', 'password': 'long-password-1', 'role': 'manager'})
    r = client.post('/api/login', json={'username': 'mon-manager', 'password': 'long-password-1'})
    h = {'Authorization': 'Bearer ' + r.json()['token']}
    assert client.get('/api/alerts', headers=h).status_code == 403
    assert client.post('/api/alerts/mute', headers=h, json={'devices': ['x'], 'minutes': 5}).status_code == 200


def test_test_message_reports_delivery_errors(client, admin_headers, monkeypatch):
    calls = []

    class Response:
        def __init__(self, code):
            self.code = code

        def raise_for_status(self):
            if self.code >= 400:
                raise RuntimeError(f'{self.code} Client Error')

    def post(url, json=None, headers=None, timeout=None):
        calls.append((url, json, headers))
        return Response(404 if 'broken' in url else 200)

    monkeypatch.setattr(monitor.requests, 'post', post)
    ok = client.post('/api/alerts/test', headers=admin_headers,
                     json={'channel': {'type': 'ntfy', 'url': 'https://ntfy.test/fleet', 'token': 't'}}).json()
    assert ok == {'ok': True}
    assert calls[-1][0] == 'https://ntfy.test' and calls[-1][1]['topic'] == 'fleet'
    assert calls[-1][2] == {'Authorization': 'Bearer t'}
    client.post('/api/alerts/test', headers=admin_headers, json={'channel': {'type': 'teams', 'url': 'https://teams.test/w'}})
    assert calls[-1][1]['attachments'][0]['contentType'] == 'application/vnd.microsoft.card.adaptive'
    bad = client.post('/api/alerts/test', headers=admin_headers,
                      json={'channel': {'type': 'slack', 'url': 'https://broken.test/x'}}).json()
    assert bad['ok'] is False and '404' in bad['error']


def test_deleting_a_device_removes_its_history(client, admin_headers):
    did, h = enroll(client, 'fp-delete-history')
    heartbeat(client, did, h, api_ok=False)
    monitor.check(time.time(), lambda *a: None)
    assert client.delete(f'/api/devices/{did}', headers=admin_headers).status_code == 200
    with core.db() as c:
        for table in ('metrics', 'issues', 'events'):
            assert not c.execute(f'SELECT 1 FROM {table} WHERE device_id=?', (did,)).fetchone()
