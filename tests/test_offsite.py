"""Backups outside the server (hub/app/offsite.py): encryption, a destination, uploads with checks and keeping the
newest ones, and restoring from a destination."""
import io
import os
import shutil

import pytest

from app import core, offsite, system


def test_encryption_round_trip(tmp_path):
    src, enc, out = tmp_path / 'a.zip', tmp_path / 'a.zip.enc', tmp_path / 'b.zip'
    data = os.urandom(3 * offsite.ENC_CHUNK + 1234)
    src.write_bytes(data)
    offsite.encrypt_file(src, enc, 'correct horse battery')
    assert offsite.is_encrypted(enc) and not offsite.is_encrypted(src)
    assert data[:64] not in enc.read_bytes()
    offsite.decrypt_file(enc, out, 'correct horse battery')
    assert out.read_bytes() == data
    with pytest.raises(offsite.TargetError, match='wrong_passphrase'):
        offsite.decrypt_file(enc, out, 'wrong horse battery')
    cut = tmp_path / 'cut.enc'
    cut.write_bytes(enc.read_bytes()[:-offsite.ENC_CHUNK])   # the last chunks are missing
    with pytest.raises(offsite.TargetError):
        offsite.decrypt_file(cut, out, 'correct horse battery')
    # an empty file still has its (empty, last) chunk
    (tmp_path / 'e').write_bytes(b'')
    offsite.encrypt_file(tmp_path / 'e', tmp_path / 'e.enc', 'p' * 12)
    offsite.decrypt_file(tmp_path / 'e.enc', out, 'p' * 12)
    assert out.read_bytes() == b''


def test_destination_settings_hide_secrets(client, admin_headers, tmp_path):
    r = client.post('/api/backup-targets', headers=admin_headers, json={
        'type': 's3', 'name': 'B2', 'endpoint': 'https://s3.eu-central-003.backblazeb2.com', 'region': 'eu-central-003',
        'bucket': 'caracal-backups', 'access_key': 'KEYID', 'secret_key': 'very-secret'})
    assert r.status_code == 200, r.text
    tid = r.json()['id']
    listed = client.get('/api/backup-targets', headers=admin_headers).json()['targets']
    t = next(x for x in listed if x['id'] == tid)
    assert t['secret_key'] == core.REDACTED and t['access_key'] == 'KEYID'
    client.patch(f'/api/backup-targets/{tid}', headers=admin_headers, json={**t, 'keep': 3})
    stored = next(x for x in offsite.settings()['targets'] if x['id'] == tid)
    assert stored['secret_key'] == 'very-secret' and stored['keep'] == 3
    bad = [{'type': 's3', 'endpoint': 'ftp://x', 'bucket': 'b', 'access_key': 'a', 'secret_key': 's'},
           {'type': 's3', 'endpoint': 'https://x', 'bucket': 'B!', 'access_key': 'a', 'secret_key': 's'},
           {'type': 'folder', 'path': str(core.DATA)}, {'type': 'folder', 'path': '/etc'}, {'type': 'nfs', 'server': 'nas', 'export': '/'},
           {'type': 'rclone', 'config': 'nothing'}, {'type': 'sftp', 'host': 'nas', 'username': 'u'}, {'type': 'ftp'}]
    for b in bad:
        assert client.post('/api/backup-targets', headers=admin_headers, json=b).status_code == 400, b
    client.delete(f'/api/backup-targets/{tid}', headers=admin_headers)


