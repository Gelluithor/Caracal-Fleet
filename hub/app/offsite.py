"""Backups outside the server: every backup the hub stores (daily or "Back up now") is copied to the chosen
destinations, checked there and the oldest ones are removed, so a dead disk or a lost server does not take the
backups with it.

Destinations: S3-compatible object storage (Amazon S3, Backblaze B2, Cloudflare R2, Wasabi, MinIO...), WebDAV
(Nextcloud, ownCloud, a NAS), SFTP, SMB (Windows share, NAS), NFS (without mounting, see nfs3.py), any cloud of
rclone (Google Drive, OneDrive, Dropbox...) and a folder mounted into the container (USB disk, a share mounted by
Docker). Backups can be encrypted with a passphrase (AES-256-GCM, key from scrypt); without it they cannot be
restored, so it belongs in a password manager too.

A destination can also be the source of a restore: its backups are listed and one is downloaded, decrypted,
checked and restored like an uploaded file (on a new server too).
"""
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import shutil
import struct
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import requests
from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from .core import DATA, REDACTED, audit, cfg, current_user, save_cfg

router = APIRouter()
TYPES = ('s3', 'webdav', 'sftp', 'smb', 'nfs', 'rclone', 'folder')
SECRETS = {'s3': ('secret_key',), 'webdav': ('password',), 'sftp': ('password', 'private_key', 'key_passphrase'),
           'smb': ('password',), 'nfs': (), 'rclone': ('config',), 'folder': ()}
REMOTE_FILE = re.compile(r'^caracal-fleet-backup-\d{8}-\d{4}\.zip(\.enc)?$')
STATUS_FILE = DATA / 'backup-targets-status.json'
TARGETS_MAX = 10
_lock = threading.Lock()
_running = {}


class TargetError(Exception):
    pass


# ---------------------------------------------------------------- encryption

MAGIC = b'CFBE1'
ENC_CHUNK = 1024 * 1024
SCRYPT_N = 2 ** 15


def _key(passphrase, salt, n=SCRYPT_N):
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    return Scrypt(salt=salt, length=32, n=n, r=8, p=1).derive(passphrase.encode())


