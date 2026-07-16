# multiACE Reliability & Architecture Review (ryvin fork, v0.81b→0.82 line)

Reviewer lens: "best-of-class, least errors" on Davinci-U1 (decay71 0.99.2b base).
Files cited by absolute path; line numbers from the working tree as of 2026-07-16.

Bottom line up front: the firmware side is a **belief-tracking state machine layered on an
unreliable transport, defended by an accumulating stack of point fixes**. Each fix is locally
correct and hard-won (the instincts in CLAUDE.md prove it), but the error surface is growing
faster than it is shrinking because three root causes are unaddressed: (a) the serial transport
is interleaved with Klipper's reactor and G-code execution instead of being an isolated layer,
(b) `head_source` — the mapping that decides *which filament physically prints* — is unverified
belief with known null-drift modes, and (c) the web console reconstructs firmware state by
parsing a debug log, requiring hand-maintained "terminal action" lists to stay coherent.

---

## 1. Top reliability risks, ranked (likelihood × user-visible impact)

### R1 — Serial write failure escalates to full Klipper shutdown (print lost)
**Where:** `/mnt/e/Code/multiACE/multiace/klipper/extras/ace.py` `_handle_serial_failure`
(1229–1281), reached from `_send_request` (1153–1227).
**Failure mode:** any write that fails after the single inline reconnect+retry sets sticky
`_serial_failed`, fires `PAUSE`, then **`printer.invoke_async_shutdown(...)`** (1273). A
shutdown is not a pause — the print is dead, heaters off, user restarts from zero.
**Root cause:** the transport treats "one write failed twice within ~0.35s" as terminal. Given
the ACE Pro's documented idle USB reset (~5s cycle, instinct #7) and pyserial's `is_open` lie
(memory: pyserial-is-open-after-reenum), the precondition for this path is *ambient*, not
exceptional. The keepalive suppresses it for cached inactive serials, but the **active** serial
is protected only by the 1s heartbeat — a reset landing in the wrong window (e.g. mid-toolchange
while the reactor is paused inside `_slow_path_switch`) still reaches this path.
**User experience:** hours-long multicolor print aborts with a Klipper shutdown banner; the
audit event lands with `swap_in_progress=True` baked in (instinct #4), so pre-force-clear web
UIs also wedged. This is the single worst outcome the project can produce and it is wired as
the *default* response to a transport blip.
**Also note:** `_send_request`'s recovery path is unreachable once `_serial_failed` is set —
line 1177 raises before any retry — so recovery is FIRMWARE_RESTART only. Sticky-fatal by design,
but the trigger threshold (2 write attempts, 0.35s apart) is far too twitchy for this hardware.

### R2 — Wrong filament prints: `head_source` is unverified belief with known null-drift
**Where:** `ace.py` `_head_source` lifecycle: `_restore_head_source` (1894), `cmd_ACE_LOAD_HEAD`
(2152, `LOAD_HEAD_SKIPPED reason=filament_present` never writes the mapping), `_on_extruder_change`
(2068, `reason=no_head_source` falls through to the **active ACE's slot N**).
**Failure mode:** documented live (memory: head-source-null-mark-loaded-recovery): a skipped or
timed-out load leaves `head_source[N]=null` for days; when T‑N is finally called mid-print the
firmware silently feeds from the start ACE's slot N — **wrong color/material extruded onto the
part**. Recovery macro `ACE_MARK_HEAD_LOADED` is itself belief-only and reverts on restart.
**Root cause:** there is no reconciliation between the belief map and physical reality (gate
status, `e{h}_filament` sensors, wheel counters) at any checkpoint — not at print start (the
`_on_print_start` check at 547–557 only validates the *ACE index range*, not that the mapping
exists or matches sensors), not at toolchange.
**Impact ranking rationale:** lower per-print likelihood than R1 but the failure is *silent*
and ruins the artifact — the user finds out layers later. For a "least errors" objective, silent
wrong-output beats loud abort in severity.

### R3 — Unbounded `wait_ace_ready()` busy-waits: Klipper main-thread wedge
**Where:** `ace.py:1449–1452`; **19 call sites** (grep: 1285, 1298, 1485, 1500, 1540, 1542,
1565, 1568, 1588, 1619, 1644, 1646, 1818, 2028, 2428, 2524, 2627, 2722).
**Failure mode:** `_info['status']` is set to `'busy'` on every `send_request` (1443) and only
returns to `'ready'` via a get_status *response*. The response pipeline can silently die:
`_callback_map = {}` is wiped on every switch path (832, 876, 913, 935, 957), the heartbeat is
unregistered on several failure paths (766–771, 1248–1250), and `_reader_cb` bails when
`_serial is None` (1380). If status is stuck `'busy'` with no heartbeat running, every one of
those 19 call sites spins in `reactor.pause` **forever** — no timeout, no escape. The G-code
queue is wedged; from the user's chair the printer is frozen mid-toolchange with heaters on.
**Root cause:** a global mutable `_info['status']` doubling as a readiness latch across
connection generations, with no timeout discipline and no generation counter.

### R4 — Switch state machine: three hand-rolled fallback paths with duplicated, divergent resets
**Where:** `_fast_path_switch` (787–853), `_slow_path_switch` (887–977), `_restore_cached_old`
(855–885), plus the inline "cache lost" branches at 909–921 and 952–967.
**Failure mode:** each path independently re-creates `_queue`, `_callback_map`, `_request_id`,
`read_buffer`, fd registration, heartbeat registration — five copies of the same reset sequence
with small differences (e.g. the 909–921 branch resets the queue but never resets
`gate_status`; the fast path resets `gate_status` to UNKNOWN but immediately trusts a
`get_info` fired into a just-drained buffer with a bare `reactor.pause(0.5)` race at 850).
Every new USB behavior discovered on hardware has added another branch (the git/instinct
history: #70 → keepalive; #74 → drain-before-promote; set_fd_wake crash → unregister_fd).
**Root cause:** connection lifecycle is not a type — it's ~15 loose attributes (`_serial`,
`_serials`, `_connected`, `_connected_per_ace`, `_serial_failed`, `serial_id`,
`_active_device_index`, `ace_dev_fd`, `heartbeat_timer`, `connect_timer`, `_keepalive_timer`,
`_callback_map`, `_queue`, `read_buffer`, `_request_id`) that every path must mutate in the
right order. The probability that all five reset sequences stay consistent through v0.82's
planned "lift start-ACE pinning" rework is low.
**User experience:** the long tail of "switch failed, now everything is weird until reboot"
reports — exactly the class the Troubleshooting "Reset to clean state" section exists for.

### R5 — Protocol integrity: CRC never verified, request-id space reused across generations
**Where:** `_process_data` (1394–1440): `crc_data = packet[4+payload_len:...]` is extracted at
1426 and **never checked** against `_calc_crc(payload)`. `_request_id` resets to 0 on every
connect/switch (833, 877, 914, 936, 958, 1007) while the keepalive writes static `id=0` frames
to inactive serials (1095–1099) and buffers are only drained best-effort (`reset_input_buffer`
inside try/except-pass, 822–825).
**Failure mode:** (a) a corrupted-but-valid-JSON payload is accepted and can flip `gate_status`,
trigger `auto_feed` preloads (heartbeat callback 1308–1360 acts on slot status changes), or
poison `_info`; (b) a stale response from the previous connection generation with a small id
matches a fresh `_callback_map` entry and runs the wrong callback with the wrong payload.
Low-probability per message, but this bus sees ~1 msg/s/device around a firmware that resets
itself — over a 10-hour print that's tens of thousands of frames per ACE.
**Root cause:** the framing layer was inherited from SnapACE/DuckACE and never hardened when
multi-device + keepalive traffic multiplied the message volume and added connection generations.

### R6 — Web console state is reconstructed from a debug log with hand-maintained coherence lists
**Where:** `/mnt/e/Code/multiACE/multiace_web/src/multiace_web/state.py`
`_SWITCH_TERMINAL_ACTIONS` (24–29) + `apply_event` force-clear (114–115); tailer
(`tailer.py`, starts at EOF, 87) + 5s `ACE_HEAD_STATUS` poll (`poller.py:67–82`) as dual writers.
**Failure mode:** every firmware audit action that can be emitted while `swap_in_progress=True`
must be manually added to the terminal set or the UI banner sticks forever — this has already
happened twice (SWITCH family, then `SERIAL_WRITE_FAILED`, instinct #4). The smart-swap leg-2
race (`_waitForSwapLeg1Propagation`, instinct #5) is another symptom of the same thing: the
web layer can't know when firmware state has settled because the log has no sequence/settled
semantics. The next firmware release that adds a new mid-swap audit action reintroduces the
stuck-banner bug by default.
**Root cause:** `multiace_state.log` is a diagnostics artifact promoted into the primary state
transport. It has no schema contract, no monotonic sequence, no "swap ended" event (the
firmware's `finally` at ace.py:1778–1779 clears the flag *silently*).
**Impact:** UI-level, not print-level — but "banner stuck / buttons dead / 409 head busy" is
precisely the ease-of-use erosion the project objective names.

### R7 — Startup blocks Klipper for up to 20s with `time.sleep`, then soft-fails into a half-alive mode
**Where:** `ace.py` `_handle_ready` 428–453: `time.sleep(1.0)` in a loop (436) — this is a hard
block of the entire Klipper reactor at ready-time, not a cooperative `reactor.pause`. On
timeout it sets `_ace_startup_failed` and returns: Klipper runs, but every ACE macro is dead
until a FIRMWARE_RESTART, and (per README line 4) a printer installed without an ACE needs a
manual `ACE_MODE_NORMAL` to even print.
**Failure mode:** an ACE mid-reset-cycle at boot (a coin flip, given the ~5s cycle vs. the scan
moment) delays every boot by up to 20s; two flaky enumerations produce the "found 1 of 2, ACE
inactive" half-state where the UI shows a live console but every action fails.
**Root cause:** startup device-set determination is a one-shot gate rather than a converging
background process; the canonical-lock design (good idea) is bolted to a blocking wait.

### R8 — 1-based/0-based `active_device` convention split
**Where:** `ace.py get_status` emits `active_device: self._active_device_index + 1` (3124);
`poller.py` consumes with `max(0, int(...get("active_device", 1)) - 1)` (188, 214); everything
else in the codebase is 0-based. The repo's own screenshot trail
(`multiace-dashboard-0based.png`) records a shipped off-by-one bug from exactly this.
**Failure mode:** every future consumer (HelixScreen SP2/SP3, plugins, decay71 iframe tabs)
must rediscover the +1. The `max(0, ...)` clamp also silently maps a missing/0 value to ACE 0 —
a wrong-target `ACE_SWITCH` from the autodry round-robin path is a *physical* action.
**Root cause:** display convention leaked into the machine-readable status contract and is now
frozen ("legacy keys MUST be preserved", 3117–3118).

### R9 — Cumulative cross-slot coupling drift (mechanical, but architecture ignores it)
**Where:** swap-park design (`ACEC__Park_T<n>`, `default_park_retract_length_mm=700`), README
20–24, instinct #2/#3.
**Failure mode:** every park retract drifts neighboring slots; after N swap cycles an untouched
slot loses drive-wheel grip and the *next* load of that slot fails or grinds — the user sees a
failure on a slot they never touched, long after the cause. No drift accounting exists anywhere
in the code: no per-slot swap counter, no warning threshold, no scheduled reseat prompt.
**Root cause:** per-operation correctness thinking applied to a cumulative physical process.
This is the top *mechanical* contributor to "mysterious" load failures and it is currently
invisible to both firmware and web console.

### R10 — Fork-of-a-fork surface: 4,100+ lines of copied stock firmware tracked by hand
**Where:** `filament_feed_ace.py` (2,171 lines) and `extruder_ace.py` (1,958 lines) are modified
copies of stock Snapmaker modules, activated by file-copy mode switching
(`ace_mode_switch.sh` + `__pycache__` purge + reboot).
**Failure mode:** every PAXX/decay71 upgrade can silently invalidate an assumption in the
copies — this has *already* happened (memory: paxx-orjson-strict-str-keys — PAXX 12-19/20's
orjson made int-keyed `get_status` dicts a **shutdown loop**, and the ryvin fork missed it
until it hit hardware). The mode switch itself has partial-failure modes (copy succeeds for 2
of 3 files, stale pycache) with no verification step.
**Root cause:** integration by duplication rather than by hook/subclass, against an upstream
(PAXX) that ships breaking changes without notice.

---

## 2. Band-aid vs. genuinely necessary

| Mechanism | Verdict | Reasoning |
|---|---|---|
| 1 Hz keepalive traffic (`_keepalive_tick`, `ace_keepalive.attempt_keepalive`) | **Necessary** (device firmware defect, unfixable host-side) — but **wrongly placed**. The requirement is real; embedding it as reactor-timer fd-juggling inside the same object that runs G-code is the fragile part. | instinct #7; issue #70/#74 |
| Close-and-reopen on write failure (never trust `is_open`) | **Necessary.** pyserial semantics + kernel re-enumeration. Correctly extracted into `ace_keepalive.py` for inactive serials — but the *active* serial path (`_send_request` 1181–1227) still does its own inline ad-hoc version. | memory: pyserial-is-open-after-reenum |
| Start-ACE pinning for the whole print | **Band-aid, and a costly one.** It spawned two secondary defect classes: entangle-detect false positives (needed `_apply_entangle_skip_for_print`, ace.py:636) and no-feed-assist underextrusion risk on long bowdens. Now that keepalive + fast-path switching exist (the fast path was literally built to make cross-ACE swaps cheap, 1846–1857), the original justification is substantially gone; v0.82's plan to lift it is correct and overdue. |
| `state.py` force-clears on terminal audits + `SERIAL_WRITE_FAILED` | **Band-aid** over a missing lifecycle contract. Correct fix is a firmware-emitted swap-end event (or seq-stamped snapshots); the list will need a third entry eventually. |
| `cmd_ACE_SWITCH` catch-all try/except (1749–1777) | **Half-necessary, half-dangerous.** Preventing Klipper's shutdown-on-exception is right for this command; swallowing *all* exception types means genuine logic bugs (KeyError in the switch paths) degrade into "switch failed, try again" loops instead of surfacing. Should whitelist transport exceptions and re-raise the rest with the same audit. |
| Entangle-skip on non-start-ACE heads | **Necessary given pinning; deletable once pinning is lifted.** Track it for removal in v0.82 — leftover skips after the pinning redesign would mask *real* tangles. |
| `tick_one_ace` input override in autodryer (instinct #8) | **Band-aid** over the tailer-lag race — a consequence of R6's log-as-transport design. Falls out for free if the web reads settled state from `get_status` instead of the log. |
| Per-iteration try/except in `_keepalive_tick`/`_open_inactive_serials` | **Necessary** (per-device fault isolation is right for a multi-device loop). |
| `_wheel_delta` / Tier-2 sensor fallback for FEED_AUTO phase3 | **Necessary** — the ACE encoder genuinely lies; trusting the head sensor is the better oracle. But it papers over R2: the fallback records a load that the encoder disputes, and nothing later re-verifies. |

Pattern to name honestly: **band-aids are begetting band-aids** (pinning → entangle-skip;
log-transport → force-clear list → leg-2 wait → tick override). Each individual fix was the
right call under fire; the *stack* is the design smell.

---

## 3. Three highest-leverage architectural changes

### A. Extract an ACE transport layer with owned connection lifecycles (kills R1, R3, R4, R5)
**Sketch:** one `AceConnection` object per physical device owning *all* of: the pyserial handle,
open/close/reopen-by-path, keepalive scheduling, framing, **CRC verification on receive**,
monotonic per-connection-generation request ids, and a request/response future map with
per-request timeouts. `BunnyAce` talks to it through two operations only:
`conn.request(method, params, timeout) -> response | TimeoutError` and
`conn.state -> {CONNECTED, RECONNECTING, DEAD}`. The reactor sees exactly one fd registration
point and one timer, both inside the connection class. "Switch active ACE" becomes a pointer
move — every serial is already open and kept alive (which is what `_serials` + keepalive
already do de facto; this change makes the implicit design the explicit one and deletes the
five duplicated reset sequences). `wait_ace_ready` is replaced by awaiting the specific
response with a timeout; a timeout returns a G-code error instead of wedging the main thread.
**Failure policy change bundled in:** demote `invoke_async_shutdown` in `_handle_serial_failure`
to PAUSE + sticky error banner + background reconnect. A paused print with heaters managed is
recoverable; a shutdown is not. Shutdown should be reserved for cases where motion safety is
actually in doubt (it isn't here — the ACE is upstream of the extruder).
**Risk/effort:** the big one — ~2–4 weeks plus multi-day hardware soak; medium risk. Mitigate:
wire protocol and audit events unchanged, so the web console and tests are unaffected; ship
behind the existing mode switch with an A/B soak print. This is the correct foundation for
v0.82's "lift pinning" — doing pinning-removal *on top of the current five-path lifecycle*
would multiply the branch count instead.

### B. Make `head_source` verified state with checkpoint reconciliation (kills R2, halves R9's blast radius)
**Sketch:** (1) every mutation of `_head_source` (including `ACE_MARK_HEAD_LOADED`) writes
through `_save_head_source()` immediately — no in-memory-only states; (2) add
`ACE_VERIFY_HEADS`: for each head, cross-check belief vs. observables — `e{h}_filament`
sensor, source slot's `gate_status`, and (where the head is on the active ACE) a short
feed-assist wheel-tick probe; (3) run it automatically at `print_stats:start_printing` and
refuse-with-prompt (not silently coast) when a to-be-used head has `head_source=null` or a
contradiction — the exact scenario that today prints the wrong filament. (4) Track a per-slot
`park_cycles_since_reseat` counter and surface a "slot N drift: reseat recommended" warning at
a calibrated threshold — turning R9 from a mystery into a maintenance prompt.
**Risk/effort:** low risk, ~1 week. The audit plumbing (`_audit_state` warnings already do
post-hoc versions of these checks at 3074–3107) shows the checks are cheap; this moves them
from "warn after the fact in a log nobody reads mid-print" to "gate before damage."

### C. Replace log-tailing with a versioned state snapshot contract (kills R6, R8, instinct #5/#8 hacks)
**Sketch:** firmware adds `state_seq` (monotonic int, bumped on every state mutation) and
`swap_in_progress` *as cleared by the finally block* to `get_status` (the `units[]` builder at
3149–3168 is already 80% of a proper contract). The web console derives `CurrentState` from a
Moonraker `objects/subscribe` websocket on `ace` (push, not 5s poll), keyed by `state_seq` —
last-writer-wins with no interleave ambiguity. `multiace_state.log` returns to being what it
is: a diagnostics/activity feed for the Activity tab only. Delete `_SWITCH_TERMINAL_ACTIONS`,
`_waitForSwapLeg1Propagation` (replace with "wait for seq > n"), and the autodryer input
override. While in there, add `active_index` (0-based) alongside the frozen 1-based
`active_device` and migrate consumers.
**Risk/effort:** medium-low, ~1–2 weeks including the 121-test suite update. Biggest payoff
per unit effort of the three for *ease-of-use* errors; do it before HelixScreen SP3 consumes
the contract, not after.

---

## 4. Accumulating tech debt that will bite within a few releases

1. **The v0.82 pinning removal is scheduled on top of the current switch lifecycle** (R4).
   Doing it without change A first means adding mid-print switching to five inconsistent reset
   paths — this is the highest-probability source of the *next* generation of field bugs.
2. **Zero automated tests on the firmware side** — 3,174-line `ace.py` + 2,171-line feed module
   with hand-on-hardware validation only, while the web side has 121 tests. The pure-logic
   parts (framing/CRC, id allocation, canonical mapping, head_source serialization, switch
   decision table) are all extractable and testable without a printer; `ace_keepalive.py` and
   `manual_heads.py` prove the pattern already exists in-repo. Every risk in section 1 would
   have been catchable by a unit test around a fake serial.
3. **Dead-but-armed code:** `_hotplug_monitor` (ace.py:481–518) is defined, never registered —
   and it contains a real bug waiting for whoever wires it up: it derives the returning
   device's index from `sorted(current)` (501–503) instead of the canonical mapping and the
   `_ace_path_sort_key` ordering, i.e. exactly the index-drift the canonical lock exists to
   prevent, and it fires an automatic `ACE_SWITCH` on that index. Delete it or fix it now.
4. **Autodry dual persistence shapes** (instinct #9): v1 reader returns misleading `mode=off`
   on v2 files, plus the repo carries autodry twice (web-console FSM *and*
   `multiace_plugins/autodry` sidecar on port 8090). Two implementations of
   humidity-triggered drying with different persistence formats is a support-ticket factory —
   pick the sidecar, delete or freeze the in-console one, migrate the state file once.
5. **Fragile serializations:** `_save_head_source` converts JSON→Python-literal via string
   `.replace(': null', ': None')` (1916–1921) — a color value or vendor string containing
   `": true"` outside the guarded pattern breaks it; the orjson incident shows this whole
   `SAVE_VARIABLE`-round-trip boundary is where firmware upgrades strike. Centralize a
   `to_klipper_literal()` helper with tests.
6. **Docs/config provenance drift:** CLAUDE.md's on-printer path is already wrong per project
   memory (deploy memory note); README has a duplicated troubleshooting section (445–446) and a
   version string mismatch ("multiACE v0.80b" in the cache-check tip vs. 0.81b/0.82 features
   documented). Small, but on a project whose operators recover from failures *by following
   these docs*, doc drift is a reliability defect.
7. **`max(0, active_device-1)` clamps in the poller** (R8) convert protocol surprises into
   silent wrong-device physical actions (round-robin `ACE_SWITCH`). Prefer fail-closed: skip
   the tick on an unparseable value.
8. **decay71/ryvin dual-track:** the printer runs decay71 0.99.2b while this repo's firmware
   line is 0.81b/0.82 — two diverging forks of the same extension, with fixes (orjson, keepalive
   variants) landing at different times in each. Without a declared merge direction, every
   hardware-discovered fix must be ported twice or gets lost; the orjson miss on the ryvin fork
   was exactly this failure.
