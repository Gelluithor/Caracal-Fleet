"""A small NFSv3 client (ONC RPC over TCP) for backups on an NFS share without mounting it.

A container cannot mount NFS shares (that needs CAP_SYS_ADMIN), so the hub speaks the protocol itself: the portmapper
finds MOUNT and NFS, MOUNT gives the handle of the export, and NFS creates, writes, reads, lists and removes files.
Only what backups need is here. Many servers accept requests only from a privileged port (the "secure" export
option, the default on Linux); the hub tries one when it runs as root.
"""
import os
import random
import socket
import struct

PORTMAP, MOUNT, NFS = 100000, 100005, 100003
CHUNK = 64 * 1024
ERRORS = {1: 'permission denied (NFS3ERR_PERM)', 2: 'no such file or directory', 5: 'I/O error', 13: 'access denied',
          17: 'already exists', 20: 'not a directory', 21: 'is a directory', 22: 'invalid argument', 27: 'file too large',
          28: 'no space left on the share', 30: 'the share is read-only', 63: 'name too long', 69: 'quota exceeded',
          70: 'stale file handle', 10001: 'bad handle', 10008: 'the server is busy, try again'}


class NfsError(Exception):
    pass


class _Packer:
    def __init__(self):
        self.parts = []

    def u32(self, v):
        self.parts.append(struct.pack('>I', v & 0xFFFFFFFF))
        return self

    def u64(self, v):
        self.parts.append(struct.pack('>Q', v))
        return self

    def opaque(self, data):
        data = data.encode() if isinstance(data, str) else data
        self.u32(len(data))
        self.parts.append(data + b'\0' * (-len(data) % 4))
        return self

    def raw(self, data):
        self.parts.append(data)
        return self

    def bytes(self):
        return b''.join(self.parts)


class _Unpacker:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def u32(self):
        v = struct.unpack_from('>I', self.data, self.pos)[0]
        self.pos += 4
        return v

    def u64(self):
        v = struct.unpack_from('>Q', self.data, self.pos)[0]
        self.pos += 8
        return v

    def fixed(self, n):
        v = self.data[self.pos:self.pos + n]
        self.pos += n + (-n % 4)
        return v

    def opaque(self):
        return self.fixed(self.u32())

    def fattr(self):
        kind, mode = self.u32(), self.u32()
        self.pos += 12                       # nlink, uid, gid
        size = self.u64()
        self.pos += 8 + 8 + 8 + 8            # used, rdev, fsid, fileid
        self.pos += 8                        # atime
        mtime = self.u32()
        self.pos += 4 + 8                    # mtime nanoseconds, ctime
        return {'type': kind, 'mode': mode, 'size': size, 'mtime': mtime}

    def post_op_attr(self):
        return self.fattr() if self.u32() else None

    def wcc(self):
        if self.u32():
            self.pos += 24                   # pre_op_attr: size, mtime, ctime
        self.post_op_attr()

    def post_op_fh(self):
        return self.opaque() if self.u32() else None


class _Rpc:
    """One TCP connection to an RPC program."""

    def __init__(self, host, port, prog, vers, timeout=20):
        self.prog, self.vers = prog, vers
        self.sock = _connect(host, port, timeout)
        self.xid = random.getrandbits(32)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass

    def _recv(self, n):
        out = b''
        while len(out) < n:
            chunk = self.sock.recv(n - len(out))
            if not chunk:
                raise NfsError('the server closed the connection')
            out += chunk
        return out

    def call(self, proc, args=b''):
        self.xid = (self.xid + 1) & 0xFFFFFFFF
        cred = _Packer().u32(0).opaque('caracal-fleet').u32(0).u32(0).u32(0).bytes()   # AUTH_SYS, uid/gid 0
        msg = (_Packer().u32(self.xid).u32(0).u32(2).u32(self.prog).u32(self.vers).u32(proc)
               .u32(1).opaque(cred).u32(0).u32(0).raw(args).bytes())
        self.sock.sendall(struct.pack('>I', 0x80000000 | len(msg)) + msg)
        reply = b''
        while True:
            mark = struct.unpack('>I', self._recv(4))[0]
            reply += self._recv(mark & 0x7FFFFFFF)
            if mark & 0x80000000:
                break
        u = _Unpacker(reply)
        if u.u32() != self.xid or u.u32() != 1:
            raise NfsError('unexpected RPC reply')
        if u.u32() != 0:
            raise NfsError('the server rejected the request (RPC authentication)')
        u.u32()
        u.opaque()                            # verifier
        accept = u.u32()
        if accept != 0:
            raise NfsError({1: 'program not available', 2: 'version not supported (NFSv3 needed)',
                            3: 'procedure not available', 4: 'bad arguments'}.get(accept, f'RPC error {accept}'))
        return u


def _connect(host, port, timeout):
    """A TCP connection, from a privileged port when possible (root in the container)."""
    last = None
    for family, kind, proto, _, address in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
        s = socket.socket(family, kind, proto)
        s.settimeout(timeout)
        if getattr(os, 'geteuid', lambda: 1)() == 0:
            for local in random.sample(range(600, 1024), 40):
                try:
                    s.bind(('::' if family == socket.AF_INET6 else '0.0.0.0', local))
                    break
                except OSError:
                    continue
        try:
            s.connect(address)
            return s
        except OSError as e:
            last = e
            s.close()
    raise NfsError(f'cannot connect to {host}:{port}: {last}')


