# multiACE — Status review and plan to proceed (2026-09-22)

**Scope:** read-only assessment of the `ryvin/multiACE` repo, its branches/PRs/issues, the
decay71 upstream, and the live Davinci-U1 printer, followed by an opinionated plan.
**Method:** every fact in §1 lists how it was checked. Nothing on the printer was written,
restarted, or sent as G-code; SSH was used only for `ls`/`grep`/`cat`/`wget -qO-` reads.
**Printer host:** `$DAVINCI_U1_HOST` = 192.168.1.136 (Moonraker `printer/info` → `ready`).

> **Headline.** The repo's own firmware (`multiace/`, 0.80b/0.81b lineage) and its own web
> console (`multiace_web/`) are no longer deployed anywhere and are incompatible with the
> firmware that actually runs the printer — decay71 **1.00.1b** (self-updated 2026-09-15,
> already *past* the v0.99.8b tag the review was asked to evaluate). The two sidecar
> plugins, which are the only deliverables that still fit the running stack, are **both
> down**: the FilamentHub plugin's init script was lost in the Aug-4 overlay wipe and the
> autodry plugin was never deployed. Meanwhile ACE B (idx 1) logged **185 `COMMS_LOST`
> events since 2026-09-16** (12 on 2026-09-22 alone) — the underpowered-USB-hub root cause
> recorded on 2026-08-19 has not been fixed. The most valuable next actions are
> operational (restore the sidecars, fix the hub power, trial decay71's Air Print
> Detection), not new firmware code.

---

## 1. Current state — verified facts

### 1.1 Repository

