"""Zero-touch SD cards and USB sticks: files for a freshly written Raspberry Pi OS Lite or DietPi card, or for
installing a PC with Alpine Linux or Ubuntu Server.

On the first boot the card connects to the network, downloads the installer from the hub and turns the device
into a CARACAL node on Docker that enrolls itself (hub/bootstrap/caracal-firstboot.sh). The enrollment token is
part of the files; the script removes it from the boot partition on the first boot. A lost card is handled by
rotating the token in Settings.

Raspberry Pi OS (Trixie and newer) is configured with cloud-init (user-data, network-config), DietPi with its own
dietpi.txt (patched copy of the one from the card), dietpi-wifi.txt and Automation_Custom_Script.sh.

PCs (x86_64, arm64):
- Alpine Linux: caracal.apkovl.tar.gz, an overlay the live system of the Alpine ISO loads from a USB stick; it
  installs Alpine on the internal disk and then CARACAL (hub/bootstrap/caracal-alpine.sh).

Raspberry Pi with Alpine Linux: a whole SD card image (.img.gz for Raspberry Pi Imager or balenaEtcher). Its only
partition (FAT32, 512 MiB) holds Alpine for the Raspberry Pi (downloaded from Alpine's mirror and checked against its
SHA-256), the overlay and usercfg.txt (KMS graphics). The live system creates the system partition in the free space
of the card, installs Alpine on it (sys mode) and continues like on a PC.
- Ubuntu Server: user-data and meta-data of the autoinstall for a USB stick named CIDATA; the installed system runs
  caracal-firstboot.sh through cloud-init.
A node template (hub/app/templates.py) chosen for the card is applied by the hub once the new node reports.
"""
import base64
import io
import ipaddress
import json
import os
import re
import secrets
import shlex
import tarfile
import time
import zipfile

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response

from . import images, templates
from .core import ADMIN_USER_RE, BOOT, DATA, audit, cfg, current_user, save_cfg

router = APIRouter()

HUB_URL_RE = re.compile(r'^https?://[A-Za-z0-9.\-]+(:\d{1,5})?(/[A-Za-z0-9._~/-]*)?$')
PREFIX_RE = re.compile(r'^[a-z0-9][a-z0-9-]{0,30}$')
USER_RE = re.compile(r'^[a-z_][a-z0-9_-]{0,31}$')
TZ_RE = re.compile(r'^[A-Za-z0-9_+\-/]{1,64}$')
COUNTRY_RE = re.compile(r'^[A-Z]{2}$')
SSH_KEY_RE = re.compile(r'^(ssh-(ed25519|rsa)|ecdsa-sha2-nistp(256|384|521)|sk-[a-z0-9@.-]+) [A-Za-z0-9+/=]{16,}( [^\r\n]{0,200})?$')
RESERVED_USERS = {'root', 'caracal', 'daemon', 'nobody'}
DIETPI_TXT_MAX = 256 * 1024
OS_NAMES = ('raspios', 'dietpi', 'alpine', 'ubuntu', 'alpine-rpi')
PC_OS = ('alpine', 'ubuntu')
ALPINE_MIRROR = 'https://dl-cdn.alpinelinux.org/alpine'
ALPINE_CACHE = DATA / 'proxy-cache' / 'alpine-rpi'
IMAGE_MIB, PART_START = 512, 2048          # the FAT32 partition: 512 MiB from sector 2048 (1 MiB)
DISK_RE = re.compile(r'^(auto|/dev/[a-z][a-z0-9]{1,15})$')


# ---------------------------------------------------------------- public: what the first boot installs

@router.get('/api/bootstrap/node-config', response_class=PlainTextResponse)
def node_config():
    """Image and version for new nodes (no secrets), read by caracal-firstboot.sh."""
    image = images.node_image()
    version = (images.latest_version() or 'latest') if image else ''
    return f'image={image}\nversion={version}\n'


# ---------------------------------------------------------------- enrollment token rotation

@router.post('/api/settings/enroll-token')
def rotate_enroll_token(r: Request):
    """New enrollment token: enrolled devices keep working, prepared SD cards and install commands stop working."""
    u = current_user(r, 'settings')
    if os.getenv('CARACAL_HUB_ENROLL_TOKEN'):
        raise HTTPException(409, 'enroll_token_from_env')
    config = cfg()
    config['enroll_token'] = secrets.token_hex(24)
    save_cfg(config)
    audit(u, 'settings.enroll_token_rotate', '')
    return {'enroll_token': config['enroll_token']}


# ---------------------------------------------------------------- SD card files

def _clean(value, limit=256):
    value = str(value or '').strip()
    if len(value) > limit or any(ord(ch) < 32 for ch in value):
        raise HTTPException(400, 'invalid_value')
    return value


