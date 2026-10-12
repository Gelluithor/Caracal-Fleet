#!/bin/bash
# Turns a Raspberry Pi (Raspberry Pi OS Lite / DietPi, arm64) or a PC with Debian, Ubuntu or Alpine Linux (x86_64,
# arm64) into a CARACAL node running on Docker, and connects it to CARACAL Fleet.
#
#   sudo bash install-node.sh --hub https://fleet.example --token ENROLL_TOKEN \
#        --image ghcr.io/OWNER/caracal-node --version 2026.10.06 [--name NAME] [--docker-pool 10.200.0.0/16] \
#        [--via-fleet [--fleet-auth-file FILE]] [--admin-file FILE] [--template ID] [--reinstall [--wipe-data]]
#
# --template: a node template of the hub, applied by the hub once the new node reports (playlist, notifications...).
#
# --reinstall installs CARACAL again from scratch (CARACAL Fleet: device -> Manage -> Reinstall CARACAL): the CARACAL
# containers and images are removed and downloaded again, the X display packages are reinstalled and the browser
# profile is created anew. --wipe-data also removes CARACAL's data in /var/lib/caracal (playlist, media, logins,
# notifications, settings), as on a new node.
#
# Debian, Ubuntu, Raspberry Pi OS and DietPi use apt and systemd; Alpine Linux uses apk and OpenRC (the X display runs
# on vt7 there, the console keeps tty1). Alpine needs bash for this script: apk add bash curl.
#
# --admin-file sets CARACAL's web administrator: a JSON file {"username", "password"} readable by root only (never
# put passwords on the command line); created on a new node, a new name and password on a converted one. The file is
# removed afterwards.
#
# --via-fleet downloads everything through the hub instead of the internet: apt (Debian, Raspberry Pi and Docker
# repositories, the sources point to <hub>/apt/<host>), Docker itself and the CARACAL image (prepared by the hub,
# loaded with docker load). Credentials: the enrollment token, or "user:password" of a device in --fleet-auth-file.
#
# --docker-pool moves Docker's own networks out of 172.16.0.0/12 (e.g. when the LAN uses those addresses): the first
# half of the private /16-/23 range is the default bridge, the second half the pool for networks Docker creates.
#
# The host only runs Docker, the X display (Xorg + Openbox on tty1) and the Fleet Agent; the CARACAL app,
# player and overlay run in containers. An existing classic CARACAL installation (/opt/caracal) is converted:
# its data in /var/lib/caracal (playlists, media, logins, database) are kept and the old application is moved
# to /opt/caracal.legacy-<date>.
set -euo pipefail

HUB=''; TOKEN=''; NAME=''; IMAGE=''; VERSION='latest'; SKIP_AGENT=''; DOCKER_POOL=''; VIA_FLEET=''; AUTH_FILE=''; ADMIN_FILE=''
TEMPLATE=''; REINSTALL=''; WIPE_DATA=''
while [ $# -gt 0 ]; do
  case "$1" in
    --hub) HUB=${2%/}; shift 2;;
    --token) TOKEN=$2; shift 2;;
    --name) NAME=$2; shift 2;;
    --image) IMAGE=$2; shift 2;;
    --version) VERSION=$2; shift 2;;
    --docker-pool) DOCKER_POOL=$2; shift 2;;
    --skip-agent) SKIP_AGENT=1; shift;;   # used when the running Fleet Agent converts its own node
    --via-fleet) VIA_FLEET=1; shift;;
    --fleet-auth-file) AUTH_FILE=$2; shift 2;;
    --admin-file) ADMIN_FILE=$2; shift 2;;
    --template) TEMPLATE=$2; shift 2;;
    --reinstall) REINSTALL=1; shift;;
    --wipe-data) WIPE_DATA=1; shift;;
    *) echo "Unknown argument: $1" >&2; exit 2;;
  esac
