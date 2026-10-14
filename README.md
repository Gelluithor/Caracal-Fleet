# CARACAL Fleet

Central management for [CARACAL](https://github.com/Gelluithor/Caracal) digital signage screens, self-hosted
with one `docker compose up`.

![CARACAL Fleet: overview, devices, a node with its live screen, the notification look editor, node templates and a
zero-touch USB stick](docs/fleet.gif)

- **Monitoring:** online state, CPU, RAM, disk, temperature and what each screen is playing right now, plus
  a *Needs attention* list with the reason for each issue; metric graphs and availability for 24 hours or 7 days and a
  timeline of outages, problems and commands per device
- **Alerts for administrators:** when a screen goes down or has a problem, a message by e-mail (SMTP), Microsoft
  Teams, Slack, Discord, ntfy or a webhook, after a delay you choose, grouped into one message per check, with a
  "resolved" message too; devices can be muted for maintenance
- **Content:** playlists with web pages, images, videos and Grafana collections; logins for web pages that need
  a username and password, as a log-in form or the browser's HTTP log-in pop-up (stored encrypted on the node only);
  show or freeze an item, skip, resume, restart the player or the device
- **On-screen notifications:** send a notification to one screen or many, change the notification settings (position,
  size, sound, limits, your own MP3 per level) and their look in a visual editor with a live preview of the TV (colours,
  icons, shape, banner, font, animation) and manage watchers that announce new tickets or issues from other apps; a notification API
  lets other apps reach many screens with one request
- **At scale:** global playlists deployed to many nodes, copying content between nodes, bulk operations, groups
  and locations
- **Zero-touch installation:** prepare an SD card for a Raspberry Pi (Raspberry Pi OS, DietPi, or a ready Alpine Linux
  card image) or a USB stick for a PC (Alpine Linux fully hands-off, Ubuntu Server autoinstall), install any running Debian, Ubuntu or Alpine with one command, or find a
  fresh Raspberry Pi in the network and install it over SSH; every installation can create the administrator of the
  node's own web administration
- **Node templates:** the playlist, notification settings and look (with picture), the countdown bar, group and
  location defined once; applied to existing nodes or baked into a zero-touch card, USB stick or installation, so a
  new node is set up as soon as it connects
- **Everything the node's web administration does:** besides content and notifications also its administrator
  (create, new name and password), the countdown bar on the TV, the notification queue, history and audit log, the
  node's own app tokens, trying a watcher or a Grafana tag before saving
- **SSH console:** a terminal on any node right in the browser (the hub connects over SSH, nothing to install)
- **Diagnostics and reinstallation:** a report of a node whose screen stays black (disk, memory, power, display
  outputs, the X server, containers and system log, with the usual causes pointed out), and *Reinstall CARACAL*
  from scratch – optionally as a factory reset with a new administrator and a template
- **Cameras:** RTSP and HTTP streams of IP cameras and recorders as playlist items, up to 9 in a grid; ONVIF cameras
  are found in the node's network and asked for their streams; their passwords stay on the node, encrypted
- **Schedules:** global playlists by time of day and day of the week, screen hours (screens off outside them through
  HDMI power saving and HDMI-CEC) and actions at a given time (restarts, screens off/on, a playlist, a template, a
  notification, updates), in the hub's time zone
- **Screen wall:** what every screen shows right now, refreshed while the page is open
- **Display watch:** the agent restarts an X display that stopped answering, the node restarts a player whose website
  stays black; both show up as problems and alerts
- **Staged updates:** a new CARACAL version goes to pilot nodes first, then in batches after a healthy wait; a failure
  stops the rollout; optionally automatic every day
- **Websites in dark mode:** per node or per website and Grafana collection – the sites' own dark look or forced
  dark for every page
- **Updates:** nodes run CARACAL in Docker and are updated from Fleet with automatic rollback
- **Nodes without internet access:** Fleet can be the only download source of a node (system packages, Docker and
  CARACAL go through the hub), chosen for the SD card, the SSH installation or per device; trusted mirrors of your
  own (a company or national apt/apk mirror) can be added
- **Fast control:** agents wait for commands (long polling), so a command reaches the screen within a second
- **Administration:** users with the roles admin, manager, operator and viewer; command history, audit log,
  Czech and English UI, custom logo, full backup and restore for server migrations, daily backups on the hub;
  one-time passwords (the user sets their own at the first sign-in), two-factor sign-in (TOTP with recovery codes) and single sign-on through OpenID Connect (Microsoft Entra ID,
  Google, Keycloak, Authentik…)

## Quick start

On any Linux server with Docker (amd64 or arm64, a Raspberry Pi works too):

```bash
mkdir caracal-fleet && cd caracal-fleet
curl -fsSLO https://raw.githubusercontent.com/Gelluithor/Caracal-Fleet/main/compose.yml
docker compose up -d
docker compose logs hub | grep "SETUP CODE"
```

Open `http://<server>:8090` and create the administrator with the one-time setup code from the log. The code
stops anyone who reaches the hub first from claiming it.

**HTTPS:** download `deploy/Caddyfile` next to `compose.yml`, point a DNS name to the server, set
`CARACAL_HUB_DOMAIN` in `.env` and run `docker compose --profile https up -d`. Caddy obtains a Let's Encrypt
certificate automatically. For an internal CA or your own certificate, see the comments in `deploy/Caddyfile`.
Any other reverse proxy works too: forward to port 8090 and pass `X-Forwarded-*` headers.

### Configuration

All settings are optional (`.env.example`):

| Variable | Meaning |
|---|---|
| `CARACAL_HUB_VERSION` | hub image version (default `latest`) |
| `CARACAL_HUB_PORT` | published port (default `8090`; with HTTPS use `127.0.0.1:8090`) |
| `CARACAL_HUB_DOMAIN` | domain for the `https` profile |
| `CARACAL_NODE_IMAGE` | default CARACAL node image (`ghcr.io/gelluithor/caracal-node`), can be changed in the UI |
| `CARACAL_HUB_ADMIN_PASSWORD` | password of the `admin` account, applied on every start; empty = create the administrator in the UI |
| `CARACAL_HUB_ENROLL_TOKEN` | enrollment token of the agents; empty = generated, shown and rotated in *Settings* |
| `CARACAL_HUB_PROXY_CACHE_GB` | cache of images and packages for nodes that download through the hub (default `20`) |
| `CARACAL_HUB_APT_HOSTS` | more apt repositories the hub mirrors, comma separated (Debian, Raspberry Pi and Docker always) |

Data live in the volume `hub-data` (`hub.db`, `config.json`, logo, media). Update the hub with
`docker compose pull && docker compose up -d`; the database is migrated automatically.

## Adding screens

A screen is a Raspberry Pi 4/5 or a PC (x86_64, arm64) with a display. Fleet turns it into a CARACAL node: Docker,
the X display, the CARACAL containers and the Fleet Agent. Supported systems: Raspberry Pi OS, DietPi, Debian,
Ubuntu (apt and systemd) and Alpine Linux (apk and OpenRC; the display runs on vt7, the console stays on tty1).

1. **SD card (zero-touch):** *Add device → Prepare SD card* (admin). Write Raspberry Pi OS Lite (64-bit) with
   Raspberry Pi Imager, or DietPi, copy the downloaded files to the boot partition and power the device on.
   It installs itself and appears in Fleet as `<prefix>-xxxxxx` after 10–30 minutes. Wi-Fi, a static IP address
   (with gateway and DNS), a time server, the time zone, Docker's address range (instead of 172.17.0.0/16), the
   download source (internet or Fleet) and an optional maintenance login are set in the same dialog. The enrollment token on the card is removed on the
   first boot; if a card is lost before that, rotate the token in *Settings*.
2. **USB stick for a PC (zero-touch):** the same dialog, *PC*:
   - **Alpine Linux** (recommended for single-purpose devices): Fleet makes `caracal.apkovl.tar.gz`, an overlay the
     live system of the official Alpine ISO (Standard or Extended) loads from the root of the stick (written with
     Rufus in ISO mode) or of a second FAT32 stick. Alpine sets up the wired network, installs itself on the largest
     internal disk (or the one set in the dialog; the disk is **erased**), reboots and installs CARACAL. The screen
     shows the progress. If the PC boots from the stick again, it finds CARACAL installed and powers off.
     Wi-Fi is not supported for this installation.
   - **Ubuntu Server 24.04+**: `user-data` and `meta-data` of the autoinstall for a second stick named `CIDATA`.
     The installer asks *Continue with autoinstall?* once (answer `yes`), erases the largest disk and installs
     Ubuntu; its first boot installs CARACAL.
3. **One command** on a running Debian, Ubuntu, Raspberry Pi OS, DietPi or Alpine (*Settings → Installation* or a
   template, admin): `curl -fsSL https://HUB/api/bootstrap/caracal-install.sh | sudo sh -s -- --hub https://HUB
   --token TOKEN [--template ID] [--via-fleet]` (on Alpine `wget -qO- … | sh -s -- …` as root).
4. **Network discovery + SSH:** *Add device → Find devices in the network* lists devices with SSH and
   recognises Raspberry Pi OS and DietPi. *Install* connects over SSH (credentials are not stored) and runs
   `hub/bootstrap/install-node.sh`.
5. **Existing CARACAL installation:** *Add device → Fleet Agent only* connects a running classic node without
   touching it. *Convert to Docker* then moves it to containers; the data in `/var/lib/caracal` are kept.

Every way of installing can also set the **administrator of the node's web administration** (CARACAL's own UI on
port 8080): *CARACAL web administration* in the SD card, SSH installation and conversion dialogs. Without it the
first person who opens the node's UI creates the administrator, so Fleet shows such nodes under *Needs attention*
and sets it later from the device (*Overview → Web administration*), for many devices at once as well. The password
goes to the node only: in a root-only file for the installers (never on a command line), removed from the hub's
command as soon as the agent fetched it, never in the history or the audit. On the SD card it is in
`caracal-firstboot.conf`, which the first boot removes from the card like the enrollment token.

