# decay71 0.99.2b vs ryvin fork — comparison + gap-closing analysis

**Date:** 2026-07-07
**Method:** 5 parallel code-review passes, each reading both trees (ryvin = working tree; decay71 = `git show v0.99.2b:…`), file:line-cited.
**Question it answers:** was the ryvin fork "smarter and more robust," or is that a feeling — and what would it take to close the gap either direction.

## Bottom line

The fork accumulated **real** robustness, but decay71 0.99.2b has independently solved most of the same problems — often better — and has pulled ahead on print-time correctness, extensibility, and hardware breadth. The three things the user likes in decay71 (no slot-lock, command queue, GUI) are **objectively better there**, not just preference. Net: keep decay71 as the base; graft the fork's few genuine wins onto it.

### ⚠️ Correction to a prior claim (and to CLAUDE.md instinct #7)
Earlier in-session it was asserted that decay71 lacks the USB keepalive and that "your keepalive would have prevented today's stall." The code contradicts that: decay71 0.99.2b **has an equivalent 1 Hz per-ACE heartbeat** (`_make_heartbeat_tick_for` / heartbeat tick sending `get_status`, cited `ace.py:2112`, `:3919`). Today's `reconnect_failed → found 0 ACEs` stall was **decay71's short, no-rescan reconnect window + pause-on-any-ACE**, not a missing keepalive.
**Unresolved:** a second review pass reported "no keepalive in decay71." The two disagree (likely "heartbeat" vs a named `keepalive`/autodry-coupled module). **Needs a 60-second tiebreak before banking.** Until then, treat "decay71 has periodic per-ACE traffic" as probable-but-unconfirmed.

---

## Axis-by-axis verdicts

### 1. USB / serial resilience
- **Prevention (idle-reset avoidance):** ~tied — both send ~1 Hz traffic to every open ACE (pending the tiebreak above).
- **Reconnect breadth:** decay71 wins — 3 backoff attempts + cascade detection + errno5 counters vs ryvin's single-shot reopen-per-tick.
- **Blast radius:** ryvin wins — a background ACE's failure never pauses the print; decay71 pauses on *any* ACE's exhausted retries.
- **Shared weakness:** neither re-scans `/dev/serial/by-path/` before a runtime reconnect (both reuse the cached path), so neither survives a re-enum that outlasts its retry window. This is the actual cause of the observed incident.

### 2. Slot-locking / cross-ACE swap
- Ryvin's **start-ACE lock is mostly obsolete debt, not a safety feature** — its own comment (`ace.py:1846`) says the USB-reset-on-switch problem that justified it was fixed by porting decay71's keepalive/fast-path; the README promises to remove it in v0.82. What survives is a single `_feed_assist_index` scalar (legacy data-model limit).
- decay71's **no-lock is deliberate architecture**: per-ACE `_feed_assist_per_ace`, per-ACE reader/writer threads (V2) or per-idx reactor fd (V1), and an explicit `preserve_print_fa` policy. Finer swap tuning too: `swap_retract_length` global **+ per-ACE + per-slot** overrides vs ryvin's single global.
- **Verdict:** user's preference is correct; decay71's no-lock is the more complete solution, not riskier.

### 3. Command queue
- decay71 has a genuine client-side FIFO (`cmdQueue`, `app.js`): dedup, one-at-a-time with ordering guarantee, **stop-on-error** (a failure halts the queue until acknowledged), pause/resume, and it refuses to dispatch during `swap_in_progress` (dodges the false-timeout bug). Backend `/api/macro` is a dumb proxy.
- ryvin: fire-and-forget `/api/command`, no queue, only soft `disabled`-button hints; concurrent clicks fire concurrent HTTP calls.
- **Verdict:** decay71 materially more robust. Portable to ryvin's `app.js` with **no backend change**.

### 4. Web GUI / extensibility
- decay71 is the better **platform**: plugin discovery (`/integration-manifest`) + reverse proxy (`/plugin/{name}`) + scoped `/api/plugin-api/*`, in-browser Pyodide preflight (single-source-of-truth swap planner), full i18n (en/de/zh, ~400-line catalogs), in-GUI self-update with a debug-flag guard.
- ryvin is the better **feature-complete deliverable today**: deep FilamentHub/Spoolman binding, autodry UI, hardware-twin, 7 native tabs, **zero external-CDN dependency**. (decay71 loads Vue from `unpkg.com` with **no fallback** — a real offline risk on a LAN printer.) ryvin's i18n is a 25-key stub, not comparable.
- **Verdict:** build on decay71's substrate; ryvin's unique UI features mostly map to plugins (proven by the FilamentHub plugin). Autodry is the exception — it's a server-side FSM, needs a *service* plugin, not an iframe.

