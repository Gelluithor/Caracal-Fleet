"""Users, roles and user groups.

A role is a set of permissions (core.PERMISSIONS): the built-in ones (administrator, manager, operator, viewer) and
custom ones. A user has a role for a scope: all devices, or chosen locations, device groups and devices. A user group
gives its members one more role with its own scope (e.g. "Brno reception": operator for the location Brno). A user
has everything the user and the user's groups give; system permissions (users, settings, audit...) count only with
the whole fleet.

Nobody can give more than they have: whoever manages users can grant only their own permissions, so a user manager
is not a way to become an administrator. The last enabled administrator cannot be removed or limited.
"""
import json
import secrets
import time

from fastapi import APIRouter, HTTPException, Request

from .core import (BUILTIN_ROLES, LANGUAGES, PERMISSIONS, ROLE_KEY_RE, USERNAME_RE, audit, current_user, db,
                   hash_password, parse_scope, role_permissions)

router = APIRouter()


async def _json(r: Request):
    try:
        d = await r.json()
    except ValueError:
        raise HTTPException(400, 'invalid_json')
    if not isinstance(d, dict):
        raise HTTPException(400, 'invalid_json')
    return d


def _text(v, limit=200):
    return str(v if v is not None else '').strip()[:limit]


def scope_value(d):
    """The scope from a form: {"all": true} or lists of locations, groups and devices (stored as JSON, '' = all)."""
    if not isinstance(d, dict) or d.get('all'):
        return ''
    out = {k: sorted({_text(x, 120) for x in d.get(k) or [] if _text(x, 120)}) for k in ('locations', 'groups', 'devices')}
    return json.dumps(out)


def scope_public(raw):
    s = parse_scope(raw)
    return {'all': True} if s is None else s


def _role_exists(c, key):
    return key in BUILTIN_ROLES or bool(key and c.execute('SELECT 1 FROM roles WHERE key=?', (key,)).fetchone())


def _check_grant(c, u, role, scope):
    """The actor must have every permission of the role (everywhere, or at least for the scope given)."""
    perms = role_permissions(c, role)
    if not u['access'].everywhere('users'):
        raise HTTPException(403, 'forbidden')
    missing = [p for p in perms if not u['access'].everywhere(p)]
    if missing:
        raise HTTPException(403, 'cannot_grant:' + ','.join(missing))


def _admins_left(c, without_user=None, user_change=None):
    """Enabled users who are administrators for the whole fleet (role admin and no scope), after a change."""
    n = 0
    for row in c.execute('SELECT id, role, scope_json, enabled, tenant_id FROM users'):
        row = dict(row)
        if user_change and row['id'] == user_change['id']:
            row.update(user_change)
        if row['id'] == without_user or not row['enabled'] or row.get('tenant_id'):
            continue
        if row['role'] == 'admin' and parse_scope(row['scope_json'] or '') is None:
            n += 1
    return n


# ---------------------------------------------------------------- users

def _user_row(c, x):
    x = dict(x)
    groups = [g['group_id'] for g in c.execute('SELECT group_id FROM user_group_members WHERE user_id=?', (x['id'],))]
    return {'id': x['id'], 'username': x['username'], 'role': x['role'], 'language': x['language'], 'enabled': bool(x['enabled']),
            'created': x['created'], 'totp': bool(x['totp_secret']), 'must_change_password': bool(x['must_change_password']),
            'sso': str(x['password_hash'] or '').startswith('sso$'), 'scope': scope_public(x['scope_json'] or ''), 'groups': groups,
            'tenant_id': x['tenant_id'] or None}


@router.get('/api/users')
def list_users(r: Request):
    current_user(r, 'users')
    with db() as c:
        return [_user_row(c, x) for x in c.execute('SELECT * FROM users ORDER BY username COLLATE NOCASE')]


def _set_groups(c, u, uid, groups):
    if groups is None:
        return
    wanted = {int(g) for g in groups if str(g).isdigit()}
    for row in c.execute('SELECT * FROM user_groups'):
        if row['id'] in wanted:
            _check_grant(c, u, row['role'], row['scope_json'])
    c.execute('DELETE FROM user_group_members WHERE user_id=?', (uid,))
    c.executemany('INSERT OR IGNORE INTO user_group_members(group_id, user_id) SELECT id, ? FROM user_groups WHERE id=?',
                  [(uid, g) for g in wanted])


