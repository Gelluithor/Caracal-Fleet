"""Local demo: Fleet hub + several mock CARACAL nodes, each with a real Fleet Agent.

    pip install -r hub/requirements.txt -r requirements-dev.txt
    python dev/demo.py            # http://127.0.0.1:8090  admin / admin-password

Data are stored in ./data-dev (delete the folder for a clean start).
"""
import importlib.util
import os
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


def main():
    from app.main import app as hub_app
    from dev.mock_node import create_app

    spec = importlib.util.spec_from_file_location('agent', ROOT / 'hub' / 'bootstrap' / 'agent.py')
    agent_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(agent_mod)

    hub = f'http://127.0.0.1:{PORT}'
    serve(hub_app, PORT)
    for i, (name, location, group, api) in enumerate(NODES):
        enrolled = requests.post(hub + '/api/device/enroll', json={
            'enroll_token': os.environ['CARACAL_HUB_ENROLL_TOKEN'], 'fingerprint': f'demo-{i}', 'name': name}).json()
        node = create_app(enrolled['device_token'], api=api)
        port = PORT + 100 + i
        serve(node, port)
        agent = agent_mod.Agent({'hub': hub, 'local_api': f'http://127.0.0.1:{port}', **enrolled})
        threading.Thread(target=agent.run, daemon=True).start()
        print(f'node {name}: {enrolled["device_id"]} local API :{port}')
    print(f'\nCARACAL Fleet demo running on {hub}  (admin / {os.environ["CARACAL_HUB_ADMIN_PASSWORD"]})')
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
