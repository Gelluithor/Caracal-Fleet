"""Node templates: parts, applying to nodes (with capability checks), automatic application after enrollment and the
template choice of zero-touch media and installations."""
import io
import json
import tarfile
import zipfile

CAPS = {'notify': True, 'notify_settings': True, 'notify_image': True, 'overlay': True, 'upload': True,
        'add_grafana_tag': True}


def enroll(client, name, template=None, fingerprint=None):
    body = {'enroll_token': 'enroll-test-token', 'fingerprint': fingerprint or 'tpl-' + name, 'name': name}
    if template is not None:
        body['template'] = template
    r = client.post('/api/device/enroll', json=body)
    assert r.status_code == 200, r.text
    return r.json()


def beat(client, dev, caps=CAPS, **extra):
    r = client.post(f"/api/device/{dev['device_id']}/heartbeat", headers={'X-Device-Token': dev['device_token']},
                    json={'api_ok': True, 'capabilities': caps, 'notifications': {'settings': {'position': 'top-right'}},
                          **extra})
    assert r.status_code == 200, r.text


def queued(client, dev):
    return {c['action']: c['payload'] for c in client.get(
        f"/api/device/{dev['device_id']}/commands", headers={'X-Device-Token': dev['device_token']}).json()}


def picture(client, h):
    png = b'\x89PNG\r\n\x1a\n' + b'\0' * 64
    r = client.post('/api/files', headers={**h, 'X-File-Name': 'logo.png', 'Content-Type': 'image/png'}, content=png)
    assert r.status_code == 200, r.text
    return r.json()['id']


def make_template(client, h, **parts):
    r = client.post('/api/templates', headers=h, json={'name': 'Lobby', **parts})
    assert r.status_code == 200, r.text
    return r.json()['id']


def test_template_parts_and_apply(client, admin_headers):
    h = admin_headers
    pid = client.post('/api/global-playlists', headers=h, json={'name': 'Lobby loop'}).json()['id']
    client.post(f'/api/global-playlists/{pid}/items', headers=h,
                json={'kind': 'web', 'name': 'Dash', 'source': 'https://example.com/d', 'duration': 30})
    tid = make_template(client, h, playlist_id=pid, playlist_mode='replace', overlay={'enabled': False, 'size': 20},
                        notify={'position': 'bottom-left', 'duration': 12, 'style': {'radius': 20}, 'bogus': 1},
                        group='Lobby screens', location='HQ')
    fid = picture(client, h)
    assert client.post(f'/api/templates/{tid}/image', headers=h, json={'file_id': fid}).status_code == 200
    t = client.get(f'/api/templates/{tid}', headers=h).json()
    assert t['notify'] == {'position': 'bottom-left', 'duration': 12, 'style': {'radius': 20}}   # unknown keys dropped
    assert t['playlist'] == {'name': 'Lobby loop', 'items': 1} and t['image']['name'] == 'logo.png'
    assert client.get(f'/api/templates/{tid}/image', headers=h).status_code == 200

    new, old = enroll(client, 'tpl-new'), enroll(client, 'tpl-old')
    beat(client, new)
    beat(client, old, caps={'notify': True, 'notify_settings': True})   # an older agent: no picture, no overlay
    r = client.post(f'/api/templates/{tid}/apply', headers=h, json={'target_ids': [new['device_id'], old['device_id']]})
    assert r.status_code == 200, r.text
    res = {x['device_id']: x for x in r.json()['results']}
    assert res[new['device_id']]['queued'] == ['organization', 'notify_image', 'notify_settings', 'overlay_settings',
                                               'import_playlist']
    assert {s['action'] for s in res[old['device_id']]['skipped']} == {'notify_image'}
    cmds = queued(client, new)
    assert cmds['notify_settings']['position'] == 'bottom-left' and cmds['overlay_settings'] == {'enabled': False, 'size': 20}
    assert cmds['import_playlist']['replace_collections'] is False   # no collections in this playlist
    assert cmds['import_playlist']['mode'] == 'replace' and cmds['import_playlist']['items'][0]['source'] == 'https://example.com/d'
    assert cmds['notify_image']['filename'] == 'logo.png'
    dev = client.get(f"/api/devices/{new['device_id']}", headers=h).json()
    assert dev['group'] == 'Lobby screens' and dev['location'] == 'HQ'
    applied = client.get(f'/api/templates/{tid}', headers=h).json()['applied']
    assert {x['device_id'] for x in applied} == {new['device_id'], old['device_id']}
    # the picture stays while the template uses it
    with __import__('app.core', fromlist=['db']).db() as c:
        assert c.execute('SELECT pinned FROM files WHERE id=?', (fid,)).fetchone()[0] == 1


