"""The node's web administrator set by every installation path: SD card, SSH installation and conversion to Docker.

The password travels in a file readable by root only (never on a command line, never stored in the hub) and the
agent creates the administrator through the node's first-run setup (or sets a new password on an existing node).
"""
import io
import json
import os
import re
import shutil
import subprocess
import zipfile

import pytest
import requests

from conftest import ROOT, serve

PW = 'admin-password-123'
BOOT = ROOT / 'hub' / 'bootstrap'
BASH = shutil.which('bash')


@pytest.fixture(scope='module')
def env(hub_app):
    url, _ = serve(hub_app)
    h = {'Authorization': 'Bearer ' + requests.post(url + '/api/login', json={'username': 'admin', 'password': PW}).json()['token']}
    requests.put(url + '/api/node-image', headers=h, json={'image': 'ghcr.io/example/caracal-node'})
    return {'url': url, 'h': h}


def card(env, **body):
    data = {'os': 'raspios', 'hub_url': env['url'], 'name_prefix': 'lobby', **body}
    return requests.post(env['url'] + '/api/sdcard', headers=env['h'], json=data, timeout=30)


def test_sd_card_creates_the_web_administrator(env):
    r = card(env, admin_user='spravce', admin_password="it's a \"secret\" 1")
    assert r.status_code == 200, r.text
    conf = zipfile.ZipFile(io.BytesIO(r.content)).read('caracal-firstboot.conf').decode()
    assert "ADMIN_USER=spravce\n" in conf and """ADMIN_PASSWORD='it'"'"'s a "secret" 1'\n""" in conf
    plain = zipfile.ZipFile(io.BytesIO(card(env).content)).read('caracal-firstboot.conf').decode()
    assert 'ADMIN_' not in plain
    for body, code in [({'admin_user': 'spravce', 'admin_password': 'short'}, 'admin_password_short'),
                       ({'admin_user': 'two words', 'admin_password': 'long-enough-1'}, 'invalid_admin_user'),
                       ({'admin_password': 'long-enough-1'}, 'invalid_admin_user'),
                       ({'admin_user': 'x', 'admin_password': 'long-enough\n1'}, 'invalid_admin_password')]:
        r = card(env, **body)
        assert r.status_code == 400 and r.json()['detail'] == code, (body, r.text)


@pytest.mark.skipif(not BASH, reason='bash not available')
def test_firstboot_passes_the_administrator_in_a_file(tmp_path):
    """run_install writes {"username", "password"} (JSON escaped, mode 600) and passes --admin-file to install-node.sh."""
    src = (BOOT / 'caracal-firstboot.sh').read_text(encoding='utf-8').replace('\r\n', '\n')
    func = re.search(r'^json_string\(\) \{.*?^\}\n', src, re.S | re.M).group(0)
    block = re.search(r'^  if \[ -n "\$\{ADMIN_USER:-\}" \]; then.*?^  fi\n', src, re.S | re.M).group(0)
    assert '"${extra[@]}"' in src and src.index(block) < src.index('bash "$work/install-node.sh"')
    script = tmp_path / 't.sh'
    script.write_text(func + 'extra=()\nwork=$1\nADMIN_USER=spravce\nADMIN_PASSWORD=\'p"a\\ss word 1\'\n' + block
                      + 'printf "%s\\n" "${extra[@]}"\n', encoding='utf-8')
    work = tmp_path / 'work'
    work.mkdir()
    r = subprocess.run([BASH, script.as_posix(), work.as_posix()], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ['--admin-file', (work / 'admin.json').as_posix()]
    assert json.loads((work / 'admin.json').read_text()) == {'username': 'spravce', 'password': 'p"a\\ss word 1'}


@pytest.mark.skipif(not BASH, reason='bash not available')
@pytest.mark.parametrize('name', ['install-node.sh', 'install-agent.sh', 'caracal-firstboot.sh'])
def test_installers_accept_the_admin_file(name):
    path = BOOT / name
    assert subprocess.run([BASH, '-n', path.as_posix()]).returncode == 0
    src = path.read_text(encoding='utf-8')
    assert '--admin-file' in src
    if name != 'caracal-firstboot.sh':
        # never a password on the command line: only the file goes to the agent
        assert 'agent.py set-admin "$ADMIN_FILE"' in src or 'ARGS+=(--admin-file "$ADMIN_FILE")' in src


class Exec:
    def __init__(self, ssh, cmd):
        self.ssh, self.cmd, self.data = ssh, cmd, io.BytesIO()
        self.channel = self

    def write(self, data):
        self.data.write(data.encode() if isinstance(data, str) else data)

    def flush(self):
        pass

    def shutdown_write(self):
        self.ssh.stdin[self.cmd] = self.data.getvalue()

    def recv_exit_status(self):
        return 0

    def read(self):
        if self.cmd.startswith('mktemp'):
            return b'/tmp/caracal-fleet-abc\n'
        if 'wc -c' in self.cmd:
            return str(len(self.data.getvalue())).encode()
        return b''

    def readline(self):
        return ''


class SSH:
    def __init__(self):
        self.commands, self.stdin = [], {}

    def connect(self, host, **kw):
        pass

    def exec_command(self, cmd, timeout=None):
        self.commands.append(cmd)
        x = Exec(self, cmd)
        return x, x, io.BytesIO(b'')

    def close(self):
        pass


@pytest.mark.parametrize('mode', ['node', 'agent'])
def test_ssh_installation_uploads_the_admin_file(env, monkeypatch, mode):
    from app import provisioning
    ssh = SSH()
    monkeypatch.setattr(provisioning, 'ssh_client', lambda *a, **kw: ssh)
    monkeypatch.setattr(provisioning, '_device_from_log', lambda job: None)
    job = provisioning.create_job('node_install', '10.0.0.5', 'admin')
    params = {'host': '10.0.0.5', 'username': 'root', 'password': 'ssh-pass', 'hub_url': env['url'], 'mode': mode,
              'image': 'ghcr.io/example/caracal-node', 'version': '2026.10.10',
              'admin': {'username': 'spravce', 'password': 'secret-password-1'}}
    provisioning._provision(job, params, {'username': 'admin'})
    upload = next(c for c in ssh.commands if c.startswith('umask 077 && cat > '))
    assert upload.endswith('/tmp/caracal-fleet-abc/admin.json')
    assert json.loads(ssh.stdin[upload]) == params['admin']
    install = next(c for c in ssh.commands if 'install-' in c and c.startswith('bash '))
    assert '--admin-file /tmp/caracal-fleet-abc/admin.json' in install and 'secret-password-1' not in install
    from app.core import db
    with db() as c:
        log = c.execute('SELECT log, state FROM jobs WHERE id=?', (job,)).fetchone()
    assert log['state'] == 'completed' and 'secret-password-1' not in log['log'] and 'spravce' in log['log']


def test_provision_api_validates_the_administrator(env):
    body = {'host': '10.0.0.6', 'username': 'pi', 'password': 'x', 'hub_url': env['url'], 'mode': 'agent'}
    for extra, code in [({'admin_username': 'spravce', 'admin_password': 'short'}, 'admin_password_short'),
                        ({'admin_password': 'long-enough-1'}, 'invalid_admin_user')]:
        r = requests.post(env['url'] + '/api/provision', headers=env['h'], json={**body, **extra})
        assert r.status_code == 400 and r.json()['detail'] == code
