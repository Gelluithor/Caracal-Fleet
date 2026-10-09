"""Fleet as the download source: apt mirror, CARACAL images for docker load, the agent and the installer."""
import gzip
import hashlib
import http.client
import io
import json
import os
import shutil
import subprocess
import tarfile
import time
from urllib.parse import urlparse

import pytest
import requests
from fastapi import FastAPI, Response

from conftest import ROOT, TMP, load_agent, serve

PW = 'admin-password-123'


def sha(data):
    return 'sha256:' + hashlib.sha256(data).hexdigest()


def fake_apt():
    app = FastAPI()
    state = {'release': 'Release 1', 'deb': b'!<arch>\npackage-1'}
    app.get('/debian/dists/trixie/InRelease')(lambda: Response(state['release'], media_type='text/plain'))
    app.get('/debian/pool/main/c/caracal/caracal_1_arm64.deb')(
        lambda: Response(state['deb'], media_type='application/vnd.debian.binary-package'))
    return app, state


def fake_registry():
    """A multi-architecture image (arm64 + amd64 + an attestation) with one gzip layer per architecture."""
    app = FastAPI()
    blobs, manifests = {}, {}
    for arch in ('arm64', 'amd64'):
        layer = gzip.compress(b'layer of ' + arch.encode() * 100)
        config = json.dumps({'architecture': arch, 'os': 'linux', 'rootfs': {'type': 'layers', 'diff_ids': []}}).encode()
        blobs[sha(layer)], blobs[sha(config)] = layer, config
        m = json.dumps({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json',
                        'config': {'digest': sha(config), 'size': len(config)},
                        'layers': [{'digest': sha(layer), 'size': len(layer)}]}).encode()
        manifests[sha(m)] = m
        manifests['arch:' + arch] = sha(m)
    index = json.dumps({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.index.v1+json', 'manifests': [
        {'digest': manifests['arch:arm64'], 'platform': {'os': 'linux', 'architecture': 'arm64'}},
        {'digest': manifests['arch:amd64'], 'platform': {'os': 'linux', 'architecture': 'amd64'}},
        {'digest': 'sha256:' + '0' * 64, 'platform': {'os': 'unknown', 'architecture': 'unknown'}}]}).encode()
    state = {'blobs': blobs, 'corrupt': set()}

    @app.get('/v2/owner/caracal-node/manifests/{ref}')
    def manifest(ref: str):
        body = index if not ref.startswith('sha256:') else manifests.get(ref)
        return Response(body, media_type='application/json') if body else Response(status_code=404)

    @app.get('/v2/owner/caracal-node/blobs/{digest}')
    def blob(digest: str):
        if digest not in blobs:
            return Response(status_code=404)
        return Response(b'tampered' if digest in state['corrupt'] else blobs[digest])
    return app, state


@pytest.fixture(scope='module')
def env(hub_app):
    from app import images, proxy
    from app.core import cfg, save_cfg
    hub_url, _ = serve(hub_app)
    apt_app, apt_state = fake_apt()
    apt_url, _ = serve(apt_app)
    reg_app, reg_state = fake_registry()
    reg_url, _ = serve(reg_app)
    h = {'Authorization': 'Bearer ' + requests.post(hub_url + '/api/login', json={'username': 'admin', 'password': PW}).json()['token']}
    enrolled = requests.post(hub_url + '/api/device/enroll', json={
        'enroll_token': 'enroll-test-token', 'fingerprint': 'proxy-node', 'name': 'Offline node'}).json()
    old_upstream, old_scheme, old_image = proxy._upstream, images.REGISTRY_SCHEME, cfg().get('node_image', '')
    proxy._upstream = lambda host: apt_url
    images.REGISTRY_SCHEME = 'http'
    c = cfg()
    c['node_image'] = reg_url.replace('http://', '') + '/owner/caracal-node'
    save_cfg(c)
    shutil.rmtree(proxy.CACHE, ignore_errors=True)
    yield {'hub': hub_url, 'h': h, 'apt': apt_state, 'reg': reg_state, 'image': c['node_image'],
           'auth': (enrolled['device_id'], enrolled['device_token']), 'enrolled': enrolled, 'proxy': proxy}
    proxy._upstream, images.REGISTRY_SCHEME = old_upstream, old_scheme
    c = cfg()
    c['node_image'] = old_image
    save_cfg(c)


