"""First-run setup, branding, security headers, login limits and backup / restore (server migration)."""
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from app import core, system

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 64
PATH_NAMES = ('DATA', 'DB_PATH', 'CFG_PATH', 'FILES', 'BRANDING', 'RESTORE_DIR', 'SETUP_CODE')


@pytest.fixture
def isolated(tmp_path, monkeypatch, hub_app):
    """A hub instance with its own, empty data directory."""
    def make(name, admin_password='admin-password-123'):
        data = tmp_path / name
        paths = {'DATA': data, 'DB_PATH': data / 'hub.db', 'CFG_PATH': data / 'config.json', 'FILES': data / 'files',
                 'BRANDING': data / 'branding', 'RESTORE_DIR': data / 'restore-pending',
                 'SETUP_CODE': data / 'setup-code'}
        from app import main
        for k, v in paths.items():
            for module in (core, system, main):
                if hasattr(module, k):
                    monkeypatch.setattr(module, k, v)
        if admin_password:
            monkeypatch.setenv('CARACAL_HUB_ADMIN_PASSWORD', admin_password)
        else:
            monkeypatch.delenv('CARACAL_HUB_ADMIN_PASSWORD', raising=False)
        monkeypatch.delenv('CARACAL_HUB_ENROLL_TOKEN', raising=False)
        core.init()
        return TestClient(hub_app)
    return make


def login(client, username='admin', password='admin-password-123'):
    r = client.post('/api/login', json={'username': username, 'password': password})
    assert r.status_code == 200, r.text
    return {'Authorization': 'Bearer ' + r.json()['token']}


def test_first_admin_is_created_in_the_ui(isolated):
    client = isolated('fresh', admin_password=None)
    assert client.get('/api/public').json()['setup_required'] is True
    code = core.SETUP_CODE.read_text().strip()
    body = {'username': 'boss', 'password': 'long-password-1'}
    assert client.post('/api/setup', json={**body, 'setup_code': 'WRONG'}).status_code == 401
    r = client.post('/api/setup', json={**body, 'setup_code': code.lower()})
    assert r.status_code == 200 and r.json()['user']['role'] == 'admin'
    assert client.get('/api/me', headers={'Authorization': 'Bearer ' + r.json()['token']}).status_code == 200
    assert client.post('/api/setup', json={**body, 'setup_code': code}).status_code == 409
    assert client.get('/api/public').json()['setup_required'] is False and not core.SETUP_CODE.exists()


def test_logo_upload_and_safe_serving(isolated):
    client = isolated('logo')
    h = login(client)
    default = client.get('/api/public').json()['logo']   # repository logo (hub/app/static/brand) or None
    bad = client.post('/api/branding/logo', content=b'not an image', headers={**h, 'Content-Type': 'image/png'})
    assert bad.status_code == 400
    assert client.post('/api/branding/logo', content=PNG, headers={**h, 'Content-Type': 'image/gif'}).status_code == 400
    r = client.post('/api/branding/logo', content=PNG, headers={**h, 'Content-Type': 'image/png'})
    assert r.status_code == 200 and r.json()['logo'].startswith('/branding/logo')
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    assert client.post('/api/branding/logo', content=svg, headers={**h, 'Content-Type': 'image/svg+xml'}).status_code == 200
    served = client.get('/branding/logo')
    assert served.headers['content-type'].startswith('image/svg+xml')
    assert 'sandbox' in served.headers['content-security-policy']   # scripts in SVG cannot run
    # removing the uploaded logo falls back to the repository logo
    after = client.delete('/api/branding/logo', headers=h).json()['logo']
    assert (after is None) == (default is None) and after != r.json()['logo']


def test_security_headers(client):
    for path in ('/', '/api/health', '/static/app.js'):
        r = client.get(path)
        assert r.headers['x-frame-options'] == 'DENY' and r.headers['x-content-type-options'] == 'nosniff'
        assert "script-src 'self'" in r.headers['content-security-policy']
    assert client.get('/openapi.json').status_code == 404