def test_upload_check_keep_and_restore(client, admin_headers, tmp_path, monkeypatch):
    folder = tmp_path / 'nas'
    folder.mkdir()
    r = client.post('/api/backup-targets', headers=admin_headers, json={'type': 'folder', 'name': 'USB', 'path': str(folder), 'keep': 2})
    tid = r.json()['id']
    assert client.post('/api/backup-targets/test', headers=admin_headers, json={'type': 'folder', 'path': str(folder)}).json() == {'ok': True, 'host_key': ''}
    assert client.put('/api/backup-targets/encryption', headers=admin_headers, json={'passphrase': 'short'}).status_code == 400
    assert client.put('/api/backup-targets/encryption', headers=admin_headers, json={'passphrase': 'a long passphrase'}).json() == {'encrypted': True}
    sent = []
    from app import monitor
    monkeypatch.setattr(monitor, 'backup_failed', lambda name, error: sent.append((name, error)))
    names = []
    for minute in range(3):
        path = system.BACKUPS / f'caracal-fleet-backup-20261014-120{minute}.zip'
        system.BACKUPS.mkdir(parents=True, exist_ok=True)
        system._build_backup(path)
        res = offsite.upload(path)
        assert res[tid]['ok'], res
        names.append(path.name + '.enc')
    there = sorted(p.name for p in folder.iterdir())
    assert there == names[1:]   # the newest two, encrypted
    assert all(offsite.is_encrypted(folder / n) for n in there)
    files = client.get(f'/api/backup-targets/{tid}/files', headers=admin_headers).json()['files']
    assert [f['name'] for f in files] == list(reversed(names[1:]))
    st = next(x for x in client.get('/api/backup-targets', headers=admin_headers).json()['targets'] if x['id'] == tid)['status']
    assert st['ok'] and st['name'] == names[-1] and st['count'] == 2
    # a broken destination alerts once, not at every backup
    shutil.rmtree(folder)
    offsite.upload(path)
    offsite.upload(path)
    assert len(sent) == 1 and sent[0][0] == 'USB'
    assert not offsite.load_status()[tid]['ok']
    # restoring from a destination: decrypted, checked and applied at the next start
    folder.mkdir()
    shutil.copy(path, folder / 'caracal-fleet-backup-20261014-1203.zip')
    offsite.encrypt_file(path, folder / 'caracal-fleet-backup-20261014-1204.zip.enc', 'a long passphrase')
    restarts = []
    monkeypatch.setattr(system, 'restart_process', lambda: restarts.append(1))
    assert client.post(f'/api/backup-targets/{tid}/restore', headers=admin_headers, json={'name': '../etc/passwd'}).status_code == 400
    r = client.post(f'/api/backup-targets/{tid}/restore', headers=admin_headers, json={'name': 'caracal-fleet-backup-20261014-1204.zip.enc', 'passphrase': 'wrong one'})
    assert r.json()['detail'] == 'wrong_passphrase'
    r = client.post(f'/api/backup-targets/{tid}/restore', headers=admin_headers, json={'name': 'caracal-fleet-backup-20261014-1204.zip.enc'})
    assert r.status_code == 200 and r.json()['restart'], r.text   # the stored passphrase
    assert (core.RESTORE_DIR / 'hub.db').exists()
    shutil.rmtree(core.RESTORE_DIR)   # not applied in this test hub
    # an encrypted backup can be uploaded too
    enc = (folder / 'caracal-fleet-backup-20261014-1204.zip.enc').read_bytes()
    assert client.post('/api/backup/restore', headers=admin_headers, content=enc).status_code == 200
    shutil.rmtree(core.RESTORE_DIR)
    client.put('/api/backup-targets/encryption', headers=admin_headers, json={'passphrase': ''})
    assert client.post('/api/backup/restore', headers=admin_headers, content=enc).json()['detail'] == 'passphrase_required'
    assert client.post('/api/backup/restore', headers={**admin_headers, 'X-Backup-Passphrase': 'a long passphrase'}, content=enc).status_code == 200
    shutil.rmtree(core.RESTORE_DIR)
    client.delete(f'/api/backup-targets/{tid}', headers=admin_headers)
    for p in system.BACKUPS.iterdir():
        p.unlink()


@pytest.mark.skipif(not shutil.which('rclone'), reason='rclone is not installed')
def test_rclone_destination(tmp_path):
    t = offsite.validate({'type': 'rclone', 'config': '[disk]\ntype = local\n', 'path': str(tmp_path / 'remote').lstrip('/')})
    t['path'] = str(tmp_path / 'remote')   # a local "cloud": absolute path
    b = offsite.backend(t)
    src = tmp_path / 'caracal-fleet-backup-20261014-1200.zip'
    src.write_bytes(b'x' * 1000)
    b.put(src, src.name)
    assert [f['name'] for f in b.list()] == [src.name] and b.list()[0]['size'] == 1000
    out = io.BytesIO()
    b.get(src.name, out)
    assert out.getvalue() == b'x' * 1000
    b.delete(src.name)
    assert b.list() == []
