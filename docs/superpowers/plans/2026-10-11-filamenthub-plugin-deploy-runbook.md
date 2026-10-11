# Post-print crew runbook: deploy and verify the FilamentHub plugin on Davinci-U1

Written 2026-10-11. Run it only once the current print (`Iron_Lung-…_1d2h27m.gcode`) has finished. Code is on `main`: plan `2026-10-11-filamenthub-picker-127b.md`, spec `../specs/2026-10-11-multiace-filament-profiles-and-filamenthub.md`.

**What ships:**
- The plugin sidecar `multiace_plugins/filamenthub` on port 8089, shown as a tab in the decay71 1.27b console.
- The tab lets you pick a FilamentHub spool or filament profile for each ACE slot.
- A pick writes the decay71 slot override, refreshes the display, and updates FilamentHub's location.

**Hard rules for every agent:**
- Run the print gate (Phase 0) before any write.
- Never restart Klipper. Never send motion, heater or load gcode.
- The only printer writes allowed in this runbook are the following, and only after Phase 0 passes:
  - the plugin install
  - the nginx drop-in and reload
  - slot-override writes to the **test slot**
  - restoring the snapshot
- Untrusted text never goes into shell strings; write it to a file first.
- Every status line is `Verified: <command> -> <result line>` or `UNVERIFIED — to confirm, run: …`.
- Commit as `ryvin <18613731+ryvin@users.noreply.github.com>`, on a branch, then merge and push.
- Helpers live in the session scratchpad:
  - `ssh.py` runs a root script from stdin on 192.168.1.136.
  - `mr.py` is a Moonraker client.
  - `upload.py` uploads over SFTP.

  If the scratchpad is gone, re-create equivalents. Root password: `snapmaker`.

## Crew

| Role | Agent | Owns |
|---|---|---|
| Lead | Opus (main session) | Gates, decisions, merges, the final report |
| Recon | general-purpose (sonnet) | Phase 1 (read-only) |
| Deployer | general-purpose (opus) | Phases 3 and 6 |
| API verifier | general-purpose (opus) | Phase 4 |
| UI verifier | general-purpose (opus) + Playwright | Phase 5 |
| Reviewer | Fable | Phase 7: adversarial review of the evidence before "done" |

Phases run in order. Phases 4 and 5 may run in parallel after Phase 3.

## Phase 0: Print gate and snapshot (Lead)

1. Check the print state with `python3 mr.py state`. Continue only if `print_stats.state` is `complete`, `standby`, `cancelled` or `error`. If it is `printing` or `paused`, stop.
2. Take a snapshot, read-only. Save each item under `scratchpad/postprint/`:
   - `GET http://192.168.1.136/multiace/api/slot-override` → `overrides.before.json`
   - `printer/objects/query?ace` with `head_source`, `aces` and `gate_status` → `ace.before.json`
   - `GET http://localhost:3000/fleet/api/ace-state?printer=u1-b` → `acestate.before.json`
   - the FilamentHub spool list `GET http://localhost:3000/api/v1/spool` → `spools.before.json`
3. Pick the **test slot**: the first slot whose gate reads `0` (empty). As of 2026-10-11 that is likely ACE 2 slot 2, internal ace=1 slot=1. If no slot is empty, stop and ask the user which slot may be relabelled temporarily.

## Phase 1: Recon pre-checks (read-only)

Report each item as Verified or UNVERIFIED:

1. **Python deps:** `/usr/bin/python3 -s -c "import fastapi,uvicorn,httpx,pydantic"` with `PYTHONPATH=/home/lava/.local/lib/python3.11/site-packages`. Also check that the old `/userdata/filamenthub-plugin/` exists, and report its contents and age.
2. **nginx:**
   - `grep -rn "auth_check" /etc/nginx`
   - `grep -rn "include .*fluidd.d" /etc/nginx`
   - `grep -rn "location /plugin/" /etc/nginx`, which should be absent
   - whether `/etc/init.d/S50nginx` exists
3. **Moonraker:** `wget -qO- "http://127.0.0.1:7125/printer/objects/query?print_stats"` from the printer answers without a token. If it doesn't, the installer's print guard reports `unknown`.
4. **decay71 backend, printer-local:**
   - `wget -qO- http://127.0.0.1:7126/api/materials | head -c 300` returns the materials DB.
   - `wget -qO- http://127.0.0.1:7126/api/integrations` shows no `filamenthub` yet.