def _options(d):
    o = {
        'os': d.get('os') if d.get('os') in OS_NAMES else '',
        'hub_url': _clean(d.get('hub_url'), 300).rstrip('/'),
        'prefix': _clean(d.get('name_prefix'), 31).lower() or 'caracal',
        'timezone': _clean(d.get('timezone'), 64),
        'wifi_ssid': _clean(d.get('wifi_ssid'), 32),
        'wifi_password': str(d.get('wifi_password') or ''),
        'wifi_country': _clean(d.get('wifi_country'), 2).upper(),
        'user': _clean(d.get('user'), 32) or 'admin',
        'password': str(d.get('password') or ''),
        'ssh_key': _clean(d.get('ssh_key'), 2000),
        'docker_pool': _clean(d.get('docker_pool'), 18),
        # 'fleet': apt, Docker and CARACAL are downloaded through the hub, the device needs no internet access
        'download_source': 'fleet' if d.get('download_source') == 'fleet' else 'internet',
        # fixed address instead of DHCP (Wi-Fi when it is set, otherwise Ethernet) and an own time server
        'static_ip': _clean(d.get('static_ip'), 18), 'gateway': _clean(d.get('gateway'), 15),
        'dns': _clean(d.get('dns'), 200), 'ntp': _clean(d.get('ntp'), 253),
        # CARACAL's web administrator, created by the installation (otherwise whoever opens it first creates it)
        'admin_user': _clean(d.get('admin_user'), 64), 'admin_password': str(d.get('admin_password') or ''),
        # Alpine: the disk to install on (auto = the largest internal disk)
        'disk': _clean(d.get('disk'), 24) or 'auto',
    }
    checks = (
        (o['os'], 'invalid_os'),
        (HUB_URL_RE.match(o['hub_url']), 'invalid_hub_url'),
        (PREFIX_RE.match(o['prefix']), 'invalid_name_prefix'),
        (not o['timezone'] or TZ_RE.match(o['timezone']), 'invalid_timezone'),
        (not o['wifi_ssid'] or 8 <= len(o['wifi_password']) <= 63, 'invalid_wifi_password'),
        (not o['wifi_ssid'] or COUNTRY_RE.match(o['wifi_country']), 'invalid_wifi_country'),
        (USER_RE.match(o['user']) and o['user'] not in RESERVED_USERS, 'invalid_user'),
        # DietPi's SSH server (Dropbear) does not accept passwords over 100 bytes
        (not o['password'] or (len(o['password']) >= 8 and len(o['password'].encode()) <= 100), 'invalid_password'),
        (not o['ssh_key'] or SSH_KEY_RE.match(o['ssh_key']), 'invalid_ssh_key'),
        (not o['docker_pool'] or _docker_pool(o['docker_pool']), 'invalid_docker_pool'),
        (not o['static_ip'] or _interface(o['static_ip']), 'invalid_static_ip'),
        (not o['static_ip'] or _gateway(o['static_ip'], o['gateway']), 'invalid_gateway'),
        (not o['dns'] or _dns(o['dns']), 'invalid_dns'),
        (not o['ntp'] or NTP_RE.match(o['ntp']), 'invalid_ntp'),
        (not (o['admin_user'] or o['admin_password']) or ADMIN_USER_RE.fullmatch(o['admin_user']),
         'invalid_admin_user'),
        (not (o['admin_user'] or o['admin_password']) or 10 <= len(o['admin_password']) <= 200,
         'admin_password_short'),
        (DISK_RE.match(o['disk']), 'invalid_disk'),
        # Alpine on a PC needs a network cable (the firmware of its Wi-Fi card is not on the stick); the Raspberry Pi
        # card has wpa_supplicant and the firmware of the Pi's Wi-Fi
        (not (o['os'] == 'alpine' and o['wifi_ssid']), 'wifi_unsupported'),
    )
    for ok, error in checks:
        if not ok:
            raise HTTPException(400, error)
    for key in ('wifi_password', 'password', 'admin_password'):
        if any(ord(ch) < 32 for ch in o[key]):
            raise HTTPException(400, 'invalid_' + key)
    o['template'] = templates.template_id(d.get('template'))
    return o


NTP_RE = re.compile(r'^[A-Za-z0-9.\-:]{1,253}$')


def _interface(value):
    """Static IPv4 address with prefix, e.g. 192.168.1.50/24; None when invalid."""
    try:
        iface = ipaddress.ip_interface(value)
    except ValueError:
        return None
    if iface.version != 4 or '/' not in value or iface.ip in (iface.network.network_address, iface.network.broadcast_address):
        return None
    return iface


def _gateway(static_ip, gateway):
    iface = _interface(static_ip)
    try:
        gw = ipaddress.ip_address(gateway)
    except ValueError:
        return False
    return iface is not None and gw in iface.network and gw != iface.ip


def _dns(value):
    """DNS servers separated by spaces or commas; [] when one of them is invalid."""
    servers = [x for x in re.split(r'[\s,]+', value) if x]
    try:
        return [str(ipaddress.ip_address(x)) for x in servers]
    except ValueError:
        return []


def _docker_pool(value):
    """Private IPv4 range /16 to /23 for Docker's networks instead of 172.17.0.0/16 and co. (install-node.sh splits it
    into the default bridge and the pool for networks Docker creates). Returns the normalized range or ''."""
    try:
        net = ipaddress.ip_network(value, strict=False)
    except ValueError:
        return ''
    return str(net) if net.version == 4 and net.is_private and 16 <= net.prefixlen <= 23 else ''


