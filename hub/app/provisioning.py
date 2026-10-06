"""Installation over SSH (background jobs) and discovery of devices in the network.

Two modes: "node" turns a clean Raspberry Pi (or a classic node) into a CARACAL node on Docker with the Fleet
Agent (install-node.sh); "agent" only installs or updates the Fleet Agent on an existing CARACAL node and never
touches CARACAL itself (player, playlists, media, database).
"""
import io
import ipaddress
import json
import re
import shlex
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import paramiko

from .core import BOOT, DATA, audit, cfg, db

KNOWN_HOSTS = DATA / 'ssh_known_hosts'

AGENT_FILES = ('agent.py', 'install-agent.sh')
NODE_FILES = ('install-node.sh', 'caracal-compose.yml')   # CARACAL node on Docker


def create_job(kind, target, username):
    now = time.time()
    with db() as c:
        return c.execute("INSERT INTO jobs(kind, target, username, state, log, created, updated) "
                         "VALUES(?,?,?,'queued','',?,?)", (kind, target, username, now, now)).lastrowid


def job_log(job_id, line, state=None, device_id=None):
    stamp = time.strftime('%H:%M:%S')
    with db() as c:
        c.execute('UPDATE jobs SET log=log||?, updated=? WHERE id=?', (f'[{stamp}] {line.rstrip()}\n', time.time(), job_id))
        if state:
            c.execute('UPDATE jobs SET state=? WHERE id=?', (state, job_id))
        if device_id:
            c.execute('UPDATE jobs SET device_id=? WHERE id=?', (device_id, job_id))


def _load_key(text, passphrase):
    for cls in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
        try:
            return cls.from_private_key(io.StringIO(text), password=passphrase or None)
        except (paramiko.SSHException, ValueError):
            continue
    raise ValueError('Unsupported or invalid private key')


def _run(ssh, job_id, cmd, stdin_data=None, timeout=1800):
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    if stdin_data is not None:
        stdin.write(stdin_data)
        stdin.flush()
    stdin.channel.shutdown_write()
    for line in iter(stdout.readline, ''):
        job_log(job_id, line)
    code = stdout.channel.recv_exit_status()
    err = stderr.read().decode(errors='replace').strip()
    if err:
        for line in err.splitlines()[-30:]:
            job_log(job_id, line)
    return code


def upload(ssh, local, remote):
    """Copy a file through a plain exec channel. SFTP is not used because Dropbear (default SSH server
    on DietPi) has no SFTP subsystem without the extra openssh-sftp-server package."""
    data = Path(local).read_bytes()
    q = shlex.quote(remote)
    stdin, stdout, stderr = ssh.exec_command(f'cat > {q} && chmod 755 {q} && wc -c < {q}', timeout=120)
    stdin.write(data)
    stdin.flush()
    stdin.channel.shutdown_write()
    code = stdout.channel.recv_exit_status()
    size = stdout.read().decode(errors='replace').strip()
    if code or size != str(len(data)):
        err = stderr.read().decode(errors='replace').strip()
        raise RuntimeError(f'Upload of {Path(local).name} failed: {err or f"size {size} != {len(data)}"}')


def start(job_id, params, user):
    threading.Thread(target=_provision, args=(job_id, params, user), daemon=True).start()


