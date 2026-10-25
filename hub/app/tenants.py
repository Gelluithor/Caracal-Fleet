"""Customers (tenants) in one Fleet, for companies that run screens for several customers.

A customer owns locations. A user, user group member or API token of a customer sees only the devices in those
locations (whatever their role and scope say) and only the customer's global playlists, media and templates; system
settings, users and the other customers stay with the operator of Fleet (users without a customer).
"""
import contextvars
import time

from fastapi import APIRouter, HTTPException, Request

from .core import SYSTEM_PERMISSIONS, Access, audit, current_user, db

router = APIRouter()
CURRENT = contextvars.ContextVar('caracal_tenant', default=None)   # the customer of the user of this request


class TenantAccess(Access):
    """Access of a customer's user: only devices in the customer's locations, no system permissions."""

    def __init__(self, grants, tenant, locations):
        super().__init__([])
        self.grants = grants
        self.tenant, self.locations = tenant, set(locations)

    def on(self, perm, device):
        d = dict(device) if not isinstance(device, dict) else device
        return d.get('location') in self.locations and super().on(perm, device)

    def has(self, perm):
        return perm not in SYSTEM_PERMISSIONS and super().has(perm)

    def everywhere(self, perm):
        return False

    def scoped(self):
        return True


def locations_of(c, tid):
    return [x['name'] for x in c.execute('SELECT name FROM device_locations WHERE tenant_id=?', (tid,))]


def restrict(c, access, tid):
    """The access of a user limited to the customer: "all devices" becomes the customer's locations."""
    locs = locations_of(c, tid)
    grants = [(p, s if s is not None else {'locations': locs, 'groups': [], 'devices': []}) for p, s in access.grants]
    return TenantAccess(grants, tid, locs)


def owned(row):
    """Whether the object (global playlist, file, template) belongs to the customer of this request (or the operator)."""
    tid = CURRENT.get()
    return tid is None or (row is not None and dict(row).get('tenant_id') == tid)


def check(row):
    if not owned(row):
        raise HTTPException(404, 'not_found')


def sql(alias=''):
    """(WHERE fragment, args) limiting a list to the customer of this request."""
    tid = CURRENT.get()
    return ('', []) if tid is None else (f' AND {alias}tenant_id=?', [tid])


def operator(u):
    if u.get('tenant_id'):
        raise HTTPException(403, 'forbidden')


def _public(c, row):
    return {'id': row['id'], 'name': row['name'], 'created': row['created'], 'locations': locations_of(c, row['id']),
            'users': c.execute('SELECT COUNT(*) FROM users WHERE tenant_id=?', (row['id'],)).fetchone()[0]}


@router.get('/api/tenants')
def list_tenants(r: Request):
    u = current_user(r)
    with db() as c:
        rows = c.execute('SELECT * FROM tenants ORDER BY name COLLATE NOCASE').fetchall()
        if u.get('tenant_id'):
            rows = [x for x in rows if x['id'] == u['tenant_id']]
        return [_public(c, x) for x in rows]


async def _fields(c, r, tid=None):
    try:
        d = await r.json()
    except ValueError:
        raise HTTPException(400, 'invalid_json')
    name = str(d.get('name') or '').strip()[:120]
    if not name:
        raise HTTPException(400, 'name_required')
    if c.execute('SELECT 1 FROM tenants WHERE name=? AND id IS NOT ?', (name, tid)).fetchone():
        raise HTTPException(409, 'already_exists')
    locs = [str(x) for x in d.get('locations') or []]
    taken = [x['name'] for x in c.execute('SELECT name, tenant_id FROM device_locations').fetchall()
             if x['name'] in locs and x['tenant_id'] not in (None, tid)]
    if taken:
        raise HTTPException(409, 'location_taken:' + ', '.join(taken))
    return name, locs


@router.post('/api/tenants')
async def create(r: Request):
    u = current_user(r, 'users')
    operator(u)
    with db() as c:
        name, locs = await _fields(c, r)
        tid = c.execute('INSERT INTO tenants(name, created) VALUES(?,?)', (name, time.time())).lastrowid
        c.executemany('UPDATE device_locations SET tenant_id=? WHERE name=?', [(tid, x) for x in locs])
    audit(u, 'tenant.create', name, {'locations': locs})
    return {'id': tid}


@router.patch('/api/tenants/{tid}')
async def edit(tid: int, r: Request):
    u = current_user(r, 'users')
    operator(u)
    with db() as c:
        if not c.execute('SELECT 1 FROM tenants WHERE id=?', (tid,)).fetchone():
            raise HTTPException(404, 'not_found')
        name, locs = await _fields(c, r, tid)
        c.execute('UPDATE tenants SET name=? WHERE id=?', (name, tid))
        c.execute('UPDATE device_locations SET tenant_id=NULL WHERE tenant_id=?', (tid,))
        c.executemany('UPDATE device_locations SET tenant_id=? WHERE name=?', [(tid, x) for x in locs])
    audit(u, 'tenant.edit', name, {'locations': locs})
    return {'ok': True}


@router.delete('/api/tenants/{tid}')
def delete(tid: int, r: Request):
    u = current_user(r, 'users')
    operator(u)
    with db() as c:
        if c.execute('SELECT 1 FROM users WHERE tenant_id=?', (tid,)).fetchone():
            raise HTTPException(409, 'tenant_in_use')
        c.execute('UPDATE device_locations SET tenant_id=NULL WHERE tenant_id=?', (tid,))
        for table in ('global_playlists', 'files', 'node_templates', 'api_tokens'):
            c.execute(f'UPDATE {table} SET tenant_id=NULL WHERE tenant_id=?', (tid,))
        c.execute('DELETE FROM tenants WHERE id=?', (tid,))
    audit(u, 'tenant.delete', str(tid))
    return {'ok': True}


def tenant_value(c, u, value):
    """The customer chosen in a form (only the operator chooses; a customer's user always gets their own)."""
    if u.get('tenant_id'):
        return u['tenant_id']
    if value in (None, '', 0, '0'):
        return None
    try:
        tid = int(value)
    except (TypeError, ValueError):
        raise HTTPException(400, 'invalid_value')
    if not c.execute('SELECT 1 FROM tenants WHERE id=?', (tid,)).fetchone():
        raise HTTPException(400, 'invalid_value')
    return tid


def names(c):
    return {x['id']: x['name'] for x in c.execute('SELECT id, name FROM tenants')}

