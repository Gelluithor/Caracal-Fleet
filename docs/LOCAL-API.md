# Local CARACAL Fleet API (agent contract)

The Fleet Agent performs **all** playback and content operations through the local HTTP API of the CARACAL node
(`http://127.0.0.1:8080/api/fleet/v1/*`). It never writes the player's control files.

The API is part of CARACAL (`app/main.py` in the caracal repository, section `CARACAL_FLEET_API_V2`). Older nodes
may run a manually applied patch `CARACAL_FLEET_API_V1` (see below); the agent detects what the node supports
and works with a reduced feature set.

Authentication: header `X-Fleet-Key` with the content of the key file the agent writes when it is enrolled:
`/etc/caracal-fleet-key` (classic installation, mode `0640 root:caracal`) and `/var/lib/caracal/.fleet-key`
(Docker, read by the container).

## Endpoints (v2)

| Operation | Method and path | Body / response |
|---|---|---|
| State | `GET /snapshot` | `{api_version: 2, runtime, version, assets, profiles, player, requests}` |
| Control | `POST /control` | `{action: show\|freeze, item_id}`, `{action: show_collection\|freeze_collection, collection_id}`, `{action: next\|unfreeze}` |
| Web page | `POST /assets/web` | `{name, source, duration, scale, auth_profile_id}` → `{id}` |
| Grafana collection | `POST /assets/grafana-tag` | `{name, grafana_url, tag, kiosk, duration, scale}` → `{id}` |
| Image / video | `POST /assets/upload` | multipart `file`, `name`, `duration` → `{id, kind}`, type from the extension |
| Media file | `GET /assets/{id}/file` | file content (copying between nodes) |
| Edit | `PUT /assets/{id}` | `name`, `duration`, `scale`; web: `source`, `auth_profile_id` (`null` = without login); collection: `grafana_url`, `tag`, `kiosk` |
| Delete | `DELETE /assets/{id}` | deletes the item and its media file |
| Order | `PUT /playlist/reorder` | `{ids: [...]}`, always the complete list, otherwise 409 |
| Login profile | `POST /profiles` | `{name, login_url, target_url, username, password, user_selector, pass_selector, submit_selector}` → `{id}` |
| Edit login | `PUT /profiles/{id}` | the same fields; empty `username`/`password`/selectors keep the stored values |
| Delete login | `DELETE /profiles/{id}` | pages that used it stay in the playlist without login → `{unassigned}` |
| Notification | `POST /notify` | `{title, message, level, duration, key, sound}` or a webhook body (Grafana, Alertmanager, Uptime Kuma) |
| Notification settings | `PUT /notify/settings` | any of `enabled, position, duration, max_queue, scale, sound, volume, sound_device, history_max, history_days, style` (`style`: the look of the notifications, see `docs/NOTIFICATIONS.md` of the node); missing keys stay |
| Clear notifications | `POST /notify/clear` | removes waiting notifications and the one on screen → `{cleared}` |
| Watcher | `POST /notify/watchers` | `{name, url, auth_type, auth_header, username, secret, client_secret, refresh_token, list_path, id_field, title_template, message_template, level, level_field, interval, verify_tls, enabled, oauth_*}` → `{id}` |
| Edit watcher | `PUT /notify/watchers/{id}` | the same fields; empty credentials keep the stored ones → `{reset}` (true when the URL or list changed) |
| Delete watcher | `DELETE /notify/watchers/{id}` | |
| Check watcher | `POST /notify/watchers/{id}/check` | checks now → `{ok, count, new, sent, first}` or `{ok: false, error}` |
| Notification sound | `POST /notify/sounds/{level}` | multipart `file`: an MP3 (at most 5 MB) for `info`, `success`, `warning` or `critical` |
| Default sound | `DELETE /notify/sounds/{level}` | the generated chime again |
| Web administrator | `POST /admin` | `{username, password}` (password 10 to 200 characters): creates the administrator of the node's web administration, or sets a new name and password of the existing one (its sessions end) → `{created}` |
| Countdown on the TV | `PUT /player/overlay` | `{enabled, size}` (bar height 4 to 200 px) |
| Skip notification | `POST /notify/skip` | ends the notification on screen |
| Remove notification | `DELETE /notify/queue/{id}` | removes a waiting notification |
| Notification log | `GET /notify/log?limit=` | `{history, history_count, audit, audit_count}`, at most 200 entries each |
| Clear history / audit | `POST /notify/history/clear`, `POST /notify/audit/clear` | the clearing of the audit log stays in it |
| Node token | `PUT /notify/tokens/{id}`, `DELETE /notify/tokens/{id}` | `{name, rate_per_min, enabled}`; new tokens are created in the node's administration only (shown once) |
| Try a watcher | `POST /notify/watchers/preview` | the watcher fields (and `id` to use the stored credentials) → `{count, samples}`, nothing is saved |
| Try a Grafana tag | `POST /grafana/discover` | `{grafana_url, tag}` → `{count, dashboards}` |

All paths start with `/api/fleet/v1`. CARACAL rules: the display time is at least 5 s (videos loop for the whole
time), the zoom is 0.5 to 3.0. Images: `.png .jpg .jpeg .webp .gif`, videos: `.mp4 .webm .mkv`.

### Snapshot

