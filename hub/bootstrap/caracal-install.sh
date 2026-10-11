#!/bin/sh
# One-command installation of a CARACAL node on a running Linux: Debian, Ubuntu, Raspberry Pi OS, DietPi or Alpine.
# CARACAL Fleet shows the command with the hub address and the enrollment token (Add device -> One command):
#
#   curl -fsSL https://HUB/api/bootstrap/caracal-install.sh | sudo sh -s -- --hub https://HUB --token TOKEN
#   wget -qO- https://HUB/api/bootstrap/caracal-install.sh | sh -s -- --hub https://HUB --token TOKEN   (Alpine, root)
#
# Options: --name NAME (otherwise <prefix>-<device id>), --name-prefix PREFIX, --template ID (a node template of the
# hub), --via-fleet (packages, Docker and CARACAL through the hub, no internet access needed).
#
# The installation itself is "caracal-firstboot.sh run", the same as on the zero-touch SD cards: Docker, the X
# display, the CARACAL containers and the Fleet Agent. POSIX sh, so that it also starts on Alpine without bash.
set -eu

HUB='' TOKEN='' NAME='' PREFIX='caracal' TEMPLATE='' VIA_FLEET=''
while [ $# -gt 0 ]; do
  case "$1" in
    --hub) HUB=${2%/}; shift 2;;
    --token) TOKEN=$2; shift 2;;
    --name) NAME=$2; shift 2;;
    --name-prefix) PREFIX=$2; shift 2;;
    --template) TEMPLATE=$2; shift 2;;
    --via-fleet) VIA_FLEET=1; shift;;
    *) echo "Unknown argument: $1" >&2; exit 2;;
  esac
done
[ "$(id -u)" -eq 0 ] || { echo 'Run as root (sudo, or doas on Alpine).' >&2; exit 1; }
[ -n "$HUB" ] && [ -n "$TOKEN" ] || { echo '--hub and --token are required' >&2; exit 2; }
case "$PREFIX" in *[!a-z0-9-]*|'') echo 'Invalid --name-prefix' >&2; exit 2;; esac

fetch() {   # URL FILE
  if command -v curl >/dev/null; then curl -fsSL --max-time 120 "$1" -o "$2"; else wget -qO "$2" "$1"; fi
}

if command -v apk >/dev/null; then
  echo '==> Alpine Linux: bash and curl'
  if [ -n "$VIA_FLEET" ]; then   # Alpine's repositories through the hub (readable by root only)
    scheme=${HUB%%://*}; rest=${HUB#*://}
    sed -i -E "s#https?://dl-cdn\.alpinelinux\.org#$scheme://enroll:$TOKEN@$rest/apt/dl-cdn.alpinelinux.org#" /etc/apk/repositories
    chmod 600 /etc/apk/repositories
  fi
  sed -i -E 's@^#[[:space:]]*(.*/community)[[:space:]]*$@\1@' /etc/apk/repositories
  apk update -q
  apk add -q bash curl coreutils
elif ! command -v bash >/dev/null || ! command -v curl >/dev/null; then
  apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq bash curl ca-certificates
fi

LIB=/usr/local/lib/caracal-firstboot
install -d -m 755 "$LIB"
fetch "$HUB/api/bootstrap/caracal-firstboot.sh" "$LIB/caracal-firstboot.sh.new"
chmod 755 "$LIB/caracal-firstboot.sh.new"
mv "$LIB/caracal-firstboot.sh.new" "$LIB/caracal-firstboot.sh"

q() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }   # shell quoting for the settings file
umask 077
{
  echo '# CARACAL installation: hub and enrollment token (removed when the node is installed)'
  echo "HUB=$(q "$HUB")"
  echo "TOKEN=$(q "$TOKEN")"
  echo "NAME_PREFIX=$(q "$PREFIX")"
  [ -n "$NAME" ] && echo "NODE_NAME=$(q "$NAME")"
  [ -n "$TEMPLATE" ] && echo "TEMPLATE=$(q "$TEMPLATE")"
  [ -n "$VIA_FLEET" ] && echo 'DOWNLOAD_SOURCE=fleet'
} > /etc/caracal-firstboot.conf
rm -f /var/lib/caracal-firstboot.done
echo "==> Installing the CARACAL node (log: /var/log/caracal-firstboot.log)"
exec bash "$LIB/caracal-firstboot.sh" run
