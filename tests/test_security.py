"""Two-factor sign-in (TOTP, recovery codes) and single sign-on (OpenID Connect)."""
import time
from urllib.parse import parse_qs, unquote, urlsplit

from fastapi import FastAPI, Form, Request

from conftest import serve


def login(client, username, password, **extra):
    return client.post('/api/login', json={'username': username, 'password': password, **extra})


def test_totp_codes_follow_rfc_6238():
    import base64
    from app.security import totp_code, totp_match
    secret = base64.b32encode(b'12345678901234567890').decode()
    assert totp_code(secret, 59 // 30) == '287082'            # RFC 6238 test vector (last 6 of 94287082)
    assert totp_code(secret, 1111111109 // 30) == '081804'
    assert totp_match(secret, '287 082', now=59) == 1 and totp_match(secret, '287082', now=59, last=1) is None
    assert totp_match(secret, '287082', now=59 + 30) == 1      # one step of clock difference is accepted
    assert totp_match(secret, '287082', now=59 + 90) is None


def test_two_factor_sign_in(client, admin_headers):
    from app.security import totp_code
    h = admin_headers
    client.post('/api/users', headers=h, json={'username': 'twofa', 'password': 'twofa-password-1', 'role': 'operator'})
    token = login(client, 'twofa', 'twofa-password-1').json()['token']
    uh = {'Authorization': 'Bearer ' + token}
    setup = client.post('/api/me/totp/setup', headers=uh).json()
    assert setup['uri'].startswith('otpauth://totp/') and '<svg' in setup['qr']
    assert client.post('/api/me/totp/enable', headers=uh, json={'code': '000000'}).json()['detail'] == 'invalid_totp'
    step = int(time.time() // 30)
    r = client.post('/api/me/totp/enable', headers=uh, json={'code': totp_code(setup['secret'], step)}).json()
    codes = r['recovery_codes']
    assert len(codes) == 10
    assert client.get('/api/me', headers=uh).json()['totp'] is True

    assert login(client, 'twofa', 'twofa-password-1').json()['detail'] == 'totp_required'
    assert login(client, 'twofa', 'wrong-password').json()['detail'] == 'invalid_credentials'
    assert login(client, 'twofa', 'twofa-password-1', code='123456').json()['detail'] == 'invalid_totp'
    assert login(client, 'twofa', 'twofa-password-1', code=totp_code(setup['secret'], step)).json()['detail'] == 'invalid_totp'  # used
    ok = login(client, 'twofa', 'twofa-password-1', code=totp_code(setup['secret'], step + 1))
    assert ok.status_code == 200, ok.text
    assert login(client, 'twofa', 'twofa-password-1', code=codes[0]).status_code == 200      # a recovery code once
    assert login(client, 'twofa', 'twofa-password-1', code=codes[0]).json()['detail'] == 'invalid_totp'
    users = {x['username']: x for x in client.get('/api/users', headers=h).json()}
    assert users['twofa']['totp']
    # an administrator turns it off for a lost phone; the user turns it off with the password
    uid = users['twofa']['id']
    assert client.post(f'/api/users/{uid}/totp/reset', headers=h).status_code == 200
    token = login(client, 'twofa', 'twofa-password-1').json()['token']
    uh = {'Authorization': 'Bearer ' + token}
    setup = client.post('/api/me/totp/setup', headers=uh).json()
    client.post('/api/me/totp/enable', headers=uh, json={'code': totp_code(setup['secret'], int(time.time() // 30))})
    assert client.post('/api/me/totp/disable', headers=uh, json={'password': 'nope'}).status_code == 401
    assert client.post('/api/me/totp/disable', headers=uh, json={'password': 'twofa-password-1'}).status_code == 200
    assert login(client, 'twofa', 'twofa-password-1').status_code == 200
    # the operator cannot reset others
    assert client.post(f'/api/users/{uid}/totp/reset', headers=uh).status_code == 403


def fake_provider(claims):
    app = FastAPI()
    seen = {}

    @app.get('/.well-known/openid-configuration')
    def disc(r: Request):
        base = str(r.base_url).rstrip('/')
        return {'issuer': base, 'authorization_endpoint': base + '/authorize', 'token_endpoint': base + '/token',
                'userinfo_endpoint': base + '/userinfo'}

    @app.post('/token')
    def token(code: str = Form(...), code_verifier: str = Form(...), client_secret: str = Form(''), redirect_uri: str = Form(...)):
        seen.update(code=code, verifier=code_verifier, secret=client_secret, redirect=redirect_uri)
        return {'access_token': 'at-' + code, 'token_type': 'Bearer'} if code != 'bad' else {'error': 'invalid_grant'}

    @app.get('/userinfo')
    def userinfo(r: Request):
        return claims[r.headers['Authorization'].removeprefix('Bearer at-')]
    return app, seen


def test_single_sign_on(client, admin_headers):
    import base64
    import hashlib
    h = admin_headers
    claims = {'anna': {'sub': 'u-1', 'preferred_username': 'anna.sso', 'email': 'anna@example.com'},
              'eve': {'sub': 'u-2', 'preferred_username': 'eve', 'email': 'eve@evil.test'},
              'admin': {'sub': 'u-3', 'preferred_username': 'admin', 'email': 'admin@example.com'}}
    app, seen = fake_provider(claims)
    issuer, _ = serve(app)
    assert client.get('/api/public').json()['sso']['enabled'] is False
    assert client.put('/api/sso/settings', headers=h, json={'enabled': True, 'issuer': ''}).json()['detail'] == 'sso_incomplete'
    r = client.put('/api/sso/settings', headers=h, json={'enabled': True, 'issuer': issuer, 'client_id': 'fleet', 'client_secret': 's3cret',
                                                       'label': 'Firma', 'allowed_domains': 'example.com', 'default_role': 'operator'})
    assert r.status_code == 200 and r.json()['client_secret'] == '•••'
    assert client.get('/api/public').json()['sso'] == {'enabled': True, 'label': 'Firma'}

    def flow(code):
        start = client.get('/api/sso/login', follow_redirects=False)
        q = parse_qs(urlsplit(start.headers['location']).query)
        assert q['code_challenge_method'] == ['S256'] and q['client_id'] == ['fleet']
        back = client.get(f"/api/sso/callback?code={code}&state={q['state'][0]}", follow_redirects=False)
        expected = base64.urlsafe_b64encode(hashlib.sha256(seen.get('verifier', '').encode()).digest()).decode().rstrip('=')
        return back.headers['location'], q, expected

    loc, q, expected = flow('anna')
    assert loc.startswith('/#sso_error=sso_no_user')            # no automatic accounts yet
    assert seen['secret'] == 's3cret' and q['code_challenge'] == [expected] and seen['redirect'].endswith('/api/sso/callback')
    client.put('/api/sso/settings', headers=h, json={'auto_create': True, 'client_secret': '•••'})
    loc, _, _ = flow('anna')
    token = unquote(loc.split('#sso=', 1)[1])
    me = client.get('/api/me', headers={'Authorization': 'Bearer ' + token}).json()
    assert me['username'] == 'anna.sso' and me['role'] == 'operator' and me['sso'] is True
    assert login(client, 'anna.sso', 'sso$').status_code == 401   # no password of their own
    assert flow('eve')[0] == '/#sso_error=sso_domain'
    assert flow('bad')[0] == '/#sso_error=sso_token'
    loc, _, _ = flow('admin')                                    # an existing account signs in (and keeps its role)
    me = client.get('/api/me', headers={'Authorization': 'Bearer ' + unquote(loc.split('#sso=', 1)[1])}).json()
    assert me['username'] == 'admin' and me['role'] == 'admin'
    assert client.get('/api/sso/callback?code=anna&state=forged', follow_redirects=False).headers['location'] == '/#sso_error=sso_state'
    client.put('/api/sso/settings', headers=h, json={'enabled': False})
    assert client.get('/api/sso/login', follow_redirects=False).status_code == 404