@router.post('/api/users')
async def create_user(r: Request):
    u = current_user(r, 'users')
    d = await _json(r)
    username, password = _text(d.get('username'), 64), str(d.get('password', ''))
    role, language = d.get('role', 'viewer'), d.get('language', 'cs')
    if not USERNAME_RE.fullmatch(username):
        raise HTTPException(400, 'invalid_username')
    if len(password) < 10:
        raise HTTPException(400, 'password_too_short')
    scope = scope_value(d.get('scope'))
    with db() as c:
        if role and not _role_exists(c, role) or language not in LANGUAGES:
            raise HTTPException(400, 'invalid_role')
        _check_grant(c, u, role, scope)
        if c.execute('SELECT 1 FROM users WHERE username=?', (username,)).fetchone():
            raise HTTPException(409, 'already_exists')
        from .tenants import tenant_value
        tenant = tenant_value(c, u, d.get('tenant_id'))
        uid = c.execute('INSERT INTO users(username, password_hash, role, language, enabled, created, must_change_password, scope_json, tenant_id) '
                        'VALUES(?,?,?,?,1,?,?,?,?)', (username, hash_password(password), role, language, time.time(),
                                                      int(bool(d.get('must_change'))), scope, tenant)).lastrowid
        _set_groups(c, u, uid, d.get('groups'))
    audit(u, 'user.create', username, {'role': role, 'scope': scope_public(scope), 'groups': d.get('groups') or [],
                                       'must_change': bool(d.get('must_change'))})
    return {'ok': True, 'id': uid}


@router.patch('/api/users/{uid}')
async def edit_user(uid: int, r: Request):
    u = current_user(r, 'users')
    d = await _json(r)
    with db() as c:
        target = c.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
        if not target:
            raise HTTPException(404, 'not_found')
        role = d.get('role', target['role'])
        language = d.get('language', target['language'])
        enabled = 1 if d.get('enabled', bool(target['enabled'])) else 0
        scope = scope_value(d['scope']) if 'scope' in d else (target['scope_json'] or '')
        if role and not _role_exists(c, role) or language not in LANGUAGES:
            raise HTTPException(400, 'invalid_role')
        if role != target['role'] or scope != (target['scope_json'] or ''):
            _check_grant(c, u, role, scope)
            _check_grant(c, u, target['role'], target['scope_json'])   # and must be able to take the old one away
        from .tenants import tenant_value
        tenant = tenant_value(c, u, d['tenant_id']) if 'tenant_id' in d else target['tenant_id']
        if not _admins_left(c, user_change={'id': uid, 'role': role, 'scope_json': scope, 'enabled': enabled, 'tenant_id': tenant}):
            raise HTTPException(409, 'last_admin')
        c.execute('UPDATE users SET role=?, language=?, enabled=?, scope_json=?, tenant_id=? WHERE id=?', (role, language, enabled, scope, tenant, uid))
        _set_groups(c, u, uid, d.get('groups'))
        if d.get('password'):
            if len(str(d['password'])) < 10:
                raise HTTPException(400, 'password_too_short')
            c.execute('UPDATE users SET password_hash=?, must_change_password=? WHERE id=?',
                      (hash_password(str(d['password'])), int(bool(d.get('must_change'))), uid))
    audit(u, 'user.edit', target['username'],
          {'role': role, 'scope': scope_public(scope), 'language': language, 'enabled': bool(enabled),
           'groups': d.get('groups'), 'password_reset': bool(d.get('password')),
           'must_change': bool(d.get('password') and d.get('must_change'))})
    return {'ok': True}


@router.delete('/api/users/{uid}')
def delete_user(uid: int, r: Request):
    u = current_user(r, 'users')
    with db() as c:
        target = c.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
        if not target:
            raise HTTPException(404, 'not_found')
        if target['id'] == u['id']:
            raise HTTPException(409, 'cannot_delete_self')
        if not _admins_left(c, without_user=uid):
            raise HTTPException(409, 'last_admin')
        _check_grant(c, u, target['role'], target['scope_json'])
        c.execute('DELETE FROM user_group_members WHERE user_id=?', (uid,))
        c.execute('DELETE FROM users WHERE id=?', (uid,))
    audit(u, 'user.delete', target['username'])
    return {'ok': True}


# ---------------------------------------------------------------- roles

