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
# Log: /var/log/caracal-firstboot.log, progress also with "journalctl -u caracal-firstboot".
set -uo pipefail

LIB=/usr/local/lib/caracal-firstboot
CONF=/etc/caracal-firstboot.conf
DONE=/var/lib/caracal-firstboot.done
LOG=/var/log/caracal-firstboot.log

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
    if [ -f /boot/dietpi/.install_stage ] && [ "$(cat /boot/dietpi/.install_stage)" != 2 ]; then busy='DietPi setup'; fi
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
    curl -fsS --max-time 10 "$HUB/api/health" >/dev/null && break
    [ "$i" -eq 60 ] && { echo 'The hub is not reachable, retrying later.'; return 1; }
    sleep 10
  done
  wait_for_os_setup

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
  if bash "$work/install-node.sh" --hub "$HUB" --token "$TOKEN" --name "$name" --image "$image" \
       --version "${version:-latest}"; then
    date -Is > "$DONE"
    rm -rf "$work" "$CONF"
    systemctl disable caracal-firstboot.service >/dev/null 2>&1
    echo '===== CARACAL node installed'
    return 0
  fi
  rm -rf "$work"
  echo 'Installation failed, retrying in a minute.'
  return 1
}

case "${1:-install}" in
  install) install_service ;;
  run) run_install ;;
  *) echo "Usage: $0 install|run" >&2; exit 2 ;;
esac
