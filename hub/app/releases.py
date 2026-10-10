"""CARACAL releases: packages of the CARACAL node software that Fleet installs on nodes.

A release is an archive of the caracal repository (zip or tar.gz, e.g. "Download ZIP" on GitHub). The agent
installs it with the release's own install.sh, which keeps /var/lib/caracal and /etc/caracal-fleet-key, and
restores the previous version when the installation fails.
"""
import hashlib
import io
import re
import secrets
import tarfile
import time
import zipfile
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from .core import FILES, audit, current_user, db, new_batch, queue_command, refresh_pin

router = APIRouter()
RELEASE_MAX = 512 * 1024 ** 2
VERSION_RE = re.compile(r'^[A-Za-z0-9._+-]{1,40}$')


def latest_version():
    with db() as c:
        row = c.execute('SELECT version FROM node_releases ORDER BY id DESC LIMIT 1').fetchone()
    return row['version'] if row else None


def _inspect(data):
    """Validate a release archive and return the VERSION file content (or '')."""
    names, read = [], None
    try:
        if zipfile.is_zipfile(io.BytesIO(data)):
            z = zipfile.ZipFile(io.BytesIO(data))
            names, read = z.namelist(), lambda n: z.read(n)
        else:
            t = tarfile.open(fileobj=io.BytesIO(data))
            members = {m.name.lstrip('./'): m for m in t.getmembers()}
            names = list(members)
            read = lambda n: t.extractfile(members[n]).read()
    except (tarfile.TarError, zipfile.BadZipFile, OSError):
        raise HTTPException(400, 'invalid_release')
    if any(n.startswith('/') or '..' in n.split('/') for n in names):
        raise HTTPException(400, 'invalid_release')
    for prefix in [''] + sorted({n.split('/')[0] + '/' for n in names if '/' in n}):
        if prefix + 'install.sh' in names and prefix + 'app/main.py' in names:
            if prefix + 'VERSION' in names:
                return read(prefix + 'VERSION').decode(errors='replace').strip()[:40]
            return ''
    raise HTTPException(400, 'invalid_release')


@router.get('/api/node-releases')
def list_releases(r: Request):
    current_user(r)
    with db() as c:
        rows = [dict(x) for x in c.execute('SELECT * FROM node_releases ORDER BY id DESC')]
    if rows:
        rows[0]['latest'] = True
    return rows


@router.post('/api/node-releases')
async def upload_release(r: Request):
    u = current_user(r, 'manage')
    data = bytearray()
    async for chunk in r.stream():
        data += chunk
        if len(data) > RELEASE_MAX:
            raise HTTPException(413, 'file_too_large')
    data = bytes(data)
    found = await run_in_threadpool(_inspect, data)
    version = unquote(r.headers.get('X-Release-Version', '')).strip() or found
    if not VERSION_RE.fullmatch(version or ''):
        raise HTTPException(400, 'version_required')
    name = unquote(r.headers.get('X-File-Name', 'caracal.zip'))[:200]
    notes = unquote(r.headers.get('X-Release-Notes', ''))[:2000]
    fid, digest = secrets.token_hex(16), hashlib.sha256(data).hexdigest()
    (FILES / fid).write_bytes(data)
    with db() as c:
        if c.execute('SELECT 1 FROM node_releases WHERE version=?', (version,)).fetchone():
            (FILES / fid).unlink(missing_ok=True)
            raise HTTPException(409, 'already_exists')
        c.execute("INSERT INTO files(id, name, kind, size, sha256, origin, created, pinned) VALUES(?,?,?,?,?,?,?,1)",
                  (fid, name, 'release', len(data), digest, 'release:' + u['username'], time.time()))
        rid = c.execute('INSERT INTO node_releases(version, notes, file_id, filename, size, sha256, username, created) '
                        'VALUES(?,?,?,?,?,?,?,?)', (version, notes, fid, name, len(data), digest, u['username'],
                                                   time.time())).lastrowid
    audit(u, 'release.upload', version, {'size': len(data), 'sha256': digest})
    return {'id': rid, 'version': version}


@router.delete('/api/node-releases/{rid}')
def delete_release(rid: int, r: Request):
    u = current_user(r, 'manage')
    with db() as c:
        rel = c.execute('SELECT * FROM node_releases WHERE id=?', (rid,)).fetchone()
        if not rel:
            raise HTTPException(404, 'not_found')
        c.execute('DELETE FROM node_releases WHERE id=?', (rid,))
        refresh_pin(c, rel['file_id'])
    audit(u, 'release.delete', rel['version'])
    return {'ok': True}


@router.post('/api/node-releases/{rid}/deploy')
async def deploy_release(rid: int, r: Request):
    """Queue the CARACAL update on the selected online nodes."""
    from .main import body, validate_command   # late import: main imports this module
    u = current_user(r, 'manage')
    d = await body(r)
    ids = list(dict.fromkeys(str(x) for x in d.get('device_ids') or []))
    if not ids:
        raise HTTPException(400, 'no_devices')
    batch, queued, skipped = new_batch(), [], []
    with db() as c:
        for did in ids:
            row = c.execute('SELECT * FROM devices WHERE id=?', (did,)).fetchone()
            if not row:
                skipped.append({'device_id': did, 'reason': 'device_not_found'})
                continue
            try:
                payload = validate_command(c, row, 'update_caracal', {'release_id': rid})
            except HTTPException as e:
                skipped.append({'device_id': did, 'reason': e.detail})
                continue
            queued.append(queue_command(c, did, 'update_caracal', payload, u['username'], batch))
        version = (c.execute('SELECT version FROM node_releases WHERE id=?', (rid,)).fetchone() or {'version': ''})['version']
    audit(u, 'release.deploy', version, {'devices': ids, 'skipped': skipped})
    return {'command_ids': queued, 'skipped': skipped, 'batch': batch}
