"""CARACAL node images: which image the nodes run, which versions the registry offers, updates and conversion.

Docker nodes are updated by pulling a new image version (the agent rolls back on failure); classic nodes
(/opt/caracal) can be converted to Docker. Versions are read from any Docker registry v2 (GHCR, Docker Hub,
GitLab, a private registry) using anonymous token authentication, so public images need no credentials.
"""
import os
import re
import time

import requests
from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from .core import (audit, cfg, current_user, db, new_batch, queue_command, save_cfg, validate_admin,
                   version_tuple)
from .devices import build

router = APIRouter()
IMAGE_RE = re.compile(r'^[a-z0-9]+([._-][a-z0-9]+)*(:[0-9]+)?(/[a-z0-9]+([._-][a-z0-9]+)*)+$')
VERSION_RE = re.compile(r'^[A-Za-z0-9._+-]{1,64}$')
MOVING_TAGS = ('latest', 'edge', 'main', 'master')
REGISTRY_SCHEME = os.getenv('CARACAL_REGISTRY_SCHEME', 'https')   # http only for local test registries
_cache = {}


def node_image():
    return cfg().get('node_image') or os.getenv('CARACAL_NODE_IMAGE', '')


def _split(image):
    first, _, rest = image.partition('/')
    if '.' in first or ':' in first or first == 'localhost':
        return first, rest
    return 'registry-1.docker.io', image if '/' in image else f'library/{image}'


def _token(header):
    """Anonymous bearer token from a 'WWW-Authenticate: Bearer realm=...,service=...,scope=...' challenge."""
    params = dict(re.findall(r'(\w+)="([^"]*)"', header or ''))
    if 'realm' not in params:
        return None
    r = requests.get(params.pop('realm'), params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    return data.get('token') or data.get('access_token')


def list_tags(image, scheme=None):
    host, repo = _split(image)
    url = f'{scheme or REGISTRY_SCHEME}://{host}/v2/{repo}/tags/list?n=1000'
    r = requests.get(url, timeout=15)
    if r.status_code == 401:
        token = _token(r.headers.get('WWW-Authenticate'))
        r = requests.get(url, timeout=15, headers={'Authorization': f'Bearer {token}'} if token else {})
    r.raise_for_status()
    return r.json().get('tags') or []


def sort_versions(tags):
    """Real versions newest first; moving tags (latest, edge) at the end."""
    fixed = [t for t in tags if t not in MOVING_TAGS and not t.startswith('sha')]
    fixed.sort(key=lambda t: (version_tuple(t), t), reverse=True)
    return fixed + [t for t in MOVING_TAGS if t in tags]


def available_versions(force=False):
    image = node_image()
    if not image:
        return {'image': '', 'versions': [], 'error': 'node_image_missing'}
    cached = _cache.get(image)
    if cached and not force and time.time() - cached['at'] < 300:
        return cached['data']
    try:
        data = {'image': image, 'versions': sort_versions(list_tags(image)), 'error': ''}
    except (requests.RequestException, ValueError) as e:
        data = {'image': image, 'versions': [], 'error': f'registry_unavailable: {e}'[:300]}
    _cache[image] = {'at': time.time(), 'data': data}
    return data


def latest_version():
    versions = [v for v in available_versions().get('versions', []) if v not in MOVING_TAGS]
    return versions[0] if versions else None


@router.get('/api/node-image')
async def get_node_image(r: Request, refresh: bool = False):
    current_user(r)
    return await run_in_threadpool(available_versions, refresh)


@router.put('/api/node-image')
async def set_node_image(r: Request):
    u = current_user(r, 'admin')
    d = await r.json()
    image = str(d.get('image', '')).strip().lower()
    if image and not IMAGE_RE.fullmatch(image):
        raise HTTPException(400, 'invalid_image')
    c = cfg()
    c['node_image'] = image
    save_cfg(c)
    _cache.pop(image, None)
    audit(u, 'image.set', image)
    return await run_in_threadpool(available_versions, True)


def _queue(r_user, ids, action, payload, wanted_runtime):
    """Queue an image command on online nodes with the given runtime; others are skipped with a reason."""
    batch, queued, skipped = new_batch(), [], []
    with db() as c:
        for did in ids:
            row = c.execute('SELECT * FROM devices WHERE id=?', (did,)).fetchone()
            if not row:
                skipped.append({'device_id': did, 'reason': 'device_not_found'})
                continue
            d = build(row)
            if not d['online']:
                skipped.append({'device_id': did, 'reason': 'device_offline'})
            elif d['runtime'] != wanted_runtime:
                skipped.append({'device_id': did, 'reason': 'not_docker' if wanted_runtime == 'docker' else
                                'already_docker' if d['runtime'] == 'docker' else 'no_caracal'})
            else:
                queued.append(queue_command(c, did, action, payload, r_user['username'], batch))
    return {'command_ids': queued, 'skipped': skipped, 'batch': batch}


async def _deploy_body(r):
    d = await r.json()
    version = str(d.get('version', '')).strip()
    if not VERSION_RE.fullmatch(version):
        raise HTTPException(400, 'version_required')
    ids = list(dict.fromkeys(str(x) for x in d.get('device_ids') or []))
    if not ids:
        raise HTTPException(400, 'no_devices')
    image = node_image()
    if not image:
        raise HTTPException(400, 'node_image_missing')
    return version, ids, image


def _prepare_for_fleet_nodes(ids, image, version):
    """Nodes that download through the hub get the image from its cache: start preparing it right away."""
    from . import proxy
    with db() as c:
        rows = [c.execute('SELECT * FROM devices WHERE id=?', (did,)).fetchone() for did in ids]
    for arch in {build(x)['arch'] for x in rows if x and build(x)['download_source'] == 'fleet'} & set(proxy.ARCHES):
        proxy.prepare(image, version, arch)


@router.post('/api/node-image/deploy')
async def deploy_version(r: Request):
    """Update Docker nodes to an image version."""
    u = current_user(r, 'manage')
    version, ids, image = await _deploy_body(r)
    res = _queue(u, ids, 'update_caracal', {'version': version, 'image': image}, 'docker')
    _prepare_for_fleet_nodes(ids, image, version)
    audit(u, 'image.deploy', version, {'devices': ids, 'skipped': res['skipped']})
    return res


@router.post('/api/node-image/convert')
async def convert_nodes(r: Request):
    """Convert classic CARACAL nodes to Docker (data in /var/lib/caracal are kept)."""
    u = current_user(r, 'manage')
    version, ids, image = await _deploy_body(r)
    d = await r.json()
    # optional: the web administrator of the converted nodes (created, or a new name and password)
    admin = validate_admin({'username': d.get('admin_username'), 'password': d.get('admin_password')}) \
        if d.get('admin_username') or d.get('admin_password') else {}
    res = _queue(u, ids, 'convert_to_docker', {'version': version, 'image': image, **admin}, 'host')
    audit(u, 'image.convert', version, {'devices': ids, 'skipped': res['skipped']})
    return res