def test_validation_and_permissions(client, admin_headers):
    h = admin_headers
    assert client.post('/api/templates', headers=h, json={'name': ''}).json()['detail'] == 'name_required'
    assert client.post('/api/templates', headers=h, json={'name': 'x', 'playlist_id': 99999}).json()['detail'] == 'playlist_not_found'
    assert client.post('/api/templates', headers=h, json={'name': 'x', 'notify': {'position': 'nowhere'}}).json()['detail'] == 'invalid_value'
    assert client.post('/api/templates', headers=h, json={'name': 'x', 'overlay': {'size': 1000}}).json()['detail'] == 'invalid_value'
    tid = make_template(client, h, group='G')
    assert client.patch(f'/api/templates/{tid}', headers=h, json={'group': ''}).status_code == 200
    assert 'group' not in client.get(f'/api/templates/{tid}', headers=h).json()
    client.post('/api/users', headers=h, json={'username': 'tpl-operator', 'password': 'operator-pass-123', 'role': 'operator'})
    op = {'Authorization': 'Bearer ' + client.post('/api/login', json={'username': 'tpl-operator', 'password': 'operator-pass-123'}).json()['token']}
    assert client.get('/api/templates', headers=op).status_code == 200
    assert client.post('/api/templates', headers=op, json={'name': 'nope'}).status_code == 403
    assert client.post(f'/api/templates/{tid}/apply', headers=op, json={'target_ids': ['x']}).status_code == 403


def test_template_applied_after_enrollment(client, admin_headers):
    h = admin_headers
    tid = make_template(client, h, notify={'duration': 9}, location='Branch')
    dev = enroll(client, 'tpl-zero', template=str(tid))
    assert queued(client, dev) == {}                       # nothing until the node reports what it supports
    beat(client, dev, caps={}, api_ok=False)               # the node's API is not up yet
    assert queued(client, dev) == {}
    beat(client, dev)
    assert queued(client, dev) == {'notify_settings': {'duration': 9}}
    beat(client, dev)                                      # only once
    assert queued(client, dev) == {}
    assert client.get(f"/api/devices/{dev['device_id']}", headers=h).json()['location'] == 'Branch'
    applied = client.get(f'/api/templates/{tid}', headers=h).json()['applied']
    assert applied[0]['automatic'] and applied[0]['device_id'] == dev['device_id']
    # an unknown template does not stop the enrollment; a deleted one is forgotten
    enroll(client, 'tpl-unknown', template='424242')
    tid2 = make_template(client, h, notify={'duration': 7})
    gone = enroll(client, 'tpl-gone', template=tid2)
    client.delete(f'/api/templates/{tid2}', headers=h)
    beat(client, gone)
    assert queued(client, gone) == {}


def test_template_from_device(client, admin_headers):
    h = admin_headers
    dev = enroll(client, 'tpl-source')
    beat(client, dev, notifications={'settings': {'position': 'center', 'duration': 15, 'style': {'radius': 4},
                                                  'sound_device': 'hw:1'}},
         overlay={'enabled': True, 'size': 30},
         assets=[{'id': 1, 'name': 'Web', 'kind': 'web', 'source': 'https://example.com', 'duration': 20},
                 {'id': 2, 'name': 'Clip', 'kind': 'video', 'source': 'clip.mp4', 'duration': 0}])
    r = client.post('/api/templates/from-device', headers=h, json={'device_id': dev['device_id'], 'name': 'Copied', 'playlist': True})
    assert r.status_code == 200, r.text
    assert r.json()['skipped'] == ['Clip']
    t = client.get(f"/api/templates/{r.json()['id']}", headers=h).json()
    assert t['notify'] == {'position': 'center', 'duration': 15, 'style': {'radius': 4}}   # no device-specific keys
    assert t['overlay'] == {'enabled': True, 'size': 30} and t['playlist']['items'] == 1 and t['playlist_mode'] == 'replace'


