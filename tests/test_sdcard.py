"""Zero-touch SD cards: generated boot partition files, validation, node configuration and token rotation."""
import io
import json
import shutil
import subprocess
import sys
import zipfile

import pytest
import requests

from conftest import ROOT, serve

PW = 'admin-password-123'
DIETPI_TXT = """# DietPi-Automation settings
AUTO_SETUP_LOCALE=C.UTF-8
AUTO_SETUP_NET_HOSTNAME=DietPi
AUTO_SETUP_AUTOMATED=0
AUTO_SETUP_GLOBAL_PASSWORD=dietpi
#AUTO_SETUP_TIMEZONE=Europe/London
AUTO_SETUP_CUSTOM_SCRIPT_EXEC=0
CONFIG_SERIAL_CONSOLE_ENABLE=1
"""


@pytest.fixture(scope='module')
def env(hub_app):
    url, _ = serve(hub_app)
    login = lambda u, p: {'Authorization': 'Bearer ' + requests.post(url + '/api/login', json={'username': u, 'password': p}).json()['token']}
    h = login('admin', PW)
    requests.post(url + '/api/users', headers=h, json={'username': 'sd-manager', 'password': 'manager-pass-123', 'role': 'manager'})
    requests.put(url + '/api/node-image', headers=h, json={'image': 'ghcr.io/example/caracal-node'})
    return {'url': url, 'h': h, 'manager': login('sd-manager', 'manager-pass-123')}


def card(env, ok=True, headers=None, **body):
    data = {'os': 'raspios', 'hub_url': env['url'], 'name_prefix': 'lobby', **body}
    r = requests.post(env['url'] + '/api/sdcard', headers=headers or env['h'], json=data, timeout=30)
    if not ok:
        return r
    assert r.status_code == 200, r.text
    z = zipfile.ZipFile(io.BytesIO(r.content))
    return {i.filename: z.read(i).decode() for i in z.infolist()}, {i.filename: i.external_attr >> 16 for i in z.infolist()}


def test_raspberry_pi_os_card(env):
    files, modes = card(env, timezone='Europe/Prague', wifi_ssid='Office "5G"', wifi_password='wifi-pass-1',
                        wifi_country='cz', user='maint', password='device-pass-1', ssh_key='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample user@pc')
    assert set(files) == {'user-data', 'network-config', 'caracal-firstboot.sh', 'caracal-firstboot.conf', 'CARACAL-README.txt'}
    ud = files['user-data']
    assert ud.startswith('#cloud-config\n') and 'hostname: "lobby"' in ud and 'timezone: "Europe/Prague"' in ud
    assert '- name: "maint"' in ud and 'plain_text_passwd: "device-pass-1"' in ud and 'ssh_pwauth: true' in ud
    assert '[bash, /boot/firmware/caracal-firstboot.sh, install]' in ud and '[systemctl, enable, --now, ssh]' in ud
    nc = files['network-config']
    assert '"Office \\"5G\\"":' in nc and 'password: "wifi-pass-1"' in nc and 'regulatory-domain: "CZ"' in nc
    conf = files['caracal-firstboot.conf']
    assert f"HUB={env['url']}" in conf and 'TOKEN=enroll-test-token' in conf and 'NAME_PREFIX=lobby' in conf
    assert modes['caracal-firstboot.sh'] == 0o755
    assert files['caracal-firstboot.sh'] == (ROOT / 'hub/bootstrap/caracal-firstboot.sh').read_text(encoding='utf-8')


def test_card_without_login_locks_the_account(env):
    files, _ = card(env)
    ud = files['user-data']
    assert 'lock_passwd: true' in ud and 'ssh_pwauth: false' in ud and 'plain_text_passwd' not in ud
    assert 'enable, --now, ssh' not in ud and 'network-config' not in files


def test_dietpi_card(env):
    files, _ = card(env, os='dietpi', dietpi_txt=DIETPI_TXT.replace('\n', '\r\n'), timezone='Europe/Prague',
                    wifi_ssid="Bob's net", wifi_password='wifi-pass-1', wifi_country='CZ')
    assert {'dietpi.txt', 'dietpi-wifi.txt', 'Automation_Custom_Script.sh'} <= set(files)
    txt = files['dietpi.txt']
    assert '\r' not in txt and 'AUTO_SETUP_AUTOMATED=1' in txt and 'AUTO_SETUP_NET_HOSTNAME=lobby' in txt
    assert 'AUTO_SETUP_TIMEZONE=Europe/Prague' in txt and '#AUTO_SETUP_TIMEZONE' not in txt
    assert 'AUTO_SETUP_GLOBAL_PASSWORD=dietpi' not in txt and 'AUTO_SETUP_GLOBAL_PASSWORD=' in txt   # random password
    assert 'AUTO_SETUP_NET_WIFI_ENABLED=1' in txt and 'CONFIG_SERIAL_CONSOLE_ENABLE=1' in txt
    assert txt.count('AUTO_SETUP_CUSTOM_SCRIPT_EXEC=0') == 1
    assert "aWIFI_SSID[0]='Bob'\\''s net'" in files['dietpi-wifi.txt']
    assert 'caracal-firstboot.sh" install' in files['Automation_Custom_Script.sh']