def encrypt_file(src, dst, passphrase):
    """MAGIC, salt, log2(n), base nonce; then chunks of 4-byte length + AES-GCM ciphertext. The last chunk is marked
    in its associated data, so a cut file is noticed."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, base = secrets.token_bytes(16), secrets.token_bytes(8)
    header = MAGIC + salt + bytes([SCRYPT_N.bit_length() - 1]) + base
    aes = AESGCM(_key(passphrase, salt))
    with open(src, 'rb') as f, open(dst, 'wb') as out:
        out.write(header)
        counter, data = 0, f.read(ENC_CHUNK)
        while True:
            nxt = f.read(ENC_CHUNK)
            last = not nxt
            ct = aes.encrypt(base + struct.pack('>I', counter), data, header + (b'\1' if last else b'\0'))
            out.write(struct.pack('>I', len(ct)) + ct)
            if last:
                return
            counter, data = counter + 1, nxt


def is_encrypted(path):
    with open(path, 'rb') as f:
        return f.read(len(MAGIC)) == MAGIC


def decrypt_file(src, dst, passphrase):
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    with open(src, 'rb') as f, open(dst, 'wb') as out:
        header = f.read(len(MAGIC) + 16 + 1 + 8)
        if len(header) < 30 or header[:len(MAGIC)] != MAGIC:
            raise TargetError('not_encrypted')
        salt, n, base = header[5:21], 2 ** header[21], header[22:30]
        if n > 2 ** 20:
            raise TargetError('invalid_backup')
        aes = AESGCM(_key(passphrase, salt, n))
        counter = 0
        while True:
            size = f.read(4)
            if len(size) < 4:
                raise TargetError('backup_truncated')
            ct = f.read(struct.unpack('>I', size)[0])
            last = f.peek(1)[:1] == b''
            try:
                data = aes.decrypt(base + struct.pack('>I', counter), ct, header + (b'\1' if last else b'\0'))
            except InvalidTag:
                if counter == 0:
                    raise TargetError('wrong_passphrase')
                raise TargetError('backup_damaged')
            out.write(data)
            if last:
                return
            counter += 1


# ---------------------------------------------------------------- destinations

def _clean_path(value):
    parts = [p for p in str(value or '').replace('\\', '/').split('/') if p and p != '.']
    if any(p == '..' for p in parts):
        raise HTTPException(400, 'invalid_path')
    return '/'.join(parts)


class S3:
    """S3 API with signature version 4 (path style, or virtual hosted for Amazon)."""
    EMPTY = hashlib.sha256(b'').hexdigest()

    def __init__(self, t):
        self.t = t
        self.prefix = _clean_path(t.get('prefix'))

    def _request(self, method, key='', query=None, data=None, payload=EMPTY, stream=False, timeout=60):
        t = self.t
        parts = urlsplit(t['endpoint'].rstrip('/'))
        host = parts.netloc
        if t.get('style') == 'virtual':
            host, path = f"{t['bucket']}.{host}", '/' + quote(key, safe='/-_.~')
        else:
            path = f"/{quote(t['bucket'], safe='')}" + ('/' + quote(key, safe='/-_.~') if key else '')
        q = '&'.join(f"{quote(k, safe='-_.~')}={quote(str(v), safe='-_.~')}" for k, v in sorted((query or {}).items()))
        now = datetime.now(timezone.utc)
        amz, day = now.strftime('%Y%m%dT%H%M%SZ'), now.strftime('%Y%m%d')
        region = t.get('region') or 'us-east-1'
        headers = {'host': host, 'x-amz-content-sha256': payload, 'x-amz-date': amz}
        signed = ';'.join(sorted(headers))
        canonical = '\n'.join([method, path, q, ''.join(f'{k}:{headers[k]}\n' for k in sorted(headers)), signed, payload])
        scope = f'{day}/{region}/s3/aws4_request'
        to_sign = '\n'.join(['AWS4-HMAC-SHA256', amz, scope, hashlib.sha256(canonical.encode()).hexdigest()])
        k = ('AWS4' + t['secret_key']).encode()
        for part in (day, region, 's3', 'aws4_request'):
            k = hmac.new(k, part.encode(), hashlib.sha256).digest()
        signature = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
        headers['Authorization'] = f"AWS4-HMAC-SHA256 Credential={t['access_key']}/{scope}, SignedHeaders={signed}, Signature={signature}"
        del headers['host']
        url = f'{parts.scheme}://{host}{path}' + (f'?{q}' if q else '')
        try:
            r = requests.request(method, url, headers=headers, data=data, stream=stream, timeout=timeout,
                                 verify=t.get('verify_tls', True))
        except requests.RequestException as e:
            raise TargetError(f'{parts.netloc}: {e}'[:300])
        if r.status_code >= 300:
            code = re.search(r'<Code>([^<]+)</Code>', r.text or '')
            msg = re.search(r'<Message>([^<]+)</Message>', r.text or '')
            raise TargetError(f"S3 {r.status_code} {code.group(1) if code else ''} {msg.group(1) if msg else ''}".strip())
        return r

    def _key(self, name):
        return f'{self.prefix}/{name}' if self.prefix else name

    def put(self, path, name):
        h = hashlib.sha256()
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b''):
                h.update(chunk)
        with open(path, 'rb') as f:
            self._request('PUT', self._key(name), data=f, payload=h.hexdigest(), timeout=3600)

    def list(self):
        out, token = [], None
        while True:
            q = {'list-type': '2', 'prefix': f'{self.prefix}/' if self.prefix else ''}
            if token:
                q['continuation-token'] = token
            text = self._request('GET', query=q).text
            for block in re.findall(r'<Contents>(.*?)</Contents>', text, re.S):
                key = re.search(r'<Key>([^<]*)</Key>', block).group(1)
                name = key.rsplit('/', 1)[-1]
                size = int(re.search(r'<Size>(\d+)</Size>', block).group(1))
                modified = re.search(r'<LastModified>([^<]+)</LastModified>', block)
                ts = datetime.fromisoformat(modified.group(1).replace('Z', '+00:00')).timestamp() if modified else 0
                if key == self._key(name):
                    out.append({'name': name, 'size': size, 'mtime': ts})
            token = re.search(r'<NextContinuationToken>([^<]+)</NextContinuationToken>', text)
            if not token:
                return out
            token = token.group(1)

    def delete(self, name):
        self._request('DELETE', self._key(name))

    def get(self, name, out):
        r = self._request('GET', self._key(name), stream=True, timeout=3600)
        for chunk in r.iter_content(1024 * 1024):
            out.write(chunk)


class WebDav:
    def __init__(self, t):
        self.t = t
        self.base = t['url'].rstrip('/') + '/'
        self.auth = (t.get('username') or '', t.get('password') or '') if t.get('username') else None

    def _request(self, method, name='', **kw):
        try:
            r = requests.request(method, self.base + quote(name), auth=self.auth, verify=self.t.get('verify_tls', True),
                                 timeout=kw.pop('timeout', 60), **kw)
        except requests.RequestException as e:
            raise TargetError(f'{urlsplit(self.base).netloc}: {e}'[:300])
        return r

    def _check(self, r, what):
        if r.status_code >= 300:
            raise TargetError(f'WebDAV {what}: HTTP {r.status_code} {r.reason}'[:300])
        return r

    def put(self, path, name):
        r = self._request('PROPFIND', headers={'Depth': '0'})
        if r.status_code == 404:
            self._check(self._request('MKCOL'), 'creating the folder')
        elif r.status_code in (401, 403):
            self._check(r, 'sign-in')
        with open(path, 'rb') as f:
            self._check(self._request('PUT', name, data=f, timeout=3600), 'upload')

    def list(self):
        r = self._request('PROPFIND', headers={'Depth': '1', 'Content-Type': 'application/xml'},
                          data='<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:getcontentlength/>'
                               '<d:getlastmodified/><d:resourcetype/></d:prop></d:propfind>')
        if r.status_code == 404:
            return []
        self._check(r, 'listing')
        import xml.etree.ElementTree as ET
        out = []
        for resp in ET.fromstring(r.content).findall('{DAV:}response'):
            if resp.find('.//{DAV:}collection') is not None:
                continue
            name = unquote((resp.findtext('{DAV:}href') or '').rstrip('/').rsplit('/', 1)[-1])
            size = resp.findtext('.//{DAV:}getcontentlength') or '0'
            modified = resp.findtext('.//{DAV:}getlastmodified')
            try:
                ts = parsedate_to_datetime(modified).timestamp() if modified else 0
            except (TypeError, ValueError):
                ts = 0
            out.append({'name': name, 'size': int(size) if size.isdigit() else 0, 'mtime': ts})
        return out

    def delete(self, name):
        self._check(self._request('DELETE', name), 'delete')

    def get(self, name, out):
        r = self._check(self._request('GET', name, stream=True, timeout=3600), 'download')
        for chunk in r.iter_content(1024 * 1024):
            out.write(chunk)


class Sftp:
    """SFTP with a password or a private key. The server's host key is remembered at the first connection and
    checked afterwards."""

    def __init__(self, t):
        self.t = t
        self.path = '/' + _clean_path(t.get('path')) if str(t.get('path') or '').startswith('/') else _clean_path(t.get('path')) or '.'

    def _connect(self):
        import paramiko
        t = self.t
        try:
            transport = paramiko.Transport((t['host'], int(t.get('port') or 22)))
            transport.start_client(timeout=20)
        except (OSError, paramiko.SSHException) as e:
            raise TargetError(f"{t['host']}: {e}"[:300])
        key = transport.get_remote_server_key()
        seen = f'{key.get_name()} {key.get_base64()}'
        if t.get('host_key') and t['host_key'] != seen:
            transport.close()
            raise TargetError('host_key_changed')
        self.seen_key = seen
        try:
            if t.get('private_key'):
                pkey = _private_key(t['private_key'], t.get('key_passphrase') or None)
                transport.auth_publickey(t['username'], pkey)
            else:
                transport.auth_password(t['username'], t.get('password') or '')
        except paramiko.SSHException as e:
            transport.close()
            raise TargetError(f'SFTP sign-in failed: {e}'[:300])
        return transport, paramiko.SFTPClient.from_transport(transport)

    def _run(self, fn):
        transport, sftp = self._connect()
        try:
            return fn(sftp)
        except OSError as e:
            raise TargetError(f'SFTP: {e}'[:300])
        finally:
            sftp.close()
            transport.close()

    def _mkdirs(self, sftp):
        current = '/' if self.path.startswith('/') else ''
        for part in [p for p in self.path.split('/') if p and p != '.']:
            current = f'{current}{part}/'
            try:
                sftp.stat(current)
            except FileNotFoundError:
                sftp.mkdir(current)

    def put(self, path, name):
        def go(sftp):
            self._mkdirs(sftp)
            tmp, final = f'{self.path}/{name}.part', f'{self.path}/{name}'
            with open(path, 'rb') as f:
                sftp.putfo(f, tmp, confirm=True)
            try:
                sftp.posix_rename(tmp, final)
            except OSError:
                try:
                    sftp.remove(final)
                except OSError:
                    pass
                sftp.rename(tmp, final)
        self._run(go)

    def list(self):
        def go(sftp):
            try:
                return [{'name': a.filename, 'size': a.st_size or 0, 'mtime': a.st_mtime or 0} for a in sftp.listdir_attr(self.path)]
            except FileNotFoundError:
                return []
        return self._run(go)

    def delete(self, name):
        self._run(lambda sftp: sftp.remove(f'{self.path}/{name}'))

    def get(self, name, out):
        self._run(lambda sftp: sftp.getfo(f'{self.path}/{name}', out))


def _private_key(text, passphrase=None):
    import paramiko
    for cls in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
        try:
            return cls.from_private_key(io.StringIO(text.strip() + '\n'), password=passphrase)
        except paramiko.PasswordRequiredException:
            raise TargetError('key_passphrase_required')
        except (paramiko.SSHException, ValueError):
            continue
    raise TargetError('invalid_private_key')


class Smb:
    """SMB 2/3 (Windows shares, NAS) with smbprotocol."""

    def __init__(self, t):
        self.t = t
        self.dir = '\\\\' + t['server'] + '\\' + t['share'] + ''.join('\\' + p for p in _clean_path(t.get('path')).split('/') if p)

    def _kw(self):
        user = self.t.get('username') or ''
        if self.t.get('domain') and user:
            user = f"{self.t['domain']}\\{user}"
        return {'username': user or None, 'password': self.t.get('password') or None,
                'port': int(self.t.get('port') or 445), 'connection_timeout': 20}

    def _run(self, fn):
        import smbclient
        from smbprotocol.exceptions import SMBException
        try:
            return fn(smbclient, self._kw())
        except (SMBException, OSError, ValueError) as e:
            raise TargetError(f'SMB: {e}'[:300])
        finally:
            try:
                smbclient.reset_connection_cache()
            except Exception:   # noqa: BLE001 - closing only
                pass

    def put(self, path, name):
        def go(smb, kw):
            smb.makedirs(self.dir, exist_ok=True, **kw)
            tmp, final = f'{self.dir}\\{name}.part', f'{self.dir}\\{name}'
            with open(path, 'rb') as f, smb.open_file(tmp, mode='wb', **kw) as out:
                shutil.copyfileobj(f, out, 1024 * 1024)
            smb.replace(tmp, final, **kw)
        self._run(go)

    def list(self):
        def go(smb, kw):
            try:
                entries = list(smb.scandir(self.dir, **kw))
            except FileNotFoundError:
                return []
            return [{'name': e.name, 'size': e.stat().st_size, 'mtime': e.stat().st_mtime} for e in entries if e.is_file()]
        return self._run(go)

    def delete(self, name):
        self._run(lambda smb, kw: smb.remove(f'{self.dir}\\{name}', **kw))

    def get(self, name, out):
        def go(smb, kw):
            with smb.open_file(f'{self.dir}\\{name}', mode='rb', **kw) as f:
                shutil.copyfileobj(f, out, 1024 * 1024)
        self._run(go)


class Nfs:
    def __init__(self, t):
        self.t = t
        self.path = _clean_path(t.get('path'))

    def _run(self, fn):
        from . import nfs3
        try:
            with nfs3.Client(self.t['server'], self.t['export']) as c:
                return fn(c)
        except (nfs3.NfsError, OSError) as e:
            raise TargetError(f'NFS: {e}'[:300])

    def put(self, path, name):
        # written under its final name (the small client has no RENAME); a cut copy is found by the size check
        def go(c):
            with open(path, 'rb') as f:
                c.put(self.path, name, f)
        self._run(go)

    def list(self):
        return self._run(lambda c: c.list(self.path))

    def delete(self, name):
        self._run(lambda c: c.remove(self.path, name))

    def get(self, name, out):
        self._run(lambda c: c.get(self.path, name, out))


class Rclone:
    """Any rclone remote (Google Drive, OneDrive, Dropbox, pCloud, Mega...): the configuration made with
    "rclone config" on a computer is pasted into Fleet. rclone may refresh the access token; the new one is kept."""

    def __init__(self, t):
        self.t = t
        m = re.search(r'^\s*\[([^\]]+)\]', t.get('config') or '', re.M)
        if not m:
            raise TargetError('invalid_rclone_config')
        self.remote = m.group(1).strip()
        self.path = _clean_path(t.get('path'))

    def _target(self, name=''):
        return f'{self.remote}:{self.path}' + (f'/{name}' if name else '')

    def _run(self, *args, timeout=600):
        if not shutil.which('rclone'):
            raise TargetError('rclone_missing')
        fd, conf = tempfile.mkstemp(prefix='rclone-', suffix='.conf', dir=DATA)
        try:
            with os.fdopen(fd, 'w') as f:
                f.write(self.t['config'])
            r = subprocess.run(['rclone', '--config', conf, '--retries', '2', '--low-level-retries', '5', *args],
                               capture_output=True, text=True, timeout=timeout)
            updated = Path(conf).read_text()
            if updated.strip() and updated != self.t['config']:
                self.t['config'] = updated
                _store_target_field(self.t['id'], 'config', updated)
            if r.returncode:
                lines = [x for x in (r.stderr or r.stdout).splitlines() if 'ERROR' in x or 'Failed' in x] or (r.stderr or '').splitlines()
                raise TargetError(('rclone: ' + (lines[-1] if lines else f'exit {r.returncode}'))[:300])
            return r.stdout
        except subprocess.TimeoutExpired:
            raise TargetError('rclone: timeout')
        finally:
            Path(conf).unlink(missing_ok=True)

    def put(self, path, name):
        self._run('copyto', str(path), self._target(name), timeout=6 * 3600)

    def list(self):
        try:
            items = json.loads(self._run('lsjson', '--files-only', self._target()) or '[]')
        except TargetError as e:
            if 'not found' in str(e).lower():
                return []
            raise
        out = []
        for x in items:
            try:
                ts = datetime.fromisoformat(str(x.get('ModTime', '')).replace('Z', '+00:00')[:32]).timestamp()
            except ValueError:
                ts = 0
            out.append({'name': x.get('Name', ''), 'size': int(x.get('Size') or 0), 'mtime': ts})
        return out

    def delete(self, name):
        self._run('deletefile', self._target(name))

    def get(self, name, out):
        with tempfile.TemporaryDirectory(dir=DATA) as tmp:
            self._run('copyto', self._target(name), f'{tmp}/{name}', timeout=6 * 3600)
            with open(f'{tmp}/{name}', 'rb') as f:
                shutil.copyfileobj(f, out, 1024 * 1024)


class Folder:
    """A folder in the container: a USB disk or a network share mounted by Docker (volumes with type nfs or cifs)."""

    def __init__(self, t):
        self.dir = Path('/' + _clean_path(t.get('path')))

    def _check(self):
        if not self.dir.is_dir():
            raise TargetError('folder_missing')

    def put(self, path, name):
        self._check()
        tmp = self.dir / (name + '.part')
        try:
            shutil.copyfile(path, tmp)
            os.replace(tmp, self.dir / name)
        except OSError as e:
            tmp.unlink(missing_ok=True)
            raise TargetError(str(e)[:300])

    def list(self):
        self._check()
        return [{'name': p.name, 'size': p.stat().st_size, 'mtime': p.stat().st_mtime} for p in self.dir.iterdir() if p.is_file()]

    def delete(self, name):
        (self.dir / name).unlink(missing_ok=True)

    def get(self, name, out):
        with open(self.dir / name, 'rb') as f:
            shutil.copyfileobj(f, out, 1024 * 1024)


BACKENDS = {'s3': S3, 'webdav': WebDav, 'sftp': Sftp, 'smb': Smb, 'nfs': Nfs, 'rclone': Rclone, 'folder': Folder}


def backend(t):
    return BACKENDS[t['type']](t)


# ---------------------------------------------------------------- configuration

def settings():
    s = cfg().get('backup_offsite') or {}
    return {'targets': [t for t in s.get('targets') or [] if isinstance(t, dict) and t.get('type') in TYPES],
            'passphrase': s.get('passphrase') or ''}


def _save(s):
    c = cfg()
    c['backup_offsite'] = s
    save_cfg(c)


def _store_target_field(tid, key, value):
    with _lock:
        s = settings()
        for t in s['targets']:
            if t['id'] == tid:
                t[key] = value
        _save(s)


def load_status():
    try:
        return json.loads(STATUS_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _write_status(tid, status):
    with _lock:
        data = load_status()
        data[tid] = {**data.get(tid, {}), **status}
        tmp = STATUS_FILE.with_suffix('.tmp')
        tmp.write_text(json.dumps(data))
        tmp.replace(STATUS_FILE)


def public_target(t):
    out = {k: (REDACTED if k in SECRETS[t['type']] and v else v) for k, v in t.items()}
    if t['type'] == 's3':
        out['access_key'] = t.get('access_key', '')
    return out


def _text(v, limit=300):
    return str(v if v is not None else '').strip()[:limit]


def _url(v):
    v = _text(v, 500)
    if urlsplit(v).scheme not in ('http', 'https') or not urlsplit(v).netloc:
        raise HTTPException(400, 'invalid_url')
    return v


def _host(v):
    v = _text(v, 255)
    if not re.fullmatch(r'[A-Za-z0-9.\-:\[\]_]{1,255}', v):
        raise HTTPException(400, 'invalid_host')
    return v


def _port(v, default):
    try:
        p = int(v or default)
    except (TypeError, ValueError):
        raise HTTPException(400, 'invalid_value')
    if not 1 <= p <= 65535:
        raise HTTPException(400, 'invalid_value')
    return p


def validate(d, old=None):
    """A destination from the form; secrets left as REDACTED keep the stored ones."""
    if not isinstance(d, dict) or d.get('type') not in TYPES:
        raise HTTPException(400, 'invalid_target')
    kind = d['type']
    old = old if old and old.get('type') == kind else {}
    t = {'id': old.get('id') or secrets.token_hex(6), 'type': kind, 'name': _text(d.get('name'), 80),
         'enabled': d.get('enabled', True) is not False}
    try:
        t['keep'] = max(1, min(int(d.get('keep') or 14), 365))
    except (TypeError, ValueError):
        raise HTTPException(400, 'invalid_value')
    for key in SECRETS[kind]:
        value = str(d.get(key) or '')
        t[key] = old.get(key, '') if value == REDACTED else (value if key in ('config', 'private_key') else value.strip())[:20000]
    if kind == 's3':
        t.update(endpoint=_url(d.get('endpoint')), region=_text(d.get('region'), 40) or 'us-east-1',
                 bucket=_text(d.get('bucket'), 63), prefix=_clean_path(d.get('prefix')), access_key=_text(d.get('access_key'), 200),
                 style='virtual' if d.get('style') == 'virtual' else 'path', verify_tls=d.get('verify_tls', True) is not False)
        if not re.fullmatch(r'[a-z0-9][a-z0-9.\-]{1,61}[a-z0-9]', t['bucket']):
            raise HTTPException(400, 'invalid_bucket')
        if not re.fullmatch(r'[a-z0-9\-]{1,40}', t['region']):
            raise HTTPException(400, 'invalid_region')
        if not t['access_key'] or not t['secret_key']:
            raise HTTPException(400, 'credentials_required')
    elif kind == 'webdav':
        t.update(url=_url(d.get('url')), username=_text(d.get('username'), 200), verify_tls=d.get('verify_tls', True) is not False)
    elif kind == 'sftp':
        t.update(host=_host(d.get('host')), port=_port(d.get('port'), 22), username=_text(d.get('username'), 200),
                 path=('/' if str(d.get('path') or '').strip().startswith('/') else '') + _clean_path(d.get('path')),
                 host_key=old.get('host_key', '') if old.get('host') == _text(d.get('host'), 255) else '')
        if not t['username'] or not (t['password'] or t['private_key']):
            raise HTTPException(400, 'credentials_required')
        if t['private_key']:
            try:
                _private_key(t['private_key'], t['key_passphrase'] or None)
            except TargetError as e:
                raise HTTPException(400, str(e))
    elif kind == 'smb':
        t.update(server=_host(d.get('server')), share=_text(d.get('share'), 80).strip('\\/'), path=_clean_path(d.get('path')),
                 username=_text(d.get('username'), 200), domain=_text(d.get('domain'), 100), port=_port(d.get('port'), 445))
        if not re.fullmatch(r'[^\\/:*?"<>|]{1,80}', t['share']):
            raise HTTPException(400, 'invalid_share')
    elif kind == 'nfs':
        export = '/' + _clean_path(d.get('export'))
        t.update(server=_host(d.get('server')), export=export, path=_clean_path(d.get('path')))
        if export == '/':
            raise HTTPException(400, 'invalid_export')
    elif kind == 'rclone':
        t['path'] = _clean_path(d.get('path'))
        if not re.search(r'^\s*\[[^\]]+\]\s*$', t['config'], re.M) or not re.search(r'^\s*type\s*=', t['config'], re.M):
            raise HTTPException(400, 'invalid_rclone_config')
    elif kind == 'folder':
        t['path'] = '/' + _clean_path(d.get('path'))
        if t['path'] == '/' or t['path'].startswith(('/proc', '/sys', '/dev', '/etc', '/opt/caracal-hub', str(DATA))):
            raise HTTPException(400, 'invalid_path')
    if not t['name']:
        t['name'] = {'s3': t.get('bucket'), 'webdav': urlsplit(t.get('url', '')).netloc, 'sftp': t.get('host'),
                     'smb': t.get('server'), 'nfs': t.get('server'), 'rclone': 'rclone', 'folder': t.get('path')}[kind] or kind
    return t


# ---------------------------------------------------------------- uploading

def _alert(t, error):
    try:
        from . import monitor
        monitor.backup_failed(t['name'], error)
    except Exception as e:   # noqa: BLE001 - an alert must not stop the backups
        print('backup alert failed:', e, flush=True)


def upload_one(t, path, name):
    """Upload, check that the copy is complete and remove the oldest ones; returns the status."""
    started = time.time()
    b = backend(t)
    b.put(path, name)
    size = os.path.getsize(path)
    files = [f for f in b.list() if REMOTE_FILE.fullmatch(f['name'])]
    copy = next((f for f in files if f['name'] == name), None)
    if not copy:
        raise TargetError('upload_not_found')
    if copy['size'] != size:
        raise TargetError(f'upload_incomplete: {copy["size"]} / {size}')
    removed = []
    for old in sorted(files, key=lambda f: f['name'])[:-t['keep']]:
        try:
            b.delete(old['name'])
            removed.append(old['name'])
        except TargetError:
            pass
    if getattr(b, 'seen_key', '') and not t.get('host_key'):
        _store_target_field(t['id'], 'host_key', b.seen_key)   # SFTP: the host key of the first connection
    return {'ok': True, 'error': '', 'ts': time.time(), 'name': name, 'size': size, 'seconds': round(time.time() - started, 1),
            'count': len(files) - len(removed), 'alerted': False}


def upload(path, only=None):
    """Copy a stored backup to the destinations (all enabled ones, or the one with the id `only`)."""
    s = settings()
    targets = [t for t in s['targets'] if (t['id'] == only if only else t.get('enabled', True))]
    if not targets:
        return {}
    work, name = Path(path), Path(path).name
    tmp = None
    if s['passphrase']:
        tmp = Path(tempfile.mkstemp(prefix='offsite-', suffix='.enc', dir=DATA)[1])
        encrypt_file(path, tmp, s['passphrase'])
        work, name = tmp, name + '.enc'
    results = {}
    try:
        for t in targets:
            _running[t['id']] = time.time()
            try:
                status = upload_one(t, work, name)
            except (TargetError, OSError) as e:
                prev = load_status().get(t['id'], {})
                status = {'ok': False, 'error': str(e)[:300], 'ts': time.time(), 'alerted': prev.get('alerted', False)}
                if not status['alerted']:
                    _alert(t, status['error'])
                    status['alerted'] = True
            finally:
                _running.pop(t['id'], None)
            _write_status(t['id'], status)
            results[t['id']] = status
            audit(None, 'system.backup_offsite', t['name'], {'ok': status['ok'], 'error': status.get('error', ''), 'file': name})
    finally:
        if tmp:
            tmp.unlink(missing_ok=True)
    return results


def upload_async(path, only=None):
    threading.Thread(target=_upload_guarded, args=(path, only), daemon=True, name='backup-offsite').start()


_upload_lock = threading.Lock()


def _upload_guarded(path, only):
    with _upload_lock:
        try:
            upload(path, only)
        except Exception as e:   # noqa: BLE001 - the thread must not die silently
            print('off-site backup failed:', e, flush=True)


def test(t):
    """Write a small file, find it, read it back and remove it."""
    b = backend(t)
    name = f'caracal-fleet-test-{secrets.token_hex(4)}.txt'
    data = f'CARACAL Fleet test {time.time()}\n'.encode()
    with tempfile.NamedTemporaryFile(dir=DATA, delete=False) as f:
        f.write(data)
        tmp = f.name
    try:
        b.put(tmp, name)
        if not any(x['name'] == name for x in b.list()):
            raise TargetError('upload_not_found')
        out = io.BytesIO()
        b.get(name, out)
        if out.getvalue() != data:
            raise TargetError('download_mismatch')
        b.delete(name)
    finally:
        os.unlink(tmp)
    return getattr(b, 'seen_key', '')


# ---------------------------------------------------------------- API

async def _json(r):
    try:
        d = await r.json()
    except ValueError:
        raise HTTPException(400, 'invalid_json')
    if not isinstance(d, dict):
        raise HTTPException(400, 'invalid_json')
    return d


def _find(tid):
    t = next((x for x in settings()['targets'] if x['id'] == tid), None)
    if not t:
        raise HTTPException(404, 'not_found')
    return t


@router.get('/api/backup-targets')
def list_targets(r: Request):
    current_user(r, 'settings')
    s, status = settings(), load_status()
    return {'targets': [{**public_target(t), 'status': status.get(t['id']), 'running': t['id'] in _running} for t in s['targets']],
            'encrypted': bool(s['passphrase']), 'rclone': bool(shutil.which('rclone'))}


@router.put('/api/backup-targets/encryption')
async def set_encryption(r: Request):
    u = current_user(r, 'settings')
    d = await _json(r)
    passphrase = str(d.get('passphrase') or '')
    if passphrase and len(passphrase) < 12:
        raise HTTPException(400, 'passphrase_too_short')
    with _lock:
        s = settings()
        s['passphrase'] = passphrase
        _save(s)
    audit(u, 'system.backup_encryption', '', {'enabled': bool(passphrase)})
    return {'encrypted': bool(passphrase)}


@router.post('/api/backup-targets')
async def create_target(r: Request):
    u = current_user(r, 'settings')
    t = validate(await _json(r))
    with _lock:
        s = settings()
        if len(s['targets']) >= TARGETS_MAX:
            raise HTTPException(400, 'too_many_targets')
        s['targets'].append(t)
        _save(s)
    audit(u, 'system.backup_target_create', t['name'], {'type': t['type']})
    return public_target(t)


@router.patch('/api/backup-targets/{tid}')
async def edit_target(tid: str, r: Request):
    u = current_user(r, 'settings')
    d = await _json(r)
    with _lock:
        s = settings()
        old = next((x for x in s['targets'] if x['id'] == tid), None)
        if not old:
            raise HTTPException(404, 'not_found')
        t = validate({**d, 'type': old['type']}, old)
        s['targets'] = [t if x['id'] == tid else x for x in s['targets']]
        _save(s)
    audit(u, 'system.backup_target_edit', t['name'], {'type': t['type'], 'enabled': t['enabled']})
    return public_target(t)


@router.delete('/api/backup-targets/{tid}')
def delete_target(tid: str, r: Request):
    u = current_user(r, 'settings')
    with _lock:
        s = settings()
        t = next((x for x in s['targets'] if x['id'] == tid), None)
        if not t:
            raise HTTPException(404, 'not_found')
        s['targets'] = [x for x in s['targets'] if x['id'] != tid]
        _save(s)
    audit(u, 'system.backup_target_delete', t['name'])
    return {'ok': True}


@router.post('/api/backup-targets/test')
async def test_target(r: Request):
    """Tests the destination as it is in the form (secrets left unchanged come from the stored one)."""
    current_user(r, 'settings')
    d = await _json(r)
    old = next((x for x in settings()['targets'] if x['id'] == _text(d.get('id'), 40)), None)
    t = validate(d, old)
    try:
        seen = await run_in_threadpool(test, t)
    except TargetError as e:
        return {'ok': False, 'error': str(e)}
    if seen and old and not old.get('host_key'):
        _store_target_field(old['id'], 'host_key', seen)
    return {'ok': True, 'host_key': seen}


@router.post('/api/backup-targets/{tid}/upload')
async def upload_now(tid: str, r: Request):
    """Copies the newest stored backup there now (a new one is made when there is none)."""
    from .system import BACKUP_FILE, BACKUPS, make_scheduled_backup
    u = current_user(r, 'settings')
    t = _find(tid)
    if tid in _running:
        raise HTTPException(409, 'upload_running')
    stored = sorted(p for p in BACKUPS.iterdir() if BACKUP_FILE.fullmatch(p.name)) if BACKUPS.is_dir() else []
    path = stored[-1] if stored else await run_in_threadpool(make_scheduled_backup)
    _running[tid] = time.time()
    upload_async(path, tid)
    audit(u, 'system.backup_offsite_now', t['name'], {'file': path.name})
    return {'ok': True, 'file': path.name}


@router.get('/api/backup-targets/{tid}/files')
async def remote_files(tid: str, r: Request):
    current_user(r, 'settings')
    t = _find(tid)
    try:
        files = await run_in_threadpool(lambda: backend(t).list())
    except TargetError as e:
        raise HTTPException(502, str(e))
    files = [f for f in files if REMOTE_FILE.fullmatch(f['name'])]
    return {'files': sorted(files, key=lambda f: f['name'], reverse=True)}


@router.post('/api/backup-targets/{tid}/restore')
async def restore_from_target(tid: str, r: Request):
    """Downloads a backup from the destination and restores it like an uploaded one (the hub restarts)."""
    from .system import restore_file
    u = current_user(r, 'settings')
    t = _find(tid)
    d = await _json(r)
    name = _text(d.get('name'), 200)
    if not REMOTE_FILE.fullmatch(name):
        raise HTTPException(400, 'invalid_backup')
    fd, tmp = tempfile.mkstemp(prefix='remote-', suffix='.bin', dir=DATA)
    try:
        with os.fdopen(fd, 'wb') as out:
            try:
                await run_in_threadpool(backend(t).get, name, out)
            except TargetError as e:
                raise HTTPException(502, str(e))
        return await restore_file(u, Path(tmp), str(d.get('passphrase') or ''), source=f"{t['name']}: {name}")
    finally:
        Path(tmp).unlink(missing_ok=True)
