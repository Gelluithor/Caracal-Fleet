#!/bin/bash
# Turns a Raspberry Pi (Raspberry Pi OS Lite / DietPi, arm64) or a Debian/Ubuntu PC into a CARACAL node
# running on Docker, and connects it to CARACAL Fleet.
#
#   sudo bash install-node.sh --hub https://fleet.example --token ENROLL_TOKEN \
#        --image ghcr.io/OWNER/caracal-node --version 2026.10.06 [--name NAME]
#
# The host only runs Docker, the X display (Xorg + Openbox on tty1) and the Fleet Agent; the CARACAL app,
# player and overlay run in containers. An existing classic CARACAL installation (/opt/caracal) is converted:
# its data in /var/lib/caracal (playlists, media, logins, database) are kept and the old application is moved
# to /opt/caracal.legacy-<date>.
set -euo pipefail

HUB=''; TOKEN=''; NAME=''; IMAGE=''; VERSION='latest'; SKIP_AGENT=''
while [ $# -gt 0 ]; do
  case "$1" in
    --hub) HUB=${2%/}; shift 2;;
    --token) TOKEN=$2; shift 2;;
    --name) NAME=$2; shift 2;;
    --image) IMAGE=$2; shift 2;;
    --version) VERSION=$2; shift 2;;
    --skip-agent) SKIP_AGENT=1; shift;;   # used when the running Fleet Agent converts its own node
    *) echo "Unknown argument: $1" >&2; exit 2;;
  esac
done
[ "$(id -u)" -eq 0 ] || { echo 'Run as root (sudo).' >&2; exit 1; }
[ -n "$HUB" ] && [ -n "$IMAGE" ] || { echo '--hub and --image are required' >&2; exit 2; }
command -v apt-get >/dev/null || { echo 'Only Debian based systems (Raspberry Pi OS, DietPi, Debian, Ubuntu) are supported.' >&2; exit 3; }
SRC_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
NODE_DIR=/opt/caracal-node
export DEBIAN_FRONTEND=noninteractive

step() { echo "==> $*"; }

step '[1/7] System packages (X display, Openbox)'
apt-get update -qq
apt-get install -y -qq ca-certificates curl xserver-xorg xserver-xorg-legacy xinit openbox unclutter \
  x11-xserver-utils dbus-x11 python3 python3-requests python3-psutil >/dev/null

step '[2/7] Docker'
if ! docker compose version >/dev/null 2>&1; then
  # official Docker packages (include "docker compose"); Debian's docker.io as a fallback
  curl -fsSL https://get.docker.com | sh || apt-get install -y -qq docker.io docker-compose
fi
systemctl enable --now docker >/dev/null
docker compose version

step '[3/7] User and data folders'
id caracal >/dev/null 2>&1 || useradd -m -s /bin/bash caracal
for g in video audio input render; do getent group "$g" >/dev/null && usermod -a -G "$g" caracal; done
install -d -o caracal -g caracal /var/lib/caracal /var/lib/caracal/media /var/lib/caracal/chromium /home/caracal \
  /home/caracal/.config /home/caracal/.config/openbox
chown -R caracal:caracal /var/lib/caracal

step '[4/7] Converting an existing classic CARACAL installation (if any)'
if [ -f /opt/caracal/app/main.py ]; then
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

step '[5/7] X display on tty1'
install -d /etc/X11/xorg.conf.d
cat > /etc/X11/Xwrapper.config <<'EOF'
allowed_users=anybody
needs_root_rights=yes
EOF
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
cat > /home/caracal/.xinitrc <<'EOF'
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
exec openbox-session
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

step '[6/7] CARACAL containers'
install -d -m 755 "$NODE_DIR"
install -m 644 "$SRC_DIR/caracal-compose.yml" "$NODE_DIR/compose.yml"
gid_of() { getent group "$1" | cut -d: -f3 | grep .; }
cat > "$NODE_DIR/.env" <<EOF
CARACAL_IMAGE=$IMAGE
CARACAL_VERSION=$VERSION
CARACAL_UID=$(id -u caracal)
CARACAL_GID=$(id -g caracal)
CARACAL_VIDEO_GID=$(gid_of video || echo 44)
CARACAL_RENDER_GID=$(gid_of render || gid_of video || echo 44)
EOF
cd "$NODE_DIR"
docker compose pull
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
else
  ARGS=(--hub "$HUB" --token "$TOKEN")
  [ -n "$NAME" ] && ARGS+=(--name "$NAME")
  bash "$SRC_DIR/install-agent.sh" "${ARGS[@]}"
fi
IP=$(hostname -I | awk '{print $1}')
echo "==> Done. CARACAL admin: http://${IP}:8080"