def test_login_is_rate_limited(client):
    for _ in range(8):
        assert client.post('/api/login', json={'username': 'ratelimited', 'password': 'x'}).status_code == 401
    assert client.post('/api/login', json={'username': 'ratelimited', 'password': 'x'}).status_code == 429
    # other accounts are not affected
    assert client.post('/api/login', json={'username': 'admin', 'password': 'admin-password-123'}).status_code == 200


def test_device_cannot_upload_without_export(client):
    dev = client.post('/api/device/enroll', json={'enroll_token': 'enroll-test-token', 'fingerprint': 'fp-up'}).json()
    r = client.post(f"/api/device/{dev['device_id']}/files", content=b'x',
                    headers={'X-Device-Token': dev['device_token'], 'X-File-Kind': 'image'})
    assert r.status_code == 403


def test_backup_and_restore_on_another_server(isolated, monkeypatch):
    # --- old server with data
    old = isolated('old')
    h = login(old)
    dev = old.post('/api/device/enroll', json={'enroll_token': core.cfg()['enroll_token'], 'fingerprint': 'mig'}).json()
    old.post('/api/org/groups', headers=h, json={'name': 'Výroba'})
    old.post('/api/users', headers=h, json={'username': 'operator1', 'password': 'long-password-1', 'role': 'operator'})
    f = old.post('/api/files', content=PNG, headers={**h, 'Content-Type': 'image/png', 'X-File-Name': 'logo.png'}).json()
    pid = old.post('/api/global-playlists', headers=h, json={'name': 'Všechny obrazovky'}).json()['id']
    old.post(f'/api/global-playlists/{pid}/items', headers=h, json={'kind': 'image', 'file_id': f['id'], 'duration': 10})
    old.post('/api/branding/logo', content=PNG, headers={**h, 'Content-Type': 'image/png'})
    secret, enroll = core.cfg()['secret'], core.cfg()['enroll_token']
    r = old.get('/api/backup', headers=h)
    assert r.status_code == 200 and r.headers['content-type'] == 'application/zip'
    archive = r.content
    names = set(zipfile.ZipFile(io.BytesIO(archive)).namelist())
    assert {'manifest.json', 'hub.db', 'config.json', 'branding/logo.png', 'files/' + f['id']} <= names

    # --- new, empty server
    new = isolated('new')
    h2 = login(new)
    restarts = []
    monkeypatch.setattr(system, 'restart_process', lambda: restarts.append(1))
    evil = io.BytesIO()
    with zipfile.ZipFile(evil, 'w') as z:
        z.writestr('../../etc/passwd', 'x')
    assert new.post('/api/backup/restore', content=evil.getvalue(), headers=h2).status_code == 400
    r = new.post('/api/backup/restore', content=archive, headers=h2)
    assert r.status_code == 200 and r.json()['restart']
    core.init()   # what happens when the container starts again

    assert core.cfg()['secret'] == secret and core.cfg()['enroll_token'] == enroll
    h3 = login(new)   # sessions of the new server are invalid, accounts come from the backup
    assert login(new, 'operator1', 'long-password-1')
    devices = new.get('/api/devices', headers=h3).json()['devices']
    assert [d['id'] for d in devices] == [dev['device_id']]
    ping = new.get(f"/api/device/{dev['device_id']}/ping", headers={'X-Device-Token': dev['device_token']})
    assert ping.status_code == 200   # agents keep working with their tokens
    p = new.get(f'/api/global-playlists/{pid}', headers=h3).json()
    assert p['items'][0]['sha256'] and (core.FILES / f['id']).read_bytes() == PNG
    assert new.get('/api/public').json()['logo'] and any(core.DATA.glob('pre-restore-*'))
    assert 'system.restore_applied' in [a['action'] for a in new.get('/api/audit', headers=h3).json()]