### 5. State persistence / recovery / autodry / customization
- **Ryvin wins:** autodry (decay71 has **none** — only a manual dry button), `ACE_MARK_HEAD_LOADED` state-only recovery lever, `default_park_retract_length_mm` geometry knob.
- **decay71 wins:** print-start head_source **integrity audit + auto-heal** (`ace.py:1540-1610`, `notify_external_load`) that ryvin lacks entirely; protocol/hardware breadth (V2 / ACE Pro 2, virtual toolheads >4 via `post_process_virtual_toolheads.py`); broader swap knobs (`swap_probe_temp`, `wiggle_scheme`, `seat_overshoot_length`, `extrusion_retry*`); i18n; self-update.
- **Correction:** `state_debug`/`usb_debug` are **not** ryvin-only — decay71 has both. (CLAUDE.md overstates.)
- **Verdict:** complementary. Ryvin hardened for 24/7 idle/auto-dry; decay71 hardened for print-time correctness + hardware breadth.

---

## Gap-closing analysis

Two directions. They are **not** equal cost.

### Direction A (recommended, bounded): ryvin's wins → decay71
Finite, one-time, and decay71's plugin system was built for it. Ranked value/effort:

| # | Item | Effort | Approach | Value |
|---|------|--------|----------|-------|
| 1 | **Autodry** | M (few days) | Standalone **service-plugin** (own FastAPI app on a plugin port, registers via `/integration-manifest`, polls Moonraker on its own schedule) — same pattern as the FilamentHub plugin. Depends on periodic ACE traffic existing (it does). | High — the one real feature gap |
| 2 | **`ACE_MARK_HEAD_LOADED`** recovery macros | S (hours) | Drop the macro + handler next to decay71's existing `ACE_CLEAR_HEADS`/`ACE_SET_HEAD_MANUAL`. | High for recovery |
| 3 | **`default_park_retract_length_mm`** | S | One config knob + park pattern. | Medium |
| 4 | **Reconnect hardening (upstream both)** | S-M | rescan by-path before retry; don't pause the print for a non-active ACE. Fixes today's actual incident — offer as a PR to decay71. | High reliability |

**Total: ~1–2 weeks, then done.** No moving target.

### Direction B (what "close the gap to decay71" literally means): decay71's lead → ryvin fork
Bring the fork up to decay71 parity. **Honest caveat first:** decay71 is actively developed, so this is chasing a moving target — you re-incur the exact divergence/maintenance cost that made the fork fall behind (orjson shutdown, installer drift, 322 commits). Every item below is also *re-implementing work decay71 already did and will keep extending.* Ranked:

| # | decay71 advantage to add to ryvin | Effort | Notes / risk |
|---|-----------------------------------|--------|--------------|
| 1 | **Command queue** (frontend FIFO) | **S** (1–2 days) | Pure `app.js`; no backend change. Highest value/effort. |
| 2 | **Print-start head_source audit + auto-heal** | **M** (3–5 days) | Firmware; real correctness win; port `ace.py:1540-1610` + `notify_external_load`. |
| 3 | **Plugin system** (discovery/proxy/plugin-api) | **M** (3–5 days) | Backend + iframe tabs in `server.py`/`app.js`. Ironic but real — unlocks the same extensibility. |
| 4 | **Full i18n (en/de/zh)** | **M** (2–4 days) | Tedious (translate all strings), low technical risk. |
| 5 | **Self-update** (`update_repo` + guarded GUI checker) | **S–M** | Needs the debug-flag safety gate to avoid bricking stock firmware. |
| 6 | **Broader swap config** (`swap_probe_temp`, `wiggle_scheme`, per-slot retract, …) | **M** | Port knobs incrementally; each needs hardware calibration. |
| 7 | **Pyodide preflight** | **M** (3–5 days) | Pulls a WASM/CDN dependency (self-host Pyodide to keep your zero-CDN advantage). |
| 8 | **No-lock / per-ACE feed_assist** | **H** (1–2+ weeks + HW test) | The v0.82 work: generalize `_feed_assist_index`→dict **and** give each ACE its own concurrently-serviced I/O (per-idx reactor fd or reader/writer threads). Keepalive is necessary-but-not-sufficient. Highest risk. |
| 9 | **ACE Pro 2 / V2 protocol** | **H** (weeks) | Only if you have ACE Pro 2 hardware; port `ace_protocol_v2.py` + threading. |
| 10 | **Virtual toolheads >4** | **M–H** | Only if you need >4 heads. |

**Total for meaningful parity (items 1–6): ~4–6 weeks, and you're still behind on 8–10 and perpetually chasing new decay71 releases.**

## Recommendation

**Direction A.** Stay on decay71; port your 3 genuine wins (autodry, Mark_Loaded, park knob) as plugins/macros, and send the reconnect-hardening patch upstream. It's bounded, it keeps you on the maintained base, and it preserves everything you actually miss. Direction B only makes sense if you specifically want to *own* the whole stack again and accept the treadmill — the comparison is clear that the fork's isolation was its core cost, not its strength.

## Open follow-ups
- 60-second keepalive tiebreak (resolve the Axis-1 disagreement) before acting on reconnect work.
- If Direction A: start with autodry-as-plugin (largest gap), reuse the FilamentHub plugin scaffold.
