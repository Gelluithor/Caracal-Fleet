"""Fleet as the download source of the nodes: nodes without internet access get everything through the hub.

A node with the download source "fleet" (chosen for the SD card, the SSH installation or later per device):

- CARACAL images: GET /api/proxy/image?version=&arch= - the hub downloads the image for the node's architecture
  from its registry, verifies every digest, packs it as a "docker load" archive and caches it. The agent (or
  install-node.sh during the installation) downloads it, checks its SHA-256 and loads it. While the hub is still
  preparing the image the answer is 202; the node asks again.
- System packages: /apt/<host>/<path> - a read-only mirror of the allowed apt repositories (Debian, Raspberry Pi,
  Docker). The node's apt sources point here. apt verifies the repository signatures itself, so the hub cannot
  alter packages. Packages (pool/) are cached, repository indexes are passed through.

Both need credentials (HTTP Basic): a device (device id and device token) or, during the installation before the
device is enrolled, the enrollment token (user "enroll"). The cache is limited in size (CARACAL_HUB_PROXY_CACHE_GB).
"""
import base64
import hashlib
import hmac
import io
import json
import os
import re
import shutil
import tarfile
import tempfile
import threading
import time
from pathlib import Path

import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

from . import images
from .core import DATA, audit, cfg, current_user, db, token_hash

router = APIRouter()
CACHE = DATA / 'proxy-cache'
APT_CACHE = CACHE / 'apt'
IMAGE_CACHE = CACHE / 'images'
DEFAULT_APT_HOSTS = ('deb.debian.org', 'security.debian.org', 'ftp.debian.org', 'archive.raspberrypi.com',
                     'archive.raspberrypi.org', 'raspbian.raspberrypi.com', 'raspbian.raspberrypi.org',
                     'download.docker.com')
ARCHES = ('arm64', 'amd64')
IMAGES_KEPT = 2                  # cached archives per image and architecture
FAIL_WINDOW, FAIL_MAX = 300, 30
MANIFEST_ACCEPT = ', '.join((
    'application/vnd.oci.image.index.v1+json', 'application/vnd.docker.distribution.manifest.list.v2+json',
    'application/vnd.oci.image.manifest.v1+json', 'application/vnd.docker.distribution.manifest.v2+json'))
_fails = {}
_jobs = {}                       # image archive key -> {'state': 'preparing'|'error', 'error', 'started'}
_jobs_lock = threading.Lock()
_trimmed = [0.0]


def apt_hosts():
    extra = [h.strip().lower() for h in os.getenv('CARACAL_HUB_APT_HOSTS', '').split(',') if h.strip()]
    return tuple(dict.fromkeys(DEFAULT_APT_HOSTS + tuple(extra)))


def cache_limit():
    try:
        return int(float(os.getenv('CARACAL_HUB_PROXY_CACHE_GB', '20')) * 1024 ** 3)
    except ValueError:
        return 20 * 1024 ** 3


def _upstream(host):
    """Base URL of an apt repository host (replaced in tests)."""
    return f'https://{host}'


# ---------------------------------------------------------------- authentication

def node_auth(r: Request):
    """A device (Basic: device id / device token) or the installation (Basic: enroll / enrollment token)."""
    ip = r.client.host if r.client else ''
    now = time.time()
    recent = [t for t in _fails.get(ip, []) if t > now - FAIL_WINDOW]
    if len(recent) >= FAIL_MAX:
        raise HTTPException(429, 'too_many_attempts')
    user, password = '', ''
    header = r.headers.get('Authorization', '')
    if header.lower().startswith('basic '):
        try:
            user, _, password = base64.b64decode(header[6:].strip()).decode().partition(':')
        except ValueError:
            pass
    ok = False
    if user == 'enroll' and password:
        ok = hmac.compare_digest(password, cfg()['enroll_token'])
    elif user and password:
        with db() as c:
            row = c.execute('SELECT token_hash FROM devices WHERE id=?', (user,)).fetchone()
        ok = bool(row) and hmac.compare_digest(row['token_hash'] or '', token_hash(password))
    if not ok:
        if len(_fails) > 10000:
            _fails.clear()
        _fails[ip] = recent + [now]
        raise HTTPException(401, 'invalid_device_token', headers={'WWW-Authenticate': 'Basic realm="CARACAL Fleet"'})
    _fails.pop(ip, None)
    return user


