#!/bin/sh
# Zero-touch installation of a CARACAL node on a PC with Alpine Linux (x86_64 or arm64), POSIX sh: the live system
# of the Alpine ISO has no bash.
#
# CARACAL Fleet generates caracal.apkovl.tar.gz, an Alpine overlay that the live system of the Alpine ISO (Standard
# or Extended) loads from the root of any FAT/ext4 USB stick or partition when it boots. The overlay starts this script
# from /etc/local.d/caracal.start:
#
#   live   (from the USB stick, root on tmpfs) sets up the network and the package repositories, installs Alpine on
#          the internal disk (ERASES it) with setup-disk, and reboots. The overlay's /etc is copied to the new system
#          (lbu), so the installed system runs this script again.
#   boot   (the installed system, at every boot until CARACAL is installed) creates the login, sets the time zone,
#          the time server and SSH, installs bash and curl, then runs "caracal-firstboot.sh run" in the background
#          (retried every minute, like the systemd service of the SD cards).
#
# Settings: /etc/caracal-firstboot.conf (HUB, TOKEN, NAME_PREFIX, DISK, TIMEZONE, LOGIN_USER, LOGIN_PASSWORD, SSH_KEY,
# STATIC_IP, GATEWAY, DNS, NTP, DOWNLOAD_SOURCE, TEMPLATE...). Log: /var/log/caracal-firstboot.log
set -u

DIR=${CARACAL_ALPINE_DIR:-/etc/caracal}
CONF=${CARACAL_FIRSTBOOT_CONF:-/etc/caracal-firstboot.conf}
LOG=${CARACAL_FIRSTBOOT_LOG:-/var/log/caracal-firstboot.log}
DONE=/var/lib/caracal-firstboot.done
SYSTEM_DONE=/var/lib/caracal-alpine-system.done
MARKER=/etc/caracal-zero-touch          # in the installed system: the disk already holds a CARACAL node
START=/etc/local.d/caracal.start

say() {
  echo "CARACAL: $*"
  echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"
  # the screen of the device shows the progress (local.d may run without a console)
  [ -w /dev/tty1 ] && echo "CARACAL: $*" > /dev/tty1 2>/dev/null
  return 0
}

load_conf() {
  [ -f "$CONF" ] || { say "$CONF not found"; exit 2; }
  # shellcheck disable=SC1090
  . "$CONF"
  : "${HUB:?}" "${TOKEN:?}"
}

# ---------------------------------------------------------------- network

interfaces() {   # wired network interfaces, Wi-Fi and virtual ones left out
  for i in /sys/class/net/*; do
    n=${i##*/}
    case "$n" in lo|wl*|docker*|veth*|br-*|virbr*|tun*|tap*) continue;; esac
    [ -d "$i/wireless" ] && continue
    [ -e "$i/device" ] || continue
    echo "$n"
  done
}

first_interface() {   # the interface with a cable plugged in, otherwise the first one
  for n in $(interfaces); do ip link set "$n" up 2>/dev/null; done
  sleep 3
  for n in $(interfaces); do [ "$(cat "/sys/class/net/$n/carrier" 2>/dev/null)" = 1 ] && { echo "$n"; return; }; done
  interfaces | head -n 1
}

network_interfaces() {   # contents of /etc/network/interfaces
  printf 'auto lo\niface lo inet loopback\n'
  if [ -n "${STATIC_IP:-}" ]; then
    n=$(first_interface)
    printf '\nauto %s\niface %s inet static\n\taddress %s\n\tgateway %s\n' "$n" "$n" "$STATIC_IP" "$GATEWAY"
  else
    for n in $(interfaces); do printf '\nauto %s\niface %s inet dhcp\n' "$n" "$n"; done
  fi
}

setup_network() {
  network_interfaces > /etc/network/interfaces
  if [ -n "${STATIC_IP:-}" ]; then
    : > /etc/resolv.conf
    for s in ${DNS:-$GATEWAY}; do echo "nameserver $s" >> /etc/resolv.conf; done
  fi
  rc-update add networking boot >/dev/null 2>&1
  rc-service networking restart >/dev/null 2>&1 || rc-service networking start >/dev/null 2>&1
}

wait_for_hub() {   # $1 = attempts of 10 s
  i=0
  while [ "$i" -lt "$1" ]; do
    wget -q --no-check-certificate -O /dev/null "$HUB/api/health" 2>/dev/null && return 0
    [ $((i % 6)) -eq 0 ] && say "waiting for the hub $HUB"
    i=$((i + 1))
    sleep 10
  done
  return 1
}