Diagnostics on a node: `sudo python3 /opt/caracal-agent/agent.py check`, `journalctl -u caracal-agent -f`
(Alpine: `/var/log/caracal-agent.log`), `/var/log/caracal-firstboot.log` (zero-touch and one-command installation).

### Node templates

*Node templates* hold any of these parts: a global playlist (replace or append), the notification settings and their
look with a picture, the countdown bar, and the group and location in Fleet. A part that is not set is left alone on
the node. *Template from a device* copies them from a node (its web pages and Grafana collections can become a new
global playlist).

- **Existing nodes:** *Apply template* on the template, or *Manage → Apply template* on a device or a selection. The
  parts are queued as normal commands (offline nodes get them when they connect); a part the node does not support
  yet (older agent or CARACAL) is skipped and reported.
- **New nodes:** choose the template on the zero-touch card or USB stick, in the one-command installation or the SSH
  installation. The node sends it with its enrollment and the hub applies it once the node reports what it supports.
  The template's page lists where it was applied.

## Nodes without internet access (Fleet as the download source)

Every node has a **download source**: *From the internet* (default) or *Through CARACAL Fleet*. It is chosen when
preparing the SD card, in the SSH installation (*Download through CARACAL Fleet*) and later per device or in bulk
(*Download source*). With *Through CARACAL Fleet* the node only needs to reach the hub:

