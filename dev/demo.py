"""Local demo: Fleet hub + several mock CARACAL nodes, each with a real Fleet Agent.

    pip install -r hub/requirements.txt -r requirements-dev.txt
    python dev/demo.py            # http://127.0.0.1:8090  admin / admin-password

When the CARACAL node repository is next to this one (../caracal, or CARACAL_NODE_REPO) and its requirements are
installed, the demo also runs the REAL node application with its own Fleet Agent and a simulated player that
follows the playlist and obeys Next / Show / Freeze. Its web administration runs on port 8180 (admin /
admin-password). With --overlay the node's notification overlay (player/overlay.py, Tk) shows the notifications as
windows on this computer's screen.

Mock nodes get a synthetic week of metric history on the first start, so the graphs are not empty.
Data are stored in ./data-dev (delete the folder for a clean start).
"""
import importlib.util
import math
import os
import random
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.getenv('DEMO_DATA', ROOT / 'data-dev'))
PORT = int(os.getenv('DEMO_PORT', '8090'))
# (name, location, group, local Fleet API version: v2 = current CARACAL, v1 = old hand-applied patch)
NODES = [('Recepce', 'Praha', 'Vstupy', 'v2'), ('Výroba hala A', 'Plzeň', 'Výroba', 'v2'),
         ('Jídelna', 'Praha', 'Společné', 'v2'), ('Sklad', 'Plzeň', 'Výroba', 'v1')]
NODE_REPO = Path(os.getenv('CARACAL_NODE_REPO', ROOT.parent / 'caracal'))   # ../Caracal on case-insensitive disks
NODE_PORT = PORT + 90

os.environ.setdefault('CARACAL_HUB_DATA', str(DATA / 'hub'))
os.environ.setdefault('CARACAL_HUB_ADMIN_PASSWORD', 'admin-password')
os.environ.setdefault('CARACAL_HUB_ENROLL_TOKEN', 'demo-enroll-token')
os.environ['CARACAL_AGENT_STATE'] = str(DATA / 'agent-state.json')
sys.path[:0] = [str(ROOT / 'hub'), str(ROOT)]


def serve(app, port):
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning'))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)