def raw_get(url, auth):
    """GET without client-side path normalisation (requests would resolve '..')."""
    u = urlparse(url)
    conn = http.client.HTTPConnection(u.hostname, u.port, timeout=10)
    token = __import__('base64').b64encode(f'{auth[0]}:{auth[1]}'.encode()).decode()
    conn.request('GET', u.path, headers={'Authorization': 'Basic ' + token})
    return conn.getresponse().status


def test_apt_mirror(env):
    base = env['hub'] + '/apt/deb.debian.org/debian'
    r = requests.get(base + '/dists/trixie/InRelease')
    assert r.status_code == 401 and 'Basic' in r.headers['WWW-Authenticate']
    assert requests.get(base + '/dists/trixie/InRelease', auth=(env['auth'][0], 'wrong')).status_code == 401
    assert requests.get(base + '/dists/trixie/InRelease', auth=('enroll', 'enroll-test-token')).text == 'Release 1'
    assert requests.get(base + '/dists/trixie/InRelease', auth=env['auth']).text == 'Release 1'
    # repository indexes are passed through, packages are cached
    deb = base + '/pool/main/c/caracal/caracal_1_arm64.deb'
    assert requests.get(deb, auth=env['auth']).content == b'!<arch>\npackage-1'
    env['apt']['release'], env['apt']['deb'] = 'Release 2', b'changed upstream'
    assert requests.get(base + '/dists/trixie/InRelease', auth=env['auth']).text == 'Release 2'
    assert requests.get(deb, auth=env['auth']).content == b'!<arch>\npackage-1'
    assert (env['proxy'].APT_CACHE / 'deb.debian.org/debian/pool/main/c/caracal/caracal_1_arm64.deb').is_file()
    assert requests.get(base + '/dists/none/InRelease', auth=env['auth']).status_code == 404
    # only the allowed repositories, no way out of the cache folder
    assert requests.get(env['hub'] + '/apt/evil.example/x', auth=env['auth']).json()['detail'] == 'apt_host_not_allowed'
    assert raw_get(env['hub'] + '/apt/deb.debian.org/debian/pool/../../../../hub.db', env['auth']) in (400, 404)


def wait_image(env, arch, timeout=30):
    url = env['hub'] + '/api/proxy/image'
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = requests.get(url, params={'version': '2026.10.09', 'arch': arch}, auth=env['auth'])
        if r.status_code != 202:
            return r
        time.sleep(0.2)
    raise AssertionError('image not prepared in time')


def test_image_archive_for_docker_load(env):
    url = env['hub'] + '/api/proxy/image'
    assert requests.get(url, params={'version': '2026.10.09', 'arch': 'arm64'}).status_code == 401
    assert requests.get(url, params={'version': '2026.10.09', 'arch': 'mips'}, auth=env['auth']).status_code == 400
    first = requests.get(url, params={'version': '2026.10.09', 'arch': 'arm64'}, auth=env['auth'])
    assert first.status_code == 202 and first.json()['state'] == 'preparing'
    r = wait_image(env, 'arm64')
    assert r.status_code == 200 and r.headers['X-Sha256'] == hashlib.sha256(r.content).hexdigest()
    with tarfile.open(fileobj=io.BytesIO(r.content)) as tar:
        manifest = json.loads(tar.extractfile('manifest.json').read())[0]
        assert manifest['RepoTags'] == [env['image'] + ':2026.10.09']
        config = tar.extractfile(manifest['Config']).read()
        assert json.loads(config)['architecture'] == 'arm64'
        layer = tar.extractfile(manifest['Layers'][0]).read()
        assert sha(layer) in env['reg']['blobs'] and gzip.decompress(layer).startswith(b'layer of arm64')
    status = requests.get(env['hub'] + '/api/proxy/status', headers=env['h']).json()
    assert any('2026.10.09' in x['name'] and 'arm64' in x['name'] for x in status['images'])