def firstboot_conf(o):
    pool = _docker_pool(o['docker_pool']) if o.get('docker_pool') else ''
    return ('# CARACAL zero-touch: hub and enrollment token (removed from the card on the first boot)\n'
            f"HUB={shlex.quote(o['hub_url'])}\nTOKEN={shlex.quote(cfg()['enroll_token'])}\n"
            f"NAME_PREFIX={shlex.quote(o['prefix'])}\n"
            + (f'DOCKER_POOL={shlex.quote(pool)}\n' if pool else '')
            + ('DOWNLOAD_SOURCE=fleet\n' if o.get('download_source') == 'fleet' else '')
            + (f"ADMIN_USER={shlex.quote(o['admin_user'])}\nADMIN_PASSWORD={shlex.quote(o['admin_password'])}\n"
               if o.get('admin_user') else '')
            + (f"TEMPLATE={o['template']}\n" if o.get('template') else ''))


def alpine_conf(o):
    """The settings of caracal-alpine.sh, which sets up the PC itself (Raspberry Pi OS and Ubuntu do that with
    cloud-init, DietPi with dietpi.txt)."""
    values = {'DISK': o['disk'], 'TIMEZONE': o['timezone'], 'LOGIN_USER': o['user'], 'LOGIN_PASSWORD': o['password'],
              'SSH_KEY': o['ssh_key'], 'STATIC_IP': o['static_ip'], 'GATEWAY': o['gateway'],
              'DNS': ' '.join(_dns(o['dns'])) if o['dns'] else '', 'NTP': o['ntp'],
              'WIFI': '1' if o['wifi_ssid'] else ''}
    return ''.join(f'{k}={shlex.quote(v)}\n' for k, v in values.items() if v)


def _tar_add(tar, name, data=b'', mode=0o644, link=''):
    info = tarfile.TarInfo(name)
    info.mtime, info.uid, info.gid, info.uname, info.gname = int(time.time()), 0, 0, 'root', 'root'
    if link:
        info.type, info.linkname, info.mode = tarfile.SYMTYPE, link, 0o777
        tar.addfile(info)
    elif name.endswith('/'):
        info.type, info.mode = tarfile.DIRTYPE, 0o755
        tar.addfile(info)
    else:
        data = data.encode() if isinstance(data, str) else data
        info.size, info.mode = len(data), mode
        tar.addfile(info, io.BytesIO(data))


