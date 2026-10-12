"""Staged updates of Docker nodes: pilots first, batches after a healthy wait, a failure stops the rollout."""
import pytest

CAPS = {'notify': True}


@pytest.fixture()
def image():
    from app import images
    old = images.node_image
    images.node_image = lambda: 'ghcr.io/owner/caracal-node'
    yield
    images.node_image = old


def node(client, name, version='2026.10.11'):
    dev = client.post('/api/device/enroll', json={'enroll_token': 'enroll-test-token', 'fingerprint': 'ro-' + name, 'name': name}).json()
    beat(client, dev, version)
    return dev


def beat(client, dev, version):
    r = client.post(f"/api/device/{dev['device_id']}/heartbeat", headers={'X-Device-Token': dev['device_token']},
                    json={'version': '4.14.0', 'api_ok': True, 'capabilities': CAPS, 'runtime': 'docker', 'caracal_version': version,
                          'player': {'player_online': True}})
    assert r.status_code == 200, r.text


def take(client, dev):
    return client.get(f"/api/device/{dev['device_id']}/commands", headers={'X-Device-Token': dev['device_token']}).json()


def finish(client, dev, cmd, ok=True):
    client.post(f"/api/device/{dev['device_id']}/commands/{cmd['id']}/result", headers={'X-Device-Token': dev['device_token']},
                json={'ok': ok, 'result': 'updated' if ok else 'container exited, previous version restored'})


def rollout(client, h, rid):
    return next(x for x in client.get('/api/rollouts', headers=h).json()['rollouts'] if x['id'] == rid)


def tick():
    from app import rollout
    rollout.tick()


def test_staged_update(client, admin_headers, image):
    h = admin_headers
    a, b, c, d = (node(client, f'ro-{i}') for i in range(4))
    ids = [x['device_id'] for x in (a, b, c, d)]
    assert client.post('/api/rollouts', headers=h, json={'version': 'bad version', 'device_ids': ids}).json()['detail'] == 'version_required'
    rid = client.post('/api/rollouts', headers=h, json={'version': '2026.10.12', 'device_ids': ids, 'pilot_ids': [a['device_id']],
                                                        'batch': 2, 'wait': 0}).json()['id']
    assert client.post('/api/rollouts', headers=h, json={'version': '2026.10.12', 'device_ids': ids}).json()['detail'] == 'rollout_running'
    ro = rollout(client, h, rid)
    assert [s['devices'] for s in ro['stages']] == [[a['device_id']], ids[1:3], ids[3:]]
    # stage 1: only the pilot
    got = take(client, a)
    assert [x['action'] for x in got] == ['update_caracal'] and got[0]['payload']['version'] == '2026.10.12'
    assert take(client, b) == []
    finish(client, a, got[0])
    beat(client, a, '2026.10.12')
    tick()                          # updated -> checking
    tick()                          # healthy -> next stage
    tick()                          # stage 2 starts
    assert rollout(client, h, rid)['current'] == 1
    gb, gc = take(client, b), take(client, c)
    assert take(client, d) == []
    finish(client, b, gb[0])
    finish(client, c, gc[0], ok=False)   # the update failed (and rolled back) on one node
    tick()
    ro = rollout(client, h, rid)
    assert ro['state'] == 'stopped' and ro['stages'][1]['state'] == 'failed' and c['device_id'] in ro['stages'][1]['failed']
    tick()
    assert take(client, d) == []    # the rest is not touched
    # resume: the stage is tried again
    assert client.post(f'/api/rollouts/{rid}/resume', headers=h).status_code == 200
    for dev in (b, c):
        cmd = take(client, dev)[0]
        finish(client, dev, cmd)
        beat(client, dev, '2026.10.12')
    tick()
    tick()
    tick()
    gd = take(client, d)
    finish(client, d, gd[0])
    beat(client, d, '2026.10.11')   # reports the old version: not healthy
    tick()
    tick()
    ro = rollout(client, h, rid)
    assert ro['state'] == 'stopped' and ro['stages'][2]['unhealthy'] == {d['device_id']: 'version_mismatch'}
    client.post(f'/api/rollouts/{rid}/cancel', headers=h)
    assert rollout(client, h, rid)['state'] == 'cancelled'
    assert client.post(f'/api/rollouts/{rid}/resume', headers=h).json()['detail'] == 'rollout_finished'


def test_wait_before_the_next_stage(client, admin_headers, image):
    import time
    from app import rollout as ro_mod
    h = admin_headers
    a, b = node(client, 'wait-a'), node(client, 'wait-b')
    rid = client.post('/api/rollouts', headers=h, json={'version': '2026.10.12', 'device_ids': [a['device_id'], b['device_id']],
                                                        'pilot_ids': [a['device_id']], 'batch': 5, 'wait': 30}).json()['id']
    finish(client, a, take(client, a)[0])
    beat(client, a, '2026.10.12')
    ro_mod.tick()
    ro_mod.tick(time.time() + 60)            # only a minute later: still waiting
    assert take(client, b) == []
    ro_mod.tick(time.time() + 31 * 60)
    ro_mod.tick(time.time() + 31 * 60)
    assert [x['action'] for x in take(client, b)] == ['update_caracal']
    client.post(f'/api/rollouts/{rid}/cancel', headers=h)


def test_automatic_updates(client, admin_headers, image, monkeypatch):
    from datetime import datetime, timezone
    from app import images, rollout as ro_mod
    h = admin_headers
    client.put('/api/schedules/settings', headers=h, json={'timezone': 'UTC'})
    monkeypatch.setattr(images, 'latest_version', lambda: '2026.10.20')
    for x in client.get('/api/rollouts', headers=h).json()['rollouts']:
        if x['state'] in ('running', 'paused', 'stopped'):
            client.post(f"/api/rollouts/{x['id']}/cancel", headers=h)
    a = node(client, 'auto-a')
    client.post('/api/devices/assign', headers=h, json={'device_ids': [a['device_id']], 'group': 'Pilots'})
    assert client.put('/api/rollouts/auto', headers=h, json={'enabled': True, 'hour': 4, 'pilot_group': 'Pilots', 'batch': 50, 'wait': 10}).json()['hour'] == 4
    three = datetime(2026, 10, 20, 3, 30, tzinfo=timezone.utc).timestamp()
    four = datetime(2026, 10, 20, 4, 5, tzinfo=timezone.utc).timestamp()
    ro_mod.tick(three)
    assert not any(x['auto'] for x in client.get('/api/rollouts', headers=h).json()['rollouts'] if x['version'] == '2026.10.20')
    ro_mod.tick(four)
    auto = [x for x in client.get('/api/rollouts', headers=h).json()['rollouts'] if x['auto']]
    assert auto and auto[0]['version'] == '2026.10.20' and auto[0]['stages'][0]['devices'] == [a['device_id']]
    ro_mod.tick(four + 60)                  # once a day
    assert len([x for x in client.get('/api/rollouts', headers=h).json()['rollouts'] if x['auto']]) == 1
    client.post(f"/api/rollouts/{auto[0]['id']}/cancel", headers=h)
    client.put('/api/rollouts/auto', headers=h, json={'enabled': False})