def seed_history(db_path, device_ids):
    """A synthetic week of metrics (with a couple of outages) for the mock nodes, once."""
    c = sqlite3.connect(db_path)
    if c.execute('SELECT COUNT(*) FROM metrics').fetchone()[0]:
        c.close()
        return
    now, bucket, rnd = time.time(), 300, random.Random(4)
    for n, did in enumerate(device_ids):
        rows = []
        for ts in range(int((now - 7 * 86400) // bucket * bucket), int(now // bucket * bucket), bucket):
            hour = ts % 86400 / 3600
            if (n == 1 and now - 30 * 3600 < ts < now - 30 * 3600 + 2400) or (n == 3 and now - 9 * 3600 < ts < now - 8 * 3600 and rnd.random() < .6):
                continue
            rows.append((did, ts, round(8 + 12 * (math.sin(hour / 24 * 6.28) + 1) + rnd.random() * 6, 1),
                         round(60 + 8 * math.sin(ts / 9000) + rnd.random() * 3, 1), 31.4, round(48 + 6 * math.sin(hour / 24 * 6.28) + rnd.random() * 2, 1)))
        c.executemany('INSERT OR REPLACE INTO metrics VALUES(?,?,?,?,?,?)', rows)
    if len(device_ids) > 3:
        c.executemany('INSERT INTO events(device_id, ts, kind, code, level, detail) VALUES(?,?,?,?,?,?)', [
            (device_ids[1], now - 30 * 3600, 'start', 'offline', 'critical', ''), (device_ids[1], now - 30 * 3600 + 2400, 'end', 'offline', 'critical', ''),
            (device_ids[3], now - 9 * 3600, 'start', 'temperature', 'warning', '74'), (device_ids[3], now - 8.2 * 3600, 'end', 'temperature', 'warning', '74')])
    c.commit()
    c.close()


def simulated_player(node):
    """What the CARACAL player does for the overlay and Fleet: plays the playlist, reports it every second and
    follows the commands of the node's admin UI (v2) and of CARACAL Fleet (v6): next, show, freeze, unfreeze."""
    index, left, frozen, seen = 0, None, False, {}
    while True:
        try:
            items = requests.get(node + '/api/player/playlist', timeout=5).json() or []
            ids = [x['id'] for x in items]
            for path, target in (('/api/v2/player/command', 'asset_id'), ('/api/v6/player/command', 'item_id')):
                cmd = requests.get(node + path, timeout=5).json()
                if path not in seen:
                    seen[path] = cmd['command_id']
                if cmd['command_id'] == seen[path]:
                    continue
                seen[path], action, aid = cmd['command_id'], cmd['action'], cmd.get(target)
                if action == 'next':
                    index, left, frozen = index + 1, None, False
                elif action in ('show', 'freeze') and aid in ids:
                    index, left, frozen = ids.index(aid), None, action == 'freeze'
                elif action == 'unfreeze':
                    frozen = False
            if items:
                index %= len(items)
                item = items[index]
                duration = max(5, int(item.get('duration') or 30))
                left = duration if left is None else left
                requests.post(node + '/api/v2/player/heartbeat', timeout=5, json={
                    'current_id': item['id'], 'current_name': item['name'], 'frozen': frozen, 'remaining': left, 'duration': duration})
                if not frozen:
                    left -= 1
                    if left <= 0:
                        index, left = index + 1, None
        except (requests.RequestException, ValueError, KeyError):
            pass
        time.sleep(1)


def start_real_node(hub, agent_mod, with_overlay):
    """The real CARACAL node application next to the mock ones, with its own agent and a simulated player."""
    if not (NODE_REPO / 'app' / 'main.py').exists():
        print(f'real CARACAL node: {NODE_REPO} not found (set CARACAL_NODE_REPO)')
        return
    try:
        import jwt, passlib, cryptography, multipart, psutil  # noqa: F401  (the node's requirements)
    except ImportError as e:
        print(f'real CARACAL node skipped: {e.name} is missing (pip install -r {NODE_REPO}/requirements.txt)')
        return
    data = DATA / 'node'
    data.mkdir(parents=True, exist_ok=True)
    enrolled = requests.post(hub + '/api/device/enroll', json={
        'enroll_token': os.environ['CARACAL_HUB_ENROLL_TOKEN'], 'fingerprint': 'demo-real-node', 'name': 'CARACAL (real node)'}).json()
    (data / 'fleet-key').write_text(enrolled['device_token'])     # what the agent writes to /etc/caracal-fleet-key
    # the node reads its paths when it is imported; the mock agents keep their own key file
    os.environ['CARACAL_DATA'], os.environ['CARACAL_FLEET_KEY_FILE'] = str(data), str(data / 'fleet-key')
    spec = importlib.util.spec_from_file_location('caracal_node_main', NODE_REPO / 'app' / 'main.py')
    mod = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True        # no __pycache__ in the node repository
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = False
        os.environ.pop('CARACAL_FLEET_KEY_FILE')
    node = f'http://127.0.0.1:{NODE_PORT}'
    serve(mod.app, NODE_PORT)
    s = requests.Session()
    password = os.environ['CARACAL_HUB_ADMIN_PASSWORD']
    if s.get(node + '/api/setup-status').json().get('needed'):
        s.post(node + '/api/setup', data={'username': 'admin', 'password': password})
    s.post(node + '/api/login', data={'username': 'admin', 'password': password})
    if not s.get(node + '/api/assets').json():
        for name, url, duration in (('Uvítání', 'https://example.com', 20), ('Výroba – Grafana', 'https://grafana.com', 30), ('Jídelníček', 'https://example.org', 15)):
            s.post(node + '/api/assets/url', data={'name': name, 'source': url, 'duration': duration})
    threading.Thread(target=simulated_player, args=(node,), daemon=True).start()
    agent = agent_mod.Agent({'hub': hub, 'local_api': node, **enrolled})
    agent.service_active = lambda name: None
    threading.Thread(target=agent.run, daemon=True).start()
    print(f'real CARACAL node: {enrolled["device_id"]}  web administration {node}  (admin / {password})')
    if with_overlay:
        # the node's notification overlay draws Tk windows on this screen; it only reads from the node
        subprocess.Popen([sys.executable, str(NODE_REPO / 'player' / 'overlay.py')], env={**os.environ, 'CARACAL_BASE': node,
                                                                                          'PYTHONDONTWRITEBYTECODE': '1'})
        print('notification overlay started (close it with Ctrl+C here)')


def main():
    from app.main import app as hub_app
    from dev.mock_node import create_app

    spec = importlib.util.spec_from_file_location('agent', ROOT / 'hub' / 'bootstrap' / 'agent.py')
    agent_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(agent_mod)

    hub = f'http://127.0.0.1:{PORT}'
    serve(hub_app, PORT)
    ids = []
    for i, (name, location, group, api) in enumerate(NODES):
        enrolled = requests.post(hub + '/api/device/enroll', json={
            'enroll_token': os.environ['CARACAL_HUB_ENROLL_TOKEN'], 'fingerprint': f'demo-{i}', 'name': name}).json()
        node = create_app(enrolled['device_token'], api=api)
        port = PORT + 100 + i
        serve(node, port)
        agent = agent_mod.Agent({'hub': hub, 'local_api': f'http://127.0.0.1:{port}', **enrolled})
        threading.Thread(target=agent.run, daemon=True).start()
        ids.append(enrolled['device_id'])
        print(f'node {name}: {enrolled["device_id"]} local API :{port}')
    seed_history(DATA / 'hub' / 'hub.db', ids)
    start_real_node(hub, agent_mod, '--overlay' in sys.argv)
    print(f'\nCARACAL Fleet demo running on {hub}  (admin / {os.environ["CARACAL_HUB_ADMIN_PASSWORD"]})')
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
