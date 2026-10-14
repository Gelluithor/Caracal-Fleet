"""Commands reach a waiting agent within a moment (long polling), and an old agent still gets them at once."""
import threading
import time

import requests

from conftest import serve


def test_waiting_agent_gets_a_command_at_once(hub_app):
    hub, _ = serve(hub_app)
    h = {'Authorization': 'Bearer ' + requests.post(hub + '/api/login', json={'username': 'admin', 'password': 'admin-password-123'}).json()['token']}
    dev = requests.post(hub + '/api/device/enroll', json={'enroll_token': 'enroll-test-token', 'fingerprint': 'long-poll', 'name': 'LP'}).json()
    dh = {'X-Device-Token': dev['device_token']}
    requests.post(f"{hub}/api/device/{dev['device_id']}/heartbeat", headers=dh, json={'version': '4.15.0', 'api_ok': True})
    # nothing queued: an old agent (no wait) gets an empty list at once, a new one waits
    t0 = time.time()
    r = requests.get(f"{hub}/api/device/{dev['device_id']}/commands", headers=dh)
    assert r.json() == [] and r.headers['X-Long-Poll'] == '1' and time.time() - t0 < 1
    t0 = time.time()
    assert requests.get(f"{hub}/api/device/{dev['device_id']}/commands?wait=1", headers=dh).json() == []
    assert 0.9 < time.time() - t0 < 3
    got = {}

    def wait():
        t = time.time()
        got['cmds'] = requests.get(f"{hub}/api/device/{dev['device_id']}/commands?wait=20", headers=dh, timeout=30).json()
        got['at'] = time.time()
        got['waited'] = got['at'] - t
    th = threading.Thread(target=wait)
    th.start()
    time.sleep(1)
    sent = time.time()
    assert requests.post(f"{hub}/api/devices/{dev['device_id']}/commands", headers=h, json={'action': 'next', 'payload': {}}).status_code == 200
    th.join(10)
    assert [c['action'] for c in got['cmds']] == ['next']
    assert got['at'] - sent < 1.5 and got['waited'] < 5
