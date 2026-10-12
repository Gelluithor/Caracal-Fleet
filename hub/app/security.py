"""Two-factor sign-in (TOTP) and single sign-on (OpenID Connect).

TOTP (RFC 6238, the codes of authenticator apps): a user turns it on in their account; the secret is shown once as a
QR code, the first code confirms it and ten one-time recovery codes are issued. Signing in then needs the password and
a code (or a recovery code); a code is accepted only once. An administrator can turn it off for a user who lost the
phone.

Single sign-on: the hub is an OpenID Connect client (authorization code flow with PKCE). The user is identified by
the claim chosen in the settings (preferred_username or email) through the provider's userinfo endpoint; an existing
user of that name signs in, a new one is created with the default role when allowed. Users created this way have no
password of their own (they cannot sign in with one).
"""
import base64
import hashlib
import hmac
import io
import json
import secrets
import struct
import threading
import time
from urllib.parse import quote, urlencode

import requests
import segno
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from .core import ROLES, USERNAME_RE, audit, cfg, check_password, current_user, db, make_session, save_cfg

router = APIRouter()
STEP, DIGITS, WINDOW = 30, 6, 1
RECOVERY_CODES = 10
OIDC_DEFAULTS = {'enabled': False, 'issuer': '', 'client_id': '', 'client_secret': '', 'scopes': 'openid email profile',
                 'username_claim': 'preferred_username', 'default_role': 'viewer', 'auto_create': False,
                 'allowed_domains': '', 'label': '', 'public_url': ''}
SSO_PASSWORD = 'sso$'          # password hash of users created by single sign-on: never matches a password
_states = {}                   # state -> (code verifier, nonce, expiry)
_discovery = {}                # issuer -> (time, document)
_lock = threading.Lock()


# ---------------------------------------------------------------- TOTP