- **CARACAL images:** the hub downloads the image for the node's architecture from the registry, checks every
  digest and keeps it in a cache; the agent downloads it from the hub, checks its SHA-256 and loads it with
  `docker load`. Updates from Fleet work the same way, with the usual rollback.
- **System packages and Docker:** the node's apt sources of the Debian, Raspberry Pi and Docker repositories point to
  `<hub>/apt/<host>/…`, a read-only mirror. apt checks the repository signatures itself, so the hub cannot alter
  packages; only these repositories are mirrored (more with `CARACAL_HUB_APT_HOSTS`). Docker is installed from
  Docker's repository through the hub.
- **Credentials:** both need the device's token (HTTP Basic, stored for apt in `/etc/apt/auth.conf.d`, readable by
  root only); the installation uses the enrollment token until the device is enrolled.

Requirements and limits: the hub itself needs internet access and disk space for the cache (images and packages,
20 GB by default, `CARACAL_HUB_PROXY_CACHE_GB`; *Settings → Fleet as the download source* shows it and clears it).
The nodes must trust the hub's HTTPS certificate (Let's Encrypt works; with an internal CA install it on the nodes).
Switching a node back to *From the internet* restores its apt sources.

- **Time:** a Raspberry Pi has no clock battery, and HTTPS and apt need the right time. Without internet access set a
  time server of your network on the SD card; otherwise the clock is set from the hub's `Date` header (read without
  certificate checks, only when no time server synchronised the clock). The agent keeps following the hub's clock.
- **Raspberry Pi OS** installs completely through the hub.
- **DietPi** works without internet access as well: the SD card sets its connectivity check to the device and the
  hub, skips DietPi's online update from GitHub during the first boot (`Automation_Custom_PreScript.sh` installs a
  hook in `/etc/bashrc.d` that runs in the first login shell right before DietPi's first-run setup) and points its
  apt sources, `dietpi.com` included, to the hub. DietPi's daily check for its own updates is switched off.

## Notification API for other apps

*Settings → Notification API* (manager and admin) creates a token per app. A token is shown once, Fleet keeps its hash,
and it reaches either all screens or chosen groups, locations and devices. The app sends:

```bash
curl -X POST https://fleet.example/api/notify \
  -H "Authorization: Bearer cft_..." -H "Content-Type: application/json" \
  -d '{"title":"Backup finished","message":"DB01","level":"success"}'
```

