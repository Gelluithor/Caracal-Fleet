"""Agent files are uploaded over a plain exec channel (works with Dropbear, which has no SFTP)."""
import io
import shlex

import pytest

from app import provisioning


class FakeChannel:
    def __init__(self, ch):
        self.ch = ch

    def shutdown_write(self):
        self.ch.closed = True

    def recv_exit_status(self):
        return self.ch.code


class FakeExec:
    def __init__(self, files, cmd, truncate=False):
        self.files, self.cmd, self.truncate, self.closed, self.code = files, cmd, truncate, False, 0
        self.buf = io.BytesIO()
        self.channel = FakeChannel(self)

    def write(self, data):
        self.buf.write(data)

    def flush(self):
        pass

    def read(self):
        data = self.buf.getvalue()[:-1] if self.truncate else self.buf.getvalue()
        path = shlex.split(self.cmd)[2]
        self.files[path] = data
        return str(len(data)).encode()


class FakeSSH:
    def __init__(self, truncate=False):
        self.files, self.truncate = {}, truncate

    def exec_command(self, cmd, timeout=None):
        assert cmd.startswith('cat > ')
        x = FakeExec(self.files, cmd, self.truncate)
        err = io.BytesIO(b'')
        return x, x, err


def test_upload_without_sftp():
    ssh = FakeSSH()
    provisioning.upload(ssh, provisioning.BOOT / 'agent.py', '/tmp/caracal-fleet-x/agent.py')
    assert ssh.files['/tmp/caracal-fleet-x/agent.py'] == (provisioning.BOOT / 'agent.py').read_bytes()


def test_upload_detects_incomplete_transfer():
    with pytest.raises(RuntimeError):
        provisioning.upload(FakeSSH(truncate=True), provisioning.BOOT / 'agent.py', '/tmp/x/agent.py')