def alpine_files(o):
    """caracal.apkovl.tar.gz: the overlay of Alpine's live system. Without an overlay Alpine adds its default boot
    services itself; .default_boot_services asks for them also with this one. /etc/local.d/caracal.start runs
    caracal-alpine.sh at the end of the boot (OpenRC service "local")."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tar:
        for d in ('etc/', 'etc/apk/', 'etc/local.d/', 'etc/runlevels/', 'etc/runlevels/default/', 'etc/caracal/'):
            _tar_add(tar, d)
        _tar_add(tar, 'etc/.default_boot_services')
        _tar_add(tar, 'etc/hostname', o['prefix'] + '\n')
        _tar_add(tar, 'etc/apk/world', 'alpine-base\n')
        _tar_add(tar, 'etc/runlevels/default/local', link='/etc/init.d/local')
        _tar_add(tar, 'etc/local.d/caracal.start', '#!/bin/sh\n# CARACAL zero-touch (generated by CARACAL Fleet)\n'
                                                   'exec sh /etc/caracal/caracal-alpine.sh start\n', 0o755)
        for name in ('caracal-alpine.sh', 'caracal-firstboot.sh'):
            _tar_add(tar, f'etc/caracal/{name}', (BOOT / name).read_text(encoding='utf-8').replace('\r\n', '\n'), 0o755)
        _tar_add(tar, 'etc/caracal-firstboot.conf', firstboot_conf(o) + alpine_conf(o), 0o600)
        if o['wifi_ssid']:
            _tar_add(tar, 'etc/wpa_supplicant/')
            _tar_add(tar, 'etc/wpa_supplicant/wpa_supplicant.conf', wpa_supplicant_conf(o), 0o600)
    return {'caracal.apkovl.tar.gz': buf.getvalue()}


def wpa_supplicant_conf(o):
    """Wi-Fi of the Raspberry Pi with Alpine: the network name in hex and the derived key (WPA-PSK), so no quoting
    can break the file and the password itself is not stored on the card."""
    import hashlib
    psk = hashlib.pbkdf2_hmac('sha1', o['wifi_password'].encode(), o['wifi_ssid'].encode(), 4096, 32).hex()
    return (f"# CARACAL zero-touch (generated by CARACAL Fleet)\nctrl_interface=/var/run/wpa_supplicant\n"
            f"country={o['wifi_country']}\n\nnetwork={{\n\tssid={o['wifi_ssid'].encode().hex()}\n\tscan_ssid=1\n"
            f"\tkey_mgmt=WPA-PSK\n\tpsk={psk}\n}}\n")


# ---------------------------------------------------------------- Raspberry Pi with Alpine Linux: a card image

def alpine_rpi_release():
    """The newest stable Alpine for the Raspberry Pi (aarch64): {'version', 'file', 'sha256', 'url'}."""
    import requests
    base = f'{ALPINE_MIRROR}/latest-stable/releases/aarch64'
    try:
        text = requests.get(f'{base}/latest-releases.yaml', timeout=20).text
    except requests.RequestException as e:
        raise HTTPException(502, f'alpine_unavailable: {e}'[:200])
    for entry in text.split('\n-'):
        fields = dict(re.findall(r'^\s*([a-z0-9_]+):\s*(\S+)\s*$', entry, re.M))
        if fields.get('flavor') == 'alpine-rpi' and fields.get('file', '').endswith('.tar.gz') and re.fullmatch(r'[0-9a-f]{64}', fields.get('sha256', '')):
            if not re.fullmatch(r'alpine-rpi-[0-9.]+-aarch64\.tar\.gz', fields['file']):
                break
            return {'version': fields.get('version', ''), 'file': fields['file'], 'sha256': fields['sha256'], 'url': f"{base}/{fields['file']}"}
    raise HTTPException(502, 'alpine_unavailable')


def alpine_rpi_tarball(release):
    """The Alpine tarball for the Raspberry Pi, downloaded once and checked against its SHA-256."""
    import hashlib
    import requests
    ALPINE_CACHE.mkdir(parents=True, exist_ok=True)
    path = ALPINE_CACHE / release['file']
    if path.is_file():
        return path
    tmp = path.with_suffix('.part')
    h = hashlib.sha256()
    try:
        with requests.get(release['url'], stream=True, timeout=(15, 600)) as r, tmp.open('wb') as f:
            r.raise_for_status()
            for chunk in r.iter_content(1024 * 1024):
                h.update(chunk)
                f.write(chunk)
        if h.hexdigest() != release['sha256']:
            raise HTTPException(502, 'alpine_checksum')
        tmp.replace(path)
    except requests.RequestException as e:
        raise HTTPException(502, f'alpine_unavailable: {e}'[:200])
    finally:
        tmp.unlink(missing_ok=True)
    for old in ALPINE_CACHE.glob('alpine-rpi-*.tar.gz'):   # only the newest one is kept
        if old != path:
            old.unlink(missing_ok=True)
    return path


USERCFG = """# CARACAL: KMS graphics (needed by the display of CARACAL), the whole screen, HDMI even when the TV starts later
dtoverlay=vc4-kms-v3d
disable_overscan=1
hdmi_force_hotplug=1
"""


def _mbr(sectors):
    """A DOS partition table with one FAT32 (LBA) partition from PART_START."""
    import struct
    mbr = bytearray(512)
    mbr[440:444] = secrets.token_bytes(4)                 # disk signature
    mbr[446:462] = struct.pack('<B3sB3sII', 0x00, b'\xfe\xff\xff', 0x0C, b'\xfe\xff\xff', PART_START, sectors)
    mbr[510:512] = b'\x55\xaa'
    return bytes(mbr)


def build_rpi_image(o, workdir):
    """caracal-alpine-rpi.img.gz in workdir: the card image with Alpine for the Raspberry Pi and the overlay."""
    import gzip
    import shutil
    import subprocess
    from pathlib import Path
    if not shutil.which('mformat') or not shutil.which('mcopy'):
        raise HTTPException(500, 'mtools_missing')
    release = alpine_rpi_release()
    tarball = alpine_rpi_tarball(release)
    work = Path(workdir)
    files = work / 'card'
    files.mkdir()
    with tarfile.open(tarball) as tar:
        members = [m for m in tar.getmembers() if (m.isfile() or m.isdir()) and not m.name.startswith('/') and '..' not in m.name.split('/')]
        try:
            tar.extractall(files, members=members, filter='data')
        except TypeError:   # Python before 3.12
            tar.extractall(files, members=members)
    for name, data in alpine_files(o).items():
        (files / name).write_bytes(data)
    (files / 'usercfg.txt').write_text(USERCFG)
    (files / 'CARACAL-README.txt').write_text(README['alpine-rpi'].format(prefix=o['prefix'], version=release['version']))
    sectors = IMAGE_MIB * 1024 * 1024 // 512
    img = work / 'caracal-alpine-rpi.img'
    with img.open('wb') as f:
        f.write(_mbr(sectors))
        f.truncate((PART_START + sectors) * 512)
    target = f'{img}@@{PART_START * 512}'
    run = lambda *cmd: subprocess.run(cmd, check=True, capture_output=True, timeout=300, env={**os.environ, 'MTOOLS_SKIP_CHECK': '1'})
    try:
        run('mformat', '-i', target, '-F', '-v', 'CARACAL', '-T', str(sectors), '-H', str(PART_START), '-h', '64', '-s', '32', '-c', '8', '::')
        for entry in sorted(files.iterdir()):
            run('mcopy', '-i', target, '-s', '-Q', str(entry), '::/')
    except subprocess.CalledProcessError as e:
        raise HTTPException(500, f'image_failed: {e.stderr.decode(errors="replace")[:200]}')
    out = work / 'caracal-alpine-rpi.img.gz'
    with img.open('rb') as src, gzip.open(out, 'wb', compresslevel=6) as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
    img.unlink()
    shutil.rmtree(files, ignore_errors=True)
    return out, release


def ubuntu_files(o):
    """Ubuntu Server autoinstall (24.04 and newer) for a USB stick named CIDATA. JSON is valid YAML, so the
    configuration is written as JSON after the #cloud-config line."""
    script = (BOOT / 'caracal-firstboot.sh').read_text(encoding='utf-8').replace('\r\n', '\n')
    user = {'name': o['user'], 'groups': ['adm', 'sudo', 'video', 'audio', 'input', 'plugdev'], 'shell': '/bin/bash',
            'sudo': 'ALL=(ALL) NOPASSWD:ALL', 'lock_passwd': not o['password']}
    if o['password']:
        user['plain_text_passwd'] = o['password']
    if o['ssh_key']:
        user['ssh_authorized_keys'] = [o['ssh_key']]
    user_data = {
        'hostname': o['prefix'], 'users': [user], 'ssh_pwauth': bool(o['password']),
        'write_files': [
            {'path': '/var/lib/caracal-zero-touch/caracal-firstboot.sh', 'permissions': '0755', 'encoding': 'b64',
             'content': base64.b64encode(script.encode()).decode()},
            {'path': '/etc/caracal-firstboot.conf', 'permissions': '0600', 'content': firstboot_conf(o)}],
        'runcmd': [['bash', '/var/lib/caracal-zero-touch/caracal-firstboot.sh', 'install']],
    }
    if o['ntp']:
        user_data['ntp'] = {'enabled': True, 'servers': [o['ntp']]}
    auto = {'version': 1, 'locale': 'en_US.UTF-8', 'keyboard': {'layout': 'us'},
            'storage': {'layout': {'name': 'direct'}},   # the largest disk, ERASED
            'ssh': {'install-server': bool(o['password'] or o['ssh_key']), 'allow-pw': bool(o['password']),
                    'authorized-keys': [o['ssh_key']] if o['ssh_key'] else []},
            'user-data': user_data, 'shutdown': 'reboot'}
    if o['timezone']:
        auto['timezone'] = o['timezone']
    if o['static_ip'] or o['wifi_ssid']:
        auto['network'] = ubuntu_network(o)
    return {'user-data': '#cloud-config\n# CARACAL zero-touch node (generated by CARACAL Fleet): Ubuntu autoinstall\n'
                         + json.dumps({'autoinstall': auto}, indent=2, ensure_ascii=False) + '\n',
            'meta-data': f'instance-id: caracal-{secrets.token_hex(4)}\n'}