done
[ "$(id -u)" -eq 0 ] || { echo 'Run as root (sudo).' >&2; exit 1; }
[ -n "$HUB" ] && [ -n "$IMAGE" ] || { echo '--hub and --image are required' >&2; exit 2; }
[ -z "$WIPE_DATA" ] || [ -n "$REINSTALL" ] || { echo '--wipe-data needs --reinstall' >&2; exit 2; }
# package manager and init system: apt + systemd (Debian family) or apk + OpenRC (Alpine Linux)
if command -v apt-get >/dev/null; then PKG=apt
elif command -v apk >/dev/null; then PKG=apk
else echo 'Supported systems: Raspberry Pi OS, DietPi, Debian, Ubuntu and Alpine Linux.' >&2; exit 3; fi
if [ -d /run/systemd/system ] && command -v systemctl >/dev/null; then INIT=systemd
elif command -v rc-service >/dev/null; then INIT=openrc
else echo 'systemd or OpenRC is required.' >&2; exit 3; fi
SRC_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
NODE_DIR=/opt/caracal-node
export DEBIAN_FRONTEND=noninteractive

step() { echo "==> $*"; }
# services: systemd units or OpenRC scripts with the same names
svc_enable() { if [ "$INIT" = systemd ]; then systemctl enable "$1.service" >/dev/null; else rc-update add "$1" default >/dev/null; fi; }
svc_restart() { if [ "$INIT" = systemd ]; then systemctl restart "$1.service"; else rc-service "$1" restart; fi; }
svc_reload() { if [ "$INIT" = systemd ]; then systemctl daemon-reload; fi; }
arch() {   # Docker architecture of this device
  if [ "$PKG" = apt ]; then dpkg --print-architecture; return; fi
  case "$(uname -m)" in x86_64) echo amd64;; aarch64) echo arm64;; *) uname -m;; esac
}
add_to_group() {   # user, group (shadow's usermod or BusyBox's addgroup)
  if command -v usermod >/dev/null; then usermod -a -G "$2" "$1"; else addgroup "$1" "$2" >/dev/null; fi
}

