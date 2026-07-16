# multiACE — Critical Review & Design Roadmap

**Date:** 2026-07-16
**Objective under review:** *best-of-class, ease of use, with the least amount of errors* using the Anycubic ACE Pro with the Snapmaker U1.
**Method:** three independent critical reviews by Fable agents (reliability/architecture, ease-of-use, planning/roadmap), plus live verification against the Davinci-U1 (decay71 0.99.2b). Full agent reports are archived in the session scratchpad (`fable-review-{reliability,ux,roadmap}.md`).

> This is an assessment + plan document, not a change. Nothing here has been implemented. It exists to sequence the work toward the objective.

---

## 0. Headline: the verified root cause of "wrong filaments"

The reported symptom — *"when multiACE pulls from FilamentHub it isn't displaying the right filaments"* — has a **confirmed, reproduced root cause** that is worse than the previously-recorded "the grid hides labels on empty slots":

**The decay71 backend actively deletes the label.** The state builder runs an eject-debounce (`.decay71-ref/multiace/web/backend/main.py:301-307`, `EJECT_DEBOUNCE_S = 0.5`, `_drop_override_if_present` at :1948): any slot reading `gate == 0` (physically empty) has its slot-override **deleted from `slot_overrides.json`** 0.5 s after it's seen empty. The FilamentHub plugin's own post-pull `render()` (`filamenthub/static/app.js:150,161`) issues the very `/api/plugin-api/state` poll that triggers the delete. So Pull writes the label, reports "applied 2, 0 errors", and its own refresh destroys it — with no error, event, or trace.

Verified live (2026-07-16): a `prune=false` pull applied `0_2 = SnapSpeed Red`; minutes later `GET /api/slot-override` shows `0_2` **absent** again. The eject-debounce is confirmed by both code and behavior.

**Two compounding defects sit on top:**
1. Pull never consults physical occupancy — `plan_reconcile()` plans from FilamentHub winners vs. current override keys only. FilamentHub's authoritative slots (ACE0 **2,3**) don't match the physically occupied slots (ACE0 **0,1**); slot 2 is empty. Nothing arbitrates tracker-vs-sensor.
2. The schema-2 `aces_covered` field (empty in the live payload) is never read anywhere in the plugin, so the vacated-ACE prune gap (`ryvin/FilamentHub#18`) stays open.

**Consequence for the memory record:** the 2026-07-09 decision *"grid hides labels on empty slots — keep as-is, do not re-open without a new reason"* is **reversed**. The new reason is this defect: the label isn't hidden, it's deleted, and the behavior makes a real mismatch read as "the product is broken."

---

## 1. The one theme that explains most errors

Across all three reviews, a single system-level cause recurs:

> **multiACE tracks *belief* about filament and hardware state across four independent sources of truth — FilamentHub location records, multiACE slot-override labels, firmware `head_source` bookkeeping, and physical sensors/RFID — but no component owns reconciling them. Every seam got a per-feature patch; the lattice as a whole is unowned.**

The same shape appears in each pillar:

- **Reliability:** `head_source` is unverified belief with documented null-drift → the firmware silently feeds the **wrong color mid-print** (memory: `head-source-null-mark-loaded-recovery`). No reconciliation against sensors at print-start or toolchange.
- **Ease of use:** the four truth-sources are rendered differently in every tab (and the FilamentHub tab is 1-based while the dashboard is 0-based), so "which filament is in which slot" has no single trustworthy answer on screen.
- **Planning:** there are 16 solid *feature* specs and **zero** program-level plans — no reconciliation architecture, no error budget, no onboarding/calibration plan. Effort flows to new seams (FilamentHub phases, HelixScreen) faster than old seams close.

"Least errors" for a filament changer *is* "never print or display the wrong material." That is precisely the unowned seam.

---

## 2. Ranked issues (distilled from the three reviews)

### Reliability (the "least errors" pillar)
| # | Issue | Where | Why it matters |
|---|---|---|---|
| R1 | A serial write failing twice ~0.35 s apart escalates to **full Klipper shutdown** (`invoke_async_shutdown`), print lost — while the ACE Pro's ~5 s idle USB reset makes the trigger *ambient* | `ace.py` `_handle_serial_failure` (~1229-1281) | Worst possible outcome, wired as the default response to a transport blip. Demote to PAUSE + background reconnect. |
| R2 | `head_source` is unverified belief; a skipped/timed-out load leaves it null and the firmware feeds the wrong slot silently | `ace.py` load/`_on_extruder_change` paths | Silent wrong-output ruins the part layers later — worse than a loud abort. |
| R3 | `wait_ace_ready()` has **no timeout across 19 call sites**; a wiped callback map + dead heartbeat wedges the Klipper main thread forever | `ace.py:1449-1452` + 19 sites | "Frozen mid-toolchange, heaters on" with no escape but reboot. |
| R4 | Connection lifecycle is ~15 loose attributes mutated by **five duplicated, divergent reset sequences** | switch paths in `ace.py` | v0.82's planned pinning-removal on top of this multiplies branch count → next generation of field bugs. |
| R5 | Framing CRC is extracted but **never verified**; request-ids reset to 0 across connection generations | `ace.py` `_process_data` (~1394-1440) | Corrupt/misrouted frames can flip gate state or run the wrong callback. |
| R6 | Web state is reconstructed from a **debug log** with a hand-maintained "terminal actions" list; every new mid-swap audit event re-introduces the stuck-banner bug | `state.py`, `tailer.py`, `poller.py` | The log has no schema/sequence/"swap ended" contract. Source of the leg-2 race and force-clear hacks. |

