# FilamentHub → multiACE plugin

A standalone decay71 plugin: adds a **FilamentHub** tab to the multiACE GUI where you
pick a spool from FilamentHub inventory for an ACE slot. It labels the slot in multiACE
(`POST /api/slot-override`) and records the spool's location back in FilamentHub.

Standalone and reloadable — decay71 discovers it by scanning `MULTIACE_PLUGIN_PORTS`
(8089–8098) for a `GET /integration-manifest`, then renders it as an iframe tab. decay71
upgrades never touch this plugin.

## Environment

Defaults live in `src/filamenthub_plugin/config.py` and `install/S66filamenthub-plugin`.
`tests/test_config.py` fails if the two drift apart. On the printer, override any of
them in `/userdata/filamenthub-plugin/env` (plain `KEY=value` lines, sourced by S66
before the defaults), then run `/etc/init.d/S66filamenthub-plugin restart`.

| Var | Default | Meaning |
|---|---|---|
| `FILAMENTHUB_URL` | `http://192.168.1.174:3000` | FilamentHub/Spoolman base URL (LAN) |
| `MULTIACE_PRINTER_ID` | `u1-b` | id used in `extra.filamenthub.location.printer` (must match your FilamentHub bindings) |
| `MULTIACE_URL` | `http://127.0.0.1:7126` | local decay71 multiACE web backend |
| `FILAMENTHUB_PLUGIN_PORT` | `8089` | must be within decay71 `MULTIACE_PLUGIN_PORTS` (8089-8098) |
| `FILAMENTHUB_ACE_STATE_URL` | `${FILAMENTHUB_URL}/fleet/api/ace-state` | ace-state read seam (Phase 4 puller) |
| `FILAMENTHUB_DESIRED_PATH` | `/userdata/filamenthub-plugin/state/desired.json` | plugin-owned desired per-slot loadout (must be on `/userdata`) |

S66 sets `PYTHONPATH` to `src`, then the vendored `deps`, then
`/home/lava/.local/lib/python3.11/site-packages` as a last resort.

## Local dev

```bash
cd multiace_plugins/filamenthub
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
FILAMENTHUB_URL=http://192.168.1.174:3000 MULTIACE_PRINTER_ID=u1-b \
  FILAMENTHUB_DESIRED_PATH=./desired.json \
  python -m filamenthub_plugin        # serves 127.0.0.1:8089
```

## Pull from FilamentHub (Phase 4)

The **Pull from FilamentHub** button (explicit only; there is no auto-pull on tab open) mirrors
FilamentHub's authoritative loaded configuration into multiACE's slot labels —
**label-only, no filament motion**. It reads FilamentHub's
`GET /fleet/api/ace-state?printer=<MULTIACE_PRINTER_ID>` seam (one winner per
slot, `disputed` losers shown but never written), enriches brand from the spool
list, and reconciles multiACE's `/api/slot-override` labels — clearing vacated
slots only on ACEs FilamentHub reports.

| Var | Default | Meaning |
|---|---|---|
| `FILAMENTHUB_ACE_STATE_URL` | `${FILAMENTHUB_URL}/fleet/api/ace-state` | ace-state read seam |

If Pull reports "ace-state not enabled (watcher provider unwired)", the
FilamentHub watcher hasn't injected its `ace_state_provider` — that's a
FilamentHub-side fix (`ryvin/FilamentHub`), not a multiACE change.

## Printer install

Everything the plugin needs lives on `/userdata/filamenthub-plugin`, which survives
reboots and firmware updates:

| Path | Contents |
|---|---|
| `src/` | plugin code, including `static/` |
| `deps/` | vendored fastapi, uvicorn, httpx and pydantic (copied from `/home/lava/.local`) |
| `state/desired.json` | desired per-slot loadout |
| `install/` | `S66filamenthub-plugin`, `common.sh`, `install_plugin.sh`, `restore.sh` |
| `env` | optional overrides (you create it) |
| `backup/` | previous nginx drop-in, kept outside the nginx include dir |

Only two pieces live on the wipeable overlay: `/etc/init.d/S66filamenthub-plugin` and
the nginx drop-in `/etc/nginx/fluidd.d/multiace-plugin.conf`.

Both scripts must run as root. They read `print_stats.state` from Moonraker
(`127.0.0.1:7125`) and refuse to run unless it is `standby`, `complete`, `cancelled`
or `error`. That includes `unknown`, when Moonraker is unreachable. Set `FORCE=1` only
when you know no print is active.

### First install

Copy this folder to the printer (e.g. `/tmp/filamenthub`), then run:

```sh
sh /tmp/filamenthub/install/install_plugin.sh
```

It is idempotent. It does the following:

1. Copies `src/` and the `install/` scripts to `/userdata/filamenthub-plugin`.
2. Vendors the deps into `deps/` from `/home/lava/.local`. It checks the copy with
   `python3 -s`, so the import check cannot quietly fall back to `~/.local`. If
   `~/.local` lacks the deps, it keeps an existing working `deps/`. With neither, it
   fails and tells you to reinstall the decay71 web console.
3. Installs `/etc/init.d/S66filamenthub-plugin`.
4. Writes the nginx drop-in, unless another file already declares `location /plugin/`.
   It runs `nginx -t` before reloading and rolls the drop-in back if the test fails.
5. Restarts the plugin and probes `127.0.0.1:8089/integration-manifest`, then the
   decay71 proxy at `127.0.0.1:7126/plugin/filamenthub/`.

The drop-in mirrors decay71's own `/multiace/` block. The `auth_request` line is only
written when PAXX's `/auth_check` exists. `proxy_pass` has no URI part, so the request
reaches decay71's `/plugin/{name}/{path}` route unchanged, and decay71 forwards it to
the plugin's port:

```nginx
location /plugin/ {
    auth_request /auth_check;
    proxy_pass http://127.0.0.1:7126;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 120s;
}
```

### After a firmware update (overlay wiped)

A PAXX update can wipe `/etc`, as it did in Aug 2026 when it removed S66. The plugin
then disappears from the multiACE tabs. Re-create the `/etc` pieces from the
`/userdata` copies:

```sh
sh /userdata/filamenthub-plugin/install/restore.sh
```

It is idempotent and uses the same print-state guard. It does not touch code, deps or
state. To upgrade the plugin code, run `install_plugin.sh` again from a fresh repo copy.
