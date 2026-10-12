"""Staged updates of CARACAL on Docker nodes.

A rollout updates its nodes in stages: first the pilot nodes, then the others in batches. A stage starts when the
previous one is done and its nodes stayed healthy for the waiting time (online, the new version running, no critical
problem); a failed update or an unhealthy node stops the rollout, so a bad version reaches only a few screens. A
stopped or paused rollout can be resumed. Nodes that are offline when their stage starts are skipped and reported.

Automatic updates: once a day at the chosen hour, when the registry has a newer version than some Docker nodes run,
the hub starts a rollout of that version for them (pilots from the chosen group first).
"""
import json
import threading
import time

from fastapi import APIRouter, HTTPException, Request

from .core import audit, cfg, current_user, db, new_batch, queue_command, save_cfg

router = APIRouter()
STATES = ('running', 'paused', 'stopped', 'done', 'cancelled')
AUTO_DEFAULTS = {'enabled': False, 'hour': 3, 'pilot_group': '', 'pilot_count': 1, 'batch': 5, 'wait': 30}
_lock = threading.Lock()


async def _json(r: Request):
    try:
        d = await r.json()
    except ValueError:
        raise HTTPException(400, 'invalid_json')
    if not isinstance(d, dict):
        raise HTTPException(400, 'invalid_json')
    return d


def _int(v, lo, hi, default):
    try:
        v = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def _public(row):
    r = dict(row)
    r['stages'] = json.loads(r.pop('stages_json') or '[]')
    r['log'] = json.loads(r.pop('log_json') or '[]')
    return r


def _log(c, rid, text, **extra):
    row = c.execute('SELECT log_json FROM rollouts WHERE id=?', (rid,)).fetchone()
    log = json.loads(row['log_json'] or '[]')[-99:]
    log.append({'ts': time.time(), 'text': text, **extra})
    c.execute('UPDATE rollouts SET log_json=?, updated=? WHERE id=?', (json.dumps(log), time.time(), rid))


def docker_nodes(c):
    from .devices import build
    return [r for r in c.execute('SELECT * FROM devices ORDER BY name').fetchall() if build(r)['runtime'] == 'docker']


def plan(ids, pilots, batch):
    """Stages: the pilots, then the others in batches."""
    pilots = [x for x in pilots if x in ids]
    rest = [x for x in ids if x not in pilots]
    stages = [pilots] if pilots else []
    stages += [rest[i:i + batch] for i in range(0, len(rest), batch)]
    return [{'devices': s, 'state': 'waiting', 'commands': {}, 'started': None, 'finished': None} for s in stages if s]


def create(c, username, version, ids, pilots, batch, wait, auto=False):
    from . import images
    if not images.VERSION_RE.fullmatch(version or ''):
        raise HTTPException(400, 'version_required')
    if not images.node_image():
        raise HTTPException(400, 'node_image_missing')
    known = {r['id'] for r in docker_nodes(c)}
    ids = [x for x in dict.fromkeys(ids) if x in known]
    if not ids:
        raise HTTPException(400, 'no_devices')
    if c.execute("SELECT 1 FROM rollouts WHERE state IN ('running','paused','stopped')").fetchone():
        raise HTTPException(409, 'rollout_running')
    stages = plan(ids, pilots, batch)
    now = time.time()
    rid = c.execute('INSERT INTO rollouts(version, image, state, stages_json, current, wait, auto, username, created, '
                    'updated, log_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (version, images.node_image(), 'running', json.dumps(stages), 0, wait, int(auto), username, now, now,
                     '[]')).lastrowid
    _log(c, rid, 'created', devices=len(ids), stages=len(stages))
    return rid


def _healthy(c, did, version):
    from .devices import build
    row = c.execute('SELECT * FROM devices WHERE id=?', (did,)).fetchone()
    if not row:
        return False, 'device_not_found'
    d = build(row)
    if not d['online']:
        return False, 'device_offline'
    if d['caracal_version'] != version:
        return False, 'version_mismatch'
    critical = [a['code'] for a in d['attention'] if a['level'] == 'critical']
    return (False, critical[0]) if critical else (True, '')


def advance(c, rid, now=None):
    """One step of a running rollout."""
    from .main import validate_command
    now = now or time.time()
    row = c.execute('SELECT * FROM rollouts WHERE id=?', (rid,)).fetchone()
    if not row or row['state'] != 'running':
        return
    stages = json.loads(row['stages_json'])
    i = row['current']
    if i >= len(stages):
        c.execute("UPDATE rollouts SET state='done', updated=? WHERE id=?", (now, rid))
        _log(c, rid, 'done')
        return
    st = stages[i]
    save = lambda **cols: c.execute(f"UPDATE rollouts SET stages_json=?, updated=?{''.join(f', {k}=?' for k in cols)} WHERE id=?",
                                    (json.dumps(stages), now, *cols.values(), rid))
    if st['state'] == 'waiting':
        batch, skipped = new_batch(), {}
        for did in st['devices']:
            r = c.execute('SELECT * FROM devices WHERE id=?', (did,)).fetchone()
            try:
                if not r:
                    raise HTTPException(404, 'device_not_found')
                payload = validate_command(c, r, 'update_caracal', {'version': row['version']})
                st['commands'][did] = queue_command(c, did, 'update_caracal', payload, f'rollout:{rid}', batch)
            except HTTPException as e:
                skipped[did] = e.detail
        st.update(state='updating', started=now, skipped=skipped)
        save()
        _log(c, rid, 'stage_started', stage=i, devices=len(st['commands']), skipped=skipped)
        return
    if st['state'] == 'updating':
        states = {did: c.execute('SELECT state, result FROM commands WHERE id=?', (cid,)).fetchone() for did, cid in st['commands'].items()}
        failed = {did: (s['result'] or s['state'])[:300] for did, s in states.items()
                  if s and s['state'] in ('failed', 'expired', 'timeout', 'cancelled', 'interrupted')}
        if failed:
            st.update(state='failed', finished=now, failed=failed)
            save(state='stopped')
            _log(c, rid, 'stage_failed', stage=i, failed=failed)
            return
        if all(s and s['state'] == 'completed' for s in states.values()):
            st.update(state='checking', finished=now)
            save()
            _log(c, rid, 'stage_updated', stage=i)
        return
    if st['state'] == 'checking':
        if now - st['finished'] < row['wait'] * 60:
            return
        bad = {}
        for did in st['commands']:
            ok, why = _healthy(c, did, row['version'])
            if not ok:
                bad[did] = why
        if bad:
            st.update(state='failed', unhealthy=bad)
            save(state='stopped')
            _log(c, rid, 'stage_unhealthy', stage=i, unhealthy=bad)
            return
        st['state'] = 'done'
        save(current=i + 1)
        _log(c, rid, 'stage_done', stage=i)


