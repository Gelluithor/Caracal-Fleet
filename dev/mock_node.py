"""In-memory mock of the local CARACAL Fleet API (docs/LOCAL-API.md) for development and tests.

It mirrors the real CARACAL node (caracal repository, app/main.py):

* api='v2' - section CARACAL_FLEET_API_V2: live v2 player state, media upload/download, Grafana collections
  (playlist assets of kind "grafana-tag" with {"grafana_url", "tag", "kiosk"} as JSON source), complete reorder.
* api='v1' - the hand-applied CARACAL_FLEET_API_V1 patch still running on older nodes: no media or Grafana
  endpoints and a snapshot with the stale v1 player state.

Run:  uvicorn dev.mock_node:app --port 8080     (key: MOCK_FLEET_KEY env, default "dev-key")
"""
import json
import os
import time

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import Response

IMAGE_EXT = ('.png', '.jpg', '.jpeg', '.webp', '.gif')
VIDEO_EXT = ('.mp4', '.webm', '.mkv')


def create_app(key=None, api='v2'):
    v2 = api == 'v2'
    app = FastAPI(title=f'CARACAL mock node ({api})')
    st = app.state
    st.key = key or os.getenv('MOCK_FLEET_KEY', 'dev-key')
    st.assets = [
        {'id': 1, 'name': 'Intranet', 'kind': 'web', 'source': 'https://example.com', 'duration': 30, 'position': 0,
         'auth_profile_id': None, 'scale': 1.0},
        {'id': 2, 'name': 'Logo', 'kind': 'image', 'source': '/media/0a1b2c.png', 'duration': 10, 'position': 1,
         'auth_profile_id': None, 'scale': 1.0},
        {'id': 3, 'name': 'Výroba dashboardy', 'kind': 'grafana-tag', 'position': 2, 'auth_profile_id': None,
         'source': json.dumps({'grafana_url': 'https://grafana.example', 'tag': 'vyroba', 'kiosk': True}),
         'duration': 60, 'scale': 1.0},
    ]
    st.files = {2: b'\x89PNG mock image'}
    st.player = {'current_id': 1, 'frozen': False, 'collection_frozen': False, 'collection_id': None,
                 'started': time.time(), 'updated': time.time(), 'created': time.time(), 'stopped': False}
    st.next_id = 10
    st.log = []

    def auth(k):
        if k != st.key:
            raise HTTPException(401, 'Invalid Fleet key')

    def new_id():
        st.next_id += 1
        return st.next_id

    def find(ident):
        for a in st.assets:
            if a['id'] == int(ident):
                return a
        raise HTTPException(404, 'Item not found')

    def expanded():
        out = []
        for a in st.assets:
            if a['kind'] != 'grafana-tag':
                out.append(a)
                continue
            for i in range(2):  # every collection resolves to two dashboards
                out.append({**a, 'id': a['id'] * 100000 + i, 'parent_id': a['id'], 'kind': 'web',
                            'name': f"{a['name']} · Dashboard {i + 1}"})
        return out

    def duration(v):
        try:
            return max(5, int(float(v)))
        except (TypeError, ValueError):
            raise HTTPException(400, 'Invalid duration')

    def scale(v):
        s = float(str(v).replace(',', '.'))
        if v2 and not 0.5 <= s <= 3:
            raise HTTPException(400, 'Scale must be 0.5 to 3.0')
        return s

    def grafana(d, current=None):
        current = current or {}
        url = str(d.get('grafana_url', current.get('grafana_url', ''))).strip().rstrip('/')
        tag = str(d.get('tag', current.get('tag', ''))).strip()
        if not url.startswith(('http://', 'https://')) or not tag:
            raise HTTPException(400, 'Invalid Grafana collection')
        return {'grafana_url': url, 'tag': tag, 'kiosk': bool(d.get('kiosk', current.get('kiosk', True)))}

    @app.get('/api/fleet/v1/snapshot')
    def snapshot(x_fleet_key: str = Header('')):
        auth(x_fleet_key)
        p = st.player
        cur = next((x for x in expanded() if x['id'] == p['current_id']), None)
        dur = (cur or {}).get('duration') or 30
        frozen = p['frozen'] or p['collection_frozen']
        if v2:
            if not p['stopped']:  # a running player sends its v2 heartbeat every second
                p['updated'] = time.time()
            player = {'current_id': p['current_id'], 'current_name': (cur or {}).get('name', ''),
                      'frozen': p['frozen'], 'collection_frozen': p['collection_frozen'],
                      'collection_id': p['collection_id'], 'duration': dur,
                      'remaining': None if frozen else max(0, round(dur - (time.time() - p['started']) % dur)),
                      'updated': p['updated'], 'player_online': time.time() - p['updated'] < 8}
            return {'api_version': 2, 'assets': st.assets, 'profiles': [{'id': 1, 'name': 'Grafana login'}],
                    'player': player}
        # v1 patch: the v1 state file is never refreshed by the v2 player
        return {'assets': st.assets, 'profiles': [{'id': 1, 'name': 'Grafana login'}],
                'player': {'current_id': p['current_id'], 'current_name': (cur or {}).get('name', ''),
                           'force_id': None, 'frozen': False, 'remaining': None, 'duration': dur,
                           'updated': p['created'] - 3600, 'player_online': True}}

    @app.post('/api/fleet/v1/control')
    async def control(r: Request, x_fleet_key: str = Header('')):
        auth(x_fleet_key)
        d = await r.json()
        a = d.get('action')
        st.log.append(d)
        items, p = expanded(), st.player
        if a in ('show', 'freeze'):
            if not any(x['id'] == int(d.get('item_id')) for x in items):
                raise HTTPException(404, 'Item unavailable')
            p.update(current_id=int(d['item_id']), frozen=a == 'freeze', collection_frozen=False,
                     collection_id=None, started=time.time())
        elif a in ('show_collection', 'freeze_collection'):
            cid = int(d.get('collection_id'))
            group = [x for x in items if x.get('parent_id') == cid]
            if not group:
                raise HTTPException(404, 'Collection unavailable')
            p.update(current_id=group[0]['id'], frozen=False, collection_frozen=True, collection_id=cid,
                     started=time.time())
        elif a == 'next':
            ids = [x['id'] for x in items]
            cur = p['current_id']
            p.update(current_id=ids[(ids.index(cur) + 1) % len(ids)] if cur in ids else ids[0], frozen=False,
                     collection_frozen=False, collection_id=None, started=time.time())
        elif a == 'unfreeze':
            p.update(frozen=False, collection_frozen=False, collection_id=None)
        else:
            raise HTTPException(400, 'Unknown action')
        return {'ok': True}

    @app.post('/api/fleet/v1/assets/web')
    async def add_web(r: Request, x_fleet_key: str = Header('')):
        auth(x_fleet_key)
        d = await r.json()
        if not d.get('name') or not str(d.get('source', '')).startswith(('http://', 'https://')):
            raise HTTPException(400, 'Invalid name or URL')
        a = {'id': new_id(), 'name': d['name'], 'kind': 'web', 'source': d['source'],
             'duration': duration(d.get('duration', 30)), 'position': len(st.assets), 'auth_profile_id': None,
             'scale': scale(d.get('scale', 1))}
        st.assets.append(a)
        return {'ok': True, 'id': a['id']}

    @app.put('/api/fleet/v1/assets/{ident}')
    async def update_asset(ident: int, r: Request, x_fleet_key: str = Header('')):
        auth(x_fleet_key)
        a, d = find(ident), await r.json()
        if not v2:  # the v1 patch writes the given columns as they are
            a.update({k: d[k] for k in ('name', 'source', 'duration', 'scale') if k in d})
            return {'ok': True}
        a.update(name=str(d.get('name', a['name'])).strip() or a['name'],
                 duration=duration(d.get('duration', a['duration'])), scale=scale(d.get('scale', a['scale'])))
        if a['kind'] == 'web' and 'source' in d:
            if not str(d['source']).startswith(('http://', 'https://')):
                raise HTTPException(400, 'Invalid URL')
            a['source'] = d['source']
        if a['kind'] == 'grafana-tag' and any(k in d for k in ('grafana_url', 'tag', 'kiosk')):
            a['source'] = json.dumps(grafana(d, json.loads(a['source'])))
        return {'ok': True}

    @app.delete('/api/fleet/v1/assets/{ident}')
    def delete_asset(ident: int, x_fleet_key: str = Header('')):
        auth(x_fleet_key)
        st.assets.remove(find(ident))
        st.files.pop(ident, None)
        return {'ok': True}

    @app.put('/api/fleet/v1/playlist/reorder')
    async def reorder(r: Request, x_fleet_key: str = Header('')):
        auth(x_fleet_key)
        d = await r.json()
        ids = [int(x) for x in (d.get('ids') or [])]
        by_id = {a['id']: a for a in st.assets}
        if v2 and sorted(ids) != sorted(by_id):
            raise HTTPException(409, 'Playlist changed, send the complete order')
        st.assets[:] = [by_id[i] for i in ids if i in by_id] + [a for a in st.assets if a['id'] not in ids]
        return {'ok': True}

    if not v2:
        return app

    @app.post('/api/fleet/v1/assets/grafana-tag')
    async def add_grafana_tag(r: Request, x_fleet_key: str = Header('')):
        auth(x_fleet_key)
        d = await r.json()
        if not str(d.get('name', '')).strip():
            raise HTTPException(400, 'Name must not be empty')
        a = {'id': new_id(), 'name': d['name'], 'kind': 'grafana-tag', 'source': json.dumps(grafana(d)),
             'duration': duration(d.get('duration', 60)), 'position': len(st.assets), 'auth_profile_id': None,
             'scale': scale(d.get('scale', 1))}
        st.assets.append(a)
        return {'ok': True, 'id': a['id']}

    @app.post('/api/fleet/v1/assets/upload')
    async def upload(r: Request, x_fleet_key: str = Header('')):
        auth(x_fleet_key)
        form = await r.form()
        f = form.get('file')
        ext = os.path.splitext(f.filename or '')[1].lower()
        kind = 'image' if ext in IMAGE_EXT else 'video' if ext in VIDEO_EXT else None
        if not kind:
            raise HTTPException(400, 'Unsupported file type')
        a = {'id': new_id(), 'name': form.get('name') or f.filename, 'kind': kind,
             'source': f'/media/x{st.next_id}{ext}', 'duration': duration(form.get('duration') or 15),
             'position': len(st.assets), 'auth_profile_id': None, 'scale': 1.0}
        st.files[a['id']] = await f.read()
        st.assets.append(a)
        return {'ok': True, 'id': a['id'], 'kind': kind}

    @app.get('/api/fleet/v1/assets/{ident}/file')
    def asset_file(ident: int, x_fleet_key: str = Header('')):
        auth(x_fleet_key)
        if ident not in st.files:
            raise HTTPException(404, 'No media file')
        return Response(st.files[ident], media_type='application/octet-stream')

    return app


app = create_app()