@pytest.mark.parametrize('body,error', [
    ({'os': 'windows'}, 'invalid_os'),
    ({'hub_url': 'ftp://hub'}, 'invalid_hub_url'),
    ({'hub_url': 'https://hub/$(reboot)'}, 'invalid_hub_url'),
    ({'name_prefix': 'Bad Name'}, 'invalid_name_prefix'),
    ({'wifi_ssid': 'net', 'wifi_password': 'short', 'wifi_country': 'CZ'}, 'invalid_wifi_password'),
    ({'wifi_ssid': 'net', 'wifi_password': 'long-enough', 'wifi_country': ''}, 'invalid_wifi_country'),
    ({'user': 'caracal'}, 'invalid_user'),
    ({'password': 'long-password\nroot'}, 'invalid_password'),
    ({'timezone': 'Europe/\nPrague'}, 'invalid_value'),
    ({'ssh_key': 'not a key'}, 'invalid_ssh_key'),
    ({'os': 'dietpi'}, 'invalid_dietpi_txt'),
    ({'os': 'dietpi', 'dietpi_txt': 'hello'}, 'invalid_dietpi_txt'),
])
def test_validation(env, body, error):
    r = card(env, ok=False, **body)
    assert r.status_code == 400 and r.json()['detail'] == error


def test_only_admin_gets_the_token(env):
    assert card(env, ok=False, headers=env['manager']).status_code == 403


def test_node_config_and_firstboot_script_are_public(env):
    cfg = requests.get(env['url'] + '/api/bootstrap/node-config').text
    assert 'image=ghcr.io/example/caracal-node\n' in cfg and 'version=' in cfg
    assert requests.get(env['url'] + '/api/bootstrap/caracal-firstboot.sh').status_code == 200


def test_enroll_token_rotation(env, monkeypatch):
    url, h = env['url'], env['h']
    r = requests.post(url + '/api/settings/enroll-token', headers=h)
    assert r.status_code == 409 and r.json()['detail'] == 'enroll_token_from_env'
    monkeypatch.delenv('CARACAL_HUB_ENROLL_TOKEN')
    assert requests.post(url + '/api/settings/enroll-token', headers=env['manager']).status_code == 403
    new = requests.post(url + '/api/settings/enroll-token', headers=h).json()['enroll_token']
    try:
        assert new != 'enroll-test-token' and requests.get(url + '/api/settings', headers=h).json()['enroll_token'] == new
        enroll = lambda tok: requests.post(url + '/api/device/enroll', json={'enroll_token': tok, 'fingerprint': 'sd-1'})
        assert enroll('enroll-test-token').status_code == 401 and enroll(new).status_code == 200
    finally:
        from app.core import cfg, save_cfg
        save_cfg({**cfg(), 'enroll_token': 'enroll-test-token'})


@pytest.mark.skipif(not shutil.which('bash'), reason='bash not available')
def test_firstboot_script_syntax():
    script = ROOT / 'hub' / 'bootstrap' / 'caracal-firstboot.sh'
    assert subprocess.run([shutil.which('bash'), '-n', str(script)]).returncode == 0


def test_dietpi_card_details(env):
    # current DietPi (v10): Wi-Fi replaces Ethernet, only real dietpi.txt keys, Dropbear's 100 byte password limit
    files, _ = card(env, os='dietpi', dietpi_txt=DIETPI_TXT, wifi_ssid='net', wifi_password='wifi-pass-1',
                    wifi_country='CZ', password='device-pass-1')
    txt = files['dietpi.txt']
    assert 'AUTO_SETUP_NET_ETHERNET_ENABLED=0' in txt and 'AUTO_SETUP_GLOBAL_PASSWORD=device-pass-1' in txt
    assert 'AUTO_SETUP_ACCEPT_LICENSE' not in txt
    assert 'reboots once' in files['CARACAL-README.txt']
    wired, _ = card(env, os='dietpi', dietpi_txt=DIETPI_TXT)
    assert 'AUTO_SETUP_NET_ETHERNET_ENABLED' not in wired['dietpi.txt']
    r = card(env, ok=False, os='dietpi', dietpi_txt=DIETPI_TXT, password='ž' * 51)   # 102 bytes
    assert r.status_code == 400 and r.json()['detail'] == 'invalid_password'