Plus: zero firmware automated tests on a 3,174-line `ace.py`; dead-but-armed `_hotplug_monitor` containing an index-drift bug; autodry duplicated in two places with two persistence shapes; JSON→Python-literal serialization via string `.replace`.

### Ease of use
| # | Issue | Why it undermines the objective |
|---|---|---|
| U1 | **Exhibit A** — the filament-identity pipeline deletes/hides labels and never arbitrates tracker-vs-sensor (see §0) | The single most important question — "what's where, can I trust it?" — is answered wrongly. |
| U2 | Four sources of truth, different per tab; **1-based vs 0-based indexing between tabs on the same screen** | Generates "wrong filament" reports with zero code defects. |
| U3 | Multi-ACE blindness: inactive ACEs render every slot as "?" with no timestamp/provenance | The flagship feature looks broken most of the time. |
| U4 | Failure recovery is G-code incantations in tooltips (`Mark_Loaded`, `ACE_CLEAR_HEADS`, entangle-skip), several non-persistent across restart | Recovery knowledge lives in the maintainer's memory files, not the product. |
| U5 | Drying has **four competing UIs**, and auto-dry can't arm without a DIY Govee BLE bridge and faults on the default `target_pct=15` | New user cannot succeed out of the box. |
| U6 | First-run landmines the installer knows about but doesn't defuse (`ace_device_count`, unload-before-use, 700 mm park-retract) | Documented foot-guns delegated to README diligence. |

### Planning
- Feature-plan-rich, **program-plan-poor**: no reconciliation architecture, error budget, or onboarding/calibration plan.
- **Strategic ambiguity**: the Kindred-Allies "graft decay71 onto the fork" plan coexists undecided with the 07-07 comparison's opposite recommendation ("keep decay71 as base") — and the printer *already runs decay71 0.99.2b*. Three version truths (VERSION `0.80b`, README v0.82 promises, live decay71 0.99.2b), no ADR.
- Calibration constants (the actual error source: 700 mm, `target_pct` floor) have **no documented home**.

---

## 3. The three highest-leverage changes

1. **Extract an ACE transport layer with owned connection lifecycles** (kills R1, R3, R4, R5). One `AceConnection` per device owning the handle, open/close/reopen-by-path, keepalive, framing, **CRC verify**, per-generation request-ids, and a request/response map with **per-request timeouts**. `wait_ace_ready` becomes "await this response or time out → G-code error" instead of wedging the main thread. Bundle the R1 policy change: demote shutdown to PAUSE + background reconnect. This is the correct foundation for v0.82's pinning-removal — doing pinning-removal on top of the current five-path lifecycle is the highest-probability source of the next bug generation. *~2-4 wks + hardware soak; wire protocol/audit unchanged so web + tests are unaffected.*

2. **Make `head_source` verified state with checkpoint reconciliation** (kills R2, halves R9 drift blast radius). Every mutation persists immediately; add `ACE_VERIFY_HEADS` (belief vs. `e{h}_filament` sensor + source-slot gate + wheel-tick probe); run it at `print_stats:start_printing` and **refuse-with-prompt** when a to-be-used head is null/contradictory. Add a per-slot park-cycle counter → "reseat recommended" before the 3 AM failure. *~1 wk, low risk — the checks already exist as post-hoc log warnings; move them to gate-before-damage.*

3. **Replace log-tailing with a versioned state-snapshot contract** (kills R6, the 1-based/0-based split, and the leg-2/force-clear hacks). Firmware adds `state_seq` (monotonic) + a real "swap ended" signal to `get_status`; web derives state from a Moonraker `objects/subscribe` websocket keyed by `state_seq`. Delete `_SWITCH_TERMINAL_ACTIONS`, `_waitForSwapLeg1Propagation`, the autodryer input override; add `active_index` (0-based) alongside the frozen 1-based `active_device`. *~1-2 wks incl. test suite; highest ease-of-use payoff per effort — do it before HelixScreen SP3 consumes the contract.*

