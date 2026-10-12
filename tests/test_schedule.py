"""Schedules (playlists by time, screen hours) and scheduled actions."""
from datetime import datetime, timezone

CAPS = {'notify': True, 'notify_settings': True, 'upload': True, 'add_grafana_tag': True}


def at(day, hhmm):
    """Timestamp of 2026-10-<day> HH:MM UTC (2026-10-12 is a Monday)."""
    h, m = hhmm.split(':')
    return datetime(2026, 10, day, int(h), int(m), tzinfo=timezone.utc).timestamp()


def enroll(client, name, version='4.14.0', group='', screen='on'):
    dev = client.post('/api/device/enroll', json={'enroll_token': 'enroll-test-token', 'fingerprint': 'sch-' + name,
                                                  'name': name}).json()
    beat(client, dev, version=version, screen=screen)
    return dev


def beat(client, dev, version='4.14.0', screen='on'):
    r = client.post(f"/api/device/{dev['device_id']}/heartbeat", headers={'X-Device-Token': dev['device_token']},
                    json={'version': version, 'api_ok': True, 'capabilities': CAPS, 'runtime': 'docker', 'screen': screen})
    assert r.status_code == 200, r.text


def delivered(client, dev):
    return client.get(f"/api/device/{dev['device_id']}/commands", headers={'X-Device-Token': dev['device_token']}).json()


def playlist(client, h, name, url):
    pid = client.post('/api/global-playlists', headers=h, json={'name': name}).json()['id']
    client.post(f'/api/global-playlists/{pid}/items', headers=h, json={'kind': 'web', 'name': name, 'source': url})
    return pid


def tick(ts):
    from app import schedule
    schedule.tick(ts)


def test_time_windows():
    from app.schedule import covers
    tz = timezone.utc
    work = {'days': [0, 1, 2, 3, 4], 'start': '08:00', 'end': '17:00'}
    assert covers(work, datetime(2026, 10, 12, 8, 0, tzinfo=tz)) and not covers(work, datetime(2026, 10, 12, 17, 0, tzinfo=tz))
    assert not covers(work, datetime(2026, 10, 17, 10, 0, tzinfo=tz))           # Saturday
    night = {'days': [4], 'start': '22:00', 'end': '06:00'}                     # Friday night into Saturday
    assert covers(night, datetime(2026, 10, 16, 23, 0, tzinfo=tz)) and covers(night, datetime(2026, 10, 17, 5, 59, tzinfo=tz))
    assert not covers(night, datetime(2026, 10, 17, 23, 0, tzinfo=tz)) and not covers(night, datetime(2026, 10, 16, 5, 0, tzinfo=tz))
    evening = {'days': [6], 'start': '18:00', 'end': '00:00'}                   # until midnight
    assert covers(evening, datetime(2026, 10, 18, 23, 59, tzinfo=tz)) and not covers(evening, datetime(2026, 10, 19, 0, 0, tzinfo=tz))


def test_schedule_validation_and_timezone(client, admin_headers):
    h = admin_headers
    assert client.put('/api/schedules/settings', headers=h, json={'timezone': 'Mars/Base'}).json()['detail'] == 'invalid_timezone'
    assert client.put('/api/schedules/settings', headers=h, json={'timezone': 'UTC'}).status_code == 200
    pid = playlist(client, h, 'Morning', 'https://example.com/m')
    base = {'name': 'S', 'targets': {'groups': ['Lobby']}}
    assert client.post('/api/schedules', headers=h, json={**base, 'targets': {}}).json()['detail'] == 'no_devices'
    assert client.post('/api/schedules', headers=h, json=base).json()['detail'] == 'schedule_empty'
    bad = {**base, 'slots': [{'days': [0], 'start': '25:00', 'end': '10:00', 'playlist_id': pid}]}
    assert client.post('/api/schedules', headers=h, json=bad).json()['detail'] == 'invalid_time'
    bad = {**base, 'slots': [{'days': [7], 'start': '08:00', 'end': '10:00', 'playlist_id': pid}]}
    assert client.post('/api/schedules', headers=h, json=bad).json()['detail'] == 'invalid_days'
    bad = {**base, 'slots': [{'days': [0], 'start': '08:00', 'end': '10:00', 'playlist_id': 99999}]}
    assert client.post('/api/schedules', headers=h, json=bad).json()['detail'] == 'playlist_not_found'
    bad = {'name': 'T', 'targets': {'groups': ['x']}, 'time': '08:00', 'days': [0], 'action': 'format_disk'}
    assert client.post('/api/scheduled-tasks', headers=h, json=bad).json()['detail'] == 'unknown_action'
    assert client.post('/api/scheduled-tasks', headers=h, json={**bad, 'action': 'reboot', 'days': [], 'date': '2026-02-30'}).json()['detail'] == 'invalid_date'


