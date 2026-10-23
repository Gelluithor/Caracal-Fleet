"""The media library: pictures and videos kept in Fleet, with where each one is used (global playlists, templates).
A file can be replaced everywhere at once; the global playlists that used it can then be deployed again.
"""
import json
import time

from fastapi import APIRouter, HTTPException, Request

from .core import audit, current_user, db, refresh_pin

router = APIRouter()
KINDS = ('image', 'video')


async def _json(r: Request):
    try:
        d = await r.json()
    except ValueError:
        raise HTTPException(400, 'invalid_json')
    return d if isinstance(d, dict) else {}


def usage(c, fid):
    out = [{'type': 'global', 'id': x['id'], 'name': x['name'], 'items': x['n']} for x in c.execute(
        'SELECT p.id, p.name, COUNT(*) n FROM global_playlist_items i JOIN global_playlists p ON p.id=i.playlist_id '
        'WHERE i.file_id=? GROUP BY p.id ORDER BY p.name', (fid,))]
    out += [{'type': 'template', 'id': x['id'], 'name': x['name']} for x in c.execute(
        'SELECT id, name FROM node_templates WHERE image_file_id=? ORDER BY name', (fid,))]
    return out


def _file(c, fid):
    row = c.execute('SELECT * FROM files WHERE id=?', (fid,)).fetchone()
    if not row or row['kind'] not in KINDS:
        raise HTTPException(404, 'file_not_found')
    return row


@router.get('/api/library')
def list_library(r: Request):
    current_user(r)
    with db() as c:
        rows = c.execute(
            "SELECT * FROM files WHERE kind IN ('image','video') AND (COALESCE(library,0)=1 OR id IN "
            "(SELECT file_id FROM global_playlist_items WHERE file_id!='') OR id IN (SELECT image_file_id FROM node_templates)) "
            "ORDER BY created DESC").fetchall()
        return [{'id': x['id'], 'name': x['name'], 'kind': x['kind'], 'size': x['size'], 'created': x['created'],
                 'origin': x['origin'], 'library': bool(x['library']), 'usage': usage(c, x['id'])} for x in rows]


@router.get('/api/library/{fid}/file')
def library_file(fid: str, r: Request):
    """The file for a thumbnail in the library (fetched by the UI with its token)."""
    from fastapi.responses import FileResponse
    from .core import FILES
    current_user(r)
    with db() as c:
        row = _file(c, fid)
    path = FILES / row['id']
    if not path.exists():
        raise HTTPException(404, 'file_not_found')
    return FileResponse(path, media_type='video/mp4' if row['kind'] == 'video' else None, filename=row['name'])


@router.post('/api/library/{fid}')
async def add(fid: str, r: Request):
    """Keeps an uploaded file (POST /api/files) in the library."""
    u = current_user(r, 'content')
    d = await _json(r)
    with db() as c:
        row = _file(c, fid)
        name = str(d.get('name') or row['name']).strip()[:200] or row['name']
        c.execute('UPDATE files SET library=1, pinned=1, name=? WHERE id=?', (name, fid))
    audit(u, 'library.add', fid, {'name': name})
    return {'ok': True}


@router.patch('/api/library/{fid}')
async def rename(fid: str, r: Request):
    u = current_user(r, 'content')
    d = await _json(r)
    name = str(d.get('name') or '').strip()[:200]
    if not name:
        raise HTTPException(400, 'name_required')
    with db() as c:
        _file(c, fid)
        c.execute('UPDATE files SET name=? WHERE id=?', (name, fid))
    audit(u, 'library.rename', fid, {'name': name})
    return {'ok': True}


@router.delete('/api/library/{fid}')
def remove(fid: str, r: Request):
    u = current_user(r, 'content')
    with db() as c:
        row = _file(c, fid)
        if usage(c, fid):
            raise HTTPException(409, 'file_in_use')
        c.execute('UPDATE files SET library=0 WHERE id=?', (fid,))
        refresh_pin(c, fid)   # the normal clean-up of uploads removes it
    audit(u, 'library.remove', fid, {'name': row['name']})
    return {'ok': True}


@router.post('/api/library/{fid}/replace')
async def replace(fid: str, r: Request):
    """Every use of the file gets another one (of the same kind); returns the global playlists that changed with their
    last deployment, so they can be deployed again."""
    from .playlists import _before_change, _touch, record_version
    u = current_user(r, 'content')
    d = await _json(r)
    with db() as c:
        old = _file(c, fid)
        new = _file(c, str(d.get('file_id') or ''))
        if new['id'] == old['id']:
            raise HTTPException(400, 'invalid_value')
        if new['kind'] != old['kind']:
            raise HTTPException(400, 'unsupported_file')
        playlists = [x['playlist_id'] for x in c.execute(
            'SELECT DISTINCT playlist_id FROM global_playlist_items WHERE file_id=?', (fid,))]
        changed = []
        for pid in playlists:
            _before_change(c, pid)
            # an item named after the old file takes the new name
            c.execute('UPDATE global_playlist_items SET name=? WHERE playlist_id=? AND file_id=? AND name=?',
                      (new['name'], pid, fid, old['name']))
            c.execute('UPDATE global_playlist_items SET file_id=? WHERE playlist_id=? AND file_id=?', (new['id'], pid, fid))
            _touch(c, pid, u)
            record_version(c, pid, u['username'], 'replace_file')
            p = c.execute('SELECT id, name FROM global_playlists WHERE id=?', (pid,)).fetchone()
            last = c.execute('SELECT targets_json, mode, created FROM global_deployments WHERE playlist_id=? ORDER BY id DESC LIMIT 1',
                             (pid,)).fetchone()
            changed.append({'id': p['id'], 'name': p['name'], 'last_deployment': {
                'targets': json.loads(last['targets_json']), 'mode': last['mode'], 'created': last['created']} if last else None})
        templates = c.execute('UPDATE node_templates SET image_file_id=?, updated=? WHERE image_file_id=?',
                              (new['id'], time.time(), fid)).rowcount
        if old['library']:
            c.execute('UPDATE files SET library=1 WHERE id=?', (new['id'],))
        c.execute('UPDATE files SET library=0 WHERE id=?', (fid,))
        refresh_pin(c, new['id'])
        refresh_pin(c, fid)
    audit(u, 'library.replace', fid, {'old': old['name'], 'new': new['name'], 'playlists': len(changed), 'templates': templates})
    return {'ok': True, 'playlists': changed, 'templates': templates}