---

## 4. Design plans the objective demands but nobody has written

- **M1 — Desired-vs-Actual Filament Reconciliation (HIGHEST).** Define the authority lattice across the four truth sources, a per-slot composite reconciliation state, the triggers (pull, load/unload audits, print-start), and the **presentation contract**: a mismatch is never rendered as silent "empty" — it renders as an explicit conflict badge with the suppressed label and a one-tap resolution. Subsumes `#18` (`aces_covered`) and the "inactive-ACE occupancy unknowable" hardware-twin gap. *This is the plan for today's defect.*
- **M2 — Fork Disposition ADR.** Formally adopt (or refute) "decay71 as base"; mark Kindred-Allies Waves 4-5 superseded; run the never-executed 60 s keepalive tiebreak; reconcile the three version stamps; define how decay71 releases are tracked.
- **M3 — Error Taxonomy & Observability.** Versioned audit-event vocabulary; every FAULTED/409/dispute → plain-language GUI message + recovery action (out of memory files); fix the sidecar `log.exception` black hole (instinct #10); an error-frequency ledger so "least errors" becomes *measurable*.
- **M4 — First-Run Onboarding & Calibration.** Guided unload-before-use, `ace_device_count`, park-retract measurement, `target_pct` run-to-floor probe, and a provenance table for every geometry/environment constant.
- **M5 — Release/Deploy Integrity.** Single version stamp; execute (not cite) the verification list; reconcile deploy docs with reality; scripted backup/rollback drill.
- **M6 — Hardware Regression.** Actually run the 8-color demo to an archived artifact; per-release multi-ACE cross-toolchange smoke.

---

## 5. Prioritized roadmap

### NOW (~2 weeks) — close the truth gaps
1. **M1 phase 1**: Pull consults physical occupancy; stop the eject-debounce from deleting tracker labels (render `EXPECTED_NOT_LOADED` ghost cards instead); conflict badge replaces silent-empty; port decay71's print-start `head_source` integrity audit. **(directly fixes today's defect)**
2. **M2 ADR**: Direction-A decision + keepalive tiebreak + version/README/instinct cleanup.
3. **Deploy the autodry plugin** (built, tested, stranded) with a calibrated `target_pct` (run-to-floor first).
4. **Reconnect hardening** (by-path rescan before retry; don't pause the print for a non-active ACE) — as an upstream decay71 PR.

**Riskiest NOW assumption:** that inactive-ACE physical occupancy is actually observable under decay71. **Run a 30-minute live probe before writing M1's spec** — if it's stale, M1 must carry explicit staleness semantics.

### NEXT (~3-6 weeks) — make "least errors" measurable
5. M3 error taxonomy/observability. 6. M4 onboarding/calibration guide. 7. Direction-A `Mark_Loaded` macros + park-retract knob into the decay71 base. 8. M5 release/deploy integrity. 9. M6 run the 8-color demo to an artifact.

### LATER (quarter+) — only after NOW/NEXT hold
10. HelixScreen SP3 (only after the Task-0 scaffolding fix and only if the touchscreen is a real usage surface; it should *consume* M1's lattice). 11. Onboarding wizard in-GUI. 12. i18n / background auto-pull / remote access.

### Cut / defer
- **CUT** Kindred-Allies Waves 4-5 (decay71 base already provides V2 protocol + hardware breadth).
- **DEFER** HelixScreen SP3 C++ (stalled 6+ wks, duplicates web console + M1).
- **DEFER** Cloudflare Access (zero error-reduction, adds attack surface to a safety-critical system).
- **STOP** further swaptimizer/postprocessor optimization (not the current error bottleneck).

---

## 6. Immediate, concrete actions for the live defect (Exhibit A)

Independent of the larger roadmap, these are shippable now:
1. **Stop the eject-debounce deleting tracker-sourced overrides**; render empty-but-labeled slots as `EXPECTED_NOT_LOADED` ghost cards. (~15 backend lines + one CSS class.)
2. **Make Pull honest**: after write, re-read and report "N labels awaiting physical load / M occupied slots unidentified" instead of raw applied/stale counts.
3. **Unify indexing**: FilamentHub tab → 0-based A/B to match the dashboard.
4. **Show the spool name** (currently in `subtype`, never displayed); replace the "loaded" placeholder with "Filament present — unknown · tap to identify."
5. **Consume `aces_covered`** for prune scope; warn (don't fail) when it's empty on a schema-2 payload.

---

*Agent reports (full detail, with file:line citations) co-located in this directory:*
[`2026-07-16-fable-reliability.md`](./2026-07-16-fable-reliability.md), [`2026-07-16-fable-ux.md`](./2026-07-16-fable-ux.md), [`2026-07-16-fable-roadmap.md`](./2026-07-16-fable-roadmap.md).