def test_image_digests_are_verified(env):
    amd_layer = next(d for d, b in env['reg']['blobs'].items() if b.startswith(b'\x1f\x8b') and b'amd64' in gzip.decompress(b))
    env['reg']['corrupt'].add(amd_layer)
    requests.get(env['hub'] + '/api/proxy/image', params={'version': '2026.10.09', 'arch': 'amd64'}, auth=env['auth'])
    r = wait_image(env, 'amd64')
    assert r.status_code == 502 and 'digest mismatch' in r.json()['detail']
    env['reg']['corrupt'].clear()
    requests.get(env['hub'] + '/api/proxy/image', params={'version': '2026.10.09', 'arch': 'amd64'}, auth=env['auth'])
    assert wait_image(env, 'amd64').status_code == 200


def test_agent_loads_image_from_hub(env, tmp_path):
    mod = load_agent()
    agent = mod.Agent({'hub': env['hub'], 'local_api': 'http://127.0.0.1:9', **env['enrolled'], 'download_source': 'fleet'})
    mod.machine_arch = lambda: 'arm64'
    seen = {}

    def fake_run(cmd, cwd=None):
        seen['cmd'] = cmd
        seen['data'] = open(cmd[-1], 'rb').read()
        return 0, 'Loaded image'
    agent.run_with_heartbeats = fake_run
    agent.heartbeat = lambda: None
    assert agent.load_image_from_hub('2026.10.09') == (0, 'Loaded image')
    assert seen['cmd'][:3] == ['docker', 'load', '-i'] and not os.path.exists(seen['cmd'][-1])
    assert json.loads(tarfile.open(fileobj=io.BytesIO(seen['data'])).extractfile('manifest.json').read())[0]['RepoTags']
    agent.conf['device_token'] = 'wrong'
    code, out = agent.load_image_from_hub('2026.10.09')
    assert code == 1 and 'HTTP 401' in out
    # the hub reports the download source and the architecture of the node
    d = requests.get(f"{env['hub']}/api/devices/{env['enrolled']['device_id']}", headers=env['h']).json()
    assert 'download_source' in d


SOURCES_LIST = 'deb http://deb.debian.org/debian trixie main contrib\ndeb http://security.debian.org/debian-security trixie-security main\ndeb https://example.org/own trixie main\n'
RASPI_SOURCES = 'Types: deb\nURIs: http://archive.raspberrypi.com/debian/\nSuites: trixie\nComponents: main\n'


def apt_tree(root):
    (root / 'sources.list.d').mkdir(parents=True)
    (root / 'sources.list').write_text(SOURCES_LIST)
    (root / 'sources.list.d' / 'raspi.sources').write_text(RASPI_SOURCES)
    return root


def test_agent_apt_sources(tmp_path, monkeypatch):
    mod = load_agent()
    monkeypatch.setattr(mod, 'APT_DIR', apt_tree(tmp_path / 'apt'))
    changed = mod.apt_use('https://fleet.example/hub', 'CRCL-1', 'tok')
    assert sorted(changed) == ['raspi.sources', 'sources.list']
    text = (tmp_path / 'apt/sources.list').read_text()
    assert 'deb https://fleet.example/hub/apt/deb.debian.org/debian trixie main' in text
    assert 'https://fleet.example/hub/apt/security.debian.org/debian-security' in text and 'https://example.org/own' in text
    assert 'URIs: https://fleet.example/hub/apt/archive.raspberrypi.com/debian/' in (tmp_path / 'apt/sources.list.d/raspi.sources').read_text()
    assert (tmp_path / 'apt/auth.conf.d/caracal-fleet.conf').read_text() == 'machine fleet.example/hub/apt login CRCL-1 password tok\n'
    assert mod.apt_use('https://fleet.example/hub', 'CRCL-1', 'tok') == []            # idempotent
    mod.apt_use('http://10.0.0.5:8090', 'CRCL-1', 'tok')                               # another hub
    assert 'deb http://10.0.0.5:8090/apt/deb.debian.org/debian trixie' in (tmp_path / 'apt/sources.list').read_text()
    assert 'machine http://10.0.0.5:8090/apt' in (tmp_path / 'apt/auth.conf.d/caracal-fleet.conf').read_text()
    mod.apt_use(None)                                                                  # back to the internet
    assert (tmp_path / 'apt/sources.list').read_text() == SOURCES_LIST.replace('http://deb.debian.org', 'https://deb.debian.org').replace('http://security.debian.org', 'https://security.debian.org')
    assert not (tmp_path / 'apt/auth.conf.d/caracal-fleet.conf').exists()