# --- download through the hub (keep APT_HOSTS in sync with the agent and the hub) ---
APT_DIR=${CARACAL_APT_DIR:-/etc/apt}
APT_HOSTS='deb.debian.org security.debian.org ftp.debian.org archive.raspberrypi.com archive.raspberrypi.org raspbian.raspberrypi.com raspbian.raspberrypi.org download.docker.com dietpi.com archive.ubuntu.com security.ubuntu.com ports.ubuntu.com'
apt_source_files() {
  local f
  for f in "$APT_DIR/sources.list" "$APT_DIR"/sources.list.d/*.list "$APT_DIR"/sources.list.d/*.sources; do
    [ -f "$f" ] && echo "$f"
  done
}
# apt sources of the known repositories -> <hub>/apt/<host>/...; credentials in auth.conf.d (root only)
apt_via_fleet() {
  local hub=$1 user=$2 password=$3 f h re machine
  for f in $(apt_source_files); do
    for h in $APT_HOSTS; do
      re=${h//./\\.}
      sed -i -E "s#https?://[^[:space:]/]+(/[^[:space:]]*)?/apt/$re(/|[[:space:]]|\$)#https://$h\2#g; s#https?://$re(/|[[:space:]]|\$)#$hub/apt/$h\1#g" "$f"
    done
  done
  case "$hub" in https://*) machine=${hub#https://};; *) machine=$hub;; esac   # apt: no protocol = https only
  install -d -m 755 "$APT_DIR/auth.conf.d"
  ( umask 077; printf 'machine %s/apt login %s password %s\n' "$machine" "$user" "$password" > "$APT_DIR/auth.conf.d/caracal-fleet.conf" )
}
# Alpine Linux: repositories -> https://user:password@<hub>/apt/<host>/... (the file becomes readable by root only)
APK_REPOSITORIES=${CARACAL_APK_REPOSITORIES:-/etc/apk/repositories}
apk_via_fleet() {
  local hub=$1 user=$2 password=$3 scheme rest
  scheme=${hub%%://*}; rest=${hub#*://}
  [ -f "$APK_REPOSITORIES" ] || return 0
  sed -i -E "s#https?://[^[:space:]/]+(/[^[:space:]]*)?/apt/dl-cdn\.alpinelinux\.org(/|[[:space:]]|\$)#https://dl-cdn.alpinelinux.org\2#g; s#https?://dl-cdn\.alpinelinux\.org(/|[[:space:]]|\$)#$scheme://$user:$password@$rest/apt/dl-cdn.alpinelinux.org\1#g" "$APK_REPOSITORIES"
  chmod 600 "$APK_REPOSITORIES"
}
# CARACAL image prepared by the hub (202 while it downloads it), checked and loaded
image_from_fleet() {
  local arch tmp code i want got
  arch=$(arch)
  tmp=$(mktemp -p /var/tmp caracal-image-XXXXXX.tar)
  for i in $(seq 1 360); do
    code=$(curl -sS -u "$FLEET_USER:$FLEET_PASSWORD" -D "$tmp.headers" -o "$tmp" -w '%{http_code}' \
      "$HUB/api/proxy/image?version=$VERSION&arch=$arch") || code=000
    [ "$code" = 200 ] && break
    if [ "$code" != 202 ]; then
      echo "Image download from the hub failed (HTTP $code): $(head -c 300 "$tmp")" >&2
      rm -f "$tmp" "$tmp.headers"; return 1
    fi
    [ $((i % 6)) -eq 1 ] && echo 'The hub is downloading the CARACAL image…'
    sleep 10
  done
  want=$(sed -n 's/^[Xx]-[Ss]ha256:[[:space:]]*//p' "$tmp.headers" | tr -d '\r')
  got=$(sha256sum "$tmp" | cut -d' ' -f1)
  if [ -z "$want" ] || [ "$want" != "$got" ]; then
    echo 'CARACAL image from the hub: checksum mismatch' >&2; rm -f "$tmp" "$tmp.headers"; return 1
  fi
  docker load -i "$tmp"
  rm -f "$tmp" "$tmp.headers"
}
# --- end of download through the hub ---

if [ -n "$VIA_FLEET" ]; then
  if [ -n "$AUTH_FILE" ]; then
    FLEET_USER=$(cut -d: -f1 "$AUTH_FILE"); FLEET_PASSWORD=$(cut -d: -f2- "$AUTH_FILE")
  else
    FLEET_USER=enroll; FLEET_PASSWORD=$TOKEN
  fi
  [ -n "$FLEET_PASSWORD" ] || { echo '--via-fleet needs --token or --fleet-auth-file' >&2; exit 2; }
  step 'Downloads go through the hub (apt/apk, Docker, CARACAL)'
  if [ "$PKG" = apt ]; then apt_via_fleet "$HUB" "$FLEET_USER" "$FLEET_PASSWORD"; else apk_via_fleet "$HUB" "$FLEET_USER" "$FLEET_PASSWORD"; fi
fi

step '[1/7] System packages (X display, Openbox)'
if [ "$PKG" = apt ]; then
  apt-get update -qq
  # a reinstallation also replaces the files of packages that are already installed (a broken X server)
  apt-get install -y -qq ${REINSTALL:+--reinstall xserver-xorg-core} ca-certificates curl xserver-xorg \
    xserver-xorg-legacy xinit openbox unclutter x11-xserver-utils dbus-x11 python3 python3-requests python3-psutil >/dev/null
  # small boards (Raspberry Pi 4 with 1-2 GB): compressed swap in memory (zram), unless there is one already
  MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
  if [ "${MEM_MB:-0}" -le 2048 ] && ! swapon --show=NAME --noheadings 2>/dev/null | grep -q zram; then
    if apt-get install -y -qq zram-tools >/dev/null 2>&1; then
      printf 'ALGO=zstd\nPERCENT=50\nPRIORITY=100\n' > /etc/default/zramswap
      systemctl enable zramswap.service >/dev/null 2>&1 || true
      systemctl restart zramswap.service >/dev/null 2>&1 && echo "zram swap on (${MEM_MB} MB of memory)" || true
    fi
  fi