def ubuntu_network(o):
    """netplan for the installer and the installed system: interface names differ per PC (enp3s0, wlp2s0)."""
    def address(static):
        if not static:
            return {'dhcp4': True, 'optional': True}
        return {'dhcp4': False, 'optional': True, 'addresses': [o['static_ip']],
                'routes': [{'to': 'default', 'via': o['gateway']}],
                'nameservers': {'addresses': _dns(o['dns']) if o['dns'] else [o['gateway']]}}
    static = bool(o['static_ip'])
    net = {'version': 2, 'ethernets': {'wired': {'match': {'name': 'en*'}, **address(static and not o['wifi_ssid'])}}}
    if o['wifi_ssid']:
        net['wifis'] = {'wireless': {'match': {'name': 'wl*'}, **address(static), 'regulatory-domain': o['wifi_country'],
                                     'access-points': {o['wifi_ssid']: {'password': o['wifi_password']}}}}
    return net


def _yaml(value):
    return json.dumps(value, ensure_ascii=False)   # JSON strings are valid YAML scalars


def raspios_files(o):
    """cloud-init NoCloud files of Raspberry Pi OS (Trixie and newer)."""
    login = bool(o['password'] or o['ssh_key'])
    lines = ['#cloud-config', '# CARACAL zero-touch node (generated by CARACAL Fleet)',
             f"hostname: {_yaml(o['prefix'])}", 'manage_etc_hosts: true']
    if o['timezone']:
        lines.append(f"timezone: {_yaml(o['timezone'])}")
    lines += ['users:', f"  - name: {_yaml(o['user'])}",
              '    groups: [adm, sudo, users, video, audio, input, plugdev, netdev]',
              '    shell: /bin/bash', '    sudo: "ALL=(ALL) NOPASSWD:ALL"',
              f"    lock_passwd: {'false' if o['password'] else 'true'}"]
    if o['password']:
        lines.append(f"    plain_text_passwd: {_yaml(o['password'])}")
    if o['ssh_key']:
        lines.append(f"    ssh_authorized_keys: [{_yaml(o['ssh_key'])}]")
    lines.append(f"ssh_pwauth: {'true' if o['password'] else 'false'}")
    if o.get('ntp'):
        lines += ['ntp:', '  enabled: true', f"  servers: [{_yaml(o['ntp'])}]"]
    lines.append('runcmd:')
    if login:
        lines.append('  - [systemctl, enable, --now, ssh]')
    lines.append('  - [bash, /boot/firmware/caracal-firstboot.sh, install]')
    files = {'user-data': '\n'.join(lines) + '\n'}
    if o['wifi_ssid'] or o.get('static_ip'):
        files['network-config'] = network_config(o)
    return files