def totp_code(secret, counter):
    key = base64.b32decode(secret + '=' * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack('>Q', counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** DIGITS).zfill(DIGITS)


def totp_match(secret, code, now=None, last=0):
    """The time step of a valid code (newer than the last one used), or None."""
    code = ''.join(ch for ch in str(code or '') if ch.isdigit())
    if len(code) != DIGITS or not secret:
        return None
    step = int((now if now is not None else time.time()) // STEP)
    for counter in range(step - WINDOW, step + WINDOW + 1):
        if counter > (last or 0) and hmac.compare_digest(totp_code(secret, counter), code):
            return counter
    return None


def _recovery_hash(code):
    return hashlib.sha256(code.replace('-', '').strip().lower().encode()).hexdigest()


def check_second_factor(c, u, code):
    """True when the code (TOTP or a recovery code) is valid for the user; a used code is spent."""
    counter = totp_match(u['totp_secret'], code, last=u['totp_last'] or 0)
    if counter:
        c.execute('UPDATE users SET totp_last=? WHERE id=?', (counter, u['id']))
        return True
    hashes = json.loads(u['totp_recovery'] or '[]')
    h = _recovery_hash(str(code or ''))
    if str(code or '').strip() and h in hashes:
        hashes.remove(h)
        c.execute('UPDATE users SET totp_recovery=? WHERE id=?', (json.dumps(hashes), u['id']))
        return True
    return False


def _qr_svg(uri):
    # a viewBox instead of a fixed size, so the page can scale it; four modules of white border (quiet zone)
    out = io.BytesIO()
    segno.make(uri, error='m').save(out, kind='svg', border=4, dark='#000000', light='#ffffff', xmldecl=False,
                                    svgns=True, omitsize=True)
    return out.getvalue().decode()


@router.post('/api/me/totp/setup')
def totp_setup(r: Request):
    u = current_user(r)
    if u['password_hash'].startswith(SSO_PASSWORD):
        raise HTTPException(409, 'sso_user')
    secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')
    with db() as c:
        c.execute('UPDATE users SET totp_pending=? WHERE id=?', (secret, u['id']))
    issuer = 'CARACAL Fleet'
    uri = f'otpauth://totp/{quote(issuer)}:{quote(u["username"])}?' + urlencode(
        {'secret': secret, 'issuer': issuer, 'digits': DIGITS, 'period': STEP})
    return {'secret': secret, 'uri': uri, 'qr': _qr_svg(uri)}


@router.post('/api/me/totp/enable')
async def totp_enable(r: Request):
    u = current_user(r)
    d = await _json(r)
    with db() as c:
        row = c.execute('SELECT totp_pending FROM users WHERE id=?', (u['id'],)).fetchone()
        counter = totp_match(row['totp_pending'], d.get('code'))
        if not row['totp_pending'] or not counter:
            raise HTTPException(400, 'invalid_totp')
        codes = ['-'.join([secrets.token_hex(2), secrets.token_hex(2)]) for _ in range(RECOVERY_CODES)]
        c.execute("UPDATE users SET totp_secret=totp_pending, totp_pending='', totp_last=?, totp_recovery=? WHERE id=?",
                  (counter, json.dumps([_recovery_hash(x) for x in codes]), u['id']))
    audit(u, 'user.totp_enable', u['username'])
    return {'ok': True, 'recovery_codes': codes}


@router.post('/api/me/totp/disable')
async def totp_disable(r: Request):
    u = current_user(r)
    d = await _json(r)
    if not check_password(str(d.get('password', '')), u['password_hash']):
        raise HTTPException(401, 'invalid_credentials')
    with db() as c:
        c.execute("UPDATE users SET totp_secret='', totp_pending='', totp_recovery='', totp_last=0 WHERE id=?", (u['id'],))
    audit(u, 'user.totp_disable', u['username'])
    return {'ok': True}


@router.post('/api/users/{uid}/totp/reset')
def totp_reset(uid: int, r: Request):
    u = current_user(r, 'admin')
    with db() as c:
        row = c.execute('SELECT username FROM users WHERE id=?', (uid,)).fetchone()
        if not row:
            raise HTTPException(404, 'not_found')
        c.execute("UPDATE users SET totp_secret='', totp_pending='', totp_recovery='', totp_last=0 WHERE id=?", (uid,))
    audit(u, 'user.totp_reset', row['username'])
    return {'ok': True}


# ---------------------------------------------------------------- single sign-on

async def _json(r: Request):
    try:
        d = await r.json()
    except ValueError:
        raise HTTPException(400, 'invalid_json')
    if not isinstance(d, dict):
        raise HTTPException(400, 'invalid_json')
    return d


def oidc_conf():
    return {**OIDC_DEFAULTS, **(cfg().get('oidc') or {})}


def public_sso():
    o = oidc_conf()
    return {'enabled': bool(o['enabled'] and o['issuer'] and o['client_id']), 'label': o['label']}


def discovery(issuer, force=False):
    issuer = issuer.rstrip('/')
    with _lock:
        hit = _discovery.get(issuer)
        if hit and not force and time.time() - hit[0] < 3600:
            return hit[1]
    try:
        doc = requests.get(issuer + '/.well-known/openid-configuration', timeout=10).json()
    except (requests.RequestException, ValueError) as e:
        raise HTTPException(502, f'sso_discovery_failed: {e}'[:300])
    if not all(doc.get(k) for k in ('authorization_endpoint', 'token_endpoint', 'userinfo_endpoint')):
        raise HTTPException(502, 'sso_discovery_failed')
    with _lock:
        _discovery[issuer] = (time.time(), doc)
    return doc


def _base(r: Request, o):
    if o['public_url']:
        return o['public_url'].rstrip('/')
    proto = r.headers.get('X-Forwarded-Proto', r.url.scheme).split(',')[0].strip()
    host = r.headers.get('X-Forwarded-Host', r.headers.get('Host', r.url.netloc)).split(',')[0].strip()
    return f'{proto}://{host}'


def _fail(code):
    return RedirectResponse('/#sso_error=' + quote(code), status_code=303)


@router.get('/api/sso/login')
def sso_login(r: Request):
    o = oidc_conf()
    if not public_sso()['enabled']:
        raise HTTPException(404, 'not_found')
    doc = discovery(o['issuer'])
    state, verifier, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(48), secrets.token_urlsafe(16)
    now = time.time()
    with _lock:
        for k in [k for k, v in _states.items() if v[2] < now]:
            del _states[k]
        _states[state] = (verifier, nonce, now + 600)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    q = {'response_type': 'code', 'client_id': o['client_id'], 'redirect_uri': _base(r, o) + '/api/sso/callback',
         'scope': o['scopes'] or 'openid', 'state': state, 'nonce': nonce, 'code_challenge': challenge,
         'code_challenge_method': 'S256'}
    return RedirectResponse(doc['authorization_endpoint'] + ('&' if '?' in doc['authorization_endpoint'] else '?') + urlencode(q),
                            status_code=303)


def _username_from(o, claims):
    raw = str(claims.get(o['username_claim']) or claims.get('preferred_username') or claims.get('email') or '').strip()
    if o['username_claim'] == 'email' or ('@' in raw and o['username_claim'] != 'preferred_username'):
        raw = raw.lower()
    return raw[:64]


@router.get('/api/sso/callback')
def sso_callback(r: Request, code: str = '', state: str = '', error: str = ''):
    o = oidc_conf()
    if error:
        return _fail(error[:60])
    with _lock:
        st = _states.pop(state, None)
    if not st or st[2] < time.time() or not code:
        return _fail('sso_state')
    try:
        doc = discovery(o['issuer'])
        token = requests.post(doc['token_endpoint'], timeout=15, data={
            'grant_type': 'authorization_code', 'code': code, 'redirect_uri': _base(r, o) + '/api/sso/callback',
            'client_id': o['client_id'], 'client_secret': o['client_secret'], 'code_verifier': st[0]}).json()
        access = token.get('access_token')
        if not access:
            return _fail('sso_token')
        claims = requests.get(doc['userinfo_endpoint'], headers={'Authorization': 'Bearer ' + access}, timeout=15).json()
    except (requests.RequestException, ValueError, HTTPException):
        return _fail('sso_provider')
    if not isinstance(claims, dict) or not claims.get('sub'):
        return _fail('sso_token')
    domains = [x.strip().lower().lstrip('@') for x in o['allowed_domains'].split(',') if x.strip()]
    email = str(claims.get('email') or '').lower()
    if domains and (not email or email.rsplit('@', 1)[-1] not in domains or claims.get('email_verified') is False):
        return _fail('sso_domain')
    username = _username_from(o, claims)
    if not USERNAME_RE.fullmatch(username or ''):
        return _fail('sso_username')
    subject = f"{o['issuer'].rstrip('/')}|{claims['sub']}"
    created = None
    with db() as c:
        u = c.execute('SELECT * FROM users WHERE sso_subject=?', (subject,)).fetchone() \
            or c.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        if not u and o['auto_create']:
            created = o['default_role'] if o['default_role'] in ROLES else 'viewer'
            c.execute("INSERT INTO users(username, password_hash, role, language, enabled, created, sso_subject) "
                      "VALUES(?,?,?,'cs',1,?,?)", (username, SSO_PASSWORD + secrets.token_hex(16), created, time.time(), subject))
            u = c.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        if u and u['enabled'] and u['sso_subject'] != subject:
            c.execute('UPDATE users SET sso_subject=? WHERE id=?', (subject, u['id']))
    if not u:
        audit(None, 'user.sso_unknown', username)
        return _fail('sso_no_user')
    if not u['enabled']:
        return _fail('sso_disabled')
    if created:
        audit({'username': username}, 'user.sso_create', username, {'role': created})
    audit(dict(u), 'user.login', u['username'], {'sso': True})
    return RedirectResponse('/#sso=' + quote(make_session(dict(u))), status_code=303)


def _public_oidc(o):
    return {**o, 'client_secret': '•••' if o['client_secret'] else ''}


@router.get('/api/sso/settings')
def get_sso(r: Request):
    current_user(r, 'admin')
    return {**_public_oidc(oidc_conf()), 'redirect_uri': _base(r, oidc_conf()) + '/api/sso/callback'}


@router.put('/api/sso/settings')
async def put_sso(r: Request):
    u = current_user(r, 'admin')
    d = await _json(r)
    old = oidc_conf()
    o = {k: d.get(k, old[k]) for k in OIDC_DEFAULTS}
    o = {**o, 'enabled': bool(o['enabled']), 'auto_create': bool(o['auto_create'])}
    for k in ('issuer', 'client_id', 'scopes', 'username_claim', 'allowed_domains', 'label', 'public_url'):
        o[k] = str(o[k] or '').strip()[:500]
    if o['client_secret'] == '•••':
        o['client_secret'] = old['client_secret']
    o['client_secret'] = str(o['client_secret'] or '')[:500]
    if o['default_role'] not in ROLES:
        raise HTTPException(400, 'invalid_value')
    if o['username_claim'] not in ('preferred_username', 'email', 'upn', 'name', 'sub'):
        raise HTTPException(400, 'invalid_value')
    for k in ('issuer', 'public_url'):
        if o[k] and not o[k].lower().startswith(('https://', 'http://')):
            raise HTTPException(400, 'invalid_url')
    if o['enabled']:
        if not o['issuer'] or not o['client_id']:
            raise HTTPException(400, 'sso_incomplete')
        discovery(o['issuer'], force=True)
    conf = cfg()
    conf['oidc'] = o
    save_cfg(conf)
    audit(u, 'system.sso', o['issuer'], {'enabled': o['enabled'], 'auto_create': o['auto_create'], 'role': o['default_role']})
    return _public_oidc(o)