```json
{
  "api_version": 2,
  "runtime": "docker",
  "version": "2026.10.07",
  "assets": [
    {"id": 1, "name": "Intranet", "kind": "web", "source": "https://…", "duration": 30, "position": 0,
     "auth_profile_id": null, "scale": 1.0},
    {"id": 3, "name": "Production", "kind": "grafana-tag", "duration": 60, "scale": 1.0,
     "source": "{\"grafana_url\": \"https://grafana…\", \"tag\": \"production\", \"kiosk\": true}"}
  ],
  "profiles": [{"id": 1, "name": "Zabbix", "login_url": "https://zabbix…/index.php",
                "target_url": "https://zabbix…/zabbix.php?action=dashboard.view", "user_selector": "#name",
                "pass_selector": "#password", "submit_selector": "#enter"}],
  "player": {"current_id": 300001, "current_name": "Production · Dashboard", "frozen": false,
             "collection_frozen": true, "collection_id": 3, "remaining": 12, "duration": 60,
             "updated": 1791281688.2, "player_online": true},
  "requests": {"reboot": 0, "restart_player": 0},
  "notifications": {"settings": {"enabled": true, "position": "top-right", "duration": 8, "sound": "off", "...": "..."},
                    "waiting": 0, "current": null, "tokens": 1,
                    "sounds": {"critical": {"name": "gong.mp3", "size": 48213, "sha256": "…", "uploaded": 1791281688.2}},
                    "watchers": [{"id": 1, "name": "Helpdesk", "url": "https://…", "auth_type": "bearer", "interval": 60,
                                  "enabled": 1, "last_check": 1791281688.2, "last_count": 12, "last_error": null,
                                  "has_credentials": 1}]}
}
```

- `player` is the live state the player sends every second to `/api/v2/player/heartbeat`.
- Dashboards of a Grafana collection are played with the id `<collection id> * 100000 + position`.
- `profiles` are login profiles of web pages, not Grafana collections: `{id, name, login_url, target_url,
  user_selector, pass_selector, submit_selector}`. The credentials are stored encrypted on the node (`vault.key` in
  the data folder) and are never returned; only the local player reads them (`/api/player/profile/{id}`).
  A page with a login opens `login_url`, fills in the form and then shows `target_url`.
- The hub forwards credentials to the agent only: they are removed from the stored command as soon as the agent
  fetched it (or it was cancelled or expired) and never appear in the command history or the audit.
- `profiles[].auth_type` is `form` (the player fills the log-in form) or `http` (HTTP Basic/Digest, the browser's
  pop-up: only `target_url` and the credentials are used; `login_url` may be empty and becomes `target_url`).
- `notifications` exists on CARACAL with on-screen notifications (2026.10.08 and newer). Watchers never contain
  their credentials or the IDs they have seen; put API keys into the authentication fields rather than into the URL,
  because the URL is reported to the hub. Changes made through the Fleet API appear in the node's notification audit
  log as "CARACAL Fleet".
- `admin` (`{configured, username}`) says whether the node's web administration has its administrator; until it
  has one, anyone who opens it first can create it, so Fleet lists such nodes under *Needs attention*. The agent
  creates it through the node's first-run setup (`POST /api/setup`) and changes it through `POST /admin`.
- `overlay` (`{enabled, size}`) is the countdown bar on the TV. `notifications.queue` lists up to 30 waiting
  notifications, `notifications.token_list` the node's own app tokens (never the tokens themselves),
  `history_count` and `audit_count` the size of the history and the audit log (their entries: `GET /notify/log`).
- `requests` counts restarts requested in the node's own admin UI. In Docker the app cannot reboot the host, so
  the agent performs a reboot when the counter increases.

### Show and freeze

Commands reach the player through its command channel (`/api/v6/player/command`), the same way as the live
control in the node's admin UI. Before `show`/`freeze` the agent checks that the player runs and the item exists.
It remembers timed freezes (`/var/lib/caracal-agent/state.json`) and sends `unfreeze` when the time is up. If
someone resumes playback directly on the node, the agent notices it in the live state.

Player and node restart: Docker nodes restart the `player` container and reboot the host; classic nodes use
`systemctl restart caracal-player.service` and `systemctl reboot`. The service name can be changed with the key
`player_service` in `/etc/caracal-agent.json`.

## Older nodes (patch `CARACAL_FLEET_API_V1`)

The manually applied patch has no media upload or export and cannot create Grafana collections or login profiles
(CARACAL before the login endpoints lists its logins in the snapshot, but they can only be managed in its own UI). The agent detects
this from the node's `/openapi.json` and reports it to the hub in `capabilities`. The UI hides these actions for
the node, and copying or deployments skip it with a notice. The agent also determines whether the player runs
from systemd, because the v1 state file is not updated by newer players. Updating CARACAL is the easiest fix.

## Overriding paths

If a CARACAL version uses different paths, they can be overridden in `/etc/caracal-agent.json`:

```json
{"local_api": "http://127.0.0.1:8080", "endpoints": {"add_web": ["POST", "/api/fleet/v1/assets/web"]}}
```

A mock of both API versions for development and tests is in `dev/mock_node.py`. `tests/test_real_node.py` runs
the tests against the real CARACAL application when its repository is next to this one (`../caracal`).
