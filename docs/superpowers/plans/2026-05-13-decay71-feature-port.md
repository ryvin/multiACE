# Decay71 feature port — implementation plan

**Date:** 2026-05-13
**Goal:** Adopt decay71/multiACE 0.95b's major improvements into ryvin/multiACE while preserving our test suite, web console architecture, and tooling.
**Branch strategy:** One branch per phase, merged sequentially. Phase 1 unblocks Phases 2 / 3 / 6.

## Why not full migration

| Their gain | Their cost |
|---|---|
| Rewritten USB engine (no start-ACE pin) | No test suite |
| Hardened load/unload + snapshot/resume | Vue UI (different stack) |
| Protocol v1/v2 abstraction (new ACE firmware) | German-first docs, opaque commit history (bulk uploads) |
| Swaptimizer (`--optimize` / `--layer`) | No Activity / Diag / Dryer tabs |
| Per-ACE FA toggles | No Govee humidity bridge, no visual-regression script |
| EN/DE i18n built in | No mobile-viewport work, no calibration help UX |

Conclusion: graft their wins onto our scaffolding.

## Phase 1 — USB engine + protocol abstraction (branch `port/usb-engine-v2`)

Surgical graft of their connection model onto our `ace.py`.

### Step 1.1 — Vendor protocol files
Copy from `.decay71-ref/multiace/klipper/extras/`:
- `ace_protocol.py` (27 lines, abstract base)
- `ace_protocol_v1.py` (107 lines, old ACE firmware — vendor 28e9:018a, JSON over UART+RTS/CTS)
- `ace_protocol_v2.py` (481 lines, new ACE firmware — vendor 1a86:55d3, protobuf over USB)

### Step 1.2 — Refactor connection management in `ace.py`
Replace single-serial model with per-ACE dicts. Critical files: `multiace/klipper/extras/ace.py`.

- `_serials[idx]` — one open serial per ACE, never closed mid-session
- `_protocols[idx]` — chosen at USB-discovery time
- `_connected_per_ace[idx]` — independent connection status
- `_reader_threads[idx]` / `_writer_threads[idx]` — daemon threads for V2
- `_writer_queues[idx]` — `queue.Queue` per ACE
- `_seq_lock` — global sequence number lock
- `_cb_locks[idx]` — per-ACE callback dispatch lock

Remove start-ACE pin logic (the print-duration single-port lock from 0.81b).

### Step 1.3 — Audit state additions
In `_audit_state()`: add `fa_context` field ('print' / 'load' / null). The `parked` flag on `head_source[h]` already exists (added earlier this session).

### Step 1.4 — Web console compatibility
Verify `multiace_web/src/multiace_web/state.py` handles the new `fa_context` field gracefully (it should — unknown keys are ignored). Add a tailer test asserting forward-compat.

### Step 1.5 — Validation
- pytest in `multiace_web/` — must remain green
- Install onto Davinci-U1 (printer in `standby`)
- `ACE_HEAD_STATUS` on both ACEs after Klipper restart
- Run cross-ACE toolchange (T0→T3 swap-park) and verify both ACEs keep feed_assist — the core win

### Step 1.6 — Commit & merge
- Commits split: protocol vendor → connection refactor → audit state → tests
- Open PR; merge after manual validation

## Phase 2 — Hardened load/unload (branch `port/hardened-load-unload`, blocked by Phase 1)

Port from their `filament_feed_ace.py` (1839 lines vs our smaller version):
- Retry/release/regrip on past-grip (the wheel-encoder ambiguity we hit on this session)
- Snapshot-on-failure (active extruder + per-head target temps captured at failure)
- multiACE-safe `resume` override

## Phase 3 — Per-ACE FA toggles (branch `feat/per-ace-fa-toggles`, blocked by Phase 1)

- New `[ace]` options: `fa_print_disable`, `fa_load_disable` (comma-separated ACE indices)
- New `[ace]` option: `fa_debug` (bool)
- Surface in Config tab with our CONFIG_KEY_HINTS help/type system

## Phase 4 — Swaptimizer (branch `feat/swaptimizer`)

Extend `multiace_web/tools/multiace_postprocess.py`:
- `--optimize`: T-index reassignment minimizing swaps (~20–30% savings)
- `--layer`: Belady-optimal layer-boundary rewrites for ≤4 colors/layer

## Phase 5 — Web UI parity (branch `feat/web-ui-parity`)

Keep our Vanilla stack. Borrow ideas:
- Wiring overlay: SVG lines on Dashboard slot→toolhead
- Editable command queue: pause/drop pending macros (new endpoint, new tab section)
- Saveable filament loadouts: `/api/snapshots` endpoint + UI
- EN/DE i18n: `/api/i18n/{lang}` endpoint, port their 303-key catalog

## Phase 6 — Calibration drop (branch `chore/drop-grip-calibration`, blocked by Phase 1)

After USB engine port, park-retract behavior changes (full per-ACE FA available means park retract may not need to clear the splitter). Re-evaluate `default_park_retract_length_mm: 700`; possibly remove or relax. Update README accordingly.

## Risk register

| Risk | Mitigation |
|---|---|
| Per-ACE threading races (their V2 reader/writer threads) | Reuse their `_seq_lock` / `_cb_locks` patterns verbatim |
| Audit log schema drift breaks web console state.py | Forward-compat test + state.py defensive parsing |
| Their `_t()` localization calls leak untranslated keys | Skip i18n shim; keep English hardcoded in our port |
| ACE Pro firmware on rig is v1 (old) — V2 path untested | Test V1 path on Davinci-U1 first; V2 validated only when newer firmware acquired |
| Print-during-port runs unsafe ops | Always check `print_stats.state` before any printer-side change |

## Out of scope

- Full Vue migration of frontend
- Their nginx auth_request → Moonraker `/access/user` pipeline (we keep bearer-token model)
- Their `_t()` localization layer (we skip for now; Phase 5 i18n is just string catalog, not Klipper-side)
