#!/bin/bash
# Installs or updates the CARACAL Fleet Agent on an existing CARACAL node.
#
# The CARACAL node itself (player, playlists, media, database, Chromium profile) is never modified
# or reinstalled. An existing enrollment (device id + token in /etc/caracal-agent.json) is kept.
#
#   sudo bash install-agent.sh --hub https://caracal.example --token ENROLL_TOKEN [--name NAME]
#        [--reenroll] [--local-api http://127.0.0.1:8080]
#
# Can also be piped from the hub:
#   curl -fsSL https://HUB/api/bootstrap/install-agent.sh | sudo bash -s -- --hub https://HUB --token TOKEN
set -euo pipefail

HUB=''; TOKEN=''; NAME=''; REENROLL=''; LOCAL_API=''
while [ $# -gt 0 ]; do
  case "$1" in
    --hub) HUB=${2%/}; shift 2;;
    --token) TOKEN=$2; shift 2;;
    --name) NAME=$2; shift 2;;
    --reenroll) REENROLL=--reenroll; shift;;
    --local-api) LOCAL_API=$2; shift 2;;
    *) echo "Unknown argument: $1" >&2; exit 2;;
  esac
done

[ "$(id -u)" -eq 0 ] || { echo 'Run as root (sudo).' >&2; exit 1; }
[ -n "$HUB" ] || { echo '--hub is required' >&2; exit 2; }

if [ ! -d /opt/caracal ] && [ ! -f /opt/caracal-node/compose.yml ]; then
  echo 'CARACAL was not found (/opt/caracal or /opt/caracal-node). Install the node first:' >&2
  echo 'in CARACAL Fleet use "Add device" -> "Install CARACAL node", or run install-node.sh.' >&2
  exit 3
fi

echo '==> Checking Python dependencies'
if ! python3 -c 'import requests, psutil' 2>/dev/null; then
  apt-get update -qq || true
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-requests python3-psutil
fi

SRC_DIR=''
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  SRC_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
fi
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT
if [ -n "$SRC_DIR" ] && [ -f "$SRC_DIR/agent.py" ]; then
  cp "$SRC_DIR/agent.py" "$TMP"
else
  echo "==> Downloading agent from $HUB"
  curl -fsSL "$HUB/api/bootstrap/agent.py" -o "$TMP"
fi
python3 -m py_compile "$TMP"
NEW_VERSION=$(python3 "$TMP" version)

OLD_VERSION='none'
# Read the version statically; agents older than 4.0 have no CLI and would start their loop.
[ -f /opt/caracal-agent/agent.py ] && OLD_VERSION=$(sed -n "s/^VERSION *= *'\([^']*\)'.*/\1/p;s/.*;VERSION='\([^']*\)'.*/\1/p" /opt/caracal-agent/agent.py | head -1)
echo "==> Fleet Agent $OLD_VERSION -> $NEW_VERSION"

install -d -m 755 /opt/caracal-agent
install -d -m 700 /var/lib/caracal-agent
install -m 755 "$TMP" /opt/caracal-agent/agent.py

echo '==> Enrollment'
ARGS=(enroll --hub "$HUB" --token "$TOKEN")
[ -n "$NAME" ] && ARGS+=(--name "$NAME")
[ -n "$REENROLL" ] && ARGS+=("$REENROLL")
[ -n "$LOCAL_API" ] && ARGS+=(--local-api "$LOCAL_API")
python3 /opt/caracal-agent/agent.py "${ARGS[@]}"

UNIT=/etc/systemd/system/caracal-agent.service
cat > "$UNIT.new" <<'EOF'
[Unit]
Description=CARACAL Fleet Agent
After=network-online.target caracal.service
Wants=network-online.target

[Service]
User=root
ExecStart=/usr/bin/python3 /opt/caracal-agent/agent.py run
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
mv "$UNIT.new" "$UNIT"
systemctl daemon-reload
systemctl enable caracal-agent.service >/dev/null
systemctl restart caracal-agent.service
sleep 3
systemctl is-active --quiet caracal-agent.service || { journalctl -u caracal-agent.service -n 30 --no-pager; exit 4; }

echo '==> Checking local CARACAL API'
python3 /opt/caracal-agent/agent.py check || echo 'WARNING: agent runs, but the local CARACAL Fleet API is not fully available (see docs/LOCAL-API.md).'
echo '==> Fleet Agent is running'
