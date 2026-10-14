"""First-run setup, branding (logo) and full backup / restore of the hub."""
import json
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
import threading
import time
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from .core import (APP_DIR, BRANDING, CFG_PATH, DATA, DB_PATH, FILES, HUB_VERSION, LANGUAGES, RESTORE_DIR, SETUP_CODE,
                   USERNAME_RE, audit, current_user, db, hash_password, make_session, public_user, setup_required)

router = APIRouter()

LOGO_TYPES = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp', 'image/svg+xml': 'svg'}
LOGO_MAX = 1024 * 1024
BACKUP_FORMAT = 'caracal-fleet-backup'
BACKUP_MAX = 8 * 1024 ** 3
BACKUP_NAMES = re.compile(r'^(manifest\.json|hub\.db|config\.json|branding/logo\.(png|jpg|webp|svg)|files/[0-9a-f]{32})$')
_setup_lock = threading.Lock()


async def _json(r: Request):
    try:
        d = await r.json()
    except ValueError:
        raise HTTPException(400, 'invalid_json')
    if not isinstance(d, dict):
        raise HTTPException(400, 'invalid_json')
    return d


# ---------------------------------------------------------------- public info and first-run setup

def logo_path():
    for folder in (BRANDING, APP_DIR / 'static' / 'brand'):
        for ext in ('svg', 'png', 'webp', 'jpg'):
            if (folder / f'logo.{ext}').exists():
                return folder / f'logo.{ext}'
    return None


@router.get('/api/public')
def public_info():
    logo = logo_path()
    from .security import public_sso
    return {'setup_required': setup_required(), 'version': HUB_VERSION, 'sso': public_sso(),
            'logo': f'/branding/logo?v={int(logo.stat().st_mtime)}' if logo else None}


@router.post('/api/setup')
async def setup(r: Request):
    """Create the first administrator. Only possible while no user exists, with the code from the log."""
    d = await _json(r)
    username, password = str(d.get('username', '')).strip(), str(d.get('password', ''))
    language = d.get('language') if d.get('language') in LANGUAGES else 'cs'
    with _setup_lock:
        if not setup_required():
            raise HTTPException(409, 'setup_done')
        code = SETUP_CODE.read_text().strip() if SETUP_CODE.exists() else ''
        if not code or not secrets.compare_digest(str(d.get('setup_code', '')).strip().upper(), code):
            raise HTTPException(401, 'invalid_setup_code')
        if not USERNAME_RE.fullmatch(username):
            raise HTTPException(400, 'invalid_username')
        if len(password) < 10:
            raise HTTPException(400, 'password_too_short')
        with db() as c:
            c.execute("INSERT INTO users(username, password_hash, role, language, enabled, created) "
                      "VALUES(?,?,'admin',?,1,?)", (username, hash_password(password), language, time.time()))
            u = dict(c.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone())
        SETUP_CODE.unlink(missing_ok=True)
    audit(u, 'system.setup', username)
    return {'token': make_session(u), 'user': public_user(u)}


# ---------------------------------------------------------------- branding

