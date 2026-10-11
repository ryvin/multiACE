# FilamentHub slot picker on decay71 1.27b: plan (2026-10-11)

Goal: in the decay71 1.27b console, let the user pick a FilamentHub filament **profile** (Spoolman `filament`) or **spool** for each ACE slot. The pick becomes the slot's identity (slot override), and FilamentHub's location tracking stays truthful.

Decision (Fable design pass): **harden and redeploy the existing `multiace_plugins/filamenthub` sidecar.** Turning on native decay71 Spoolman mode (`ACE_SET_SPOOLMAN MODE=spoolman`) was rejected for now, for four reasons:
- It searches spools only, so it cannot pick a profile.
- It never writes FilamentHub's `extra.filamenthub.location`.
- Switching worlds drops the current local bindings.

It can be added later for consumption booking. Ownership would split: the plugin owns overrides and FilamentHub location, and decay71 owns bindings and `/use`.

Before this plan, WP0 merged `feat/filamenthub-reconciliation` (desired-state store) into main as `5f0313b`. That suite passes 69 tests.

## Work packages

All work packages are TDD. Commit as `ryvin <18613731+ryvin@users.noreply.github.com>`.

**WP1: FilamentHub write hardening.** Files: `spoolman.py` (`_set_location`, `assign_spool`) and `tests/test_spoolman.py`.
- The PATCH body must keep the existing `extra` keys (`card_uid`, `tags`, `pressure_advance_matrix`, ...). Merge into a copy of the spool's current `extra`.
- Set `location.asserted_by = "user:assign"` and an ISO `asserted_at`.
- Set the standard Spoolman `location` string, mirroring FilamentHub `spool_watcher.py:write_location`:
  - `"u1-b/ace1 T2"` when ace > 0
  - `"u1-b T2"` when ace is 0
- Unassign sets location to None and clears the standard string.

**WP2: Profiles.** Add a new `filaments.py`.
- `list_filaments()` calls `GET /api/v1/filament` and returns `{filament_id, name, vendor, material, color, extruder_temp, bed_temp}`.
- `create_spool_from_filament()` mirrors FilamentHub `cloneSpoolWithVirtualTag`:
  - `extra.card_uid = json.dumps("VIRT-xxxxxxxx")`
  - `extra.filamenthub = json.dumps(json.dumps({"schema":1,"virtual_tag":true}))`
  - weight from `filament.weight`, defaulting to 1000
- `app.py` gains `GET /filaments` and `POST /assign-profile {filament_id, ace, slot, create_spool:false}`.
  - Without `create_spool`, it writes the override plus a desired-store entry and no FilamentHub location.
  - With `create_spool`, it creates the spool and then reuses the `/assign` path.
- Tests: `tests/test_filaments.py` and `test_app.py`, both using respx.

**WP3: Subtype and material mapping.** Files: `mapping.py`, `multiace_client.get_materials()` (`GET /api/materials` returns `db[material][vendor]=[subtypes]`) and `tests/test_mapping.py`.
- Port decay71's `_spoolman_subtype_guess`: whole-word match against known subtypes, longest first, falling back to the text after the material token (≤32 characters).
- Port `_spoolman_material_split`: `PLA+` becomes `PLA` with subtype `Plus`.
- Test cases:
  - "Matte Black" with PLA gives "Matte".
  - "Galaxy Blue" gives "".
  - "PLA+" gives ("PLA", "Plus").
  - If the materials DB is unreachable, subtype is "".

**WP4: Refresh and schema 2.**
- In `multiace_client.py`, after `set_override`/`clear_override`, POST `/api/plugin-api/gcode {"script":"MULTIACE_REFRESH_OVERRIDES"}`. This is best-effort and logged.
- `ace_state.py` accepts schema 1 or 2.
- `reconcile.py` uses `aces_covered` to scope clears.

**WP5: UI.** Files: `static/app.js`, `index.html`, `style.css`.
- The picker gets two tabs: Spools and Profiles. Profiles shows vendor, name, material and temps, plus a "create spool in FilamentHub" checkbox.
- Slot cards show desired against observed. Depends on WP2 and WP4.

**WP6: Deployment persistence.**
- `install/S66filamenthub-plugin` defaults:
  - `MULTIACE_PRINTER_ID=u1-b`
  - `FILAMENTHUB_URL=http://192.168.1.174:3000`
  - `FILAMENTHUB_DESIRED_PATH=/userdata/filamenthub-plugin/state/desired.json`
  - PYTHONPATH falls back to `/userdata/filamenthub-plugin/deps`
- `install/install_plugin.sh` vendors site-packages into `deps/`. Its nginx drop-in carries `auth_request /auth_check;`.
- A new `install/restore.sh` re-creates S66 and the drop-in after an overlay wipe.
- Docs: `ops/decay71_overlay/README.md` and the plugin README env table.

**WP7: FilamentHub config.** Set `"ace_count": 2` on `u1-b` in `/mnt/e/Code/FilamentHub/config/printers.json`. The file is gitignored and holds runtime config.

Parallelism:
- WP1, WP2, WP3+WP4, WP6 and WP7 can run in parallel.
- WP5 runs after WP2 and WP4.
- Installing on the printer, reloading nginx and all live checks wait until no print is running.

## E2E verification (after the print)

1. Discovery and rendering:
   - `curl 127.0.0.1:8089/integration-manifest` answers.
   - `/multiace/api/integrations` lists `filamenthub`.
   - The tab's iframe renders the plugin, not Fluidd's HTML.
2. Spoolman PATCH probe on a scratch spool: PATCH `{extra:{filamenthub:...}}` on its own, then check whether `tags` survives. Record the answer here.
3. Assign a spool to an occupied slot. Check:
   - The override carries material, brand, guessed subtype and colour.
   - The dashboard label updates.
   - The ace-state winner has `asserted_by=user:assign` and `aces_covered=[0,1]`.
   - The spool's `extra` keys are intact.
4. Assign a profile and check that the override and desired entry exist. If "create spool" was ticked, check for a `VIRT-` uid.
5. Clear a slot and check both sides are cleared. Eject a slot and check the plugin shows "expected (not loaded)".
6. Pull and check that only covered ACEs are pruned.

## Unverified assumptions

- Spoolman 0.23 PATCH replaces `extra` rather than merging it.
- `/home/lava/.local` still holds fastapi, uvicorn, httpx and pydantic.
- nginx still has no `/plugin/` location.
- `auth_check` exists.
- `/api/materials` on 127.0.0.1:7126 works without auth.
