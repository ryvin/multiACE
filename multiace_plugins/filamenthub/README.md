# FilamentHub → multiACE plugin

A standalone decay71 plugin: adds a **FilamentHub** tab to the multiACE GUI where you
pick a spool from FilamentHub inventory for an ACE slot. It labels the slot in multiACE
(`POST /api/slot-override`) and records the spool's location back in FilamentHub.

Standalone and reloadable — decay71 discovers it by scanning `MULTIACE_PLUGIN_PORTS`
(8089–8098) for a `GET /integration-manifest`, then renders it as an iframe tab. decay71
upgrades never touch this plugin.

## Environment

| Var | Default | Meaning |
|---|---|---|
| `FILAMENTHUB_URL` | `https://filamenthub.pinedamail.com` | FilamentHub/Spoolman base URL |
| `MULTIACE_PRINTER_ID` | `u1-1` | id used in `extra.filamenthub.location.printer` (must match your FilamentHub bindings) |
| `MULTIACE_URL` | `http://127.0.0.1:7126` | local multiACE web |
| `FILAMENTHUB_PLUGIN_PORT` | `8089` | must be within decay71 `MULTIACE_PLUGIN_PORTS` (8089–8098) |

## Local dev

```bash
cd multiace_plugins/filamenthub
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
FILAMENTHUB_URL=https://filamenthub.pinedamail.com MULTIACE_PRINTER_ID=u1-1 \
  python -m filamenthub_plugin        # serves 127.0.0.1:8089
```

## Printer install

Copy this folder to the printer, then (only when NO print is active):

```sh
sh install/install_plugin.sh
```

It deploys to `/userdata/filamenthub-plugin`, creates a venv, registers
`/etc/init.d/S66filamenthub-plugin`, ensures an nginx `location /plugin/` route exists
(adds one if decay71 hasn't), starts the sidecar, and curls the manifest to confirm.

Config defaults are baked into `install/S66filamenthub-plugin`; edit that file to change
`FILAMENTHUB_URL` / `MULTIACE_PRINTER_ID`.
