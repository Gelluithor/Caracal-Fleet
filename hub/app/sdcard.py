"""Zero-touch SD cards: files for the boot partition of a freshly written Raspberry Pi OS Lite or DietPi card.

On the first boot the card connects to the network, downloads the installer from the hub and turns the device
into a CARACAL node on Docker that enrolls itself (hub/bootstrap/caracal-firstboot.sh). The enrollment token is
part of the files; the script removes it from the boot partition on the first boot. A lost card is handled by
rotating the token in Settings.

Raspberry Pi OS (Trixie and newer) is configured with cloud-init (user-data, network-config), DietPi with its own
dietpi.txt (patched copy of the one from the card), dietpi-wifi.txt and Automation_Custom_Script.sh.
"""
import io
import ipaddress
import json
import os
import re
import secrets
import shlex
import time
import zipfile

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response

from . import images
from .core import BOOT, audit, cfg, current_user, save_cfg

router = APIRouter()

HUB_URL_RE = re.compile(r'^https?://[A-Za-z0-9.\-]+(:\d{1,5})?(/[A-Za-z0-9._~/-]*)?$')
PREFIX_RE = re.compile(r'^[a-z0-9][a-z0-9-]{0,30}$')
USER_RE = re.compile(r'^[a-z_][a-z0-9_-]{0,31}$')
TZ_RE = re.compile(r'^[A-Za-z0-9_+\-/]{1,64}$')
COUNTRY_RE = re.compile(r'^[A-Z]{2}$')
SSH_KEY_RE = re.compile(r'^(ssh-(ed25519|rsa)|ecdsa-sha2-nistp(256|384|521)|sk-[a-z0-9@.-]+) [A-Za-z0-9+/=]{16,}( [^\r\n]{0,200})?$')
RESERVED_USERS = {'root', 'caracal', 'daemon', 'nobody'}
DIETPI_TXT_MAX = 256 * 1024
OS_NAMES = ('raspios', 'dietpi')


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
    u = current_user(r, 'admin')
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
    )
    for ok, error in checks:
        if not ok:
            raise HTTPException(400, error)
    for key in ('wifi_password', 'password'):
        if any(ord(ch) < 32 for ch in o[key]):
            raise HTTPException(400, 'invalid_' + key)
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
            + ('DOWNLOAD_SOURCE=fleet\n' if o.get('download_source') == 'fleet' else ''))


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
}


FLEET_NOTE = """
Downloads through CARACAL Fleet: system packages, Docker and CARACAL come from the hub, the device only needs to
reach the hub (no internet access). Without a time server on the card the clock is set from the hub.
"""


@router.post('/api/sdcard')
async def sd_card(r: Request):
    u = current_user(r, 'admin')     # the files contain the enrollment token
    try:
        d = await r.json()
    except ValueError:
        raise HTTPException(400, 'invalid_json')
    if not isinstance(d, dict):
        raise HTTPException(400, 'invalid_json')
    o = _options(d)
    if not images.node_image():
        raise HTTPException(400, 'node_image_missing')
    if o['os'] == 'dietpi':
        dietpi_txt = str(d.get('dietpi_txt') or '')
        if not dietpi_txt or len(dietpi_txt) > DIETPI_TXT_MAX:
            raise HTTPException(400, 'invalid_dietpi_txt')
        files = dietpi_files(o, dietpi_txt)
    else:
        files = raspios_files(o)
    files['caracal-firstboot.sh'] = (BOOT / 'caracal-firstboot.sh').read_text(encoding='utf-8')
    files['caracal-firstboot.conf'] = firstboot_conf(o)
    readme = README[o['os']].format(prefix=o['prefix'])
    if o['download_source'] == 'fleet':
        readme = readme.replace('internet access needed', 'only access to the hub needed') + FLEET_NOTE
    files['CARACAL-README.txt'] = readme

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, content in sorted(files.items()):
            info = zipfile.ZipInfo(name, time.localtime()[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o755 if name.endswith('.sh') else 0o644) << 16
            z.writestr(info, content.replace('\r\n', '\n'))
    audit(u, 'sdcard.create', o['os'], {'hub_url': o['hub_url'], 'prefix': o['prefix'], 'wifi': bool(o['wifi_ssid']),
                                        'docker_pool': o['docker_pool'], 'download_source': o['download_source'],
                                        'static_ip': o['static_ip'], 'ntp': o['ntp']})
    return Response(buf.getvalue(), media_type='application/zip', headers={
        'Content-Disposition': f'attachment; filename="caracal-sdcard-{o["os"]}.zip"'})