5. **Port free:** `netstat -ltn | grep 8089` should be empty.
6. **FilamentHub reachable from the printer:** `wget -qO- http://192.168.1.174:3000/api/v1/info` returns Spoolman 0.23.0.
7. **Stale FilamentHub data.** List the `acestate.before.json` winners for u1-b and the `asserted_at` of each. As of 2026-10-11 all of them were July `user:assign`. Compare each winner with `overrides.before.json`, and write a table with columns slot, FH winner, current override, and match yes/no.

**Gate.** Stop and ask the user if any of these hold:
- the deps are missing
- `auth_check` is absent
- `location /plugin/` already exists

## Phase 2: User decision on stale FilamentHub assignments (Lead)

Pull is now explicit only; the auto-pull on open was removed in this commit. Pressing **Pull** with `prune=true` would still apply July winners over today's correct labels. Quad replenish matches backup spools on those labels.

Show the user the Phase 1.7 table and ask for one of:
- **(a)** Leave FilamentHub as is and don't press Pull. This is the default.
- **(b)** Unassign the stale spools in FilamentHub, by writing `location=None` for those spool ids through the plugin's `/unassign` **after** deploy.
- **(c)** Re-assign the correct spools to the current slots through the new picker after deploy, which makes FilamentHub truthful.

Record the choice. No pruning Pull happens in this runbook unless the user picks (c) and the table then matches.

## Phase 3: Deploy (Deployer)

1. Re-check the print gate.
2. Pack `multiace_plugins/filamenthub` without `.venv`, `__pycache__` or `docs/screenshots`, upload it to `/tmp/filamenthub`, and check that line endings are LF.
3. On the printer as root, run `sh /tmp/filamenthub/install/install_plugin.sh`. Capture the full output to `scratchpad/postprint/install.log`.
4. Verify:
   - `wget -qO- http://127.0.0.1:8089/integration-manifest` returns `"name": "filamenthub"`.
   - `wget -qO- "http://127.0.0.1:7126/api/integrations?refresh=1"` lists `filamenthub`.
   - `nginx -t` passes, and `/etc/nginx/fluidd.d/multiace-plugin.conf` contains `auth_request`. Skip the `auth_request` check if Phase 1 showed `auth_check` absent.
   - From the PC, `curl -s http://192.168.1.136/plugin/filamenthub/ | grep -c "Profiles"` is ≥ 1, meaning the plugin page is served and not the Fluidd SPA fallback.
   - `/etc/init.d/S66filamenthub-plugin status` reports running.
   - `/userdata/filamenthub-plugin/{src,deps,state,install}` all exist.
5. Regression check on the existing console:
   - `GET /multiace/api/state` returns 200, and `device_count` is 2.
   - app.js md5 is still the overlay-patched one recorded in `ops/decay71_overlay/README.md`.

**Rollback:**
1. `/etc/init.d/S66filamenthub-plugin stop`
2. `rm /etc/init.d/S66filamenthub-plugin /etc/nginx/fluidd.d/multiace-plugin.conf`
3. `nginx -t && nginx -s reload`
4. Restore overrides from `overrides.before.json` (Phase 6 step 3).

## Phase 4: API end-to-end (API verifier), test slot only

Use `http://192.168.1.136/plugin/filamenthub/`, or `127.0.0.1:8089` on the printer if auth blocks the PC. `T` is the test slot.

1. **Read paths:**
   - `GET filaments` returns about 83 items with temps.
   - `GET spools` returns the inventory.
   - `GET slots` returns states for all 8 slots.
2. **Profile-only assign:** `POST assign-profile {filament_id:<a PLA profile>, ace:T.ace, slot:T.slot}`. Then check:
   - `GET /multiace/api/slot-override` shows `T` with material, brand, and a DB-valid subtype or `""`.
   - `desired.json` has the entry.
   - FilamentHub is unchanged: diff `GET /api/v1/spool` against `spools.before.json`.
3. **Assign with a new spool:** `POST assign-profile {…, create_spool:true}`. Then check:
   - a new spool exists with `extra.card_uid` starting `"VIRT-`
   - its `extra.filamenthub` is double-encoded, with `location.asserted_by == "user:assign"`
   - its top-level `location` is `"u1-b/ace1 T<n>"`
   - the ace-state winner for `T` is that spool
