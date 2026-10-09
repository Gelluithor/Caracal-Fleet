#!/bin/bash
# Zero-touch installation of a CARACAL node from a prepared SD card.
#
# CARACAL Fleet generates the files for the boot partition of a freshly written Raspberry Pi OS Lite or DietPi
# card: this script, caracal-firstboot.conf (hub address and enrollment token) and the hook of the OS
# (cloud-init user-data or DietPi's Automation_Custom_Script.sh), which runs "caracal-firstboot.sh install".
#
#   install   copies the script and its configuration to the system (the token is removed from the boot
#             partition) and starts caracal-firstboot.service in the background
#   run       (the service) waits for the network and the end of the OS first-boot setup, downloads the
#             installer from the hub and turns the device into a CARACAL node; retried until it succeeds
#
# Without internet access (download source "fleet" on the card) the clock is set from the hub when no time server
# synchronised it (a Raspberry Pi has no clock battery; HTTPS and apt need the right time). DietPi additionally:
#   dietpi-prepare  (Automation_Custom_PreScript.sh, early in DietPi's first boot) installs a login hook
#   dietpi-offline  (that hook, /etc/bashrc.d/00-caracal-offline.sh) runs in the first login shell right before
#                   DietPi's first-run setup (dietpi-login from /etc/bashrc.d/dietpi.bash, *.sh files come first):
#                   it waits for the hub, sets the clock, points apt to the hub and skips DietPi's online update
#                   from GitHub, which cannot work without internet. Running in the login shell itself, it cannot
#                   come too late.
#
# Log: /var/log/caracal-firstboot.log, progress also with "journalctl -u caracal-firstboot".
set -uo pipefail

LIB=${CARACAL_FIRSTBOOT_LIB:-/usr/local/lib/caracal-firstboot}   # paths can be changed for tests
CONF=${CARACAL_FIRSTBOOT_CONF:-/etc/caracal-firstboot.conf}
DONE=/var/lib/caracal-firstboot.done
LOG=${CARACAL_FIRSTBOOT_LOG:-/var/log/caracal-firstboot.log}
STAGE=${CARACAL_DIETPI_STAGE:-/boot/dietpi/.install_stage}   # DietPi's install stage (0 online update, 1 setup, 2 done)

# --- download through the hub (keep in sync with install-node.sh, the agent and the hub) ---
APT_DIR=${CARACAL_APT_DIR:-/etc/apt}
APT_HOSTS='deb.debian.org security.debian.org ftp.debian.org archive.raspberrypi.com archive.raspberrypi.org raspbian.raspberrypi.com raspbian.raspberrypi.org download.docker.com dietpi.com'
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
# --- end of download through the hub ---