def _role_public(c, key, name='', description='', perms=None, builtin=False):
    users = c.execute('SELECT COUNT(*) FROM users WHERE role=?', (key,)).fetchone()[0]
    groups = c.execute('SELECT COUNT(*) FROM user_groups WHERE role=?', (key,)).fetchone()[0]
    return {'key': key, 'name': name, 'description': description, 'builtin': builtin, 'users': users, 'groups': groups,
            'permissions': [p for p in PERMISSIONS if p in (perms if perms is not None else role_permissions(c, key))]}


@router.get('/api/roles')
def list_roles(r: Request):
    """The roles with their permissions (also for users who only see them in their account)."""
    current_user(r)
    with db() as c:
        out = [_role_public(c, k, builtin=True) for k in BUILTIN_ROLES]
        for row in c.execute('SELECT * FROM roles ORDER BY name COLLATE NOCASE'):
            out.append(_role_public(c, row['key'], row['name'], row['description'], role_permissions(c, row['key'])))
    return {'roles': out, 'permissions': list(PERMISSIONS)}


def _role_fields(d):
    name = _text(d.get('name'), 80)
    if not name:
        raise HTTPException(400, 'name_required')
    perms = [p for p in PERMISSIONS if p in (d.get('permissions') or [])]
    if not perms:
        raise HTTPException(400, 'permissions_required')
    if 'view' not in perms:
        perms.insert(0, 'view')   # everything else needs to see the devices
    return name, _text(d.get('description'), 500), perms


@router.post('/api/roles')
async def create_role(r: Request):
    u = current_user(r, 'users')
    name, description, perms = _role_fields(await _json(r))
    missing = [p for p in perms if not u['access'].everywhere(p)]
    if missing:
        raise HTTPException(403, 'cannot_grant:' + ','.join(missing))
    key = 'r' + secrets.token_hex(4)
    with db() as c:
        if c.execute('SELECT 1 FROM roles WHERE name=?', (name,)).fetchone():
            raise HTTPException(409, 'already_exists')
        c.execute('INSERT INTO roles(key, name, description, permissions_json, created, updated) VALUES(?,?,?,?,?,?)',
                  (key, name, description, json.dumps(perms), time.time(), time.time()))
    audit(u, 'role.create', name, {'permissions': perms})
    return {'ok': True, 'key': key}


@router.patch('/api/roles/{key}')
async def edit_role(key: str, r: Request):
    u = current_user(r, 'users')
    if not ROLE_KEY_RE.fullmatch(key):
        raise HTTPException(400, 'builtin_role')
    name, description, perms = _role_fields(await _json(r))
    with db() as c:
        row = c.execute('SELECT * FROM roles WHERE key=?', (key,)).fetchone()
        if not row:
            raise HTTPException(404, 'not_found')
        old = role_permissions(c, key)
        missing = [p for p in set(perms) | old if not u['access'].everywhere(p)]
        if missing:
            raise HTTPException(403, 'cannot_grant:' + ','.join(missing))
        if c.execute('SELECT 1 FROM roles WHERE name=? AND key!=?', (name, key)).fetchone():
            raise HTTPException(409, 'already_exists')
        c.execute('UPDATE roles SET name=?, description=?, permissions_json=?, updated=? WHERE key=?',
                  (name, description, json.dumps(perms), time.time(), key))
    audit(u, 'role.edit', name, {'permissions': perms})
    return {'ok': True}


@router.delete('/api/roles/{key}')
def delete_role(key: str, r: Request):
    u = current_user(r, 'users')
    with db() as c:
        row = c.execute('SELECT * FROM roles WHERE key=?', (key,)).fetchone()
        if not row:
            raise HTTPException(404 if ROLE_KEY_RE.fullmatch(key) else 400, 'not_found' if ROLE_KEY_RE.fullmatch(key) else 'builtin_role')
        used = c.execute('SELECT (SELECT COUNT(*) FROM users WHERE role=?) + (SELECT COUNT(*) FROM user_groups WHERE role=?)',
                         (key, key)).fetchone()[0]
        if used:
            raise HTTPException(409, 'role_in_use')
        c.execute('DELETE FROM roles WHERE key=?', (key,))
    audit(u, 'role.delete', row['name'])
    return {'ok': True}


# ---------------------------------------------------------------- user groups

def _group_public(c, row):
    members = [m['user_id'] for m in c.execute('SELECT user_id FROM user_group_members WHERE group_id=?', (row['id'],))]
    return {'id': row['id'], 'name': row['name'], 'description': row['description'], 'role': row['role'],
            'scope': scope_public(row['scope_json'] or ''), 'members': members, 'created': row['created']}