def auto_start(c, now=None):
    """Automatic updates: a rollout of the newest version, once a day at the configured hour."""
    from . import images
    from .schedule import local_now
    conf = {**AUTO_DEFAULTS, **(cfg().get('auto_update') or {})}
    if not conf['enabled']:
        return None
    now = now or time.time()
    local = local_now(now)
    today = local.strftime('%Y-%m-%d')
    if local.hour != conf['hour'] or cfg().get('auto_update_day') == today:
        return None
    full = cfg()
    full['auto_update_day'] = today
    save_cfg(full)
    version = images.latest_version()
    if not version or c.execute("SELECT 1 FROM rollouts WHERE state IN ('running','paused','stopped')").fetchone():
        return None
    from .devices import build
    nodes = [r for r in docker_nodes(c) if build(r)['caracal_version'] != version]
    if not nodes:
        return None
    pilots = [r['id'] for r in nodes if conf['pilot_group'] and r['device_group'] == conf['pilot_group']] \
        or [r['id'] for r in nodes[:conf['pilot_count']]]
    rid = create(c, 'auto-update', version, [r['id'] for r in nodes], pilots, conf['batch'], conf['wait'], auto=True)
    c.execute('INSERT INTO audit(username, action, target, detail, created) VALUES(?,?,?,?,?)',
              ('auto-update', 'rollout.create', str(rid), json.dumps({'version': version, 'devices': len(nodes)}), now))
    return rid


def tick(now=None):
    with _lock, db() as c:
        try:
            auto_start(c, now)
        except HTTPException:
            pass
        for row in c.execute("SELECT id FROM rollouts WHERE state='running'").fetchall():
            advance(c, row['id'], now)


# ---------------------------------------------------------------- API

@router.get('/api/rollouts')
def list_rollouts(r: Request):
    current_user(r)
    with db() as c:
        rows = [_public(x) for x in c.execute('SELECT * FROM rollouts ORDER BY id DESC LIMIT 20')]
    return {'rollouts': rows, 'auto': {**AUTO_DEFAULTS, **(cfg().get('auto_update') or {})}}


@router.post('/api/rollouts')
async def start_rollout(r: Request):
    u = current_user(r, 'manage')
    d = await _json(r)
    ids = [str(x) for x in d.get('device_ids') or []]
    pilots = [str(x) for x in d.get('pilot_ids') or []]
    with db() as c:
        rid = create(c, u['username'], str(d.get('version') or '').strip(), ids, pilots,
                     _int(d.get('batch'), 1, 500, 5), _int(d.get('wait'), 0, 1440, 30))
    audit(u, 'rollout.create', str(rid), {'version': d.get('version'), 'devices': len(ids), 'pilots': pilots})
    tick()
    return {'id': rid}


@router.post('/api/rollouts/{rid}/{action}')
def control_rollout(rid: int, action: str, r: Request):
    u = current_user(r, 'manage')
    if action not in ('pause', 'resume', 'cancel'):
        raise HTTPException(404, 'not_found')
    with db() as c:
        row = c.execute('SELECT * FROM rollouts WHERE id=?', (rid,)).fetchone()
        if not row:
            raise HTTPException(404, 'not_found')
        if row['state'] in ('done', 'cancelled'):
            raise HTTPException(409, 'rollout_finished')
        if action == 'resume':
            # a stopped stage is started again (its failed nodes are updated once more)
            stages = json.loads(row['stages_json'])
            if row['current'] < len(stages) and stages[row['current']]['state'] == 'failed':
                stages[row['current']].update(state='waiting', commands={})
            c.execute("UPDATE rollouts SET state='running', stages_json=?, updated=? WHERE id=?", (json.dumps(stages), time.time(), rid))
        else:
            c.execute('UPDATE rollouts SET state=?, updated=? WHERE id=?', ('paused' if action == 'pause' else 'cancelled', time.time(), rid))
        _log(c, rid, action, user=u['username'])
    audit(u, f'rollout.{action}', str(rid))
    if action == 'resume':
        tick()
    return {'ok': True}


@router.put('/api/rollouts/auto')
async def auto_settings(r: Request):
    u = current_user(r, 'manage')
    d = await _json(r)
    conf = {'enabled': bool(d.get('enabled')), 'hour': _int(d.get('hour'), 0, 23, 3),
            'pilot_group': str(d.get('pilot_group') or '').strip()[:120], 'pilot_count': _int(d.get('pilot_count'), 0, 50, 1),
            'batch': _int(d.get('batch'), 1, 500, 5), 'wait': _int(d.get('wait'), 0, 1440, 30)}
    full = cfg()
    full['auto_update'] = conf
    save_cfg(full)
    audit(u, 'rollout.auto', '', conf)
    return conf
