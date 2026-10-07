"""Web console: an interactive SSH shell on a node, bridged to the browser over a WebSocket.

The hub connects to the node over SSH (host key pinned on first use, shared with the SSH installation) and
relays the terminal. Credentials are sent with the first WebSocket message and are never stored.

Protocol: the browser opens /api/devices/{id}/console and sends one JSON text message
  {"token", "username", "password" | "private_key" + "passphrase", "host"?, "port"?, "cols", "rows",
   "forget_host_key"?}
The hub answers {"type": "connected"} or {"type": "error", "code", "detail"}. Afterwards binary messages carry
the terminal data in both directions and the browser sends {"type": "resize", "cols", "rows"} as text; the hub
ends with {"type": "closed", "reason"}.
"""
import asyncio
import concurrent.futures
import json
import threading
import time

import paramiko
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from .core import audit, db, user_from_token
from .provisioning import load_key, ssh_client

router = APIRouter()

HELLO_TIMEOUT = 20       # seconds for the first message with the credentials
IDLE_TIMEOUT = 1800      # a console without any input is closed after 30 minutes
READ_CHUNK = 65536


def _size(v, low, high, default):
    try:
        return max(low, min(high, int(v)))
    except (TypeError, ValueError):
        return default


def _open_shell(p):
    ssh = ssh_client(p['host'], p['port'], p['forget_host_key'])
    kw = dict(port=p['port'], username=p['username'], timeout=15, auth_timeout=20, banner_timeout=20,
              look_for_keys=False, allow_agent=False)
    try:
        if p['private_key']:
            kw['pkey'] = load_key(p['private_key'], p['passphrase'] or p['password'])
        else:
            kw['password'] = p['password']
        ssh.connect(p['host'], **kw)
        ssh.get_transport().set_keepalive(30)
        chan = ssh.invoke_shell(term='xterm-256color', width=p['cols'], height=p['rows'])
    except BaseException:
        ssh.close()
        raise
    return ssh, chan


def _reader(chan, loop, queue):
    """Thread: SSH output -> asyncio queue (blocks while the queue is full, so a fast producer cannot flood
    the hub's memory); None marks the end of the shell."""
    try:
        while True:
            data = chan.recv(READ_CHUNK)
            if not data:
                break
            put = asyncio.run_coroutine_threadsafe(queue.put(data), loop)
            while True:
                try:
                    put.result(timeout=1)
                    break
                except concurrent.futures.TimeoutError:
                    if chan.closed:   # the browser left while the queue was full
                        put.cancel()
                        return
    except Exception:  # noqa: BLE001 - the channel or the event loop closed
        pass
    finally:
        try:
            asyncio.run_coroutine_threadsafe(queue.put(None), loop).result(timeout=5)
        except Exception:  # noqa: BLE001
            pass


async def _fail(ws, code, detail=''):
    try:
        await ws.send_json({'type': 'error', 'code': code, 'detail': detail[:300]})
        await ws.close(code=1000)
    except (RuntimeError, WebSocketDisconnect):
        pass


@router.websocket('/api/devices/{did}/console')
async def console(ws: WebSocket, did: str):
    await ws.accept()
    try:
        hello = json.loads(await asyncio.wait_for(ws.receive_text(), HELLO_TIMEOUT))
        if not isinstance(hello, dict):
            raise ValueError
    except WebSocketDisconnect:
        return
    except (asyncio.TimeoutError, ValueError, KeyError):
        return await _fail(ws, 'invalid_request')
    try:
        user = user_from_token(str(hello.get('token') or ''), 'manage')
    except HTTPException as e:
        return await _fail(ws, e.detail)

    with db() as c:
        row = c.execute('SELECT name, ip, status_json FROM devices WHERE id=?', (did,)).fetchone()
    if not row:
        return await _fail(ws, 'device_not_found')
    try:
        status = json.loads(row['status_json'] or '{}')
    except ValueError:
        status = {}
    p = {
        'host': str(hello.get('host') or '').strip()[:255] or status.get('ip') or row['ip'] or '',
        'port': _size(hello.get('port'), 1, 65535, 22),
        'username': str(hello.get('username') or '').strip()[:64],
        'password': str(hello.get('password') or ''),
        'private_key': str(hello.get('private_key') or '').strip()[:16000],
        'passphrase': str(hello.get('passphrase') or ''),
        'forget_host_key': bool(hello.get('forget_host_key')),
        'cols': _size(hello.get('cols'), 20, 500, 80),
        'rows': _size(hello.get('rows'), 5, 200, 24),
    }
    if not p['host'] or not p['username'] or not (p['password'] or p['private_key']):
        return await _fail(ws, 'missing_fields')

    target = f"{p['username']}@{p['host']}:{p['port']}"
    try:
        ssh, chan = await asyncio.to_thread(_open_shell, p)
    except paramiko.AuthenticationException:
        audit(user, 'console.failed', did, {'target': target, 'error': 'authentication'})
        return await _fail(ws, 'ssh_auth_failed')
    except paramiko.BadHostKeyException:
        audit(user, 'console.failed', did, {'target': target, 'error': 'host key changed'})
        return await _fail(ws, 'ssh_host_key_changed')
    except (OSError, EOFError, ValueError, paramiko.SSHException) as e:
        audit(user, 'console.failed', did, {'target': target, 'error': str(e)[:200]})
        return await _fail(ws, 'ssh_connect_failed', str(e))

    audit(user, 'console.open', did, {'target': target, 'name': row['name']})
    started = time.time()
    loop = asyncio.get_running_loop()
    output = asyncio.Queue(maxsize=64)
    threading.Thread(target=_reader, args=(chan, loop, output), daemon=True).start()

    async def pump_out():
        while (data := await output.get()) is not None:
            await ws.send_bytes(data)
        return 'exit'

    async def pump_in():
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive(), IDLE_TIMEOUT)
            except asyncio.TimeoutError:
                return 'idle'
            if msg['type'] == 'websocket.disconnect':
                return 'disconnect'
            if msg.get('bytes'):
                await loop.run_in_executor(None, chan.sendall, msg['bytes'])
            elif msg.get('text'):
                try:
                    ctl = json.loads(msg['text'])
                except ValueError:
                    continue
                if isinstance(ctl, dict) and ctl.get('type') == 'resize':
                    chan.resize_pty(width=_size(ctl.get('cols'), 20, 500, 80),
                                    height=_size(ctl.get('rows'), 5, 200, 24))

    reason, tasks = 'error', []
    try:
        await ws.send_json({'type': 'connected', 'target': target})
        tasks = [asyncio.create_task(pump_out()), asyncio.create_task(pump_in())]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        task = done.pop()
        reason = task.result() if not task.exception() else 'error'
    finally:
        for task in tasks:
            task.cancel()
        try:
            chan.close()
        except (OSError, EOFError, paramiko.SSHException):   # the node already closed the connection
            pass
        ssh.close()
        audit(user, 'console.close', did, {'target': target, 'reason': reason,
                                           'seconds': round(time.time() - started)})
    if reason != 'disconnect':
        try:
            await ws.send_json({'type': 'closed', 'reason': reason})
            await ws.close(code=1000)
        except (RuntimeError, WebSocketDisconnect):
            pass