def test_zero_touch_media_carry_the_template(client, admin_headers):
    h = admin_headers
    client.put('/api/node-image', headers=h, json={'image': 'ghcr.io/example/caracal-node'})
    tid = make_template(client, h, notify={'duration': 10})
    base = {'hub_url': 'https://fleet.example', 'name_prefix': 'kiosk', 'template': tid, 'user': 'maint',
            'password': 'device-pass-1', 'ssh_key': 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample user@pc'}
    files = lambda r: {i.filename: zipfile.ZipFile(io.BytesIO(r.content)).read(i) for i in zipfile.ZipFile(io.BytesIO(r.content)).infolist()}

    r = client.post('/api/sdcard', headers=h, json={**base, 'os': 'raspios'})
    assert f'TEMPLATE={tid}\n' in files(r)['caracal-firstboot.conf'].decode()

    r = client.post('/api/sdcard', headers=h, json={**base, 'os': 'alpine', 'timezone': 'Europe/Prague', 'disk': '/dev/nvme0n1'})
    assert r.status_code == 200, r.text
    z = files(r)
    assert set(z) == {'caracal.apkovl.tar.gz', 'CARACAL-README.txt'}
    tar = tarfile.open(fileobj=io.BytesIO(z['caracal.apkovl.tar.gz']))
    members = {m.name: m for m in tar.getmembers()}
    assert {'etc/.default_boot_services', 'etc/hostname', 'etc/apk/world', 'etc/local.d/caracal.start',
            'etc/caracal/caracal-alpine.sh', 'etc/caracal/caracal-firstboot.sh', 'etc/caracal-firstboot.conf'} <= set(members)
    assert members['etc/runlevels/default/local'].issym() and members['etc/runlevels/default/local'].linkname == '/etc/init.d/local'
    assert members['etc/caracal-firstboot.conf'].mode == 0o600 and members['etc/local.d/caracal.start'].mode == 0o755
    conf = tar.extractfile('etc/caracal-firstboot.conf').read().decode()
    for line in (f'TEMPLATE={tid}', 'DISK=/dev/nvme0n1', 'TIMEZONE=Europe/Prague', 'LOGIN_USER=maint',
                 'LOGIN_PASSWORD=device-pass-1', 'TOKEN=enroll-test-token', 'NAME_PREFIX=kiosk'):
        assert line in conf.splitlines(), line
    assert tar.extractfile('etc/hostname').read() == b'kiosk\n'
    assert client.post('/api/sdcard', headers=h, json={**base, 'os': 'alpine', 'disk': '/dev/sda; reboot'}).json()['detail'] == 'invalid_disk'
    assert client.post('/api/sdcard', headers=h, json={**base, 'os': 'alpine', 'wifi_ssid': 'net', 'wifi_password': 'wifi-pass-1',
                                                       'wifi_country': 'CZ'}).json()['detail'] == 'wifi_unsupported'

    r = client.post('/api/sdcard', headers=h, json={**base, 'os': 'ubuntu', 'static_ip': '10.0.0.5/24', 'gateway': '10.0.0.1'})
    assert r.status_code == 200, r.text
    z = files(r)
    assert set(z) == {'user-data', 'meta-data', 'CARACAL-README.txt'}
    text = z['user-data'].decode()
    assert text.startswith('#cloud-config\n')
    auto = json.loads(text.split('\n', 2)[2])['autoinstall']
    assert auto['storage'] == {'layout': {'name': 'direct'}} and auto['ssh']['install-server'] is True
    assert auto['network']['ethernets']['wired']['addresses'] == ['10.0.0.5/24']
    ud = auto['user-data']
    assert ud['users'][0]['name'] == 'maint' and ud['users'][0]['plain_text_passwd'] == 'device-pass-1'
    conf = next(f for f in ud['write_files'] if f['path'] == '/etc/caracal-firstboot.conf')
    assert conf['permissions'] == '0600' and f'TEMPLATE={tid}\n' in conf['content']
    assert ud['runcmd'] == [['bash', '/var/lib/caracal-zero-touch/caracal-firstboot.sh', 'install']]
    assert client.post('/api/sdcard', headers=h, json={**base, 'os': 'ubuntu', 'template': 999999}).json()['detail'] == 'template_not_found'


def test_bootstrap_serves_the_pc_installers(client):
    for name in ('caracal-alpine.sh', 'caracal-install.sh'):
        r = client.get(f'/api/bootstrap/{name}')
        assert r.status_code == 200 and r.text.startswith('#!/bin/sh')


def test_alpine_repositories_through_the_hub(tmp_path):
    """The agent and install-node.sh point Alpine's repositories to the hub the same way (credentials in the address),
    and back to the internet."""
    import shutil
    import subprocess
    from conftest import ROOT, load_agent
    mod = load_agent()
    repos = 'https://dl-cdn.alpinelinux.org/alpine/v3.24/main\nhttps://dl-cdn.alpinelinux.org/alpine/v3.24/community\n'
    fleet = mod.apk_rewrite(repos, 'https://fleet.example', 'CRCL-1', 'tok/en')
    assert fleet.splitlines()[0] == 'https://CRCL-1:tok%2Fen@fleet.example/apt/dl-cdn.alpinelinux.org/alpine/v3.24/main'
    assert mod.apk_rewrite(fleet, 'https://new.example', 'CRCL-1', 't') .count('/apt/') == 2   # never nested
    assert mod.apk_rewrite(fleet) == repos
    bash = shutil.which('bash')
    if not bash:
        return
    src = (ROOT / 'hub/bootstrap/install-node.sh').read_text(encoding='utf-8')
    block = src[src.index('# --- download through the hub'):src.index('# --- end of download through the hub ---')]
    f = tmp_path / 'repositories'
    f.write_text(repos)
    script = tmp_path / 's.sh'
    script.write_text(block + '\napk_via_fleet https://fleet.example enroll secret\n')
    r = subprocess.run([bash, str(script)], capture_output=True, text=True, env={'CARACAL_APK_REPOSITORIES': str(f), 'PATH': '/usr/bin:/bin'})
    assert r.returncode == 0, r.stderr
    assert f.read_text() == mod.apk_rewrite(repos, 'https://fleet.example', 'enroll', 'secret')