def _address_lines(o, static):
    """netplan lines of one interface: DHCP or the static address of the card."""
    if not static:
        return ['      dhcp4: true', '      optional: true']
    out = ['      dhcp4: false', '      optional: true', f"      addresses: [{_yaml(o['static_ip'])}]",
           '      routes:', '        - to: default', f"          via: {_yaml(o['gateway'])}"]
    dns = _dns(o['dns']) if o.get('dns') else [o['gateway']]
    out += ['      nameservers:', f"        addresses: [{', '.join(_yaml(x) for x in dns)}]"]
    return out


def network_config(o):
    """cloud-init network-config v2: the static address goes to Wi-Fi when it is set, otherwise to Ethernet."""
    static = bool(o.get('static_ip'))
    lines = ['network:', '  version: 2', '  renderer: NetworkManager', '  ethernets:', '    eth0:']
    lines += _address_lines(o, static and not o['wifi_ssid'])
    if o['wifi_ssid']:
        lines += ['  wifis:', '    wlan0:'] + _address_lines(o, static) + [
            f"      regulatory-domain: {_yaml(o['wifi_country'])}",
            '      access-points:', f"        {_yaml(o['wifi_ssid'])}:",
            f"          password: {_yaml(o['wifi_password'])}"]
    return '\n'.join(lines) + '\n'


def patch_dietpi_txt(text, values):
    """Sets KEY=value lines of dietpi.txt; keys missing in the file are appended."""
    lines = text.replace('\r\n', '\n').split('\n')
    for key, value in values.items():
        pattern = re.compile(rf'^\s*#?\s*{re.escape(key)}=')
        hits = [i for i, line in enumerate(lines) if pattern.match(line)]
        if hits:
            lines[hits[0]] = f'{key}={value}'
            for i in reversed(hits[1:]):
                del lines[i]
        else:
            lines.append(f'{key}={value}')
    return '\n'.join(lines).rstrip('\n') + '\n'


def _bash_quote(value):
    return "'" + value.replace("'", "'\\''") + "'"


def dietpi_files(o, dietpi_txt):
    if 'AUTO_SETUP_AUTOMATED' not in dietpi_txt:
        raise HTTPException(400, 'invalid_dietpi_txt')
    password = o['password'] or secrets.token_urlsafe(18)   # never keep DietPi's well-known default password
    values = {
        'AUTO_SETUP_AUTOMATED': '1',
        'AUTO_SETUP_GLOBAL_PASSWORD': password,
        'AUTO_SETUP_NET_HOSTNAME': o['prefix'],
        'AUTO_SETUP_CUSTOM_SCRIPT_EXEC': '0',
        'SURVEY_OPTED_IN': '0',
    }
    if o['timezone']:
        values['AUTO_SETUP_TIMEZONE'] = o['timezone']
    if o['ssh_key']:
        values['AUTO_SETUP_SSH_PUBKEY'] = o['ssh_key']
    if o['wifi_ssid']:
        # DietPi uses one adapter: with Wi-Fi enabled it disables Ethernet anyway
        values.update({'AUTO_SETUP_NET_WIFI_ENABLED': '1', 'AUTO_SETUP_NET_ETHERNET_ENABLED': '0',
                       'AUTO_SETUP_NET_WIFI_COUNTRY_CODE': o['wifi_country']})
    if o.get('static_ip'):
        iface = _interface(o['static_ip'])
        values.update({'AUTO_SETUP_NET_USESTATIC': '1', 'AUTO_SETUP_NET_STATIC_GATEWAY': o['gateway'],
                       'AUTO_SETUP_NET_STATIC_DNS': ' '.join(_dns(o['dns']) if o.get('dns') else [o['gateway']])})
        if 'AUTO_SETUP_NET_STATIC_MASK' in dietpi_txt:   # older DietPi: address and netmask separately
            values.update({'AUTO_SETUP_NET_STATIC_IP': str(iface.ip), 'AUTO_SETUP_NET_STATIC_MASK': str(iface.netmask)})
        else:
            values['AUTO_SETUP_NET_STATIC_IP'] = str(iface)
    if o.get('ntp'):
        values.update({'CONFIG_NTP_MODE': '2', 'CONFIG_NTP_MIRROR': o['ntp']})
    if o.get('download_source') == 'fleet':
        # No internet: the connectivity check pings the device itself and resolves the hub, the time comes from the
        # time server of the card or from the hub (caracal-firstboot.sh), DietPi does not look for its own updates
        # on GitHub. Automation_Custom_PreScript.sh skips the online DietPi update of the first boot and points apt
        # to the hub (see caracal-firstboot.sh dietpi-prepare).
        values.update({'CONFIG_CHECK_CONNECTION_IP': '127.0.0.1',
                       'CONFIG_CHECK_DNS_DOMAIN': re.sub(r'^https?://([^/:]+).*$', r'\1', o['hub_url']),
                       'CONFIG_CHECK_DIETPI_UPDATES': '0'})
        if not o.get('ntp'):
            values['CONFIG_NTP_MODE'] = '0'
    files = {
        'dietpi.txt': patch_dietpi_txt(dietpi_txt, values),
        'Automation_Custom_Script.sh': (
            '#!/bin/bash\n# CARACAL zero-touch: started by DietPi at the end of its first-boot setup\n'
            'for d in /boot/firmware /boot; do\n'
            '  [ -f "$d/caracal-firstboot.sh" ] && exec bash "$d/caracal-firstboot.sh" install\n'
            'done\necho "caracal-firstboot.sh not found on the boot partition" >&2\nexit 1\n'),
    }
    if o.get('download_source') == 'fleet':
        files['Automation_Custom_PreScript.sh'] = (
            '#!/bin/bash\n# CARACAL zero-touch without internet access: started by DietPi early in its first boot\n'
            'for d in /boot/firmware /boot; do\n'
            '  [ -f "$d/caracal-firstboot.sh" ] && exec bash "$d/caracal-firstboot.sh" dietpi-prepare\n'
            'done\necho "caracal-firstboot.sh not found on the boot partition" >&2\nexit 1\n')
    if o['wifi_ssid']:
        files['dietpi-wifi.txt'] = (
            '# Wi-Fi for DietPi (generated by CARACAL Fleet)\n'
            f"aWIFI_SSID[0]={_bash_quote(o['wifi_ssid'])}\naWIFI_KEY[0]={_bash_quote(o['wifi_password'])}\n"
            "aWIFI_KEYMGR[0]='WPA-PSK'\naWIFI_PROTO[0]=''\naWIFI_PAIRWISE[0]=''\naWIFI_AUTH_ALG[0]=''\n"
            "aWIFI_EAP[0]=''\naWIFI_IDENTITY[0]=''\naWIFI_PASSWORD[0]=''\naWIFI_PHASE1[0]=''\naWIFI_PHASE2[0]=''\n"
            "aWIFI_CERT[0]=''\n")
    return files


