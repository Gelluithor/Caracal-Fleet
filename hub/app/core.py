"""Shared configuration, database, security and command-queue helpers of the Fleet hub."""
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException, Request

HUB_VERSION = '4.12.0'
APP_DIR = Path(__file__).resolve().parent
BOOT = APP_DIR.parent / 'bootstrap'
DATA = Path(os.getenv('CARACAL_HUB_DATA', '/var/lib/caracal-hub'))
DB_PATH = DATA / 'hub.db'
CFG_PATH = DATA / 'config.json'
FILES = DATA / 'files'
BRANDING = DATA / 'branding'
RESTORE_DIR = DATA / 'restore-pending'      # backup unpacked by /api/backup/restore, applied at start-up
SETUP_CODE = DATA / 'setup-code'            # one-time code for creating the first administrator

ONLINE_TIMEOUT = 35            # seconds without heartbeat before a node is offline
DELIVERY_TIMEOUT = 900         # delivered command without result -> timeout
FILE_RETENTION = 7 * 86400     # uploaded media are kept for a week
ROLES = ('admin', 'manager', 'operator', 'viewer')
LANGUAGES = ('cs', 'en')
USERNAME_RE = re.compile(r'^[A-Za-z0-9._@-]{2,64}$')

# Permission name -> roles allowed.
PERMS = {
    'view': {'admin', 'manager', 'operator', 'viewer'},
    'control': {'admin', 'manager', 'operator'},   # playback, show/freeze, restart player/node
    'content': {'admin', 'manager', 'operator'},   # playlists, media, collections, copying
    'manage': {'admin', 'manager'},                # devices, groups, locations, SSH install, audit
    'admin': {'admin'},                            # users and system settings
}

# Command action -> (permission, ttl seconds for queued state or None = no expiry).
ACTIONS = {
    'next': ('control', 600),
    'unfreeze': ('control', 600),
    'show': ('control', 600),
    'freeze': ('control', 600),
    'show_collection': ('control', 600),
    'freeze_collection': ('control', 600),
    'restart_player': ('control', 600),
    'reboot': ('control', 600),
    'snapshot': ('view', 600),
    'add_web': ('content', None),
    'add_media': ('content', None),
    'update_asset': ('content', None),
    'delete_asset': ('content', None),
    'reorder': ('content', None),
    'add_collection': ('content', None),
    'update_collection': ('content', None),
    'delete_collection': ('content', None),
    'add_profile': ('content', None),
    'update_profile': ('content', None),
    'delete_profile': ('content', None),
    'import_playlist': ('content', None),
    'export_assets': ('content', None),
    'update_agent': ('manage', None),
    'set_hub': ('manage', None),
    'update_caracal': ('manage', 600),
    'convert_to_docker': ('manage', 600),
    # on-screen notifications of the nodes
    'notify': ('control', 600),
    'notify_clear': ('control', 600),
    'notify_settings': ('content', None),
    'add_watcher': ('content', None),
    'update_watcher': ('content', None),
    'delete_watcher': ('content', None),
    'check_watcher': ('content', 600),
    'notify_sound': ('content', None),
    'set_download_source': ('manage', None),
    # what else the node's web administration offers (CARACAL 2026.10.10 and newer)
    'set_admin': ('manage', None),
    'overlay_settings': ('content', None),
    'notify_skip': ('control', 600),
    'notify_remove': ('control', 600),
    'notify_log': ('view', 600),
    'notify_history_clear': ('content', None),
    'notify_audit_clear': ('manage', None),
    'update_notify_token': ('manage', None),
    'delete_notify_token': ('manage', None),
    'preview_watcher': ('content', 600),
    'grafana_discover': ('content', 600),
}

# Payload keys that must not stay in the hub: login credentials of web pages and of notification watchers travel
# only to the node.
SECRET_KEYS = ('username', 'password', 'secret', 'client_secret', 'refresh_token')
SECRET_ACTIONS = ('add_profile', 'update_profile', 'add_watcher', 'update_watcher', 'preview_watcher', 'set_admin',
                  'convert_to_docker')   # convert_to_docker: optionally with the node's web administrator
REDACTED = '•••'


ADMIN_USER_RE = re.compile(r'[^\s]{1,64}')