Fleet queues one notification per screen; every node keeps its own queue and limits. The body is passed to the nodes
unchanged, so JSON (`title`, `message`, `level` info/success/warning/critical, `duration`, `key`, `sound`), Grafana
and Alertmanager webhooks, Uptime Kuma webhooks and plain text with the `Title` and `X-Level` headers all work. The
token can also be sent as `X-Caracal-Token` or as the Basic auth password. `?group=`, `?location=` and `?device=`
narrow the request to part of the token's screens. Each token has a rate limit (30 requests per minute by default);
refused tokens are audited. Screens whose CARACAL has no notifications are skipped (`skipped` in the response).

## Updating CARACAL on the nodes

*CARACAL updates* shows the versions available in the registry of the node image. *Update* (per node, in bulk or
*Update outdated*) makes the agent pull the new image while the screen keeps playing, replace the containers,
check that the app and the player run, and restore the previous version on failure. The screen is off for a few
seconds only. Try a new version on one node first.

Classic nodes (`/opt/caracal`) can still be updated with source archives of the caracal repository (section
*Classic nodes*): the agent backs up the installation, runs `install.sh` and rolls back on failure.

Your own node image (a fork, a private registry): build it with the workflow in the caracal repository and set
the image in *CARACAL updates*. Fleet reads versions from any Docker registry v2 (GHCR, Docker Hub, GitLab,
Harbor…) anonymously, so the image must be public or reachable without credentials.

## Roles

| Role | Permissions |
|---|---|
| viewer | read: state, content, command history |
| operator | + playback control, player and node restart, playlists, media, collections, website logins, copying |
| manager | + devices, groups, locations, node templates, installation and updates, audit |
| admin | + users, system settings, enrollment token, SD cards and USB sticks, one-command installation, backup |

## How commands work

The UI queues a command; the agent fetches the queue every 3 s, executes the command through the node's local
API ([docs/LOCAL-API.md](docs/LOCAL-API.md)) and reports the result. The agent never writes the player's control
files. Playback commands are only sent to online nodes and expire after 10 minutes; content changes for offline
nodes wait until they reconnect. Media are copied through the hub and verified with SHA-256; in *replace* mode the
old items are deleted only after all new ones were added.

## Backup and migration

*Settings → Backup & migration* downloads a ZIP with the database (devices and their tokens, users, groups,
locations, global playlists, history, audit), `config.json`, the logo and the media of global playlists. Restore it
on the new server in the same place. On the same domain the agents reconnect by themselves; for a new domain,
select the devices on the old hub and use *Redirect to another hub*. The backup contains secrets – store it safely.

## Security

- Passwords: PBKDF2-SHA256 (240,000 iterations). Sessions are HMAC-signed and invalidated by a password change
  or by disabling the account.
- Sign-in is rate limited per address (8 per 5 min) and per account (20 per 5 min); failures are audited and the
  response time does not reveal existing accounts.
- Content-Security-Policy without inline scripts, `X-Frame-Options: DENY`, `nosniff`, HSTS, `no-referrer`,
  no caching of API responses, no public OpenAPI.
- Agents authenticate with per-device tokens; the enrollment token only registers new devices and can be
  rotated at any time without affecting enrolled ones.
- SSH installation and the web console (admin and manager only) pin the host key on first use; credentials are
  never stored. Opening and closing a console is audited; an idle console is closed after 30 minutes.
- Backups are validated before restore (allowed files only, database integrity, an administrator exists).
- Passwords of the nodes' web administrators and the credentials of login profiles and watchers only travel to the
  node; the hub removes them from a command once the agent fetched it.

Known limitations: the hub container runs as root and backups are not encrypted. Whoever controls the hub controls
the nodes (through agent and CARACAL updates), so protect admin and manager accounts.

## Repository layout

```
hub/app/            FastAPI hub (API + web UI in hub/app/static, no build step)
hub/bootstrap/      Fleet Agent and installers served to the nodes (agent, node, first boot, Alpine, one command)
deploy/             Caddyfile for the https profile
dev/                mock CARACAL node and a local demo
docs/LOCAL-API.md   contract of the local CARACAL API used by the agent
tests/              API, end-to-end hub ↔ agent ↔ node tests, translations
```

## Development

```bash
pip install -r hub/requirements.txt -r requirements-dev.txt
python -m pytest tests            # everything; tests against the real node app run when ../caracal exists
python dev/demo.py                # http://127.0.0.1:8090, admin / admin-password, 4 mock nodes
```

Releases: raise `HUB_VERSION` in `hub/app/core.py` and push the tag `v<version>`. GitHub Actions runs the tests and
publishes `ghcr.io/<owner>/caracal-fleet:<version>` and `:latest` for amd64 and arm64; pushes to `main` publish
`:edge`.