4. **Clear:** `POST unassign {T}`. Then check:
   - the override for `T` is gone
   - the `desired.json` entry is gone
   - the spool's location is null or ""
   - its other `extra` keys are intact
   Then **DELETE the scratch spool** with `DELETE /api/v1/spool/<id>` and confirm with a 404 on GET.
5. **Refresh:** `klippy.log` shows `MULTIACE_REFRESH_OVERRIDES` handled after steps 2 and 4.
6. **Eject behaviour:** skip. It needs physical slot changes and is a user step, listed under Follow-ups.
7. **Pull:** only if the user chose (c) in Phase 2 and the table matches. Run `POST pull {prune:true}` and check that only covered ACEs change.

## Phase 5: UI end-to-end (UI verifier), Playwright

Open `http://192.168.1.136/multiace/`, then the FilamentHub tab, at 1280×900 and 390×844:

1. The tab renders inside the iframe, and the grid shows ACE 1 and ACE 2 with slots 1–4.
2. The slot cards match `overrides.before.json`, apart from the test slot.
3. On the test slot:
   - open the picker
   - use the Profiles tab: search "PLA", pick one, see the success toast, and see the card update
   - reopen it and pick **Clear slot**: the confirm dialog appears inside the iframe, and the card clears
4. Spools tab: the list renders with weights. **Do not assign a real spool.**
5. Escape closes the picker, and there is no horizontal scroll on the phone layout.

Save screenshots to `scratchpad/postprint/ui-*.png`. If `confirm()` is blocked by iframe sandboxing, record that as a defect, because the Clear button then never fires.

## Phase 6: Persistence and restore (Deployer)

1. Simulate an overlay wipe without rebooting:
   1. `/etc/init.d/S66filamenthub-plugin stop`
   2. move S66 and the nginx drop-in to `/userdata/filamenthub-plugin/backup/sim-wipe/`
   3. `nginx -s reload`
   4. run `sh /userdata/filamenthub-plugin/install/restore.sh`
   5. re-run the Phase 3 step 4 checks
2. The print guard needs a known-bad input. A refusal can't be forced while idle, so instead:
   - run `MOONRAKER_URL=http://127.0.0.1:1 sh /userdata/filamenthub-plugin/install/restore.sh` (`common.sh` honours `MOONRAKER_URL`)
   - check that it refuses with state `unknown` and changes nothing
3. **Restore labels:** for every key in `overrides.before.json` that differs from the current state:
   - `POST /multiace/api/slot-override` with that key's value
   - then `POST /multiace/api/plugin-api/gcode {"script":"MULTIACE_REFRESH_OVERRIDES"}`

   Diff the result against the snapshot; it must be identical.
4. Reboot persistence is **not** tested here, since it needs the user's OK to reboot. List it in Follow-ups.

## Phase 7: Review, docs, memory (Reviewer + Lead)

1. **Fable reviewer:** give it all Phase 0–6 evidence files. It must check that every "Verified" line has a matching captured output, and list any gaps.
2. **Update the plan doc:**
   - mark the "Unverified assumptions" as resolved, with evidence
   - append a "Deployed 20xx-xx-xx" section with md5s for `/userdata/filamenthub-plugin/src` and app.js
3. **Update memory:** rewrite `filamenthub-ace-state-puller.md` as live again, recording the deploy path, `restore.sh`, the no-auto-pull rule, printer id `u1-b`, and the FilamentHub stale-data caveat. Update the `MEMORY.md` line, then run `claude-sync.sh`.
4. Commit, merge, push. Then the final report to the user.

## Follow-ups (not in this run)

- **Physical checks (user):**
  - eject and reinsert the test spool, and confirm the plugin shows "Not loaded" and then "Verified"
  - reboot persistence of the plugin
- **USB fault isolation (user, between prints):** plug ACE 2 straight into a free port on the main 7-port hub, bypassing the 4-port hub at `3-1.3.3.4`. Then the Lead counts `dmesg` `USB disconnect` lines for `3-1.3.3.4*` over one print.
- **Next builds, pending the user's choice from the Fable roadmap in the spec:**
  - #4 explicit `subtype` / `orca_profile` filament fields
  - #11/#10 backup advisor and next-spool queue
  - #7 Orca colour parity
  - #5 tag writing from profiles
- **Refill ACE 2 slot 2.** Label it to match the PLA `7A1212` profile if it should be the backup for ACE 1 slot 2.
