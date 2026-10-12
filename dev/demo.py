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


def fake_screen(node, local, item, left, duration, frozen):
    """A picture of the TV for the screenshot preview: the page being shown, the countdown bar and the notification
    the overlay would show, drawn with the node's own renderer (player/notify_render.py; Pillow needed)."""
    import io
    sys.path.insert(0, str(NODE_REPO / 'player'))
    import notify_render as R
    from PIL import ImageDraw
    sw, sh = 1920, 1080
    img = R.gradient((sw, sh), (14, 20, 32), (26, 36, 56), 'diagonal')
    d = ImageDraw.Draw(img)
    d.text((90, 70), item.get('name', ''), font=R.font('DejaVu Sans', True, 54), fill=(240, 244, 250))
    d.text((92, 140), item.get('source', ''), font=R.font('DejaVu Sans', False, 24), fill=(140, 150, 170))
    rnd = random.Random(item.get('id'))
    for i, (x, y, w, h) in enumerate(((90, 210, 1100, 480), (1240, 210, 590, 220), (1240, 470, 590, 220), (90, 730, 1740, 260))):
        d.rounded_rectangle((x, y, x + w, y + h), 22, fill=(30, 41, 62))
        pts = [(x + 30 + k * (w - 60) / 23, y + h - 40 - rnd.random() * (h - 110)) for k in range(24)]
        d.line(pts, fill=((232, 93, 63), (59, 130, 246), (34, 197, 94), (245, 158, 11))[i], width=5, joint='curve')
    bar = 8
    d.rectangle((0, sh - bar, sw, sh), fill=(17, 24, 39))
    d.rectangle((0, sh - bar, sw if frozen else int(sw * (1 - left / max(1, duration))), sh), fill=(232, 93, 63))
    try:
        data = local.get(node + '/api/notify/overlay', timeout=5).json()
        cur = data.get('current') if data.get('enabled') else None
        if cur:
            st = {**(data.get('style') or {}), **{k: v for k, v in (cur.get('look') or {}).items() if k in ('image', 'image_pos', 'image_size')}}
            pic = None
            if st.get('image') and data.get('image'):
                from PIL import Image
                pic = Image.open(io.BytesIO(local.get(node + '/api/notify/image/overlay', timeout=5).content)).convert('RGBA')
            g = R.layout(cur, {**data, 'bar_height': bar}, st, sw, sh, pic)
            box = R.window_box(g, sw, sh)
            out, rect, color, _ = R.render(cur, data, st, g, box, img.crop(box), pic)
            img.paste(out, box[:2])
            if rect:
                frac = max(0.0, min(1.0, float(cur.get('remaining') or 0) / max(1.0, float(cur.get('duration') or 1))))
                ImageDraw.Draw(img).rectangle((box[0] + rect[0], box[1] + rect[1], box[0] + rect[0] + int((rect[2] - rect[0]) * frac), box[1] + rect[3]), fill=color)
    except Exception as e:   # noqa: BLE001 - the preview still shows the page
        print('demo screen: notification not drawn:', e)
    buf = io.BytesIO()
    img.resize((1280, 720)).save(buf, 'JPEG', quality=80)
    return buf.getvalue()


def simulated_player(node, key_file):
    """What the CARACAL player does for the overlay and Fleet: plays the playlist, reports it every second and
    follows the commands of the node's admin UI and of CARACAL Fleet (v6): next, show, freeze, unfreeze."""
    index, left, frozen, seen = 0, None, False, {}
    local = requests.Session()   # the node's local endpoints want the key from its data directory, as the player
    while True:
        try:
            local.headers['X-Caracal-Local'] = key_file.read_text().strip()
            items = local.get(node + '/api/player/playlist-expanded', timeout=5).json() or []
            ids = [x['id'] for x in items]
            for path, target in (('/api/v6/player/command', 'item_id'),):
                cmd = local.get(node + path, timeout=5).json()
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
                local.post(node + '/api/v2/player/heartbeat', timeout=5, json={
                    'current_id': item['id'], 'current_name': item['name'], 'frozen': frozen, 'remaining': left, 'duration': duration})
                # the overlay's job on a real device: a picture of the screen when the admin UI or Fleet asks
                shot = local.get(node + '/api/notify/overlay', timeout=5).json().get('screenshot')
                if shot and shot != seen.get('shot'):
                    seen['shot'] = shot
                    try:
                        local.post(node + f'/api/screenshot/upload?id={shot}', data=fake_screen(node, local, item, left, duration, frozen), timeout=10)
                    except ImportError:
                        local.post(node + f'/api/screenshot/upload?id={shot}&error=Pillow%20is%20missing', timeout=10)
                if not frozen:
                    left -= 1
                    if left <= 0:
                        index, left = index + 1, None
        except (requests.RequestException, ValueError, KeyError, OSError):
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
    # no Grafana here: a collection lists made-up dashboards
    mod._grafana_discover = lambda url, tag: [{'title': name, 'url': f'{url}/d/{tag}-{i}'} for i, name in
                                              enumerate(('Linka 1 – výkon', 'Linka 2 – teploty', 'Sklad – zásoby', 'Energie'))]
    node = f'http://127.0.0.1:{NODE_PORT}'
    serve(mod.app, NODE_PORT)
    s = requests.Session()
    password = os.environ['CARACAL_HUB_ADMIN_PASSWORD']
    if s.get(node + '/api/setup-status').json().get('needed'):
        s.post(node + '/api/setup', data={'username': 'admin', 'password': password})
    s.post(node + '/api/login', data={'username': 'admin', 'password': password})
    if not s.get(node + '/api/assets').json():
        for name, url, duration in (('Uvítání', 'https://example.com', 20), ('Výroba – Grafana', 'https://grafana.com', 30), ('Jídelníček', 'https://example.org', 15)):
            s.post(node + '/api/v9/assets/web', json={'name': name, 'source': url, 'duration': duration})
        s.post(node + '/api/assets/grafana-tag', data={'name': 'Výroba', 'grafana_url': 'https://grafana.example', 'tag': 'vyroba', 'duration': 20})
    threading.Thread(target=simulated_player, args=(node, data / 'local.key'), daemon=True).start()
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