repositories() {   # Alpine's repositories of this release: the internet or the hub
  release=$(cut -d. -f1,2 /etc/alpine-release)
  base=https://dl-cdn.alpinelinux.org/alpine/v$release
  printf '%s/main\n%s/community\n' "$base" "$base"
}

hub_repositories() {   # <hub>/apt/dl-cdn.alpinelinux.org/... with the enrollment token (readable by root only)
  scheme=${HUB%%://*}
  rest=${HUB#*://}
  repositories | sed "s#^https://dl-cdn.alpinelinux.org#$scheme://enroll:$TOKEN@$rest/apt/dl-cdn.alpinelinux.org#"
}

write_repositories() {   # $1: keep the lines of the boot medium (live system)
  media=''
  [ -n "${1:-}" ] && media=$(grep '^/media/' /etc/apk/repositories 2>/dev/null)
  {
    [ -n "$media" ] && echo "$media"
    if [ "${DOWNLOAD_SOURCE:-}" = fleet ]; then hub_repositories; else repositories; fi
  } > /etc/apk/repositories.new
  chmod 600 /etc/apk/repositories.new
  mv /etc/apk/repositories.new /etc/apk/repositories
}

# ---------------------------------------------------------------- live system: install Alpine on the disk

boot_disks() {   # disks holding a mounted file system (the boot medium)
  awk '$1 ~ /^\/dev\// {print $1}' /proc/mounts | while read -r dev; do
    d=${dev#/dev/}
    for b in /sys/block/*; do
      [ -e "$b/$d" ] || [ "${b##*/}" = "$d" ] && echo "${b##*/}"
    done
  done | sort -u
}

pick_disk() {   # the largest internal disk that is not the boot medium (DISK=auto) or the one set on the card
  if [ -n "${DISK:-}" ] && [ "$DISK" != auto ]; then
    [ -b "$DISK" ] && echo "$DISK"
    return
  fi
  skip=" $(boot_disks | tr '\n' ' ') "
  best='' size=0
  for b in /sys/block/*; do
    n=${b##*/}
    case "$n" in loop*|ram*|sr*|zram*|fd*|md*|dm-*|mmcblk*boot*) continue;; esac
    case "$skip" in *" $n "*) continue;; esac
    s=$(cat "$b/size" 2>/dev/null || echo 0)
    [ "$s" -ge 8388608 ] || continue          # at least 4 GiB (512-byte sectors)
    if [ "$s" -gt "$size" ]; then best=$n; size=$s; fi
  done
  [ -n "$best" ] && echo "/dev/$best"
}

already_installed() {   # a partition of the disk holds a CARACAL node installed from such a stick
  mkdir -p /mnt/caracal-check
  for p in "$1"*; do
    [ "$p" = "$1" ] && continue
    mount -o ro "$p" /mnt/caracal-check 2>/dev/null || continue
    found=''
    [ -f "/mnt/caracal-check$MARKER" ] && found=1
    umount /mnt/caracal-check
    [ -n "$found" ] && return 0
  done
  return 1
}

live() {
  load_conf
  say 'zero-touch installation of Alpine Linux and CARACAL started'
  setup_network
  wait_for_hub 90 || { say "the hub $HUB is not reachable: check the network cable and the hub address"; exit 1; }
  disk=$(pick_disk)
  if [ -z "$disk" ]; then
    say 'no internal disk found (at least 4 GB); set the disk on the card in CARACAL Fleet'
    exit 1
  fi
  if already_installed "$disk"; then
    say "CARACAL is already installed on $disk. Remove the USB stick; the device powers off in 2 minutes."
    sleep 120
    poweroff
    exit 0
  fi
  say "installing Alpine Linux on $disk (the disk is erased)"
  hostname "${NAME_PREFIX:-caracal}"
  echo "${NAME_PREFIX:-caracal}" > /etc/hostname
  write_repositories keep-media
  apk update -q >> "$LOG" 2>&1
  date -u '+%Y-%m-%dT%H:%M:%SZ' > "$MARKER"
  # the overlay's /etc (this script, the settings, the network) goes to the new system with lbu
  if ! ERASE_DISKS="$disk" setup-disk -m sys "$disk" >> "$LOG" 2>&1; then
    say "setup-disk failed, see $LOG"
    tail -n 20 "$LOG" > /dev/tty1 2>/dev/null
    exit 1
  fi
  say 'Alpine Linux installed, rebooting into the new system'
  sleep 5
  reboot
}