| Fact | Value | How checked |
|---|---|---|
| `main` == `origin/main` | `092343d` (2026-07-24, "fix(web): reject non-finite config values") | `git rev-parse main origin/main` |
| Working tree | clean except **35 untracked**: 33 root `*.png`, `CLAUDE.md.bak.1778602707`, `multiace_backups/` (764 KB printer backup tgz) | `git status --porcelain` |
| Remotes | `origin`=ryvin (GitHub fork of **decay71/multiACE**, `fork=true parent=decay71/multiACE`), `upstream`=BlackFrogKok/SnapAce, `decay71` | `git remote -v`; `gh api repos/ryvin/multiACE` |
| `gh` default repo | **not set** — bare `gh issue list` resolves to `BlackFrogKok/SnapAce` | `gh repo set-default --view` → "no default repository has been set" |
| Fork firmware version stamps | `multiace/VERSION` = `0.80b`; README = "v0.81b First Light Hotfix 1" (promises v0.82); CLAUDE.md = "0.81b" | `cat multiace/VERSION`; `grep` README/CLAUDE.md |
| Fork `multiace/` vs decay71 v0.99.6.2b | 57 files, **+4,483 / −29,092** lines (the fork lacks decay71's web, tools, `ace_bg_swap`, `ace_tipform`, v2 protocol breadth) | `git diff --stat v0.99.6.2b main -- multiace/` |
| Merge-base with decay71/main | `f4cf5b0` (early SnapACE-era README commit) — histories effectively unrelated | `git merge-base main decay71/main` |
| Test suites (local, this session) | `multiace_web`: **429 passed, 1 xfailed** (72 s); `filamenthub`: **54 passed**; `autodry`: **95 passed** | `.venv/bin/pytest -q` in each dir |
| Last ops commits | `d1dce49..c9bbbfd` (2026-07-16) = entangle observe patch + raw ACE-serial log patch | `git log --format='%h %ci %s'` |

### 1.2 decay71 upstream

| Fact | Value | How checked |
|---|---|---|
| Releases after 0.99.6.2b | v0.99.8b "Resupply Run" (2026-08-20), V1.00b (2026-09-07), **V1.00.1b (2026-09-07, latest)** | `gh release list -R decay71/multiACE` |
| Local tags | `v0.99.8b`=`d4af822`, `V1.00.1b`=`0c0c735`, `V1.00b` present after fetch | `git tag -l`, `git ls-remote --tags decay71` |
| `decay71/main` | `1ec5e7c` (2026-09-08), `multiace/VERSION` = **1.00.1b**; 40 commits past v0.99.8b, all "Update README.md"/"Add files via upload" | `git log v0.99.8b..decay71/main`, `git show decay71/main:multiace/VERSION` |
| v0.99.6.2b → v0.99.8b diff | 27 files, **+13,160 / −1,105**; `ace.py` alone +5,289; new `web/backend/ace2_ota.py` | `git diff --stat v0.99.6.2b v0.99.8b` |
| 0.99.8b headline features | Quad Replenish (runout failover to a matching spool), spool management + Spoolman/SpoolLink sync, humidity-controlled drying (ACE 2 only), per-pair purge from slicer flush matrix, **Air Print Detection** (`resistance_pause`, "airlog" flow sampler), ACE 2 OTA flash, parked-position background swaps (experimental), parallel preload | README diff between tags |
| 1.00.1b additions | RFID UID read/write (ACE2-Open), spool binding by card UID, per-spool pressure advance, shipped API docs, stock firmware 1.6.0 support, "installer fixes"; 1.00.1b itself is the fix for the 1.00b load failure on firmware < 1.6.0 | `git show decay71/main:multiace/README.md` lines 12–52 |
| PR acceptance upstream | **1 merged ever** (#23 zh translation). Closed unmerged: #47/#41 Spoolman integration, #36 main_state fix, #34 PAXX 1.4 install persistence | `gh pr list -R decay71/multiACE --state all` |
| Open upstream issues | 3: **#129** (2026-09-22) stock firmware restores `printer.cfg` on reboot unless `/oem/printer_data/.fluidd` exists; #89 tip-forming results; #86 background-swap discussion | `gh issue list -R decay71/multiACE` |

### 1.3 Live printer (Davinci-U1)

| Fact | Value | How checked |
|---|---|---|
| Print state | `print_stats.state = complete` (idle) | Moonraker `objects/query?print_stats` |
| Firmware | **decay71 1.00.1b** (`MULTIACE_VERSION = "1.00.1b"` in `/home/lava/klipper/klippy/extras/ace.py`, files dated Sep 15); web `/multiace/api/version` → `1.00.1b+f026fc15`; Klipper `1.5.2.13_20260722` (PAXX 1.5.2.x), Moonraker 1.5.2 | SSH `grep`, `curl` |
| ACEs | `device_count=2`, both `ready`/`connected`, model "Anycubic Color Engine Pro", fw V1.3.863, `active_device=1`, `head_source` populated for heads 0–3, `head_manual` all False, `mode=multi`, `airprint_detection=False`, `quad_replenish=false`, `spool_mode=local`, `spoolman_url=''` | `objects/query?ace` |
| Live `ace.cfg` | `ace_device_count: 2`, `retract_speed: 36` (survived the 1.00.1b self-update), `seat_overshoot_length: 20`, `resistance_pause: false`, `state_debug/usb_debug/fa_debug: false` | SSH `grep` of `/home/lava/printer_data/config/extended/ace.cfg` |
| Web console | decay71's own (`/home/lava/multiace_web`, `S98multiace-web`, `S99multiace-web-watchdog` + inittab respawn). `/multiace/health` → 404 (the fork console's route) confirms it is not the fork | `curl`, SSH `ls /etc/init.d`, `grep /etc/inittab` |
| nginx | `/etc/nginx/fluidd.d/multiace-web.conf:16: absolute_redirect off;` present (fix survived); no `location /plugin/` — decay71 proxies plugins through its own app at `/multiace/plugin/<name>/` | SSH `grep` |
| **Sidecar plugins** | **Neither running.** `wget 127.0.0.1:8089` and `:8090` → connection refused; `/multiace/plugin/filamenthub/` → `404 plugin 'filamenthub' not registered`; no `S66filamenthub-plugin`/`S67autodry-plugin` in `/etc/init.d`; `/userdata/filamenthub-plugin/src` exists (Jul-9 pre-reconciliation code, root 0700); `/userdata/autodry-plugin` **does not exist**; no Govee bridge (`:8091` refused, no `S64govee-bridge`); `/userdata/multiace-web` (fork console) gone | SSH `ls`, `wget`; `curl` via nginx |
| Bare `/plugin/<name>/` "200" | is **Fluidd's SPA fallback** (returns Fluidd `index.html`) — not evidence of a plugin | `curl` body inspection |
| PR #28 artefact | `S59multiace-prewarm` **is installed** on the printer (`/etc/init.d/S59multiace-prewarm`, `multiace_prewarm.log` dated Aug 7) | SSH `ls` |
| decay71 #129 exposure | **Not exposed**: `/oem/printer_data/.fluidd` exists (Apr 26), `[include extended/ace.cfg]` at `printer.cfg:1363` | SSH `ls`, `grep` |
| Ops observe/raw-serial patches | **Not applied** (markers `ENTANGLE_OBSERVE_ONLY`/`ACE-RAW`/`entangle-OBSERVE` = 0/0/0; firmware replaced twice since 07-16) | SSH `grep -c` |
| **USB health** | `multiace_state.log*`: **185 `COMMS_LOST idx=1`** (log.3 5, log.2 2, log.1 171, current 14 since the 2026-09-21 14:35 reboot; 12 on 2026-09-22 between 19:22 and 22:02 UTC, another 00:21 on 09-23); 2 `SERIAL_WRITE_FAILED_FIRST`/`SERIAL_WRITE_RECOVERED`; `dmesg` shows `4-1.3.3.4.3` (ACE B) re-enumerating as `ttyACM3` repeatedly. Uptime 1 d 9 h. ACE B currently `ready` (firmware re-latches silently) | SSH `grep -c`, `dmesg`, `uptime` |
| Stale `klippy.log` path | `/home/lava/printer_data/logs/klippy.log*` all dated Aug 4 — the live log is `/oem/klippylogs/klippy.log` (docs/tools that tail the old path are wrong) | SSH `find / -name klippy.log -mmin -120` |

### 1.4 Memory-recorded bugs vs decay71 1.00.1b (what upgrading fixed / re-broke)

The printer is already on the newest decay71, so "should we upgrade to v0.99.8b" is moot.
What matters is which recorded problems the running build resolves:

| Memory entry | Status on decay71 1.00.1b | Evidence |
|---|---|---|
| orjson `Dict key must be str` (PAXX 12-19+) | **Fixed upstream** — `get_status` emits `{str(k): v for k,v in self._head_source.items()}` | `git grep` v0.99.8b `ace.py:14514`; live `head_source` keys are strings |
| Entangle-detect false positive on non-start-ACE heads | **Moot** — decay71 calls `skip_entangle_check(True)` on all 4 heads at ready (`_disable_stock_entangle_detect`), so no false pause but also no real-tangle net | `git grep` decay71/main `ace.py:1544-1558, 1958` |
| Snag detection "impossible (wheels frozen)" | **Reopened** — 0.99.8b adds Air Print Detection (`resistance_pause`, `airlog` eddy/flow sampler, `ACE_SET_AIRPRINT_DETECTION`). PR #28's port-plan flagged exactly this. It is **OFF** on the printer | `ace.py:86-90, 137, 750-788, 9267`; live `airprint_detection: False` |
| `retract_speed` too fast (shipped 80) | **Not fixed upstream** — `ace.cfg` still ships `80` (code default 50); the installer's merge preserved the live `36` through the self-update. A fresh install or `--keep-config`-less reinstall regresses | `git show decay71/main:…/ace.cfg:110`; live `36` |
| nginx `absolute_redirect off` fix | **Not upstream** (0 hits in the template). Survived the 09-15 self-update only because `multiace_update.sh` runs as `lava` and skips the nginx drop-in; a root reinstall overwrites it | `git grep absolute_redirect` = 0; installer line 541 log text |
| USB reconnect gives up after 3 attempts, no by-path rescan | **Not fixed upstream** — `CONNECT_ATTEMPTS = 3`, backoff `(0.3, 0.8, 1.6)` in `_reconnect_or_pause` | decay71/main `ace.py:2002, 4404-4418` |
| Head MANUAL-mode catch-22 / no Mark-Loaded override | **Still present** (memory 2026-09-16 on 1.00.1b) | memory `ace-head-manual-mode-blocks-ops` |
| Eject-debounce deletes tracker labels ("Exhibit A") | **Still present** — `EJECT_DEBOUNCE_S` + `_drop_override_if_present` at `main.py:320-321, 3558` | `git grep` decay71/main |
| Stock firmware restores `printer.cfg` (decay71 #129) | **Not exposed** on this printer (`.fluidd` marker exists) | SSH `ls` |
| Filament runout mid-print (SnapAce #18 class) | **Addressed upstream** by Quad Replenish (`quad_replenish`, off by default) | `ace.cfg:47-52` |

### 1.5 Planning-doc status (what was done vs outstanding)

| Doc | Status |
|---|---|
| `reviews/2026-07-16-critical-review-and-roadmap.md` | NOW-1 (M1 phase 1) built on an unmerged branch, never deployed. NOW-2 (M2 fork-disposition ADR) **never written — this document is that decision** (§4 Phase 0). NOW-3 (deploy autodry) not done. NOW-4 (reconnect hardening upstream) not done. "Riskiest assumption" probe (inactive-ACE occupancy observable) not run. |
| `plans/2026-07-16-filamenthub-reconciliation.md` | Tasks 1–6 implemented in one commit `d66d82c` on `feat/filamenthub-reconciliation` (13 files, +395/−35, tests included); Task 7 (live verify + deploy) not done; branch is 5 behind main. |
| `reference/2026-07-07-decay71-vs-ryvin-comparison.md` | Recommendation "Direction A — stay on decay71" was followed de facto (printer runs decay71) but never ratified; Direction-A items #2–#4 (Mark_Loaded, park knob, reconnect hardening) not started. The "60-second keepalive tiebreak" was settled by the 07-16 review (decay71 has a 1 Hz heartbeat). |
| `plans/2026-06-20-decay71-098b-kindred-allies-port.md` | Waves 4–5 were **CUT** by the 07-16 review; Waves 1–3 partially shipped as fork-web PRs #15–#26 — all now dead code because the fork web is undeployed. |
| Ops experiment (`d1dce49..c9bbbfd`) | **Concluded 2026-07-16**: all wheel channels frozen in ACE mode → wheel-based snag detection impossible (memory `snag-detection-no-wheel-signal-ace-mode`). The raw-serial experiment (`c9bbbfd`) has **no recorded result** anywhere (memory or docs) — treat as never run. Referenced `2026-07-16-fable-snag-review.md` **does not exist** in the repo. Both patches are anchored to 0.99.2b sources and are dead against 1.00.1b. |

---

## 2. Triage table — branches, PRs, issues

### 2.1 Local / remote branches (`git cherry main <branch>`; "equivalent" = patch already in main)

| Branch | Unique commits | Verdict | Action |
|---|---|---|---|
| `docs/decay71-followups` | 0 (59 equivalent) | fully merged via rebase | **Delete** |
| `feat/multiace-web-operations` | 0 (47 equivalent) | fully merged | **Delete** |
| `origin/feat/wheel-encoder-fallback` | 0 (32 equivalent; merge `4733e3c`; PR #11 closed) | fully merged | **Delete remote** |
| `feat/web-filament-sync` | 0 (PR #26 merged `e139146`) | merged | **Delete** |
| `feat/web-reseat-slot` | 0 (PR #25 merged `22958bc`) | merged | **Delete** |
| `feat/web-unassign-slot` | 0 (PR #23 merged `f3a0ad7`) | merged | **Delete** |
| `fix/web-load-mate-pair-head` | 0 (PR #24 merged `918acff`) | merged | **Delete** |
| `feat/decay71-0.99.6.2b-portable-fixes` (= **PR #28**, mergeable) | 1 (`f41926b`: `S59multiace-prewarm` + installer wiring + port-plan doc) | additive, already field-deployed on the printer since Aug 7 | **Merge, then archive the firmware tree** (Phase 0). Its port-plan doc is the best written record of why the fork firmware is not patch-portable — keep it. |
| `feat/filamenthub-reconciliation` | 1 (`d66d82c`, M1 phase 1, 5 behind) | live, valuable, untested against current main | **Rebase → run 54+ tests → keep for Phase 3** (UNVERIFIED here — to confirm, run: `git worktree add /tmp/fhr feat/filamenthub-reconciliation && cd /tmp/fhr/multiace_plugins/filamenthub && pytest -q`) |

Also: set `gh api -X PATCH repos/ryvin/multiACE -f delete_branch_on_merge=true` (currently `false`) and `gh repo set-default ryvin/multiACE` so bare `gh` commands stop targeting SnapAce.

### 2.2 Pull requests / issues on `ryvin/multiACE`

| Item | Verdict | Action |
|---|---|---|
| PR #28 prewarm hook | see above | Merge |
| Issue #27 "Phase 4: consume FilamentHub ace-state (contract + required config fix)" | Landmine #1 is **still live**: `install/S66filamenthub-plugin:10` defaults `MULTIACE_PRINTER_ID` to `u1-1`; FilamentHub's real ids are `u1-a`/`u1-b`/`kobra`, and `u1-1` returns an empty `200` silently | Fix the default (or make the var mandatory and fail loud at startup) in Phase 1, then close #27 |

### 2.3 The 12 "open issues" — they are on **BlackFrogKok/SnapAce**, not this repo

`gh issue list` with no default repo resolved to the `upstream` remote. ryvin cannot close them; they are useful only as field intel about the SnapACE/0.8x lineage this fork descends from. Decay71 status per issue (grep of `decay71/main`):

| SnapAce # | Title | decay71 1.00.1b | Our action |
|---|---|---|---|
| #18 Filament ran out | Quad Replenish (runout failover) | none — decay71 covers it |
| #17 Can auto-load but not unload | hardened unload path + `ace_tipform` | none |
| #16 `/dev/ttyUSB` connect | decay71 scans `/dev/serial/by-path` | none |
| #15 Panda Breath | third-party hardware — out of scope | none |
| #14 `extruder.py` missing `nozzle_diameter` (PAXX 12) | 47 `nozzle_diameter` refs in decay71 kinematics/extras | none |
| #12 / #10 firmware 1.3.0 incompat / ttyACM0 | decay71 tested on 1.3–1.6 / PAXX 12-16+ | none |
| #9 1 toolhead only | `ace_device_count`/mode handling | none |
| #7 ACE Pro 2 (100 comments) | supported since 0.97b; OTA flash in 0.99.8b | none |
| #6 / #3 per-head PTFE length | per-ACE + per-slot `swap_retract_length` overrides | none |
| #2 feed/retract command | `ACE_FEED` / `ACE_RETRACT` exist | none |
| PR #13 "v1.3.0-paxx12-16" (3 files, +298/−146) | superseded by decay71 | none |

**Recommendation:** do not spend time on SnapAce's tracker. If anything, post one comment on #7 pointing at decay71 (already referenced in decay71's README).

### 2.4 decay71 issues worth our attention

| # | Relevance | Action |
|---|---|---|
| #129 stock firmware restores `printer.cfg` | Not exposed here (`.fluidd` present), but the fork installer's persistence logic and our memory should record the `/oem/printer_data/.fluidd` rule | Add to memory; nothing to build |
| #86 background-swap discussion | overlaps our cross-slot-drift instinct #2 | Optional: post our drift observation |
| #89 tip-forming results | we run PLA at `retract_speed 36`/`seat_overshoot 20` | Optional data point |

---

## 3. Risks and open questions

1. **ACE B USB instability is the dominant reliability risk right now** (185 drops in a week; 12/day). Memory `davinci-usb-hub-instability-root-cause` (2026-08-19) already diagnosed a bus-powered Genesys hub chain shared with the USB-ethernet NIC. Until a powered hub separates the NIC from the ACEs, every mid-swap drop is a pause (or a wrong-color print if `head_source` drifts). No software change in this repo fixes this.
2. **Upstream is not PR-friendly.** decay71 ships opaque uploads and has closed 4 of 5 substantive PRs. Anything we need in the firmware must be either (a) filed as an issue with a minimal patch and *hoped for*, or (b) carried as a small, idempotent **post-update overlay** we re-apply after each `ACE_UPDATE_APPLY` (the observe-patch pattern: anchored, refuses on mismatch, `--revert`, preserves `lava:lava 644`). Big local firmware forks are exactly the treadmill the 07-07 comparison warned about.
3. **Two competing slot-identity models.** decay71 1.00 now has native spool management (Spoolman/SpoolLink sync, RFID UID binding, per-spool PA). The FilamentHub plugin mirrors labels into `slot_overrides.json`, which the decay71 backend deletes on eject (Exhibit A). **Open question:** does FilamentHub expose (or can it expose) a Spoolman-compatible API? If yes, `spoolman_url → FilamentHub` retires most of the plugin. Check on the FilamentHub side first (file an issue in `ryvin/FilamentHub` per the cross-project rule) before investing further in M1.
4. **Overlay wipes.** `/etc/init.d` entries and `/home/lava/*` are overlay-resident; the Aug-4 event erased the fork web, the sidecar init scripts and the Govee bridge. `/userdata` survived. Any sidecar deployment needs a documented, scripted "restore after wipe" path, or it will silently die again.
5. **Auto-dry has no humidity source.** ACE Pro reports `humidity: None` (verified 07-08); the Govee bridge is gone. Deploying autodry now yields an inert FSM (manual `/dry` only). Only deploy it together with a humidity source, or not at all.
6. **Documentation truth drift.** `CLAUDE.md` still describes the fork stack as the running system (0.81b, `ACE_HEAD_STATUS` poller, `systemctl restart klipper`, "~121 tests", "keep both logs enabled"); README promises v0.82; `VERSION` says 0.80b; `MEMORY.md` carries two conflicting decay71 versions and a duplicated index entry. New sessions will act on the wrong model.
7. **Unverified:** whether `feat/filamenthub-reconciliation` tests pass on top of current main; whether Air Print Detection actually catches a snag on this rig (it is a candidate, not a result); where decay71 1.00.1b's own web lists plugins (`GET /api/integrations`, route confirmed in source, not exercised live because no plugin is up).

---

## 4. Prioritized plan

### Phase 0 — Decide the repo's role and clean house (no printer time; ~half a day)

**Decision (this is the M2 ADR the 07-16 review asked for):**
`ryvin/multiACE` is a **downstream of decay71**, not a parallel firmware. Its job is:
(1) sidecar plugins and ops tooling that run *alongside* decay71 on the Davinci-U1,
(2) a small, re-applyable overlay of local fixes for decay71 that upstream has not taken,
(3) issues (with patches) to decay71, and (4) the design/ops record. The fork's own
firmware (`multiace/`) and web console (`multiace_web/`) are **frozen** — kept in history,
not maintained.

Tasks:
- [ ] Merge PR #28, then tag `fork-firmware-final` on the merge commit.
- [ ] Add a top-of-README banner and rewrite CLAUDE.md §"Project Overview" to the decision above: printer runs decay71 (`ACE_UPDATE_CHECK`/`ACE_UPDATE_APPLY`, `/etc/init.d/S60klipper restart`), `multiace/` and `multiace_web/` are frozen, plugins are the active surface. Fix: `systemctl` → `S60klipper`, live `klippy.log` path `/oem/klippylogs/klippy.log`, test counts (429/54/95), remove "keep both logs enabled" (decay71 ships them off; enabling `state_debug` is our choice, not a dependency).
- [ ] Delete the 7 fully-merged branches (§2.1) locally and `origin/feat/wheel-encoder-fallback`; `gh repo set-default ryvin/multiACE`; enable `delete_branch_on_merge`.
- [ ] Clean-up list in §5.
- [ ] Fix `MULTIACE_PRINTER_ID` default (`u1-a`, or fail-loud when unset) in `multiace_plugins/filamenthub/install/S66filamenthub-plugin` + README; close #27.
- [ ] Memory hygiene: one entry "Davinci-U1 runs decay71 1.00.1b (2026-09-15)"; drop the duplicate/contradicting index lines; record the `.fluidd` rule (#129), the live klippy.log path, "plugins were found down 2026-09-22", "raw-serial experiment never produced a result".
- **Acceptance:** `git branch --no-merged main` lists only `feat/filamenthub-reconciliation`; `gh pr list -R ryvin/multiACE` empty; CLAUDE.md contains no `ACE_HEAD_STATUS`/`systemctl`/`0.81b`; `git status` clean.

### Phase 1 — Restore the sidecars (printer idle required; one evening)

- [ ] Add `tools/printer/restore_services.sh` (idempotent; root): re-creates `S66filamenthub-plugin` (and optionally the Govee bridge) from `/userdata`, verifies `wget -qO- 127.0.0.1:8089/integration-manifest`, and prints `GET /multiace/api/integrations`. Document it as the post-wipe runbook.
- [ ] Redeploy the FilamentHub plugin (`sh install/install_plugin.sh` as root, printer idle) with the corrected printer id.
- [ ] Decide autodry: deploy **only if** a humidity source is restored the same evening (Govee bridge on a port outside 8089–8098 or inside the plugin range with its own manifest); otherwise leave it in-repo and say so in README.
- **Acceptance (all read-only checks):** `curl http://$DAVINCI_U1_HOST/multiace/plugin/filamenthub/integration-manifest` → 200 JSON; `GET /multiace/api/integrations` lists `filamenthub`; a `POST /pull` with `prune=false` returns `errors: 0` and `print_stats.state` unchanged; `ls /etc/init.d/S66*` exists; reboot survival tested once (`uptime` resets, manifest still 200).

### Phase 2 — Reliability on the actual failure (hardware + one test print)

- [ ] **Powered USB hub**: ACEs on an externally powered hub, NIC on a different port/hub, shorter chain (memory 2026-08-19). No code.
- [ ] **Air Print Detection trial**: with the printer idle, `ACE_SET_AIRPRINT_DETECTION ENABLE=1` (persists via save_variables per `ace.py:9267-9278`), run a multi-colour print, then induce a snag on a non-active slot's spool. Record whether it pauses and how late versus the camera noodle detector. This supersedes both dead ops patches.
- [ ] Optional: `ACE_SET_QUAD_REPLENISH ENABLE=1` trial with a duplicate spool, since same-identity spools are common here.
- **Acceptance:** `COMMS_LOST` rate in `multiace_state.log` ≤ 1/day over 3 days (baseline 12/day on 09-22); a memory entry replacing "snag detection impossible" with the measured Air-Print result (pass or fail); `docs/superpowers/reviews/` gains a short result note and the two ops patch scripts are moved to `tools/legacy/` with a header saying they target 0.99.2b.

### Phase 3 — FilamentHub identity: decide, then finish M1 (mostly no printer time; deploy needs idle)

- [ ] File `ryvin/FilamentHub` issue: "Spoolman-compatible read API so decay71 `spoolman_url` can point at FilamentHub?" If feasible, that becomes the integration and the plugin shrinks to a UI shim.
- [ ] If not feasible within a few weeks: rebase `feat/filamenthub-reconciliation`, run tests, deploy (plan Task 7), verify `EXPECTED_NOT_LOADED` ghost cards persist across polls despite decay71's eject-debounce.
- [ ] File decay71 issue for the eject-debounce deleting externally-set overrides (attach the 15-line patch from the 07-16 review §6.1).
- **Acceptance:** either `spoolman_url` set on the printer and the spool list shows FilamentHub spools, or the plugin's `GET /slots` shows the six reconciliation states live with zero motion.

### Phase 4 — Upstream overlay and issues (no printer time to write; idle time to validate)

Carry these as `overlay/` patch scripts (observe-patch pattern) **and** file each as a decay71 issue with the patch attached:
- [ ] nginx template: `absolute_redirect off; port_in_redirect off;` in `multiace-web.nginx.conf` (trivial, high value behind any TLS proxy).
- [ ] `_reconnect_or_pause`: rescan `/dev/serial/by-path` and extend attempts before pausing; do not pause the print for a non-active ACE. Our flaky ACE B is the natural test bed — validate **after** Phase 2's hub fix so the two changes are not confounded.
- [ ] `retract_speed` default 80 → document/lower (issue with the spool-winder evidence from memory `ace-retract-speed-tangle`).
- [ ] Feature request: state-only "mark head loaded" override + manual-mode exit without physical unload (memory `ace-head-manual-mode-blocks-ops`).
- **Acceptance:** each overlay script applies and reverts cleanly against the `V1.00.1b` tag in a local checkout (`git worktree add … V1.00.1b`), refuses on a mismatched anchor (tested with a deliberately edited copy), and each has an upstream issue URL recorded in the script header.

### Cut / defer (explicit)
- **Cut:** any further work on `multiace/` firmware or `multiace_web/` (frozen); Kindred-Allies Waves 4–5; swaptimizer/8-colour post-processor (decay71's preflight and `post_process_virtual_toolheads.py` cover it); HelixScreen SP3 (stalled since May, consumes a contract the fork no longer provides).
- **Defer:** Cloudflare Access hardening; i18n; anything in the 07-16 "LATER" list.

---

## 5. What to delete / clean up

| Item | Action |
|---|---|
| 33 untracked `*.png` at repo root | Move the one referenced by a doc (`multiace-dashboard-0based.png`, cited from a plan) into `docs/superpowers/assets/` and fix the link; delete the other 32 (they are session screenshots; `.gitignore` already blocks `hardware-*`/`embed-*`/`fluidd-*`/`humidity-*` — extend it to `/*.png`). |
| `CLAUDE.md.bak.1778602707` | Delete (77-line diff vs current; superseded by the Phase-0 rewrite). |
| `multiace_backups/` (untracked, 764 KB tgz + manifest from 2026-06-29) | Move out of the repo (e.g. `~/backups/davinci-u1/`); add `/multiace_backups/` to `.gitignore`. Printer-side copies remain in `/userdata/multiace-backups/`. |
| `docs/superpowers/reviews/__pycache__/` | Delete (ignored, but stray). |
| `docs/superpowers/reviews/{observe_entangle_patch,ace_raw_serial_log_patch}.py` | Move to `tools/legacy/` with a "targets decay71 0.99.2b — dead against 1.00.1b" header, or delete; the 07-16 memory entry already holds the conclusion. |
| `.decay71-ref/` (ignored, 2 MB, 0.99.2b snapshot) | Delete — the `decay71` remote + tags (`git show V1.00.1b:…`) replace it. Update the four docs that cite `.decay71-ref/...:line` to cite `V1.00.1b:path:line` or accept the citations as historical. |
| Branches | The 7 in §2.1 + remote `feat/wheel-encoder-fallback`. |
| Stale doc claims | `CLAUDE.md` (see Phase 0), `README.md` "v0.82 will lift…" promises, `multiace/VERSION 0.80b` vs README 0.81b, `MEMORY.md` duplicate + contradicting entries, references to the non-existent `2026-07-16-fable-snag-review.md`. |

---

*Verification commands used are listed inline in §1; nothing here was inferred from a
document's description of itself except where marked UNVERIFIED.*
