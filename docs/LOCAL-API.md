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
| Web page | `POST /assets/web` | `{name, source, duration, scale}` → `{id}` |
| Grafana collection | `POST /assets/grafana-tag` | `{name, grafana_url, tag, kiosk, duration, scale}` → `{id}` |
| Image / video | `POST /assets/upload` | multipart `file`, `name`, `duration` → `{id, kind}`, type from the extension |
| Media file | `GET /assets/{id}/file` | file content (copying between nodes) |
| Edit | `PUT /assets/{id}` | `name`, `duration`, `scale`; web: `source`; collection: `grafana_url`, `tag`, `kiosk` |
| Delete | `DELETE /assets/{id}` | deletes the item and its media file |
| Order | `PUT /playlist/reorder` | `{ids: [...]}`, always the complete list, otherwise 409 |

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
  "profiles": [{"id": 1, "name": "Grafana login"}],
  "player": {"current_id": 300001, "current_name": "Production · Dashboard", "frozen": false,
             "collection_frozen": true, "collection_id": 3, "remaining": 12, "duration": 60,
             "updated": 1791281688.2, "player_online": true},
  "requests": {"reboot": 0, "restart_player": 0}
}
```

- `player` is the live state the player sends every second to `/api/v2/player/heartbeat`.
- Dashboards of a Grafana collection are played with the id `<collection id> * 100000 + position`.
- `profiles` are login profiles of web pages (without credentials), not Grafana collections.
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

The manually applied patch has no media upload or export and cannot create Grafana collections. The agent detects
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