else
  # Docker is in Alpine's community repository
  sed -i -E 's@^#[[:space:]]*(.*/community)[[:space:]]*$@\1@' /etc/apk/repositories
  apk update -q
  # eudev: Xorg finds the graphics card and input devices through udev
  apk add -q ca-certificates curl bash shadow eudev xorg-server xf86-input-libinput xinit openbox unclutter-xfixes \
    xset xsetroot xrandr xhost dbus dbus-x11 mesa-dri-gallium mesa-egl font-dejavu python3 py3-requests py3-psutil \
    >/dev/null
  [ -z "$REINSTALL" ] || apk fix -q xorg-server xinit openbox mesa-dri-gallium >/dev/null 2>&1 || true
  if command -v setup-devd >/dev/null; then setup-devd udev >/dev/null 2>&1 || true; fi
  # firmware of the usual PC graphics (a full kernel; virtual machines use virtio/bochs without firmware)
  if [ -d "/lib/modules/$(uname -r)/kernel/drivers/gpu/drm/i915" ]; then
    apk add -q linux-firmware-i915 linux-firmware-amdgpu linux-firmware-radeon >/dev/null 2>&1 || true
  fi
fi

step '[2/7] Docker'
DOCKER_CHANGED=''
if [ -n "$DOCKER_POOL" ]; then
  # written before Docker is installed, so that it starts with these networks right away
  install -d -m 755 /etc/docker
  DOCKER_CHANGED=$(python3 - "$DOCKER_POOL" <<'PY'
import ipaddress, json, sys
from pathlib import Path
try:
    net = ipaddress.ip_network(sys.argv[1], strict=False)
except ValueError:
    sys.exit(f'Invalid --docker-pool {sys.argv[1]}')
if net.version != 4 or not net.is_private or not 16 <= net.prefixlen <= 23:
    sys.exit(f'--docker-pool must be a private IPv4 range from /16 to /23, not {sys.argv[1]}')
bridge_half, pool_half = net.subnets(prefixlen_diff=1)
bridge = next(bridge_half.subnets(new_prefix=24))
path = Path('/etc/docker/daemon.json')
try:
    current = json.loads(path.read_text())
except (OSError, ValueError):
    current = {}
wanted = {**current, 'bip': f'{bridge.network_address + 1}/24',
          'default-address-pools': [{'base': str(pool_half), 'size': 24}]}
if wanted != current:
    path.write_text(json.dumps(wanted, indent=2) + '\n')
    print('changed')
print(f'Docker networks: bridge {bridge}, pool {pool_half}', file=sys.stderr)
PY
  ) || exit 2
fi
if [ "$PKG" = apk ]; then
  docker compose version >/dev/null 2>&1 || apk add -q docker docker-cli-compose >/dev/null
elif ! docker compose version >/dev/null 2>&1; then
  if [ -n "$VIA_FLEET" ]; then
    # Docker's own repository through the hub (what get.docker.com would set up)
    . /etc/os-release
    DIST=debian; [ "${ID:-}" = ubuntu ] && DIST=ubuntu
    install -d -m 755 /etc/apt/keyrings
    curl -fsSL -u "$FLEET_USER:$FLEET_PASSWORD" "$HUB/apt/download.docker.com/linux/$DIST/gpg" -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] $HUB/apt/download.docker.com/linux/$DIST ${VERSION_CODENAME:-bookworm} stable" \
      > /etc/apt/sources.list.d/docker.list
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin >/dev/null \
      || apt-get install -y -qq docker.io docker-compose
  else
    # official Docker packages (include "docker compose"); Debian's docker.io as a fallback
    curl -fsSL https://get.docker.com | sh || apt-get install -y -qq docker.io docker-compose
  fi