# ---------------------------------------------------------------- apt mirror

def _immutable(path):
    # packages never change under their name; repository indexes (dists/) do
    return '/pool/' in '/' + path or path.endswith(('.deb', '.udeb'))


def _cache_file(host, path):
    target = (APT_CACHE / host / path).resolve()
    if not str(target).startswith(str((APT_CACHE / host).resolve())):
        raise HTTPException(400, 'invalid_path')
    return target


def _store(url, target):
    """Download a package into the cache; None when the upstream does not have it."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + f'.{os.getpid()}.{threading.get_ident()}.part')
    try:
        with requests.get(url, stream=True, timeout=(15, 300)) as up:
            if up.status_code != 200:
                return up.status_code
            with tmp.open('wb') as f:
                for chunk in up.iter_content(1024 * 256):
                    f.write(chunk)
        tmp.replace(target)
        return 200
    finally:
        tmp.unlink(missing_ok=True)


def trim_cache(force=False):
    """Keep the cache under its limit: the least recently used files go first."""
    if not force and time.time() - _trimmed[0] < 300:
        return
    _trimmed[0] = time.time()
    files = [p for p in CACHE.rglob('*') if p.is_file() and not p.name.endswith('.part')] if CACHE.exists() else []
    total = sum(p.stat().st_size for p in files)
    for p in sorted(files, key=lambda x: x.stat().st_mtime):
        if total <= cache_limit():
            break
        total -= p.stat().st_size
        p.unlink(missing_ok=True)


@router.api_route('/apt/{host}/{path:path}', methods=['GET', 'HEAD'])
async def apt(host: str, path: str, r: Request):
    node_auth(r)
    host = host.lower()
    if host not in apt_hosts():
        raise HTTPException(404, 'apt_host_not_allowed')
    if not path or '..' in path.split('/') or '\\' in path:
        raise HTTPException(400, 'invalid_path')
    url = f'{_upstream(host)}/{path}'
    if _immutable(path):
        target = _cache_file(host, path)
        if not target.is_file():
            status = await run_in_threadpool(_store, url, target)
            if status != 200:
                raise HTTPException(status if status in (403, 404, 410) else 502, 'upstream_unavailable')
            await run_in_threadpool(trim_cache)
        os.utime(target)   # recently used, kept longer
        return FileResponse(target, media_type='application/vnd.debian.binary-package')
    # repository indexes: passed through (apt sends If-Modified-Since and gets 304 when nothing changed)
    headers = {k: v for k, v in r.headers.items() if k.lower() in ('if-modified-since', 'if-none-match')}
    try:
        up = await run_in_threadpool(lambda: requests.request(r.method, url, headers=headers, stream=True,
                                                              timeout=(15, 120)))
    except requests.RequestException:
        raise HTTPException(502, 'upstream_unavailable')
    keep = {k: v for k, v in up.headers.items() if k.lower() in ('content-type', 'last-modified', 'etag', 'content-length')}
    if r.method == 'HEAD' or up.status_code in (304, 404):
        up.close()
        if r.method != 'HEAD':   # no body: the upstream length does not apply
            keep.pop('Content-Length', None)
            keep.pop('content-length', None)
        return Response(status_code=up.status_code, headers=keep)

    def body():
        try:
            yield from up.iter_content(1024 * 64)
        finally:
            up.close()
    return StreamingResponse(body(), status_code=up.status_code, headers=keep)


# ---------------------------------------------------------------- CARACAL images for "docker load"

def _safe(text):
    return re.sub(r'[^A-Za-z0-9._-]', '_', text)


def image_archive(image, version, arch):
    return IMAGE_CACHE / f'{_safe(image)}__{_safe(version)}__{arch}.tar'


class Registry:
    """Anonymous registry v2 access with one bearer token per image (like images.list_tags)."""

    def __init__(self, image):
        self.host, self.repo = images._split(image)
        self.headers = {}

    def get(self, path, accept=None, stream=False):
        url = f'{images.REGISTRY_SCHEME}://{self.host}/v2/{self.repo}/{path}'
        headers = {**self.headers, **({'Accept': accept} if accept else {})}
        r = requests.get(url, headers=headers, stream=stream, timeout=(15, 600))
        if r.status_code == 401 and not self.headers:
            token = images._token(r.headers.get('WWW-Authenticate'))
            if token:
                self.headers = {'Authorization': f'Bearer {token}'}
                r = requests.get(url, headers={**self.headers, **({'Accept': accept} if accept else {})},
                                 stream=stream, timeout=(15, 600))
        r.raise_for_status()
        return r

    def manifest(self, ref, digest=None):
        r = self.get(f'manifests/{ref}', MANIFEST_ACCEPT)
        if digest and 'sha256:' + hashlib.sha256(r.content).hexdigest() != digest:
            raise RuntimeError(f'manifest {ref}: digest mismatch')
        return r.json()

    def blob(self, digest, target):
        """Download a blob and check that its content matches its digest."""
        h = hashlib.sha256()
        with self.get(f'blobs/{digest}', stream=True) as r, open(target, 'wb') as f:
            for chunk in r.iter_content(1024 * 1024):
                h.update(chunk)
                f.write(chunk)
        if 'sha256:' + h.hexdigest() != digest:
            raise RuntimeError(f'blob {digest[:19]}: digest mismatch')


def build_archive(image, version, arch):
    """Image of one architecture as a docker-archive (manifest.json, config, layers; docker load decompresses them)."""
    reg = Registry(image)
    m = reg.manifest(version)
    if 'manifests' in m:   # multi-architecture index: the manifest of this architecture (attestations are skipped)
        entry = next((x for x in m['manifests'] if (x.get('platform') or {}).get('os') == 'linux'
                      and (x.get('platform') or {}).get('architecture') == arch), None)
        if not entry:
            raise RuntimeError(f'{image}:{version} has no linux/{arch} image')
        m = reg.manifest(entry['digest'], entry['digest'])
    if 'config' not in m or 'layers' not in m:
        raise RuntimeError('unsupported image manifest')
    target = image_archive(image, version, arch)
    target.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='image-', dir=target.parent))
    try:
        config = m['config']['digest']
        layers = [x['digest'] for x in m['layers']]
        tmp = target.with_suffix('.tar.part')
        with tarfile.open(tmp, 'w') as tar:
            members = [(f"{config.split(':', 1)[1]}.json", config)] + \
                      [(f"{d.split(':', 1)[1]}/layer.tar", d) for d in layers]
            for name, digest in members:
                blob = work / digest.split(':', 1)[1]
                reg.blob(digest, blob)
                tar.add(blob, arcname=name)
                blob.unlink()
            manifest = json.dumps([{'Config': members[0][0], 'RepoTags': [f'{image}:{version}'],
                                    'Layers': [n for n, _ in members[1:]]}]).encode()
            info = tarfile.TarInfo('manifest.json')
            info.size = len(manifest)
            info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(manifest))
        h = hashlib.sha256()
        with open(tmp, 'rb') as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b''):
                h.update(chunk)
        tmp.replace(target)
        target.with_suffix('.sha256').write_text(h.hexdigest())
    finally:
        shutil.rmtree(work, ignore_errors=True)
        target.with_suffix('.tar.part').unlink(missing_ok=True)
    # older archives of this image and architecture are not needed any more
    olds = sorted(IMAGE_CACHE.glob(f'{_safe(image)}__*__{arch}.tar'), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in olds[IMAGES_KEPT:]:
        old.unlink(missing_ok=True)
        old.with_suffix('.sha256').unlink(missing_ok=True)
    trim_cache(force=True)


def _ready(image, version, arch):
    path = image_archive(image, version, arch)
    sha = path.with_suffix('.sha256')
    if not path.is_file() or not sha.is_file():
        return None
    # moving tags (latest, edge) are checked again after an hour
    if version in images.MOVING_TAGS and time.time() - path.stat().st_mtime > 3600:
        return None
    return path, sha.read_text().strip()


def prepare(image, version, arch):
    """Start preparing an image archive in the background; returns the job state or None when it is ready."""
    if _ready(image, version, arch):
        return None
    key = (image, version, arch)
    with _jobs_lock:
        job = _jobs.get(key)
        if job and job['state'] == 'preparing':
            return job
        job = _jobs[key] = {'state': 'preparing', 'error': '', 'started': time.time()}

    def work():
        try:
            build_archive(image, version, arch)
            with _jobs_lock:
                _jobs.pop(key, None)
        except Exception as e:  # noqa: BLE001 - reported to the node that asks
            with _jobs_lock:
                _jobs[key] = {'state': 'error', 'error': str(e)[:300], 'started': job['started']}
    threading.Thread(target=work, daemon=True, name='image-' + _safe(version)).start()
    return job


@router.get('/api/proxy/image')
def proxy_image(r: Request, version: str = '', arch: str = ''):
    node_auth(r)
    image = images.node_image()
    if not image:
        raise HTTPException(400, 'node_image_missing')
    if not images.VERSION_RE.match(version):
        raise HTTPException(400, 'version_required')
    if arch not in ARCHES:
        raise HTTPException(400, 'invalid_value')
    key = (image, version, arch)
    with _jobs_lock:
        failed = _jobs.get(key) if (_jobs.get(key) or {}).get('state') == 'error' else None
        if failed:
            _jobs.pop(key, None)   # the next request tries again
    if failed:
        raise HTTPException(502, f"image_download_failed: {failed['error']}")
    ready = _ready(image, version, arch)
    if ready:
        path, sha = ready
        os.utime(path)
        return FileResponse(path, media_type='application/x-tar', headers={'X-Sha256': sha, 'X-Image': f'{image}:{version}'})
    job = prepare(image, version, arch) or {'started': time.time()}
    return JSONResponse({'state': 'preparing', 'image': f'{image}:{version}', 'arch': arch,
                         'seconds': int(time.time() - job['started'])}, status_code=202)


# ---------------------------------------------------------------- administration

def _size(path):
    return sum(p.stat().st_size for p in path.rglob('*') if p.is_file()) if path.exists() else 0


@router.get('/api/proxy/status')
def proxy_status(r: Request):
    current_user(r, 'manage')
    archives = [{'name': p.name.replace('__', ' · ').removesuffix('.tar'), 'size': p.stat().st_size,
                 'updated': p.stat().st_mtime} for p in sorted(IMAGE_CACHE.glob('*.tar'))] if IMAGE_CACHE.exists() else []
    with _jobs_lock:
        jobs = [{'image': f'{k[0]}:{k[1]}', 'arch': k[2], **v} for k, v in _jobs.items()]
    return {'apt_hosts': list(apt_hosts()), 'cache_bytes': _size(CACHE), 'cache_limit': cache_limit(),
            'apt_bytes': _size(APT_CACHE), 'images': archives, 'jobs': jobs}


@router.post('/api/proxy/cache/clear')
def proxy_clear(r: Request):
    u = current_user(r, 'admin')
    freed = _size(CACHE)
    shutil.rmtree(CACHE, ignore_errors=True)
    audit(u, 'proxy.cache_clear', '', {'bytes': freed})
    return {'ok': True, 'freed': freed}