# ---------------------------------------------------------------- installed system

setup_system() {
  [ -f "$SYSTEM_DONE" ] && return 0
  say 'preparing the system (login, time, SSH)'
  write_repositories
  apk update -q >> "$LOG" 2>&1 || return 1
  apk add -q bash curl coreutils tzdata chrony shadow >> "$LOG" 2>&1 || return 1
  if [ -n "${TIMEZONE:-}" ] && [ -f "/usr/share/zoneinfo/$TIMEZONE" ]; then
    ln -sf "/usr/share/zoneinfo/$TIMEZONE" /etc/localtime
    echo "$TIMEZONE" > /etc/timezone
  fi
  if [ -n "${NTP:-}" ]; then
    printf 'server %s iburst\nmakestep 1 3\nrtcsync\n' "$NTP" > /etc/chrony/chrony.conf
  fi
  rc-update add chronyd default >/dev/null 2>&1
  rc-service chronyd start >/dev/null 2>&1
  if [ -n "${LOGIN_PASSWORD:-}" ] || [ -n "${SSH_KEY:-}" ]; then
    user=${LOGIN_USER:-admin}
    id "$user" >/dev/null 2>&1 || adduser -D -s /bin/sh "$user"
    for g in wheel video audio input netdev; do addgroup "$user" "$g" >/dev/null 2>&1; done
    if [ -n "${LOGIN_PASSWORD:-}" ]; then
      printf '%s:%s\n' "$user" "$LOGIN_PASSWORD" | chpasswd >/dev/null
    else
      passwd -u "$user" >/dev/null 2>&1 || usermod -p '*' "$user" 2>/dev/null   # key only, no password
    fi
    apk add -q doas openssh >> "$LOG" 2>&1
    mkdir -p /etc/doas.d
    echo 'permit persist :wheel' > /etc/doas.d/caracal.conf
    chmod 600 /etc/doas.d/caracal.conf
    if [ -n "${SSH_KEY:-}" ]; then
      install -d -m 700 -o "$user" -g "$user" "/home/$user/.ssh"
      echo "$SSH_KEY" > "/home/$user/.ssh/authorized_keys"
      chown "$user:$user" "/home/$user/.ssh/authorized_keys"
      chmod 600 "/home/$user/.ssh/authorized_keys"
    fi
    if [ -z "${LOGIN_PASSWORD:-}" ]; then
      sed -i -E 's/^#?PasswordAuthentication .*/PasswordAuthentication no/' /etc/ssh/sshd_config
    fi
    rc-update add sshd default >/dev/null 2>&1
    rc-service sshd restart >/dev/null 2>&1 || rc-service sshd start >/dev/null 2>&1
  fi
  passwd -l root >/dev/null 2>&1   # no root login (setup-disk left it without a password)
  date -u '+%Y-%m-%dT%H:%M:%SZ' > "$SYSTEM_DONE"
}

boot() {
  if [ -f "$DONE" ]; then
    rm -f "$START"
    exit 0
  fi
  load_conf
  until setup_system; do
    say 'system packages not available yet, retrying in a minute'
    sleep 60
  done
  install -d -m 755 /usr/local/lib/caracal-firstboot
  install -m 755 "$DIR/caracal-firstboot.sh" /usr/local/lib/caracal-firstboot/caracal-firstboot.sh
  say 'installing CARACAL (Docker, display, node); progress in /var/log/caracal-firstboot.log'
  until bash /usr/local/lib/caracal-firstboot/caracal-firstboot.sh run >/dev/null 2>&1; do
    sleep 60
  done
  rm -f "$START"
  say 'CARACAL node installed'
}

root_fs() { awk '$2 == "/" {t = $3} END {print t}' /proc/mounts; }

case "${1:-}" in
  live) live ;;
  boot) boot ;;
  start)   # from /etc/local.d/caracal.start: the live system or the installed one; in the background
    if [ "$(root_fs)" = tmpfs ]; then live
    else setsid sh "$0" boot </dev/null >/dev/null 2>&1 & fi ;;
  *) echo "Usage: $0 start|live|boot" >&2; exit 2 ;;
esac