fi
svc_enable docker
if [ "$INIT" = systemd ]; then systemctl start docker; else rc-service docker start >/dev/null 2>&1 || true; fi
# a Docker that was already running reads daemon.json only when it starts
[ -z "$DOCKER_CHANGED" ] || svc_restart docker
for i in $(seq 1 30); do docker info >/dev/null 2>&1 && break; sleep 2; done
docker compose version

if [ -n "$REINSTALL" ]; then
  step 'Reinstallation: removing the CARACAL containers and images'
  if [ -f "$NODE_DIR/compose.yml" ]; then
    (cd "$NODE_DIR" && docker compose down --remove-orphans --rmi all) || true
  fi
  rm -f "$NODE_DIR/compose.yml" "$NODE_DIR/.env"
  # the browser profile (cache, cookies); the logins of websites are kept in CARACAL's database
  rm -rf /var/lib/caracal/chromium
  if [ -n "$WIPE_DATA" ] && [ -d /var/lib/caracal ]; then
    find /var/lib/caracal -mindepth 1 -maxdepth 1 -exec rm -rf {} +
    echo 'CARACAL data removed (playlist, media, logins, notifications, settings).'
  fi
fi

step '[3/7] User and data folders'
id caracal >/dev/null 2>&1 || useradd -m -s /bin/bash caracal
for g in video audio input render; do getent group "$g" >/dev/null && add_to_group caracal "$g"; done
install -d -o caracal -g caracal /var/lib/caracal /var/lib/caracal/media /var/lib/caracal/chromium /home/caracal \
  /home/caracal/.config /home/caracal/.config/openbox
chown -R caracal:caracal /var/lib/caracal

# a PC: graphics drivers are normally loaded at boot; load them now on a system that has just been installed
if [ ! -e /dev/dri ] && command -v modprobe >/dev/null && ! grep -qi raspberry /proc/device-tree/model 2>/dev/null; then
  for m in i915 amdgpu radeon nouveau virtio-gpu bochs vmwgfx; do modprobe -q "$m" 2>/dev/null || true; done
  sleep 3
fi

# Raspberry Pi: the player and the overlay need the KMS graphics driver (/dev/dri, passed to the containers).
# Raspberry Pi OS enables it by default, DietPi does not. The change needs a reboot; this is checked before anything
# is converted, so a node is never left without CARACAL. Exit code 5 = run again after the reboot.
if grep -qi raspberry /proc/device-tree/model 2>/dev/null; then
  CONFIG_TXT=''
  for f in /boot/firmware/config.txt /boot/config.txt; do [ -f "$f" ] && { CONFIG_TXT=$f; break; }; done
  if [ -n "$CONFIG_TXT" ] && ! grep -Eq '^[[:blank:]]*dtoverlay=vc4-f?kms-v3d' "$CONFIG_TXT"; then
    if [ -x /boot/dietpi/func/dietpi-set_hardware ]; then
      /boot/dietpi/func/dietpi-set_hardware rpi-opengl vc4-kms-v3d
    else
      printf '\n[all]\ndtoverlay=vc4-kms-v3d\n' >> "$CONFIG_TXT"
    fi
    echo "KMS graphics driver enabled in $CONFIG_TXT"
    if [ ! -e /dev/dri ]; then
      echo 'REBOOT REQUIRED: reboot the device and run the installation again to finish it.'
      exit 5
    fi
  fi
fi
[ -e /dev/dri ] || { echo 'No graphics device (/dev/dri) found: the CARACAL player needs a KMS/DRM display driver.' >&2; exit 6; }

step '[4/7] Converting an existing classic CARACAL installation (if any)'
if [ -f /opt/caracal/app/main.py ] && [ "$INIT" = systemd ]; then
  for svc in caracal-player caracal-overlay caracal-boot-info caracal; do
    systemctl disable --now "$svc.service" >/dev/null 2>&1 || true
    rm -f "/etc/systemd/system/$svc.service"
  done
  mv /opt/caracal "/opt/caracal.legacy-$(date +%Y%m%d-%H%M%S)"
  rm -f /etc/sudoers.d/caracal
  echo 'Classic installation converted; data in /var/lib/caracal kept.'