@router.get('/branding/logo')
def branding_logo():
    path = logo_path()
    if not path:
        raise HTTPException(404, 'not_found')
    media = {v: k for k, v in LOGO_TYPES.items()}[path.suffix[1:]]
    # an SVG opened directly must not be able to run scripts in the hub's origin
    return FileResponse(path, media_type=media, headers={
        'Cache-Control': 'no-cache', 'X-Content-Type-Options': 'nosniff',
        'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; sandbox"})


def _valid_logo(data, ext):
    if ext == 'png':
        return data.startswith(b'\x89PNG\r\n\x1a\n')
    if ext == 'jpg':
        return data.startswith(b'\xff\xd8\xff')
    if ext == 'webp':
        return data[:4] == b'RIFF' and data[8:12] == b'WEBP'
    head = data[:2048].lstrip().lower()
    return (head.startswith(b'<svg') or head.startswith(b'<?xml')) and b'<svg' in data[:4096].lower()


@router.post('/api/branding/logo')
async def upload_logo(r: Request):
    u = current_user(r, 'admin')
    ext = LOGO_TYPES.get(r.headers.get('Content-Type', '').split(';')[0].strip())
    if not ext:
        raise HTTPException(400, 'unsupported_file')
    data = b''
    async for chunk in r.stream():
        data += chunk
        if len(data) > LOGO_MAX:
            raise HTTPException(413, 'file_too_large')
    if not _valid_logo(data, ext):
        raise HTTPException(400, 'unsupported_file')
    BRANDING.mkdir(parents=True, exist_ok=True)
    for old in BRANDING.glob('logo.*'):
        old.unlink()
    (BRANDING / f'logo.{ext}').write_bytes(data)
    audit(u, 'system.logo', ext, {'size': len(data)})
    return public_info()


@router.delete('/api/branding/logo')
def delete_logo(r: Request):
    u = current_user(r, 'admin')
    for old in BRANDING.glob('logo.*'):
        old.unlink()
    audit(u, 'system.logo_delete')
    return public_info()


# ---------------------------------------------------------------- backup / restore

def _build_backup(path):
    snapshot = path.with_suffix('.db')
    src, dst = sqlite3.connect(DB_PATH), sqlite3.connect(snapshot)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    with db() as c:
        files = [r['id'] for r in c.execute('SELECT id FROM files WHERE pinned=1')]   # playlists, releases
    manifest = {'format': BACKUP_FORMAT, 'version': 1, 'hub_version': HUB_VERSION, 'created': time.time(),
                'files': len(files)}
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('manifest.json', json.dumps(manifest, indent=2))
        z.write(snapshot, 'hub.db')
        z.write(CFG_PATH, 'config.json')
        logo = next(iter(sorted(BRANDING.glob('logo.*'))), None)
        if logo:
            z.write(logo, f'branding/{logo.name}')
        for fid in files:
            if (FILES / fid).exists():
                z.write(FILES / fid, f'files/{fid}', compress_type=zipfile.ZIP_STORED)
    snapshot.unlink(missing_ok=True)


@router.get('/api/backup')
async def export_backup(r: Request):
    u = current_user(r, 'admin')
    fd, name = tempfile.mkstemp(prefix='backup-', suffix='.zip', dir=DATA)
    os.close(fd)
    path = Path(name)
    await run_in_threadpool(_build_backup, path)
    audit(u, 'system.backup', path.name, {'size': path.stat().st_size})
    filename = time.strftime('caracal-fleet-backup-%Y%m%d-%H%M.zip')
    return FileResponse(path, media_type='application/zip', filename=filename,
                        background=BackgroundTask(path.unlink, missing_ok=True))


def _validate_and_unpack(archive, target):
    try:
        z = zipfile.ZipFile(archive)
    except zipfile.BadZipFile:
        raise HTTPException(400, 'invalid_backup')
    with z:
        names = [n for n in z.namelist() if not n.endswith('/')]
        if any(not BACKUP_NAMES.fullmatch(n) for n in names) or 'hub.db' not in names or 'config.json' not in names:
            raise HTTPException(400, 'invalid_backup')
        if sum(i.file_size for i in z.infolist()) > BACKUP_MAX:
            raise HTTPException(400, 'invalid_backup')
        try:
            manifest = json.loads(z.read('manifest.json'))
            config = json.loads(z.read('config.json'))
        except (KeyError, ValueError):
            raise HTTPException(400, 'invalid_backup')
        if manifest.get('format') != BACKUP_FORMAT or not config.get('secret') or not config.get('enroll_token'):
            raise HTTPException(400, 'invalid_backup')
        target.mkdir(parents=True)
        for n in names:  # names are whitelisted above, so no path traversal is possible
            dest = target / n
            dest.parent.mkdir(parents=True, exist_ok=True)
            with z.open(n) as src, dest.open('wb') as out:
                shutil.copyfileobj(src, out)
    try:
        c = sqlite3.connect(target / 'hub.db')
        ok = c.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        admins = c.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND enabled=1").fetchone()[0]
        c.close()
    except sqlite3.DatabaseError:
        ok, admins = False, 0
    if not ok or not admins:
        raise HTTPException(400, 'invalid_backup')
    return manifest


def restart_process():
    """Exit so that Docker Swarm (restart_policy on-failure) starts the hub again and applies the backup."""
    time.sleep(1)
    os._exit(3)


@router.post('/api/backup/restore')
async def restore_backup(r: Request):
    u = current_user(r, 'admin')
    fd, name = tempfile.mkstemp(prefix='restore-', suffix='.zip', dir=DATA)
    archive, size = Path(name), 0
    staging = DATA / 'restore-staging'
    try:
        with os.fdopen(fd, 'wb') as f:
            async for chunk in r.stream():
                size += len(chunk)
                if size > BACKUP_MAX:
                    raise HTTPException(413, 'file_too_large')
                f.write(chunk)
        shutil.rmtree(staging, ignore_errors=True)
        manifest = await run_in_threadpool(_validate_and_unpack, archive, staging)
        shutil.rmtree(RESTORE_DIR, ignore_errors=True)
        staging.rename(RESTORE_DIR)
    finally:
        archive.unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)
    audit(u, 'system.restore', '', {'backup_created': manifest.get('created'),
                                    'backup_hub_version': manifest.get('hub_version')})
    threading.Thread(target=restart_process, daemon=True).start()
    return {'ok': True, 'restart': True}


# ---------------------------------------------------------------- scheduled backups

# Backups made by the hub itself, once a day at the chosen hour, kept in the data folder (the newest `keep` ones).
# They are full backups like the downloaded ones; copying them off the server is up to the administrator.
BACKUPS = DATA / 'backups'
BACKUP_FILE = re.compile(r'^caracal-fleet-backup-\d{8}-\d{4}\.zip$')
BACKUP_DEFAULTS = {'enabled': False, 'hour': 2, 'keep': 7}


def backup_conf():
    from .core import cfg
    return {**BACKUP_DEFAULTS, **(cfg().get('backup_schedule') or {})}


def make_scheduled_backup(now=None):
    from .schedule import local_now
    BACKUPS.mkdir(parents=True, exist_ok=True)
    path = BACKUPS / local_now(now).strftime('caracal-fleet-backup-%Y%m%d-%H%M.zip')
    tmp = path.with_suffix('.part')
    _build_backup(tmp)
    tmp.replace(path)
    keep = backup_conf()['keep']
    for old in sorted(p for p in BACKUPS.iterdir() if BACKUP_FILE.fullmatch(p.name))[:-keep]:
        old.unlink(missing_ok=True)
    return path


def backup_tick(now=None):
    """Called by the schedule loop: the daily backup at its hour."""
    from .core import cfg, save_cfg
    from .schedule import local_now
    conf = backup_conf()
    if not conf['enabled']:
        return None
    local = local_now(now)
    today = local.strftime('%Y-%m-%d')
    full = cfg()
    if local.hour != conf['hour'] or full.get('backup_day') == today:
        return None
    full['backup_day'] = today
    save_cfg(full)
    path = make_scheduled_backup(now)
    with db() as c:
        c.execute('INSERT INTO audit(username, action, target, detail, created) VALUES(?,?,?,?,?)',
                  ('schedule', 'system.backup', path.name, json.dumps({'size': path.stat().st_size, 'scheduled': True}), time.time()))
    return path


def _backup_list():
    if not BACKUPS.is_dir():
        return []
    return [{'name': p.name, 'size': p.stat().st_size, 'created': p.stat().st_mtime}
            for p in sorted(BACKUPS.iterdir(), reverse=True) if BACKUP_FILE.fullmatch(p.name)]


@router.get('/api/backups')
def list_backups(r: Request):
    current_user(r, 'admin')
    return {'settings': backup_conf(), 'backups': _backup_list(), 'folder': str(BACKUPS)}


@router.put('/api/backups/settings')
async def backup_settings(r: Request):
    from .core import cfg, save_cfg
    u = current_user(r, 'admin')
    d = await _json(r)
    try:
        conf = {'enabled': bool(d.get('enabled')), 'hour': int(d.get('hour', 2)), 'keep': int(d.get('keep', 7))}
    except (TypeError, ValueError):
        raise HTTPException(400, 'invalid_value')
    if not 0 <= conf['hour'] <= 23 or not 1 <= conf['keep'] <= 90:
        raise HTTPException(400, 'invalid_value')
    full = cfg()
    full['backup_schedule'] = conf
    save_cfg(full)
    audit(u, 'system.backup_schedule', '', conf)
    return conf


@router.post('/api/backups/run')
async def run_backup(r: Request):
    u = current_user(r, 'admin')
    path = await run_in_threadpool(make_scheduled_backup)
    audit(u, 'system.backup', path.name, {'size': path.stat().st_size, 'stored': True})
    return {'name': path.name, 'size': path.stat().st_size}


def _backup_path(name):
    if not BACKUP_FILE.fullmatch(name) or not (BACKUPS / name).is_file():
        raise HTTPException(404, 'not_found')
    return BACKUPS / name


@router.get('/api/backups/{name}')
def download_backup(name: str, r: Request):
    u = current_user(r, 'admin')
    path = _backup_path(name)
    audit(u, 'system.backup_download', name)
    return FileResponse(path, media_type='application/zip', filename=name)


@router.delete('/api/backups/{name}')
def delete_backup(name: str, r: Request):
    u = current_user(r, 'admin')
    _backup_path(name).unlink()
    audit(u, 'system.backup_delete', name)
    return {'ok': True}


# ---------------------------------------------------------------- updates of CARACAL Fleet itself (app/selfupdate.py)

@router.get('/api/system/update')
async def update_info(r: Request, refresh: bool = False):
    from . import selfupdate
    current_user(r, 'admin')
    data = await run_in_threadpool(selfupdate.check, refresh)
    return {**data, **selfupdate.status()}


@router.post('/api/system/update')
async def update_start(r: Request):
    from . import selfupdate
    u = current_user(r, 'admin')
    d = await _json(r)
    version = str(d.get('version') or '').strip()
    await run_in_threadpool(selfupdate.start, version, u['username'])
    audit(u, 'system.update', version, {'from': HUB_VERSION})
    return {'ok': True}