def _status(u, what):
    st = u.u32()
    if st:
        raise NfsError(f'{what}: {ERRORS.get(st, f"NFS error {st}")}')


class Client:
    """client = Client('nas.local', '/export/backups'); client.put('dir/file', fileobj) ..."""

    def __init__(self, host, export, timeout=20):
        self.host, self.export, self.timeout = host, export, timeout
        pm = _Rpc(host, 111, PORTMAP, 2, timeout)
        try:
            mount_port = pm.call(3, _Packer().u32(MOUNT).u32(3).u32(6).u32(0).bytes()).u32()
            nfs_port = pm.call(3, _Packer().u32(NFS).u32(3).u32(6).u32(0).bytes()).u32() or 2049
        except (NfsError, OSError):
            mount_port, nfs_port = 0, 2049
        finally:
            pm.close()
        if not mount_port:
            raise NfsError('the server offers no NFSv3 MOUNT service over TCP (portmapper on port 111)')
        m = _Rpc(host, mount_port, MOUNT, 3, timeout)
        try:
            u = m.call(1, _Packer().opaque(export).bytes())
            st = u.u32()
            if st:
                raise NfsError(f'the export {export} cannot be mounted: {ERRORS.get(st, f"error {st}")}')
            self.root = u.opaque()
        finally:
            m.close()
        self.rpc = _Rpc(host, nfs_port, NFS, 3, timeout)

    def close(self):
        self.rpc.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---------------------------------------------------------------- paths

    def lookup(self, parent, name):
        u = self.rpc.call(3, _Packer().opaque(parent).opaque(name).bytes())
        st = u.u32()
        if st == 2:
            return None
        if st:
            raise NfsError(f'lookup of {name}: {ERRORS.get(st, f"NFS error {st}")}')
        return u.opaque()

    def folder(self, path, create=False):
        """The handle of a folder below the export."""
        fh = self.root
        for part in [p for p in path.strip('/').split('/') if p]:
            found = self.lookup(fh, part)
            if found is None:
                if not create:
                    raise NfsError(f'the folder {path} does not exist')
                u = self.rpc.call(9, _Packer().opaque(fh).opaque(part).u32(1).u32(0o750).u32(0).u32(0).u32(0)
                                  .u32(0).u32(0).bytes())
                _status(u, f'creating the folder {part}')
                found = u.post_op_fh() or self.lookup(fh, part)
            fh = found
        return fh

    # ---------------------------------------------------------------- files

    def put(self, folder, name, fileobj):
        """Write a file (replacing an existing one); returns the number of bytes."""
        dirfh = self.folder(folder, create=True)
        sattr = _Packer().u32(1).u32(0o600).u32(0).u32(0).u32(1).u64(0).u32(0).u32(0).bytes()   # mode 600, size 0
        u = self.rpc.call(8, _Packer().opaque(dirfh).opaque(name).u32(0).raw(sattr).bytes())   # UNCHECKED
        _status(u, f'creating {name}')
        fh = u.post_op_fh() or self.lookup(dirfh, name)
        offset = 0
        while True:
            data = fileobj.read(CHUNK)
            if not data:
                break
            view = 0
            while view < len(data):
                u = self.rpc.call(7, _Packer().opaque(fh).u64(offset).u32(len(data) - view).u32(2)   # FILE_SYNC
                                  .opaque(data[view:]).bytes())
                _status(u, f'writing {name}')
                u.wcc()
                written = u.u32()
                if not written:
                    raise NfsError(f'writing {name}: the server wrote nothing')
                view += written
                offset += written
        return offset

    def get(self, folder, name, out):
        dirfh = self.folder(folder)
        fh = self.lookup(dirfh, name)
        if fh is None:
            raise NfsError(f'{name} not found')
        offset = 0
        while True:
            u = self.rpc.call(6, _Packer().opaque(fh).u64(offset).u32(CHUNK).bytes())
            _status(u, f'reading {name}')
            u.post_op_attr()
            u.u32()
            eof = u.u32()
            data = u.opaque()
            out.write(data)
            offset += len(data)
            if eof or not data:
                return offset

    def remove(self, folder, name):
        u = self.rpc.call(12, _Packer().opaque(self.folder(folder)).opaque(name).bytes())
        _status(u, f'removing {name}')

    def list(self, folder):
        """[{name, size, mtime}] of the files in a folder (READDIRPLUS)."""
        try:
            dirfh = self.folder(folder)
        except NfsError:
            return []
        out, cookie, verf = [], 0, b'\0' * 8
        while True:
            u = self.rpc.call(17, _Packer().opaque(dirfh).u64(cookie).raw(verf).u32(8192).u32(65536).bytes())
            _status(u, 'listing the folder')
            u.post_op_attr()
            verf = u.data[u.pos:u.pos + 8]
            u.pos += 8
            while u.u32():
                u.u64()
                name = u.opaque().decode(errors='replace')
                cookie = u.u64()
                attr = u.post_op_attr()
                u.post_op_fh()
                if attr and attr['type'] == 1 and name not in ('.', '..'):
                    out.append({'name': name, 'size': attr['size'], 'mtime': attr['mtime']})
            if u.u32():
                return out