@pytest.mark.skipif(not shutil.which('bash'), reason='bash not available')
def test_install_node_enables_kms_on_raspberry_pi(tmp_path):
    """The KMS step of install-node.sh (paths moved into tmp_path): DietPi ships without KMS, so the player would have
    no /dev/dri; the installer enables it and asks for a reboot (exit 5) before converting anything."""
    src = (ROOT / 'hub' / 'bootstrap' / 'install-node.sh').read_text(encoding='utf-8')
    start = src.index('# Raspberry Pi: the player and the overlay need')
    end = src.index("step '[4/7]")
    snippet = src[start:end]
    for old, new in (('/proc/device-tree/model', 'model'), ('/boot/firmware/config.txt', 'fw/config.txt'),
                     ('/boot/config.txt', 'config.txt'), ('/boot/dietpi/func/dietpi-set_hardware', 'set_hardware'),
                     ('/dev/dri', 'dri')):
        snippet = snippet.replace(old, f'{tmp_path}/{new}')
    (tmp_path / 'snippet.sh').write_text(snippet)
    run = lambda: subprocess.run([shutil.which('bash'), str(tmp_path / 'snippet.sh')], capture_output=True, text=True)
    (tmp_path / 'fw').mkdir()
    (tmp_path / 'model').write_bytes(b'Raspberry Pi 4 Model B Rev 1.5\0')
    (tmp_path / 'fw' / 'config.txt').write_text('#dtoverlay=vc4-kms-v3d,noaudio\n')
    r = run()   # Raspberry Pi OS style config without DietPi tools: the overlay is appended
    assert r.returncode == 5 and 'REBOOT REQUIRED' in r.stdout
    assert (tmp_path / 'fw' / 'config.txt').read_text().endswith('[all]\ndtoverlay=vc4-kms-v3d\n')
    (tmp_path / 'dri').mkdir()   # after the reboot
    assert run().returncode == 0
    (tmp_path / 'dri').rmdir()
    (tmp_path / 'model').unlink()   # a PC without a graphics device
    assert run().returncode == 6


def test_docker_address_range(env):
    files, _ = card(env, docker_pool='10.200.5.0/16')
    assert 'DOCKER_POOL=10.200.0.0/16\n' in files['caracal-firstboot.conf']   # normalized
    assert 'DOCKER_POOL' not in card(env)[0]['caracal-firstboot.conf']
    for bad in ('8.8.0.0/16', '10.0.0.0/8', '192.168.1.0/24', 'fd00::/64', '10.200.0.0/16; reboot', 'x'):
        r = card(env, ok=False, docker_pool=bad)
        assert r.status_code == 400 and r.json()['detail'] in ('invalid_docker_pool', 'invalid_value'), bad


def test_install_node_writes_docker_networks(tmp_path):
    """The daemon.json part of install-node.sh: bridge in the first half, address pool in the second, other keys kept."""
    src = (ROOT / 'hub' / 'bootstrap' / 'install-node.sh').read_text(encoding='utf-8')
    code = src[src.index("<<'PY'\n") + 7:src.index('\nPY\n')].replace('/etc/docker/daemon.json', str(tmp_path / 'daemon.json'))
    (tmp_path / 'daemon.py').write_text(code)
    run = lambda pool: subprocess.run([sys.executable, str(tmp_path / 'daemon.py'), pool], capture_output=True, text=True)
    (tmp_path / 'daemon.json').write_text('{"log-driver": "journald"}')
    r = run('10.200.0.0/16')
    assert r.returncode == 0 and r.stdout.strip() == 'changed'
    assert json.loads((tmp_path / 'daemon.json').read_text()) == {
        'log-driver': 'journald', 'bip': '10.200.0.1/24', 'default-address-pools': [{'base': '10.200.128.0/17', 'size': 24}]}
    assert run('10.200.0.0/16').stdout == ''          # unchanged: Docker is not restarted again
    assert run('192.168.0.0/23').returncode == 0
    assert json.loads((tmp_path / 'daemon.json').read_text())['default-address-pools'] == [{'base': '192.168.1.0/24', 'size': 24}]
    assert run('8.8.0.0/16').returncode != 0 and run('10.0.0.0/24').returncode != 0
