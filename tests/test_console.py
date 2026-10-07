"""SSH web console: WebSocket <-> SSH shell bridge against a small paramiko SSH server."""
import json
import socket
import threading
import time

import paramiko
import pytest

from app import core

USER, PASSWORD = 'pi', 'raspberry-test'


class _Server(paramiko.ServerInterface):
    def __init__(self):
        self.shell = threading.Event()
        self.size = None

    def check_auth_password(self, username, password):
        ok = (username, password) == (USER, PASSWORD)
        return paramiko.AUTH_SUCCESSFUL if ok else paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return 'password'

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == 'session' else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_pty_request(self, channel, term, width, height, pixelwidth, pixelheight, modes):
        self.size = (term, width, height)
        return True

    def check_channel_window_change_request(self, channel, width, height, pixelwidth, pixelheight):
        self.size = (self.size[0], width, height)
        return True

    def check_channel_shell_request(self, channel):
        self.shell.set()
        return True


@pytest.fixture(scope='module')
def ssh_server():
    """Echo shell: every input line is answered with 'echo:<line>', 'size' reports the pty size and 'exit'
    ends the session."""
    host_key = paramiko.RSAKey.generate(2048)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    sock.listen(5)
    port = sock.getsockname()[1]

    def session(conn):
        transport = paramiko.Transport(conn)
        transport.add_server_key(host_key)
        server = _Server()
        try:
            transport.start_server(server=server)
            chan = transport.accept(10)
            if chan is None or not server.shell.wait(10):
                return
            chan.send(b'welcome\r\n')
            buf = b''
            while True:
                data = chan.recv(1024)
                if not data:
                    break
                buf += data
                while b'\r' in buf:
                    line, buf = buf.split(b'\r', 1)
                    if line == b'exit':
                        chan.send_exit_status(0)
                        chan.close()
                        return
                    if line == b'size':
                        chan.send(f'size:{server.size[1]}x{server.size[2]}\r\n'.encode())
                    else:
                        chan.send(b'echo:' + line + b'\r\n')
        except Exception:  # noqa: BLE001 - the client went away
            pass
        finally:
            transport.close()

    def accept():
        while True:
            try:
                conn, _ = sock.accept()
            except OSError:
                return
            threading.Thread(target=session, args=(conn,), daemon=True).start()

    threading.Thread(target=accept, daemon=True).start()
    yield port
    sock.close()


@pytest.fixture(scope='module')
def device_id():
    did = 'CRCL-CONSOLE1'
    with core.db() as c:
        c.execute("INSERT OR REPLACE INTO devices(id, token_hash, name, ip, version, last_seen, status_json, created) "
                  "VALUES(?, '', 'Console node', '127.0.0.1', '', ?, '{}', ?)", (did, time.time(), time.time()))
    return did


def _token(headers):
    return headers['Authorization'].removeprefix('Bearer ')


def _read_until(ws, needle):
    out = b''
    while needle not in out:
        msg = ws.receive()
        assert msg['type'] == 'websocket.send', msg
        if msg.get('text'):
            pytest.fail(f'unexpected control message {msg["text"]} while waiting for {needle!r}')
        out += msg['bytes']
    return out


def test_console_session(client, admin_headers, ssh_server, device_id):
    with client.websocket_connect(f'/api/devices/{device_id}/console') as ws:
        ws.send_text(json.dumps({'token': _token(admin_headers), 'username': USER, 'password': PASSWORD,
                                 'port': ssh_server, 'cols': 100, 'rows': 30}))
        assert ws.receive_json() == {'type': 'connected', 'target': f'{USER}@127.0.0.1:{ssh_server}'}
        _read_until(ws, b'welcome')
        ws.send_bytes(b'hello \xc5\xbe\r')
        _read_until(ws, 'echo:hello ž'.encode())
        ws.send_bytes(b'size\r')
        _read_until(ws, b'size:100x30')
        ws.send_text(json.dumps({'type': 'resize', 'cols': 132, 'rows': 40}))
        ws.send_bytes(b'size\r')
        _read_until(ws, b'size:132x40')
        ws.send_bytes(b'exit\r')
        assert ws.receive_json() == {'type': 'closed', 'reason': 'exit'}
    rows = client.get('/api/audit?q=console', headers=admin_headers).json()
    actions = [r['action'] for r in rows if r['target'] == device_id]
    assert 'console.open' in actions and 'console.close' in actions
    assert all(PASSWORD not in (r['detail'] or '') for r in rows)
    assert f'127.0.0.1]:{ssh_server}' in (core.DATA / 'ssh_known_hosts').read_text()   # host key pinned


def test_console_wrong_password(client, admin_headers, ssh_server, device_id):
    with client.websocket_connect(f'/api/devices/{device_id}/console') as ws:
        ws.send_text(json.dumps({'token': _token(admin_headers), 'username': USER, 'password': 'wrong',
                                 'port': ssh_server}))
        msg = ws.receive_json()
    assert msg['type'] == 'error' and msg['code'] == 'ssh_auth_failed'


def test_console_requires_manage_role(client, admin_headers, device_id):
    r = client.post('/api/users', headers=admin_headers,
                    json={'username': 'console-op', 'password': 'long-password-1', 'role': 'operator'})
    assert r.status_code == 200, r.text
    op = client.post('/api/login', json={'username': 'console-op', 'password': 'long-password-1'}).json()['token']
    for token, code in ((op, 'forbidden'), ('garbage', 'session_expired')):
        with client.websocket_connect(f'/api/devices/{device_id}/console') as ws:
            ws.send_text(json.dumps({'token': token, 'username': USER, 'password': PASSWORD}))
            assert ws.receive_json() == {'type': 'error', 'code': code, 'detail': ''}


def test_console_unknown_device_and_missing_fields(client, admin_headers, device_id):
    for did, hello, code in (('CRCL-NOPE', {'username': USER, 'password': PASSWORD}, 'device_not_found'),
                             (device_id, {'username': USER}, 'missing_fields')):
        with client.websocket_connect(f'/api/devices/{did}/console') as ws:
            ws.send_text(json.dumps({'token': _token(admin_headers), **hello}))
            assert ws.receive_json()['code'] == code


def test_console_unreachable_host(client, admin_headers, device_id):
    with socket.socket() as s:   # a port with nobody listening
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
    with client.websocket_connect(f'/api/devices/{device_id}/console') as ws:
        ws.send_text(json.dumps({'token': _token(admin_headers), 'username': USER, 'password': PASSWORD,
                                 'port': port}))
        assert ws.receive_json()['code'] == 'ssh_connect_failed'


def test_terminal_assets_are_served(client):
    for name in ('xterm.js', 'addon-fit.js', 'xterm.css'):
        assert client.get(f'/static/vendor/xterm/{name}').status_code == 200