README = {
    'raspios': """CARACAL zero-touch SD card - Raspberry Pi OS Lite (64-bit, Trixie or newer)

1. Write Raspberry Pi OS Lite (64-bit) to the card with Raspberry Pi Imager. OS customisation is not needed:
   these files replace it.
2. Copy all files from this archive to the boot partition of the card ("bootfs"), overwrite existing files.
3. Insert the card into the Raspberry Pi, connect the screen and the network and power it on.

The first boot installs Docker, the display and CARACAL (10-20 minutes, internet access needed). The node then
appears in CARACAL Fleet as "{prefix}-xxxxxx". Log on the device: /var/log/caracal-firstboot.log
""",
    'dietpi': """CARACAL zero-touch SD card - DietPi

1. Write DietPi for Raspberry Pi (64-bit) to the card.
2. Copy all files from this archive to the boot partition of the card, overwrite dietpi.txt.
3. Insert the card into the Raspberry Pi, connect the screen and the network and power it on.

DietPi finishes its automatic first-boot setup, then Docker, the display and CARACAL are installed
(15-30 minutes, internet access needed). On a Raspberry Pi the graphics driver (KMS) is enabled and the device
reboots once during the installation. The node then appears in CARACAL Fleet as "{prefix}-xxxxxx".
With Wi-Fi set on the card, DietPi uses Wi-Fi only (Ethernet is disabled).
Log on the device: /var/log/caracal-firstboot.log
""",
    'alpine': """CARACAL zero-touch USB stick - Alpine Linux on a PC (x86_64 or arm64)

WARNING: the installation ERASES the internal disk of the PC ({disk}).

1. Download Alpine Linux "Standard" or "Extended" (x86_64) from https://alpinelinux.org/downloads/
2. Write it to a USB stick with Rufus (ISO mode, the default). The stick then stays writable (FAT32).
   With balenaEtcher or dd the stick is read-only: put caracal.apkovl.tar.gz on a second USB stick (FAT32) instead.
3. Copy caracal.apkovl.tar.gz to the root of the stick (next to the "boot" and "apks" folders).
4. Connect the PC to the network with a cable, plug in the stick(s) and the screen, boot from the stick.

Alpine starts, finds the overlay and installs itself on the disk ({minutes_alpine}), reboots, then installs Docker,
the display and CARACAL ({minutes_caracal}). The progress is shown on the screen. If the PC boots from the stick
again, it finds CARACAL already installed and powers off: remove the stick and power it on.
The node then appears in CARACAL Fleet as "{prefix}-xxxxxx". Log on the device: /var/log/caracal-firstboot.log
""",
    'alpine-rpi': """CARACAL zero-touch SD card - Alpine Linux {version} on a Raspberry Pi (3, 4, 5, Zero 2 W; 64-bit)

1. Write caracal-alpine-rpi.img.gz to the SD card with Raspberry Pi Imager ("Use custom") or balenaEtcher.
   Do not change settings in the imager: the card is configured by CARACAL Fleet.
2. Insert the card, connect the screen and the network cable (or use the Wi-Fi set in CARACAL Fleet), power on
   the Raspberry Pi.

Alpine starts, creates the system partition in the free space of the card and installs itself (5-15 minutes),
reboots, then installs Docker, the display and CARACAL (15-30 minutes). The progress is shown on the screen.
The node then appears in CARACAL Fleet as "{prefix}-xxxxxx". Log on the device: /var/log/caracal-firstboot.log
""",
    'ubuntu': """CARACAL zero-touch USB stick - Ubuntu Server on a PC (24.04 LTS or newer)

WARNING: the installation ERASES the largest disk of the PC.

1. Write Ubuntu Server (live server ISO) to a USB stick (balenaEtcher, Rufus or dd).
2. Format a second USB stick as FAT32 with the name CIDATA and copy user-data and meta-data to it.
3. Connect the PC to the network, plug in both sticks and the screen, boot from the Ubuntu stick.
4. The installer finds the configuration and asks "Continue with autoinstall?": type yes and press Enter.
   (No question with "autoinstall" added to the kernel command line in the boot menu.)

Ubuntu installs itself and reboots (remove the sticks then if the PC would boot from them again). The first boot
installs Docker, the display and CARACAL (10-20 minutes). The node then appears in CARACAL Fleet as
"{prefix}-xxxxxx". Log on the device: /var/log/caracal-firstboot.log
""",
}