def test_playlists_and_screen_by_time(client, admin_headers):
    h = admin_headers
    client.put('/api/schedules/settings', headers=h, json={'timezone': 'UTC'})
    morning, day, night = (playlist(client, h, n, f'https://example.com/{n}') for n in ('am', 'day', 'night'))
    dev = enroll(client, 'lobby-1')
    old = enroll(client, 'lobby-old', version='4.12.0')
    for d in (dev, old):
        client.post('/api/devices/assign', headers=h, json={'device_ids': [d['device_id']], 'group': 'Sched lobby'})
    sid = client.post('/api/schedules', headers=h, json={
        'name': 'Lobby', 'targets': {'groups': ['Sched lobby']}, 'default_playlist_id': night,
        'slots': [{'days': [0, 1, 2, 3, 4], 'start': '07:00', 'end': '10:00', 'playlist_id': morning},
                  {'days': [0, 1, 2, 3, 4], 'start': '10:00', 'end': '18:00', 'playlist_id': day}],
        'screen': [{'days': [0, 1, 2, 3, 4], 'start': '07:00', 'end': '20:00'}]}).json()['id']
    listed = client.get('/api/schedules', headers=h).json()
    s = next(x for x in listed['schedules'] if x['id'] == sid)
    assert sorted(s['devices']) == sorted([dev['device_id'], old['device_id']])

    tick(at(12, '08:00'))
    cmds = delivered(client, dev)
    assert [c['action'] for c in cmds] == ['import_playlist']
    assert cmds[0]['payload']['global_playlist'] == 'am' and cmds[0]['payload']['mode'] == 'replace'
    assert [c['action'] for c in delivered(client, old)] == ['import_playlist']   # playlists work with any agent
    tick(at(12, '09:00'))                       # the same slot: nothing again
    assert delivered(client, dev) == []
    tick(at(12, '12:00'))
    assert [c['payload']['global_playlist'] for c in delivered(client, dev)] == ['day']
    beat(client, dev, screen='on')
    tick(at(12, '21:00'))                       # evening: the default playlist, the screen goes off
    got = {c['action']: c['payload'] for c in delivered(client, dev)}
    assert got['import_playlist']['global_playlist'] == 'night' and got['screen'] == {'state': 'off'}
    assert 'screen' not in {c['action'] for c in delivered(client, old)}       # agent 4.12 cannot switch the screen
    tick(at(12, '21:01'))                       # the screen still reports on: not sent again right away
    assert delivered(client, dev) == []
    beat(client, dev, screen='off')
    tick(at(13, '07:30'))
    got = {c['action']: c['payload'] for c in delivered(client, dev)}
    assert got['screen'] == {'state': 'on'} and got['import_playlist']['global_playlist'] == 'am'
    # editing the schedule deploys its current playlist again
    client.patch(f'/api/schedules/{sid}', headers=h, json={'enabled': True})
    beat(client, dev, screen='on')
    tick(at(13, '07:40'))
    assert [c['action'] for c in delivered(client, dev)] == ['import_playlist']
    client.delete(f'/api/schedules/{sid}', headers=h)
    tick(at(13, '12:00'))
    assert delivered(client, dev) == []