def validate_admin(d):
    """The web administrator of the node (CARACAL's own admin UI): like the node's first-run setup."""
    username, password = str(d.get('username') or '').strip(), str(d.get('password') or '')
    if not ADMIN_USER_RE.fullmatch(username):
        raise HTTPException(400, 'invalid_admin_user')
    if not 10 <= len(password) <= 200:
        raise HTTPException(400, 'admin_password_short')
    return {'username': username, 'password': password}


def redact(action, payload):
    """Copy of a command payload without credentials (for the history, the audit and the stored command)."""
    if action not in SECRET_ACTIONS or not isinstance(payload, dict):
        return payload
    return {k: (REDACTED if k in SECRET_KEYS and v else v) for k, v in payload.items()}


def redact_json(action, payload_json):
    if action not in SECRET_ACTIONS:
        return payload_json
    try:
        return json.dumps(redact(action, json.loads(payload_json or '{}')), ensure_ascii=False)
    except ValueError:
        return '{}'


def _agent_version():
    try:
        m = re.search(r"^VERSION\s*=\s*'([^']+)'", (BOOT / 'agent.py').read_text(encoding='utf-8'), re.M)
        return m.group(1) if m else '0'
    except OSError:
        return '0'


AGENT_VERSION = _agent_version()


def version_tuple(v):
    return tuple(int(x) for x in re.findall(r'\d+', str(v or ''))[:3]) or (0,)


# ---------------------------------------------------------------- database

@contextmanager
def db():
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS devices(id TEXT PRIMARY KEY, token_hash TEXT, name TEXT, ip TEXT, version TEXT,
  last_seen REAL, status_json TEXT, device_group TEXT DEFAULT '', location TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS commands(id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT, action TEXT,
  payload_json TEXT DEFAULT '{}', state TEXT, result TEXT, created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password_hash TEXT,
  role TEXT DEFAULT 'viewer', language TEXT DEFAULT 'cs', enabled INTEGER DEFAULT 1, created REAL);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, action TEXT, target TEXT,
  detail TEXT, created REAL);
CREATE TABLE IF NOT EXISTS device_groups(name TEXT PRIMARY KEY, description TEXT DEFAULT '', created REAL);
CREATE TABLE IF NOT EXISTS device_locations(name TEXT PRIMARY KEY, description TEXT DEFAULT '',
  address TEXT DEFAULT '', created REAL);
CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY, name TEXT, kind TEXT, size INTEGER, sha256 TEXT,
  origin TEXT, created REAL);
CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, target TEXT, username TEXT,
  state TEXT, log TEXT DEFAULT '', device_id TEXT DEFAULT '', created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS global_playlists(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT,
  description TEXT DEFAULT '', created REAL, updated REAL, updated_by TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS global_playlist_items(id INTEGER PRIMARY KEY AUTOINCREMENT, playlist_id INTEGER,
  position INTEGER DEFAULT 0, kind TEXT, name TEXT, source TEXT DEFAULT '', duration INTEGER DEFAULT 30,
  scale REAL DEFAULT 1, file_id TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS node_releases(id INTEGER PRIMARY KEY AUTOINCREMENT, version TEXT, notes TEXT DEFAULT '',
  file_id TEXT, filename TEXT, size INTEGER, sha256 TEXT, username TEXT, created REAL);
CREATE TABLE IF NOT EXISTS global_deployments(id INTEGER PRIMARY KEY AUTOINCREMENT, playlist_id INTEGER,
  batch TEXT, mode TEXT, targets_json TEXT, username TEXT, created REAL);
CREATE TABLE IF NOT EXISTS notify_tokens(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, token_hash TEXT UNIQUE,
  prefix TEXT, scope_json TEXT DEFAULT '{}', rate_per_min INTEGER DEFAULT 30, enabled INTEGER DEFAULT 1, created REAL,
  created_by TEXT DEFAULT '', last_used REAL);
CREATE INDEX IF NOT EXISTS idx_commands_device_state ON commands(device_id, state);
-- monitoring: metrics in five-minute buckets, problems that are going on, and the timeline of events
CREATE TABLE IF NOT EXISTS metrics(device_id TEXT, ts INTEGER, cpu REAL, ram REAL, disk REAL, temp REAL,
  PRIMARY KEY(device_id, ts));
CREATE TABLE IF NOT EXISTS issues(device_id TEXT, code TEXT, level TEXT, detail TEXT DEFAULT '', since REAL,
  alerted INTEGER DEFAULT 0, PRIMARY KEY(device_id, code));
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT, ts REAL, kind TEXT, code TEXT,
  level TEXT, detail TEXT DEFAULT '');
CREATE INDEX IF NOT EXISTS idx_events_device ON events(device_id, ts);
"""

# Columns added after the first releases; existing databases are migrated in place.
COLUMNS = [
    ('devices', 'device_group', "TEXT DEFAULT ''"),
    ('devices', 'location', "TEXT DEFAULT ''"),
    ('devices', 'notes', "TEXT DEFAULT ''"),
    ('devices', 'created', 'REAL'),
    ('commands', 'payload_json', "TEXT DEFAULT '{}'"),
    ('commands', 'updated', 'REAL'),
    ('commands', 'username', "TEXT DEFAULT ''"),
    ('commands', 'batch', "TEXT DEFAULT ''"),
    ('commands', 'expires', 'REAL'),
    ('commands', 'followup_json', "TEXT DEFAULT ''"),
    ('files', 'pinned', 'INTEGER DEFAULT 0'),
    ('jobs', 'result_json', "TEXT DEFAULT ''"),
]


def cfg():
    return json.loads(CFG_PATH.read_text())


def save_cfg(c):
    tmp = CFG_PATH.with_suffix('.tmp')
    tmp.write_text(json.dumps(c, indent=2))
    tmp.replace(CFG_PATH)


def init():
    DATA.mkdir(parents=True, exist_ok=True)
    FILES.mkdir(parents=True, exist_ok=True)
    BRANDING.mkdir(parents=True, exist_ok=True)
    restored_from = apply_pending_restore()
    env_token = os.getenv('CARACAL_HUB_ENROLL_TOKEN')
    if not CFG_PATH.exists():
        save_cfg({'enroll_token': env_token or secrets.token_hex(24), 'secret': secrets.token_hex(32)})
    config = cfg()
    changed = False
    if env_token and config.get('enroll_token') != env_token:
        config['enroll_token'] = env_token
        changed = True
    if not config.get('secret'):
        config['secret'] = secrets.token_hex(32)
        changed = True
    if changed:
        save_cfg(config)

    with db() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript(SCHEMA)
        for table, col, ddl in COLUMNS:
            cols = {r['name'] for r in c.execute(f'PRAGMA table_info({table})')}
            if col not in cols:
                c.execute(f'ALTER TABLE {table} ADD COLUMN {col} {ddl}')
        # indexes on migrated columns can only be created after the migration
        c.execute('CREATE INDEX IF NOT EXISTS idx_commands_batch ON commands(batch)')
        # Groups/locations that exist only as text on devices become managed entries.
        for table, col in (('device_groups', 'device_group'), ('device_locations', 'location')):
            c.execute(f"INSERT OR IGNORE INTO {table}(name, created) SELECT DISTINCT {col}, ? FROM devices "
                      f"WHERE {col} IS NOT NULL AND {col} != ''", (time.time(),))
        c.execute("UPDATE jobs SET state='interrupted', updated=? WHERE state IN ('queued','running')", (time.time(),))
        _sync_admin(c, config)
        if restored_from:
            c.execute("INSERT INTO audit(username, action, target, detail, created) VALUES('system', "
                      "'system.restore_applied', '', ?, ?)", (json.dumps({'previous_data': restored_from}), time.time()))
    cleanup_files()


def _sync_admin(c, config):
    env_password = os.getenv('CARACAL_HUB_ADMIN_PASSWORD')
    legacy_hash = config.get('admin_hash')
    row = c.execute("SELECT id, password_hash FROM users WHERE username='admin'").fetchone()
    has_users = c.execute('SELECT COUNT(*) FROM users').fetchone()[0] > 0
    if not row and (env_password or legacy_hash):
        if env_password:
            pw_hash, shown = hash_password(env_password), 'password from CARACAL_HUB_ADMIN_PASSWORD'
        else:
            pw_hash, shown = 'legacy$' + legacy_hash, 'existing legacy administrator password'
        c.execute("INSERT INTO users(username, password_hash, role, language, enabled, created) "
                  "VALUES('admin', ?, 'admin', 'cs', 1, ?)", (pw_hash, time.time()))
        print('INITIAL ADMIN USER: admin', flush=True)
        print('INITIAL ADMIN PASSWORD:', shown, flush=True)
    elif not has_users:
        # First start without CARACAL_HUB_ADMIN_PASSWORD: the administrator is created in the web UI.
        # The one-time setup code (only in the container log) stops strangers from claiming the hub first.
        if not SETUP_CODE.exists():
            SETUP_CODE.write_text(secrets.token_hex(4).upper())
            os.chmod(SETUP_CODE, 0o600)
        print('=' * 60, flush=True)
        print('CARACAL Fleet: no administrator yet. Open the web UI and create one.', flush=True)
        print('SETUP CODE:', SETUP_CODE.read_text().strip(), flush=True)
        print('=' * 60, flush=True)
    elif env_password and not check_password(env_password, row['password_hash']):
        c.execute("UPDATE users SET password_hash=?, role='admin', enabled=1 WHERE username='admin'",
                  (hash_password(env_password),))
    elif env_password:
        c.execute("UPDATE users SET role='admin', enabled=1 WHERE username='admin'")


def cleanup_files():
    """Temporary uploads expire after FILE_RETENTION; files of global playlists and CARACAL releases stay."""
    limit = time.time() - FILE_RETENTION
    with db() as c:
        old = [r['id'] for r in c.execute(
            "SELECT id FROM files WHERE created<? AND COALESCE(pinned, 0)=0 "
            "AND id NOT IN (SELECT file_id FROM global_playlist_items WHERE file_id != '') "
            "AND id NOT IN (SELECT file_id FROM node_releases)", (limit,))]
        for fid in old:
            (FILES / fid).unlink(missing_ok=True)
            c.execute('DELETE FROM files WHERE id=?', (fid,))


def refresh_pin(c, fid):
    """A file stays pinned while a global playlist item or a CARACAL release uses it."""
    used = c.execute('SELECT 1 FROM global_playlist_items WHERE file_id=? UNION SELECT 1 FROM node_releases '
                     'WHERE file_id=?', (fid, fid)).fetchone()
    c.execute('UPDATE files SET pinned=? WHERE id=?', (1 if used else 0, fid))


def setup_required():
    with db() as c:
        return c.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0


def apply_pending_restore():
    """Replace the database, configuration, branding and media with an uploaded backup.

    The backup is validated and unpacked by the restore endpoint; it is applied here, before any database
    connection is opened, and the previous data are kept in DATA/pre-restore-<timestamp>.
    """
    if not (RESTORE_DIR / 'hub.db').exists():
        return None
    keep = DATA / time.strftime('pre-restore-%Y%m%d-%H%M%S')
    keep.mkdir()
    for name in ('hub.db', 'hub.db-wal', 'hub.db-shm', 'config.json'):
        if (DATA / name).exists():
            shutil.move(str(DATA / name), str(keep / name))
    for folder in ('branding', 'files'):
        if (DATA / folder).exists():
            shutil.move(str(DATA / folder), str(keep / folder))
    for name in ('hub.db', 'config.json'):
        if (RESTORE_DIR / name).exists():
            shutil.move(str(RESTORE_DIR / name), str(DATA / name))
    for folder in ('branding', 'files'):
        src = RESTORE_DIR / folder
        shutil.move(str(src), str(DATA / folder)) if src.exists() else (DATA / folder).mkdir(exist_ok=True)
    shutil.rmtree(RESTORE_DIR, ignore_errors=True)
    print(f'Backup restored, previous data kept in {keep}', flush=True)
    return keep.name


# ---------------------------------------------------------------- passwords and sessions

def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    return salt + '$' + hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 240000).hex()


def check_password(password, stored):
    if not stored:
        return False
    if stored.startswith('legacy$'):
        return hmac.compare_digest(stored[7:], hashlib.sha256(password.encode()).hexdigest())
    try:
        salt, _ = stored.split('$', 1)
    except ValueError:
        return False
    return hmac.compare_digest(stored, hash_password(password, salt))


SESSION_TTL = 12 * 3600


def _sign(body):
    return hmac.new(cfg()['secret'].encode(), body.encode(), hashlib.sha256).hexdigest()


def _pw_fingerprint(pw_hash):
    return hashlib.sha256((pw_hash or '').encode()).hexdigest()[:12]


def make_session(u):
    body = f"{u['username']}:{int(time.time()) + SESSION_TTL}:{_pw_fingerprint(u['password_hash'])}"
    return body + ':' + _sign(body)


def current_user(r: Request, perm='view'):
    return user_from_token(r.headers.get('Authorization', '').removeprefix('Bearer ').strip(), perm)


def user_from_token(raw, perm='view'):
    """User of a session token (also used by WebSockets, which cannot send the Authorization header)."""
    try:
        name, exp, fp, sig = raw.rsplit(':', 3)
        body = f'{name}:{exp}:{fp}'
        if int(exp) < time.time() or not hmac.compare_digest(sig, _sign(body)):
            raise ValueError
        with db() as c:
            u = c.execute('SELECT * FROM users WHERE username=? AND enabled=1', (name,)).fetchone()
        if not u or not hmac.compare_digest(fp, _pw_fingerprint(u['password_hash'])):
            raise ValueError
    except (ValueError, TypeError):
        raise HTTPException(401, 'session_expired')
    u = dict(u)
    if u['role'] not in PERMS[perm]:
        raise HTTPException(403, 'forbidden')
    return u


def public_user(u):
    return {'id': u['id'], 'username': u['username'], 'role': u['role'], 'language': u['language'] or 'cs',
            'permissions': sorted(p for p, roles in PERMS.items() if u['role'] in roles)}


def audit(u, action, target='', detail=''):
    if not isinstance(detail, str):
        detail = json.dumps(detail, ensure_ascii=False)
    with db() as c:
        c.execute('INSERT INTO audit(username, action, target, detail, created) VALUES(?,?,?,?,?)',
                  (u['username'] if u else 'system', action, target, detail[:4000], time.time()))


# ---------------------------------------------------------------- device auth and command queue

def token_hash(tok):
    return hashlib.sha256(tok.encode()).hexdigest()


def device_auth(r: Request, did):
    with db() as c:
        row = c.execute('SELECT token_hash FROM devices WHERE id=?', (did,)).fetchone()
    tok = r.headers.get('X-Device-Token', '')
    if not row or not tok or not hmac.compare_digest(row['token_hash'] or '', token_hash(tok)):
        raise HTTPException(401, 'invalid_device_token')


def queue_command(c, device_id, action, payload, username, batch='', followup=None):
    now = time.time()
    ttl = ACTIONS[action][1]
    cur = c.execute(
        'INSERT INTO commands(device_id, action, payload_json, state, result, created, updated, username, batch, '
        'expires, followup_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
        (device_id, action, json.dumps(payload or {}, ensure_ascii=False), 'queued', '', now, now, username, batch,
         now + ttl if ttl else None, json.dumps(followup) if followup else ''))
    return cur.lastrowid


def scrub_secrets(c):
    """Credentials stay in the queue only until the agent fetched the command (or it was cancelled/expired)."""
    # rows with a key still holding a real value (not empty, not redacted yet)
    marks = ' OR '.join(f"(payload_json LIKE '%\"{k}\": \"%' AND payload_json NOT LIKE '%\"{k}\": \"\"%' "
                        f"AND payload_json NOT LIKE '%\"{k}\": \"{REDACTED}\"%')" for k in SECRET_KEYS)
    for row in c.execute(f"SELECT id, action, payload_json FROM commands WHERE state != 'queued' AND action IN "
                         f"({','.join('?' * len(SECRET_ACTIONS))}) AND ({marks})", SECRET_ACTIONS).fetchall():
        clean = redact_json(row['action'], row['payload_json'])
        if clean != row['payload_json']:
            c.execute('UPDATE commands SET payload_json=? WHERE id=?', (clean, row['id']))


def sweep_commands(c):
    now = time.time()
    c.execute("UPDATE commands SET state='expired', updated=?, result='not delivered in time' "
              "WHERE state='queued' AND expires IS NOT NULL AND expires<?", (now, now))
    c.execute("UPDATE commands SET state='timeout', updated=?, result='no result from agent' "
              "WHERE state='delivered' AND action NOT IN ('update_caracal','convert_to_docker') AND updated<?",
              (now, now - DELIVERY_TIMEOUT))
    # installing CARACAL (apt, pip, image downloads) takes much longer than other commands
    c.execute("UPDATE commands SET state='timeout', updated=?, result='no result from agent' "
              "WHERE state='delivered' AND action IN ('update_caracal','convert_to_docker') AND updated<?",
              (now, now - 4 * 3600))
    scrub_secrets(c)


def new_batch():
    return secrets.token_hex(6)