@pytest.mark.skipif(not shutil.which('bash'), reason='bash not available')
def test_installer_apt_sources_match_the_agent(tmp_path):
    """install-node.sh rewrites the sources like the agent (it runs before Python and the agent are installed)."""
    src = (ROOT / 'hub/bootstrap/install-node.sh').read_text(encoding='utf-8')
    block = src[src.index('# --- download through the hub'):src.index('# --- end of download through the hub ---')]
    root = apt_tree(tmp_path / 'apt')
    script = tmp_path / 'snippet.sh'
    script.write_text(block + '\napt_via_fleet https://fleet.example/hub enroll secret-token\n', newline='\n')
    r = subprocess.run([shutil.which('bash'), script.as_posix()], capture_output=True, text=True,
                       env={**os.environ, 'CARACAL_APT_DIR': root.as_posix()})
    assert r.returncode == 0, r.stderr
    mod = load_agent()
    assert (root / 'sources.list').read_text() == mod.apt_rewrite(SOURCES_LIST, 'https://fleet.example/hub')
    assert (root / 'sources.list.d/raspi.sources').read_text() == mod.apt_rewrite(RASPI_SOURCES, 'https://fleet.example/hub')
    assert (root / 'auth.conf.d/caracal-fleet.conf').read_text() == 'machine fleet.example/hub/apt login enroll password secret-token\n'
    # a second run (e.g. a new hub address) does not nest the addresses
    script.write_text(block + '\napt_via_fleet https://new.example enroll t\n', newline='\n')
    subprocess.run([shutil.which('bash'), script.as_posix()], env={**os.environ, 'CARACAL_APT_DIR': root.as_posix()}, check=True)
    assert (root / 'sources.list').read_text() == mod.apt_rewrite(SOURCES_LIST, 'https://new.example')


def test_sd_card_and_installation_options(env):
    import zipfile
    r = requests.post(env['hub'] + '/api/sdcard', headers=env['h'], json={
        'os': 'raspios', 'hub_url': 'https://fleet.example', 'name_prefix': 'tv', 'download_source': 'fleet'})
    assert r.status_code == 200, r.text
    files = zipfile.ZipFile(io.BytesIO(r.content))
    assert 'DOWNLOAD_SOURCE=fleet' in files.read('caracal-firstboot.conf').decode()
    assert 'through CARACAL Fleet' in files.read('CARACAL-README.txt').decode()
    plain = zipfile.ZipFile(io.BytesIO(requests.post(env['hub'] + '/api/sdcard', headers=env['h'], json={
        'os': 'raspios', 'hub_url': 'https://fleet.example', 'name_prefix': 'tv'}).content))
    assert 'DOWNLOAD_SOURCE' not in plain.read('caracal-firstboot.conf').decode()
    firstboot = (ROOT / 'hub/bootstrap/caracal-firstboot.sh').read_text(encoding='utf-8')
    assert '[ "${DOWNLOAD_SOURCE:-}" = fleet ] && extra+=(--via-fleet)' in firstboot
    # switching an enrolled node
    url = f"{env['hub']}/api/devices/{env['enrolled']['device_id']}/commands"
    assert requests.post(url, headers=env['h'], json={'action': 'set_download_source', 'payload': {'source': 'cloud'}}).json()['detail'] == 'invalid_value'
    assert requests.post(url, headers=env['h'], json={'action': 'set_download_source', 'payload': {'source': 'fleet'}}).status_code == 200