@router.get('/api/user-groups')
def list_groups(r: Request):
    current_user(r, 'users')
    with db() as c:
        return [_group_public(c, x) for x in c.execute('SELECT * FROM user_groups ORDER BY name COLLATE NOCASE')]


def _group_fields(c, u, d, old=None):
    name = _text(d.get('name', old['name'] if old else ''), 80)
    if not name:
        raise HTTPException(400, 'name_required')
    role = d.get('role', old['role'] if old else '')
    if not _role_exists(c, role):
        raise HTTPException(400, 'invalid_role')
    scope = scope_value(d['scope']) if 'scope' in d else (old['scope_json'] if old else '')
    _check_grant(c, u, role, scope)
    if old:
        _check_grant(c, u, old['role'], old['scope_json'])
    return name, _text(d.get('description', old['description'] if old else ''), 500), role, scope


def _set_members(c, gid, members):
    if members is None:
        return
    ids = {int(x) for x in members if str(x).isdigit()}
    c.execute('DELETE FROM user_group_members WHERE group_id=?', (gid,))
    c.executemany('INSERT OR IGNORE INTO user_group_members(group_id, user_id) SELECT ?, id FROM users WHERE id=?',
                  [(gid, x) for x in ids])


@router.post('/api/user-groups')
async def create_group(r: Request):
    u = current_user(r, 'users')
    d = await _json(r)
    with db() as c:
        name, description, role, scope = _group_fields(c, u, d)
        if c.execute('SELECT 1 FROM user_groups WHERE name=?', (name,)).fetchone():
            raise HTTPException(409, 'already_exists')
        gid = c.execute('INSERT INTO user_groups(name, description, role, scope_json, created) VALUES(?,?,?,?,?)',
                        (name, description, role, scope, time.time())).lastrowid
        _set_members(c, gid, d.get('members'))
    audit(u, 'user_group.create', name, {'role': role, 'scope': scope_public(scope), 'members': d.get('members') or []})
    return {'ok': True, 'id': gid}


@router.patch('/api/user-groups/{gid}')
async def edit_group(gid: int, r: Request):
    u = current_user(r, 'users')
    d = await _json(r)
    with db() as c:
        old = c.execute('SELECT * FROM user_groups WHERE id=?', (gid,)).fetchone()
        if not old:
            raise HTTPException(404, 'not_found')
        name, description, role, scope = _group_fields(c, u, d, old)
        if c.execute('SELECT 1 FROM user_groups WHERE name=? AND id!=?', (name, gid)).fetchone():
            raise HTTPException(409, 'already_exists')
        c.execute('UPDATE user_groups SET name=?, description=?, role=?, scope_json=? WHERE id=?', (name, description, role, scope, gid))
        _set_members(c, gid, d.get('members'))
    audit(u, 'user_group.edit', name, {'role': role, 'scope': scope_public(scope), 'members': d.get('members')})
    return {'ok': True}


@router.delete('/api/user-groups/{gid}')
def delete_group(gid: int, r: Request):
    u = current_user(r, 'users')
    with db() as c:
        old = c.execute('SELECT * FROM user_groups WHERE id=?', (gid,)).fetchone()
        if not old:
            raise HTTPException(404, 'not_found')
        _check_grant(c, u, old['role'], old['scope_json'])
        c.execute('DELETE FROM user_group_members WHERE group_id=?', (gid,))
        c.execute('DELETE FROM user_groups WHERE id=?', (gid,))
    audit(u, 'user_group.delete', old['name'])
    return {'ok': True}


# ---------------------------------------------------------------- renamed and removed locations, groups and devices

def rename_in_scopes(c, kind, old, new=None):
    """Scopes follow a renamed location or device group (and lose a removed one). kind: 'locations' or 'groups'."""
    for table, col, key in (('users', 'scope_json', 'id'), ('user_groups', 'scope_json', 'id')):
        for row in c.execute(f"SELECT {key}, {col} FROM {table} WHERE {col} != ''").fetchall():
            s = parse_scope(row[col])
            if s is None or old not in s[kind]:
                continue
            s[kind] = sorted({x for x in s[kind] if x != old} | ({new} if new else set()))
            c.execute(f'UPDATE {table} SET {col}=? WHERE {key}=?', (json.dumps(s), row[key]))