# Without a time source the clock of a Raspberry Pi is behind (last shutdown, or the date of the image) and HTTPS
# certificates look "not yet valid". Only when no time server synchronised the clock, the time is read from the
# hub's Date header without checking the certificate (nothing else is taken from that answer).
sync_clock_from_hub() {
  [ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" = yes ] && return 0
  local date hub_ts now diff
  date=$(curl -ksSI --max-time 15 "$HUB/api/health" 2>/dev/null | tr -d '\r' | sed -n 's/^[Dd]ate: //p' | head -1)
  [ -n "$date" ] || return 1
  hub_ts=$(date -d "$date" +%s 2>/dev/null) || return 1
  now=$(date +%s)
  diff=$(( hub_ts > now ? hub_ts - now : now - hub_ts ))
  if [ "$diff" -gt 120 ]; then
    date -s "@$hub_ts" >/dev/null && echo "Clock set from the hub: $(date -Is) (was ${diff} s off)"
  fi
}

boot_file() {
  for d in /boot/firmware /boot; do [ -f "$d/$1" ] && { echo "$d/$1"; return 0; }; done
  return 1
}

install_service() {
  [ -f "$DONE" ] && { echo 'CARACAL is already installed.'; exit 0; }
  local src conf
  src=$(boot_file caracal-firstboot.sh) || src=$0
  conf=$(boot_file caracal-firstboot.conf) || conf=''
  install -d -m 755 "$LIB"
  install -m 755 "$src" "$LIB/caracal-firstboot.sh"
  if [ -n "$conf" ]; then
    install -m 600 "$conf" "$CONF"
    rm -f "$conf"                       # the boot partition is readable by anyone holding the card
  fi
  [ -f "$CONF" ] || { echo "caracal-firstboot.conf not found" >&2; exit 2; }
  cat > /etc/systemd/system/caracal-firstboot.service <<EOF
[Unit]
Description=CARACAL zero-touch installation
Wants=network-online.target
After=network-online.target
ConditionPathExists=!$DONE

[Service]
Type=oneshot
ExecStart=$LIB/caracal-firstboot.sh run
Restart=on-failure
RestartSec=60
TimeoutStartSec=infinity

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable caracal-firstboot.service >/dev/null 2>&1
  # do not block the OS first-boot setup that called us
  systemctl start --no-block caracal-firstboot.service
  echo 'CARACAL installation started in the background (journalctl -u caracal-firstboot -f).'
}

wait_for_os_setup() {
  local i
  for i in $(seq 1 180); do                 # at most 30 minutes, then try anyway
    local busy=''
    if command -v cloud-init >/dev/null && ! cloud-init status 2>/dev/null | grep -qE 'done|disabled|error'; then
      busy='cloud-init'
    fi
    if [ -f "$STAGE" ] && [ "$(cat "$STAGE")" != 2 ]; then busy='DietPi setup'; fi
    if pgrep -x apt-get >/dev/null || pgrep -x dpkg >/dev/null || pgrep -x apt >/dev/null; then busy='apt'; fi
    [ -z "$busy" ] && return 0
    [ $((i % 6)) -eq 1 ] && echo "Waiting for $busy to finish…"
    sleep 10
  done
}

node_name() {
  local serial
  serial=$(tr -d '\0' < /proc/device-tree/serial-number 2>/dev/null || true)
  [ -n "$serial" ] || serial=$(cat /etc/machine-id 2>/dev/null || hostname)
  echo "${NAME_PREFIX:-caracal}-$(echo "$serial" | tail -c 7 | tr 'A-Z' 'a-z' | tr -cd 'a-z0-9')"
}

run_install() {
  exec > >(tee -a "$LOG") 2>&1
  echo "===== $(date -Is) CARACAL zero-touch installation"
  [ -f "$DONE" ] && { echo 'Already installed.'; return 0; }
  # shellcheck disable=SC1090
  . "$CONF"
  [ -n "${HUB:-}" ] && [ -n "${TOKEN:-}" ] || { echo "HUB and TOKEN must be set in $CONF"; return 2; }

  echo "Waiting for the hub $HUB"
  local i
  for i in $(seq 1 60); do
    sync_clock_from_hub || true
    curl -fsS --max-time 10 "$HUB/api/health" >/dev/null && break
    [ "$i" -eq 60 ] && { echo 'The hub is not reachable, retrying later.'; return 1; }
    sleep 10
  done
  wait_for_os_setup
  # the OS setup may reboot (e.g. DietPi after a kernel update) while a previous attempt was installing packages
  DEBIAN_FRONTEND=noninteractive dpkg --configure -a || true

  local name
  name=$(node_name)
  if [ "$(hostname)" != "$name" ]; then
    echo "Hostname: $name"
    hostnamectl set-hostname "$name" 2>/dev/null || echo "$name" > /etc/hostname
    grep -q "127.0.1.1" /etc/hosts && sed -i "s/^127\.0\.1\.1.*/127.0.1.1\t$name/" /etc/hosts \
      || echo -e "127.0.1.1\t$name" >> /etc/hosts
  fi

  local work cfg image version
  work=$(mktemp -d)
  for f in install-node.sh caracal-compose.yml install-agent.sh agent.py; do
    curl -fsS --max-time 60 "$HUB/api/bootstrap/$f" -o "$work/$f" || { echo "Download of $f failed"; return 1; }
  done
  cfg=$(curl -fsS --max-time 60 "$HUB/api/bootstrap/node-config") || { echo 'Node configuration not available'; return 1; }
  image=$(echo "$cfg" | sed -n 's/^image=//p')
  version=$(echo "$cfg" | sed -n 's/^version=//p')
  [ -n "$image" ] || { echo 'The hub has no CARACAL node image set (CARACAL updates), retrying later.'; return 1; }

  echo "Installing $image:${version:-latest} as $name"
  local code=0 extra=()
  [ -n "${DOCKER_POOL:-}" ] && extra+=(--docker-pool "$DOCKER_POOL")
  [ "${DOWNLOAD_SOURCE:-}" = fleet ] && extra+=(--via-fleet)
  bash "$work/install-node.sh" --hub "$HUB" --token "$TOKEN" --name "$name" --image "$image" \
    --version "${version:-latest}" "${extra[@]}" || code=$?
  rm -rf "$work"
  if [ "$code" -eq 0 ]; then
    date -Is > "$DONE"
    rm -f "$CONF"
    systemctl disable caracal-firstboot.service >/dev/null 2>&1
    echo '===== CARACAL node installed'
    return 0
  fi
  if [ "$code" -eq 5 ]; then
    # the graphics driver was enabled: the installation continues after the reboot (the service is still enabled)
    echo 'Rebooting to load the graphics driver, the installation continues afterwards.'
    systemctl reboot
    return 0
  fi
  echo "Installation failed (exit $code), retrying in a minute."
  return 1
}

HOOK=${CARACAL_DIETPI_HOOK:-/etc/bashrc.d/00-caracal-offline.sh}

dietpi_prepare() {
  # runs inside DietPi's first-boot service, before the network is up: copies this script and installs the hook
  install -d -m 755 "$LIB" "$(dirname "$HOOK")"
  local src
  src=$(boot_file caracal-firstboot.sh) || src=$0
  install -m 755 "$src" "$LIB/caracal-firstboot.sh"
  cat > "$HOOK" <<EOF
# CARACAL zero-touch without internet access (removed when done). Sourced by interactive bash shells before
# /etc/bashrc.d/dietpi.bash starts DietPi's first-run setup; one shell at a time (tty1 autologin, SSH).
if [ "\$(id -u)" = 0 ] && [ "\$(cat $STAGE 2>/dev/null)" = 0 ]; then
  flock /run/caracal-dietpi-offline.lock bash $LIB/caracal-firstboot.sh dietpi-offline
fi
EOF
  chmod 644 "$HOOK"
  echo "CARACAL: DietPi first boot without internet access prepared ($HOOK)"
}

dietpi_offline() {
  # stage 0 = DietPi's online update (GitHub) still to do; another shell may have finished meanwhile
  [ "$(cat "$STAGE" 2>/dev/null)" = 0 ] || return 0
  exec > >(tee -a "$LOG") 2>&1
  echo "===== $(date -Is) CARACAL: DietPi first boot without internet access"
  local conf i
  conf=$(boot_file caracal-firstboot.conf) || conf=$CONF
  # shellcheck disable=SC1090
  . "$conf"
  echo "Waiting for the hub $HUB"
  for i in $(seq 1 180); do                 # at most 30 minutes
    curl -ksS --max-time 10 -o /dev/null "$HUB/api/health" 2>/dev/null && break
    if [ "$i" -eq 180 ]; then
      echo 'The hub is not reachable: check the network, the DNS and the hub address; log in again to retry.'
      return 1
    fi
    [ $((i % 6)) -eq 1 ] && echo 'The hub is not reachable yet, waiting…'
    sleep 10
  done
  sync_clock_from_hub || echo 'The clock could not be read from the hub.'
  # DietPi wrote its sources (Debian, Raspberry Pi, dietpi.com) during its first-boot settings
  apt_via_fleet "$HUB" enroll "$TOKEN"
  echo 'apt downloads through the hub'
  # stage 1 = DietPi's online update done; its first-run setup then upgrades the packages through the hub
  echo 1 > "$STAGE"
  rm -f "$HOOK"
  echo 'DietPi online update of the first boot skipped, DietPi continues with its first-run setup'
}

case "${1:-install}" in
  install) install_service ;;
  run) run_install ;;
  dietpi-prepare) dietpi_prepare ;;
  dietpi-offline) dietpi_offline ;;
  *) echo "Usage: $0 install|run|dietpi-prepare|dietpi-offline" >&2; exit 2 ;;
esac