FLEET_NOTE = """
Downloads through CARACAL Fleet: system packages, Docker and CARACAL come from the hub, the device only needs to
reach the hub (no internet access). Without a time server on the card the clock is set from the hub.
"""


async def _rpi_image(u, o):
    import shutil
    import tempfile
    from fastapi.responses import FileResponse
    from starlette.background import BackgroundTask
    from starlette.concurrency import run_in_threadpool
    work = tempfile.mkdtemp(prefix='caracal-rpi-', dir=DATA)
    try:
        path, release = await run_in_threadpool(build_rpi_image, o, work)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    audit(u, 'sdcard.create', o['os'], {'hub_url': o['hub_url'], 'prefix': o['prefix'], 'alpine': release['version'],
                                        'download_source': o['download_source'], 'static_ip': o['static_ip'], 'template': o['template']})
    return FileResponse(path, media_type='application/gzip', filename='caracal-alpine-rpi.img.gz',
                        background=BackgroundTask(shutil.rmtree, work, ignore_errors=True))


@router.post('/api/sdcard')
async def sd_card(r: Request):
    u = current_user(r, 'settings')     # the files contain the enrollment token
    try:
        d = await r.json()
    except ValueError:
        raise HTTPException(400, 'invalid_json')
    if not isinstance(d, dict):
        raise HTTPException(400, 'invalid_json')
    o = _options(d)
    if not images.node_image():
        raise HTTPException(400, 'node_image_missing')
    if o['os'] == 'alpine-rpi':
        return await _rpi_image(u, o)
    if o['os'] == 'dietpi':
        dietpi_txt = str(d.get('dietpi_txt') or '')
        if not dietpi_txt or len(dietpi_txt) > DIETPI_TXT_MAX:
            raise HTTPException(400, 'invalid_dietpi_txt')
        files = dietpi_files(o, dietpi_txt)
    elif o['os'] == 'alpine':
        files = alpine_files(o)
    elif o['os'] == 'ubuntu':
        files = ubuntu_files(o)
    else:
        files = raspios_files(o)
    if o['os'] not in PC_OS:   # Raspberry Pi: the script and its settings on the boot partition
        files['caracal-firstboot.sh'] = (BOOT / 'caracal-firstboot.sh').read_text(encoding='utf-8')
        files['caracal-firstboot.conf'] = firstboot_conf(o)
    readme = README[o['os']].format(prefix=o['prefix'], disk='the largest internal disk' if o['disk'] == 'auto'
                                    else o['disk'], minutes_alpine='5-10 minutes', minutes_caracal='10-20 minutes')
    if o['download_source'] == 'fleet':
        readme = readme.replace('internet access needed', 'only access to the hub needed') + FLEET_NOTE
    files['CARACAL-README.txt'] = readme

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, content in sorted(files.items()):
            info = zipfile.ZipInfo(name, time.localtime()[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o755 if name.endswith('.sh') else 0o644) << 16
            z.writestr(info, content if isinstance(content, bytes) else content.replace('\r\n', '\n'))
    audit(u, 'sdcard.create', o['os'], {'hub_url': o['hub_url'], 'prefix': o['prefix'], 'wifi': bool(o['wifi_ssid']),
                                        'docker_pool': o['docker_pool'], 'download_source': o['download_source'],
                                        'static_ip': o['static_ip'], 'ntp': o['ntp'], 'template': o['template']})
    return Response(buf.getvalue(), media_type='application/zip', headers={
        'Content-Disposition': f'attachment; filename="caracal-sdcard-{o["os"]}.zip"'})