else
  echo 'No classic installation found.'
fi

step '[5/7] X display'
install -d /etc/X11/xorg.conf.d
if [ "$PKG" = apt ]; then
  cat > /etc/X11/Xwrapper.config <<'EOF'
allowed_users=anybody
needs_root_rights=yes
EOF
fi
if [ -d /sys/module/vc4 ] || grep -qi raspberry /proc/device-tree/model 2>/dev/null; then
  cat > /etc/X11/xorg.conf.d/99-vc4.conf <<'EOF'
Section "OutputClass"
    Identifier "vc4"
    MatchDriver "vc4"
    Driver "modesetting"
    Option "PrimaryGPU" "true"
EndSection
EOF
fi
# systemd starts X as the user caracal (startx through Xwrapper); OpenRC starts it as root and the session (Openbox)
# runs as caracal
if [ "$INIT" = systemd ]; then SESSION='exec openbox-session'
else SESSION="exec su -s /bin/sh caracal -c 'HOME=/home/caracal DISPLAY=:0 exec openbox-session'"; fi
cat > /home/caracal/.xinitrc <<EOF
#!/bin/sh
export DISPLAY=:0
export XAUTHORITY=/home/caracal/.Xauthority
xset -dpms
xset s off
xset s noblank
xsetroot -solid black
# the CARACAL containers run as this user: let them in also after startx renewed the cookie in ~/.Xauthority
# (the containers see the file they were started with)
xhost +SI:localuser:caracal >/dev/null
xrandr --auto
unclutter -idle 1 -root &
$SESSION
EOF
cat > /home/caracal/.config/openbox/rc.xml <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<openbox_config xmlns="http://openbox.org/3.4/rc">
  <applications>
    <application class="Chromium*" name="*">
      <decor>no</decor><fullscreen>yes</fullscreen><maximized>yes</maximized>
      <position force="yes"><x>0</x><y>0</y></position><focus>yes</focus><desktop>all</desktop>
    </application>
  </applications>
</openbox_config>
EOF
chmod 755 /home/caracal/.xinitrc
# compose.yml mounts this file into the containers; Docker would create a directory if it did not exist yet
rmdir /home/caracal/.Xauthority 2>/dev/null || true
[ -f /home/caracal/.Xauthority ] || install -m 600 /dev/null /home/caracal/.Xauthority
chown -R caracal:caracal /home/caracal
if [ "$INIT" = openrc ]; then
  cat > /etc/init.d/caracal-display <<'EOF'
#!/sbin/openrc-run
# CARACAL display: X on vt7 (the console stays on tty1) with Openbox, restarted when it stops
description="CARACAL Display Server"
supervisor=supervise-daemon
command=/usr/bin/xinit
command_args="/home/caracal/.xinitrc -- /usr/bin/Xorg :0 vt7 -nolisten tcp -nocursor"
respawn_delay=5
respawn_max=0
output_log=/var/log/caracal-display.log
error_log=/var/log/caracal-display.log

depend() {
  need localmount
  use udev dbus
  after docker
}

start_pre() {
  rm -f /tmp/.X0-lock /tmp/.X11-unix/X0
}
EOF
  chmod 755 /etc/init.d/caracal-display
  rc-update add dbus default >/dev/null 2>&1 || true
  rc-service dbus start >/dev/null 2>&1 || true
  svc_enable caracal-display
  svc_restart caracal-display
else
cat > /etc/systemd/system/caracal-display.service <<'EOF'
[Unit]
Description=CARACAL Display Server
After=systemd-user-sessions.service getty@tty1.service
Conflicts=getty@tty1.service
Wants=systemd-user-sessions.service

