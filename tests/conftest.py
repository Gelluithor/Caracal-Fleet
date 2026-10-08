import importlib.util
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix='caracal-fleet-test-'))
os.environ['CARACAL_HUB_DATA'] = str(TMP / 'hub')
os.environ['CARACAL_HUB_ADMIN_PASSWORD'] = 'admin-password-123'
os.environ['CARACAL_HUB_ENROLL_TOKEN'] = 'enroll-test-token'
os.environ['CARACAL_AGENT_CONFIG'] = str(TMP / 'agent' / 'caracal-agent.json')
os.environ['CARACAL_FLEET_KEY_FILE'] = str(TMP / 'agent' / 'caracal-fleet-key')
os.environ['CARACAL_AGENT_STATE'] = str(TMP / 'agent' / 'state.json')
(TMP / 'agent').mkdir(parents=True, exist_ok=True)
sys.path[:0] = [str(ROOT / 'hub'), str(ROOT)]


def load_agent():
    spec = importlib.util.spec_from_file_location('caracal_agent', ROOT / 'hub' / 'bootstrap' / 'agent.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


SERVERS = []


def serve(app):
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    SERVERS.append((server, thread))
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    return f'http://127.0.0.1:{port}', server


def pytest_sessionfinish(session, exitstatus):
    # Stop the test servers before the interpreter shuts down: daemon threads that still serve while Python
    # finalizes can crash it (a segmentation fault after all tests passed, seen on Python 3.13 in CI).
    for server, _ in SERVERS:
        server.should_exit = True
    for _, thread in SERVERS:
        thread.join(timeout=10)


@pytest.fixture(scope='session')
def hub_app():
    from app.main import app
    return app


@pytest.fixture(scope='session')
def client(hub_app):
    from fastapi.testclient import TestClient
    return TestClient(hub_app)


@pytest.fixture(scope='session')
def admin_headers(client):
    r = client.post('/api/login', json={'username': 'admin', 'password': 'admin-password-123'})
    assert r.status_code == 200, r.text
    return {'Authorization': 'Bearer ' + r.json()['token']}