def _provision(job_id, p, user):
    host, port, username = p['host'], int(p.get('port') or 22), p['username']
    password = p.get('password') or ''
    ssh = paramiko.SSHClient()
    # Trust on first use: a node's host key is stored on the first connection and verified afterwards.
    KNOWN_HOSTS.touch(exist_ok=True)
    ssh.load_host_keys(str(KNOWN_HOSTS))
    if p.get('forget_host_key'):
        name = host if port == 22 else f'[{host}]:{port}'
        if name in ssh.get_host_keys():
            del ssh.get_host_keys()[name]
            ssh.save_host_keys(str(KNOWN_HOSTS))
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    job_log(job_id, f'Connecting to {username}@{host}:{port}', state='running')
    try:
        kw = dict(port=port, username=username, timeout=20, auth_timeout=20, banner_timeout=20,
                  look_for_keys=False, allow_agent=False)
        if p.get('private_key'):
            kw['pkey'] = _load_key(p['private_key'], p.get('passphrase') or password)
        else:
            kw['password'] = password
        ssh.connect(host, **kw)
        job_log(job_id, 'SSH connected')

        _, out, _ = ssh.exec_command('mktemp -d /tmp/caracal-fleet-XXXXXX', timeout=30)
        tmp = out.read().decode().strip()
        if not tmp.startswith('/tmp/caracal-fleet-'):
            raise RuntimeError('Cannot create temporary directory on the node')
        node = p.get('mode') == 'node'
        for name in AGENT_FILES + (NODE_FILES if node else ()):
            upload(ssh, BOOT / name, f'{tmp}/{name}')
        job_log(job_id, f'Installation files uploaded to {tmp}')

        args = ['--hub', p['hub_url'], '--token', cfg()['enroll_token']]
        if p.get('name'):
            args += ['--name', p['name']]
        if node:
            args += ['--image', p['image'], '--version', p['version']]
            job_log(job_id, f"Installing CARACAL node {p['image']}:{p['version']} (Docker)")
        elif p.get('reenroll'):
            args.append('--reenroll')
        script = 'install-node.sh' if node else 'install-agent.sh'
        cmd = f'bash {shlex.quote(tmp + "/" + script)} ' + ' '.join(shlex.quote(a) for a in args)
        stdin_data = None
        if username != 'root':
            if not password:
                cmd = f'sudo -n {cmd}'
            else:
                cmd = f"sudo -S -p '' {cmd}"
                stdin_data = password + '\n'
        code = _run(ssh, job_id, cmd, stdin_data)
        _run(ssh, job_id, f'rm -rf {shlex.quote(tmp)}', timeout=30)
        if node and code == 5:   # install-node.sh enabled the graphics driver (e.g. DietPi), it needs a reboot
            reboot = 'systemctl reboot' if username == 'root' else \
                ('sudo -n systemctl reboot' if not password else "sudo -S -p '' systemctl reboot")
            try:
                _run(ssh, job_id, reboot, stdin_data, timeout=30)
            except Exception:  # noqa: BLE001 - the connection drops when the device reboots
                pass
            raise RuntimeError('The graphics driver was enabled and the device is rebooting. '
                               'Run the installation again in about a minute to finish it.')
        if code:
            raise RuntimeError(f'Installer finished with exit code {code}')

        device_id = _device_from_log(job_id)
        if device_id:
            with db() as c:
                if p.get('group') or p.get('location'):
                    c.execute("UPDATE devices SET device_group=COALESCE(NULLIF(?, ''), device_group), "
                              "location=COALESCE(NULLIF(?, ''), location) WHERE id=?",
                              (p.get('group', ''), p.get('location', ''), device_id))
                for table, value in (('device_groups', p.get('group')), ('device_locations', p.get('location'))):
                    if value:
                        c.execute(f'INSERT OR IGNORE INTO {table}(name, created) VALUES(?,?)', (value, time.time()))
        job_log(job_id, 'CARACAL node installed and connected' if node else 'Fleet Agent installed and running',
                state='completed', device_id=device_id)
        audit(user, 'provision.node' if node else 'provision.agent', host, {'device_id': device_id, 'job': job_id})
    except paramiko.AuthenticationException:
        job_log(job_id, 'SSH authentication failed', state='failed')
    except paramiko.BadHostKeyException:
        job_log(job_id, 'SSH host key of the node has CHANGED. If the node was reinstalled, repeat the '
                        'installation with "Forget stored host key"; otherwise this may be an attack.', state='failed')
    except Exception as e:  # noqa: BLE001 - every failure is reported in the job log
        job_log(job_id, f'ERROR: {e}', state='failed')
    finally:
        ssh.close()


def _device_from_log(job_id):
    with db() as c:
        log = c.execute('SELECT log FROM jobs WHERE id=?', (job_id,)).fetchone()['log']
    m = re.findall(r'DEVICE_ID=(\S+)', log)
    return m[-1] if m else ''


# ---------------------------------------------------------------- network discovery

MAX_SCAN = 1024


def discovery_hosts(cidr):
    """Hosts of a private network to scan (at most a /22)."""
    net = ipaddress.ip_network(cidr, strict=False)
    if net.version != 4 or not (net.is_private or net.is_loopback) or net.num_addresses > MAX_SCAN:
        raise ValueError('invalid network')
    return [str(ip) for ip in (net.hosts() if net.num_addresses > 2 else net)]


def _probe(ip, port):
    try:
        with socket.create_connection((ip, port), timeout=0.8) as s:
            s.settimeout(1.5)
            try:
                banner = s.recv(256).decode(errors='replace').strip()
            except OSError:
                banner = ''
    except OSError:
        return None
    lower = banner.lower()
    system = 'Raspberry Pi OS' if 'raspbian' in lower else 'DietPi' if 'dropbear' in lower else \
        'Debian' if 'debian' in lower else 'Ubuntu' if 'ubuntu' in lower else ''
    return {'ip': ip, 'port': port, 'banner': banner[:120], 'system': system}


def start_discovery(job_id, hosts, port):
    threading.Thread(target=_discover, args=(job_id, hosts, port), daemon=True).start()


def _discover(job_id, hosts, port):
    job_log(job_id, f'Scanning {len(hosts)} addresses for SSH on port {port}', state='running')
    with ThreadPoolExecutor(max_workers=64) as pool:
        found = [x for x in pool.map(lambda ip: _probe(ip, port), hosts) if x]
    with db() as c:
        known = {r['ip']: dict(r) for r in c.execute('SELECT id, name, ip FROM devices')}
        for x in found:
            dev = known.get(x['ip'])
            x['device_id'], x['device_name'] = (dev['id'], dev['name']) if dev else ('', '')
        c.execute('UPDATE jobs SET result_json=? WHERE id=?', (json.dumps(found), job_id))
    job_log(job_id, f'Found {len(found)} device(s) with SSH', state='completed')