def test_scheduled_actions(client, admin_headers):
    h = admin_headers
    client.put('/api/schedules/settings', headers=h, json={'timezone': 'UTC'})
    dev = enroll(client, 'task-1')
    tid = client.post('/api/scheduled-tasks', headers=h, json={
        'name': 'Nightly restart', 'targets': {'devices': [dev['device_id'], 'CRCL-MISSING']}, 'time': '03:00',
        'days': [0, 1, 2, 3, 4, 5, 6], 'action': 'restart_player'}).json()['id']
    note = client.post('/api/scheduled-tasks', headers=h, json={
        'name': 'Lunch', 'targets': {'devices': [dev['device_id']]}, 'time': '11:55', 'date': '2026-10-14',
        'action': 'notify', 'payload': {'title': 'Lunch', 'message': 'In 5 minutes', 'level': 'info'}}).json()['id']
    from app.core import db
    with db() as c:   # created "now" (after 03:00 today in real time): forget that, the test runs on its own clock
        c.execute("UPDATE scheduled_tasks SET last_day=''")
    tick(at(12, '02:59'))
    assert delivered(client, dev) == []
    tick(at(12, '03:00'))
    assert [c['action'] for c in delivered(client, dev)] == ['restart_player']
    tick(at(12, '03:05'))                      # once a day
    assert delivered(client, dev) == []
    tick(at(13, '03:20'))                      # too late (the hub was down for longer than 10 minutes)
    assert delivered(client, dev) == []
    tick(at(14, '11:56'))
    got = delivered(client, dev)
    assert [c['action'] for c in got] == ['notify'] and got[0]['payload']['title'] == 'Lunch'
    tick(at(21, '11:56'))                      # a date task runs only on its date
    assert delivered(client, dev) == []
    tasks = {t['id']: t for t in client.get('/api/schedules', headers=h).json()['tasks']}
    assert tasks[note]['last_result']['queued'] == [dev['device_id']]
    # run now: offline devices are skipped with a reason
    r = client.post(f'/api/scheduled-tasks/{tid}/run', headers=h).json()
    assert r['queued'] == [dev['device_id']]
    off = client.post('/api/scheduled-tasks', headers=h, json={'name': 'Screens off', 'targets': {'devices': [dev['device_id']]},
                                                               'time': '22:00', 'days': [0], 'action': 'screen_off'}).json()['id']
    r = client.post(f'/api/scheduled-tasks/{off}/run', headers=h).json()
    assert r['queued'] == [dev['device_id']]
    assert {c['action']: c['payload'] for c in delivered(client, dev)}['screen'] == {'state': 'off'}
    assert client.post('/api/scheduled-tasks/999999/run', headers=h).status_code == 404


def test_display_attention(client, admin_headers):
    import time
    dev = enroll(client, 'display')
    r = client.post(f"/api/device/{dev['device_id']}/heartbeat", headers={'X-Device-Token': dev['device_token']},
                    json={'version': '4.14.0', 'api_ok': True, 'capabilities': CAPS, 'screen': 'on',
                          'display': {'state': 'down', 'since': time.time() - 300, 'restarts': 2},
                          'screen_watch': {'black_since': time.time() - 600}})
    assert r.status_code == 200
    d = client.get(f"/api/devices/{dev['device_id']}", headers=admin_headers).json()
    codes = {a['code']: a for a in d['attention']}
    assert codes['display_down']['level'] == 'critical' and 'screen_black' not in codes
    client.post(f"/api/device/{dev['device_id']}/heartbeat", headers={'X-Device-Token': dev['device_token']},
                json={'version': '4.14.0', 'api_ok': True, 'capabilities': CAPS, 'screen': 'on', 'display': {'state': 'ok'},
                      'screen_watch': {'black_since': time.time() - 600}})
    codes = {a['code'] for a in client.get(f"/api/devices/{dev['device_id']}", headers=admin_headers).json()['attention']}
    assert 'screen_black' in codes and 'display_down' not in codes
    client.post(f"/api/device/{dev['device_id']}/heartbeat", headers={'X-Device-Token': dev['device_token']},
                json={'version': '4.14.0', 'api_ok': True, 'capabilities': CAPS, 'screen': 'off',
                      'screen_watch': {'black_since': time.time() - 600}})
    codes = {a['code'] for a in client.get(f"/api/devices/{dev['device_id']}", headers=admin_headers).json()['attention']}
    assert 'screen_black' not in codes        # switched off on purpose