[Service]
Type=simple
User=caracal
Group=caracal
PAMName=login
TTYPath=/dev/tty1
StandardInput=tty
StandardOutput=journal
StandardError=journal
TTYReset=yes
TTYVHangup=yes
TTYVTDisallocate=yes
Environment=HOME=/home/caracal
Environment=DISPLAY=:0
Environment=XAUTHORITY=/home/caracal/.Xauthority
WorkingDirectory=/home/caracal
ExecStartPre=/bin/rm -f /tmp/.X0-lock
ExecStartPre=/bin/rm -f /tmp/.X11-unix/X0
ExecStart=/usr/bin/startx /home/caracal/.xinitrc -- :0 vt1 -keeptty -nolisten tcp -nocursor
Restart=always
RestartSec=5

[Install]
WantedBy=graphical.target
EOF
systemctl daemon-reload
systemctl set-default graphical.target >/dev/null
systemctl enable caracal-display.service >/dev/null
systemctl restart caracal-display.service
fi

step '[6/7] CARACAL containers'
install -d -m 755 "$NODE_DIR"
install -m 644 "$SRC_DIR/caracal-compose.yml" "$NODE_DIR/compose.yml"
gid_of() { getent group "$1" | cut -d: -f3 | grep .; }
# the containers join "video" and "render" (Docker Compose 5 refuses the same group twice): a system without a
# "render" group (Alpine) gets one; udev gives it the render nodes of the graphics card (/dev/dri/renderD*)
if ! gid_of render >/dev/null; then
  if command -v groupadd >/dev/null; then groupadd -r render; else addgroup -S render; fi
  add_to_group caracal render
  if command -v udevadm >/dev/null; then udevadm trigger --subsystem-match=drm >/dev/null 2>&1 || true; fi
fi
cat > "$NODE_DIR/.env" <<EOF
CARACAL_IMAGE=$IMAGE
CARACAL_VERSION=$VERSION
CARACAL_UID=$(id -u caracal)
CARACAL_GID=$(id -g caracal)
CARACAL_VIDEO_GID=$(gid_of video || echo 44)
CARACAL_RENDER_GID=$(gid_of render || gid_of video || echo 44)
CARACAL_AUDIO_GID=$(gid_of audio || echo 29)
EOF
cd "$NODE_DIR"
if [ -n "$VIA_FLEET" ]; then
  image_from_fleet
else
  docker compose pull
fi
docker compose up -d
for i in $(seq 1 60); do
  curl -fsS http://127.0.0.1:8080/api/setup-status >/dev/null 2>&1 && break
  sleep 3
done
curl -fsS http://127.0.0.1:8080/api/setup-status >/dev/null || { docker compose logs --tail 50; exit 4; }
echo "CARACAL $VERSION is running"

step '[7/7] Fleet Agent'
if [ -n "$SKIP_AGENT" ]; then
  echo 'Fleet Agent already installed.'
  if [ -n "$ADMIN_FILE" ]; then
    python3 /opt/caracal-agent/agent.py set-admin "$ADMIN_FILE" \
      || echo 'WARNING: the web administrator was not set; set it in CARACAL Fleet (device -> Web administration).'
  fi
else
  ARGS=(--hub "$HUB" --token "$TOKEN")
  [ -n "$NAME" ] && ARGS+=(--name "$NAME")
  # the enrolled agent switches apt to its own device credentials
  [ -n "$VIA_FLEET" ] && ARGS+=(--download-source fleet)
  # the agent creates CARACAL's web administrator through the node's first-run setup
  [ -n "$ADMIN_FILE" ] && ARGS+=(--admin-file "$ADMIN_FILE")
  # the node template of the hub (applied once the node reports)
  [ -n "$TEMPLATE" ] && ARGS+=(--template "$TEMPLATE")
  bash "$SRC_DIR/install-agent.sh" "${ARGS[@]}"
fi
if [ -n "$ADMIN_FILE" ]; then rm -f "$ADMIN_FILE"; fi
IP=$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -1)
[ -n "$IP" ] || IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo "==> Done. CARACAL admin: http://${IP}:8080"
