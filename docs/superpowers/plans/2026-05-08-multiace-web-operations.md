# multiACE Web — per-head Operations (Load/Unload + smart-swap) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-head Unload to the slot-row chevron menu and make the existing `→ T<n>` Load smart — detecting head state and chaining unload+load when displacing a loaded head, with a 3-second cancellable toast for displacement swaps.

**Architecture:** Single-file frontend change in `app.js` plus a small CSS addition. No backend changes. All multi-step operations are sequenced via existing `sendScript`/`sendCommand` helpers from the frontend. Firmware capability (`ACE_PARK_HEAD`) is probed at init via `/printer/objects/list` and cached on `state.swapParkAvailable`. Phase 1 ships Unload + same-ACE smart-swap + a feature-flagged cross-ACE-with-park path; the park path activates automatically when firmware deploys.

**Tech Stack:** Vanilla JS (no build step) / CSS custom properties / Playwright (manual e2e + a new structural test script)

**Spec:** `docs/superpowers/specs/2026-05-08-multiace-web-operations-design.md`

---

## Codebase grounding notes

Read these before starting any task. They prevent naming bugs.

- **`head_source[N].ace`** — the field is `.ace`, NOT `.ace_index`. The spec uses `ace_index` in pseudocode but the live data structure (conftest.py, state.py, app.js everywhere) uses `.ace`. All code in this plan uses `.ace`.
- **`tName(i)`** — returns `T${i+1}` (1-based display). `T0` → `"T1"`. Live in `app.js:1311`.
- **`slotName(i)`** — returns `Slot ${i+1}`. Live in `app.js:1312`.
- **ACE letter** — `String.fromCharCode(65 + ace)` (0→A, 1→B). Used in toast text.
- **Slot label for toast** — `String(slot + 1)` (1-based, no `slotName` call — slotName includes "Slot" word which is too long for a toast).
- **Existing toast system** — `toast(msg, kind)` at `app.js:35`. Appends a `<div class="toast">` to `#toast-container`, auto-removes after 4 000 ms. Does **not** support interactive buttons. The smart-swap confirmation is richer (countdown + Cancel button) so this plan introduces a second helper `showSwapConfirm(...)` that renders an interactive toast-like element — see Task 2. The two coexist; `showSwapConfirm` appends to `#toast-container` as well.
- **`openHeadTargetMenu(anchor, ace, slot)`** — the chevron click handler at `app.js:1409`. This is the function the plan extends.
- **`renderSlotCard(ace, slotIdx)`** — builds each slot card including `loadedToEntry`, `loadedToHead`, the split-button, and the existing `unloadBtn` at `app.js:1447–1538`. This is where the Unload menu item is rendered.
- **`setView(name)`** at `app.js:2133` — tab router. Needs a hook for "navigate away" abort.
- **`printState.state`** — string field from `printState` global. Unsafe values: `"printing"`, `"paused"`. Safe values: `"standby"`, `"complete"`, `"cancelled"`, `"error"`.
- **`state.swap_in_progress`** — boolean on the `state` global.
- **Parked-state CSS** — does NOT exist yet. Task 1 adds a minimal backfill (`.card.parked` dashed-border style) before any functional work.

---

## File structure

| File | Status | Purpose |
|---|---|---|
| `multiace_web/src/multiace_web/static/app.js` | modify | Core logic: capability detection, head-state helper, Unload menu item, smart-swap, swap confirm toast, cancel/navigate-away logic, gating helpers, `setView` hook |
| `multiace_web/src/multiace_web/static/style.css` | modify | `.card.parked` visual (dashed border + badge); `.swap-confirm-toast` interactive toast variant |
| `multiace_web/tests/test_state.py` | modify | Assert `head_source[h].parked` round-trips via serialized payload |
| `multiace_web/tools/e2e_operations.py` | **create** | Playwright structural test: mock-state assertions (chevron menu items, toast text, gating) — no live hardware actions |

---

## Task 1: Parked-state CSS backfill + `head_source[h].parked` state test

This task adds the `.card.parked` visual declared in the swap-park spec (§2) and verifies the `parked` field round-trips through state serialization. No logic changes yet.

**Files:**
- Modify: `multiace_web/src/multiace_web/static/style.css`
- Modify: `multiace_web/tests/test_state.py`

- [ ] **Step 1: Write the failing state round-trip test**

```python
# multiace_web/tests/test_state.py — add after the last existing test

def test_head_source_parked_field_round_trips():
    """parked:True on head_source must survive apply_event → serialise."""
    from multiace_web.state import CurrentState
    s = CurrentState()
    s.apply_event({
        "action": "PARK_HEAD",
        "params": {"head": 0, "ace": 0, "slot": 0},
        "active_device": 0,
        "connected": True,
        "swap_in_progress": False,
        "gate_status": [1, 1, 1, 1],
        "head_source": {
            "0": {"ace": 0, "slot": 0, "type": "PLA", "color": "ff0000", "parked": True},
            "1": None, "2": None, "3": None,
        },
        "sensors": {"0": False, "1": False, "2": False, "3": False},
    })
    assert s.head_source[0] is not None
    assert s.head_source[0].get("parked") is True
    # Serialised snapshot (as sent over WS) must preserve the flag.
    payload = s.as_dict()
    assert payload["head_source"]["0"]["parked"] is True
```

- [ ] **Step 2: Run the test and confirm it fails (if `parked` field is stripped)**

```bash
cd multiace_web && pytest tests/test_state.py::test_head_source_parked_field_round_trips -v
```

Expected: PASS (state.py does dict-passthrough so `parked` likely already flows through). If it FAILS, it means `CurrentState.apply_event` is whitelisting fields — inspect `state.py` and add `"parked"` to the allowed keys. In either outcome proceed.

- [ ] **Step 3: Add the `.card.parked` CSS to `style.css` after the `.head-target-menu-item[disabled]` rule (around line 1299)**

```css
/* ---- Parked-state slot card (filament is in ACE-side bowden, not at head) ---- */
.card.parked {
  border: 1px dashed var(--border);
  background: color-mix(in srgb, var(--surface) 85%, transparent);
}
.card.parked .color-band {
  opacity: 0.4;
}
.pill.parked-badge {
  background: color-mix(in srgb, var(--warn, #f59e0b) 20%, transparent);
  color: var(--warn, #f59e0b);
  border: 1px solid var(--warn, #f59e0b);
}
```

- [ ] **Step 4: Run the full pytest suite to confirm no regressions**

```bash
cd multiace_web && pytest -v
```

Expected: all existing tests pass.

- [ ] **Step 5: Commit**

```bash
git add multiace_web/src/multiace_web/static/style.css multiace_web/tests/test_state.py
git -c user.name="Raul" -c user.email="raul@leadingbit.com" commit -m "feat(web): ops — parked-state CSS backfill + head_source.parked round-trip test"
```

---

## Task 2: State-extension helpers and swap-confirm toast subsystem

Add `state.swapParkAvailable`, `state.smartSwapPending`, the capability-detection probe, and the interactive `showSwapConfirm` toast helper. No chevron menu changes yet — just the new shared infrastructure.

**Files:**
- Modify: `multiace_web/src/multiace_web/static/app.js`

- [ ] **Step 1: Extend the `state` object at the top of `app.js` (around line 4)**

Add two new fields immediately after `last_error: null`:

```js
  last_error: null,
  // --- Operations (smart-swap) ---
  swapParkAvailable: false,   // cached: ACE_PARK_HEAD firmware verb detected
  smartSwapPending: null,     // {head, leg, startedAt} | null — cross-leg UI lock
```

- [ ] **Step 2: Add `detectSwapParkAvailable()` and call it at DOMContentLoaded**

Add this function after the `sendCommand` function (around line 218), before `confirmDialog`:

```js
/**
 * Probe Moonraker for the ACEC__Park_T0 macro, which is the convenience macro
 * added by the swap-park firmware.  Preferred over probing ACE_PARK_HEAD via
 * /printer/gcode/help because some Klipper builds only enumerate macros there,
 * not core commands.
 *
 * Returns true if the macro object exists, false otherwise (including on any
 * network error — fail closed so Phase 1 fallback always runs).
 */
async function detectSwapParkAvailable() {
  try {
    const r = await fetch(api("api/command"), {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeader() },
      body: JSON.stringify({ script: "RESPOND MSG=multiace_probe" }),
    });
    // We don't actually care about the script response — we're probing via
    // the object list instead.
    void r;
  } catch (_) {}
  // Real probe: check if the gcode_macro ACEC__Park_T0 object exists.
  try {
    const r = await fetch(api("api/print"), { headers: authHeader() });
    // /api/print doesn't list macros.  Use Moonraker directly via the nginx
    // proxy path.  The web console is served at /multiace/ so Moonraker is at
    // the same origin root — but we don't have a direct Moonraker proxy here.
    // Fall back: query /printer/objects/list through the poller endpoint.
    // If that's not available, return false (Phase 1 fallback).
    void r;
    return false;  // placeholder replaced in next step
  } catch (_) {
    return false;
  }
}
```

Wait — the web console has NO direct Moonraker proxy available to the browser (it goes through `/api/command`). The spec's probe uses `fetch('/printer/${printerId}/printer/gcode/help')` which assumes a printer-id-keyed proxy that does not exist. The correct approach: probe via the existing `/api/command` endpoint by attempting a `RESPOND TYPE=command MSG=probe` and checking the response, or — simpler — fetch the multiACE state and check if `state.device_count > 0` combined with a one-time `GET /api/state` that now includes a `swap_park_available` field injected by the backend `poller.py`.

**Actually**, the simplest probe that matches the existing architecture: add one line to `poller.py`'s Moonraker call for `/printer/objects/list` and expose `swap_park_available: bool` in `/api/state`. The frontend then just reads `state.swapParkAvailable` from the WS payload.

Revise Step 2 — implement the probe on the backend:

- [ ] **Step 2 (revised): Add `swap_park_available` to `poller.py` and `state.py`**

In `multiace_web/src/multiace_web/poller.py`, locate the `ACEHeadStatusPoller` (or equivalent class that calls `/printer/objects/query?ace`) and add a companion call that checks for `gcode_macro ACEC__Park_T0` in the objects list. Add to the poller's poll loop (find the `async def _poll` or equivalent method):

```python
# In poller.py — inside the async poll method, after the head-status query:
async def _probe_swap_park(self, client: httpx.AsyncClient) -> bool:
    """Return True if ACE_PARK_HEAD convenience macro is registered in Klipper."""
    try:
        r = await client.get(
            f"{self._moonraker_url}/printer/objects/list",
            timeout=4.0,
        )
        if not r.is_success:
            return False
        objects: list[str] = r.json().get("result", {}).get("objects", [])
        return "gcode_macro ACEC__Park_T0" in objects
    except Exception:
        return False
```

Then in the same poll cycle, write the result to the shared state:

```python
state.swap_park_available = await self._probe_swap_park(client)
```

In `multiace_web/src/multiace_web/state.py`, add the field to `CurrentState`:

```python
swap_park_available: bool = False
```

And include it in `as_dict()` (or wherever the WS payload is built — look for the dict comprehension that lists all exported fields):

```python
"swap_park_available": self.swap_park_available,
```

- [ ] **Step 3: Write a test for the probe helper in `test_poller.py`**

```python
# multiace_web/tests/test_poller.py — add after existing tests

@pytest.mark.asyncio
async def test_probe_swap_park_true_when_macro_present(respx_mock):
    from multiace_web.poller import ACEHeadStatusPoller  # or actual class name
    # Adjust import to match the actual class that has _probe_swap_park.
    respx_mock.get("http://moonraker:7125/printer/objects/list").mock(
        return_value=httpx.Response(200, json={
            "result": {"objects": ["gcode_macro ACEC__Park_T0", "gcode_macro ACEC__Unload_T0"]}
        })
    )
    poller = ACEHeadStatusPoller("http://moonraker:7125", state=None)
    async with httpx.AsyncClient() as c:
        result = await poller._probe_swap_park(c)
    assert result is True


@pytest.mark.asyncio
async def test_probe_swap_park_false_when_macro_absent(respx_mock):
    from multiace_web.poller import ACEHeadStatusPoller
    respx_mock.get("http://moonraker:7125/printer/objects/list").mock(
        return_value=httpx.Response(200, json={
            "result": {"objects": ["gcode_macro ACEC__Unload_T0"]}
        })
    )
    poller = ACEHeadStatusPoller("http://moonraker:7125", state=None)
    async with httpx.AsyncClient() as c:
        result = await poller._probe_swap_park(c)
    assert result is False


@pytest.mark.asyncio
async def test_probe_swap_park_false_on_network_error(respx_mock):
    from multiace_web.poller import ACEHeadStatusPoller
    respx_mock.get("http://moonraker:7125/printer/objects/list").mock(
        side_effect=httpx.ConnectError("refused")
    )
    poller = ACEHeadStatusPoller("http://moonraker:7125", state=None)
    async with httpx.AsyncClient() as c:
        result = await poller._probe_swap_park(c)
    assert result is False
```

**Note:** Before writing the test, read `multiace_web/src/multiace_web/poller.py` to confirm the exact class name and constructor signature. Adjust the import and constructor call accordingly.

- [ ] **Step 4: In `app.js`, map `state.swapParkAvailable` from the WS payload**

The `ws.sock.onmessage` handler already does `Object.assign(state, msg.payload)` (line ~94) so `swap_park_available` from the server will land on `state`. However the JS field name uses camelCase while Python uses snake_case. Add a mapping step:

In the `ws.sock.onmessage` handler, after `Object.assign(state, msg.payload)`, add:

```js
// Map snake_case capability flag to camelCase JS field.
if (typeof msg.payload.swap_park_available === "boolean") {
  state.swapParkAvailable = msg.payload.swap_park_available;
}
```

Also do the same in `fetchState()` after `Object.assign(state, body)`.

- [ ] **Step 5: Add the `showSwapConfirm(opts)` helper to `app.js` after `confirmDialog` (around line 235)**

The existing `toast()` helper is text-only. The smart-swap confirmation needs a countdown and a Cancel button. Add a separate helper that renders into `#toast-container`:

```js
/**
 * showSwapConfirm — interactive 3-second toast with countdown and Cancel.
 *
 * opts: {
 *   text: string,          // e.g. "Swap A2 → B1 (~6 min)"
 *   onConfirm: () => void, // called when countdown reaches 0
 *   onCancel: () => void,  // called on Cancel click or navigate-away
 * }
 *
 * Returns a { cancel() } handle so callers can abort programmatically.
 */
function showSwapConfirm({ text, onConfirm, onCancel }) {
  const el = document.createElement("div");
  el.className = "toast swap-confirm-toast";

  const msgSpan = document.createElement("span");
  msgSpan.className = "swap-confirm-msg";
  el.appendChild(msgSpan);

  const cancelBtn = document.createElement("button");
  cancelBtn.className = "swap-confirm-cancel";
  cancelBtn.textContent = "Cancel";
  el.appendChild(cancelBtn);

  document.getElementById("toast-container").appendChild(el);

  let remaining = 3;
  let cancelled = false;
  let intervalId = null;

  function updateLabel() {
    msgSpan.textContent = `${text} — Cancel (${remaining}…)`;
  }
  updateLabel();

  function cleanup() {
    if (intervalId) clearInterval(intervalId);
    el.remove();
  }

  function doCancel() {
    if (cancelled) return;
    cancelled = true;
    cleanup();
    onCancel();
  }

  cancelBtn.addEventListener("click", doCancel);

  intervalId = setInterval(() => {
    remaining -= 1;
    if (remaining <= 0) {
      cleanup();
      if (!cancelled) {
        // Switch to "in progress" non-interactive toast before firing.
        toast(`${text} — swap in progress`, "info");
        onConfirm();
      }
    } else {
      updateLabel();
    }
  }, 1000);

  // Cancel on tab close
  const beforeUnload = () => doCancel();
  window.addEventListener("beforeunload", beforeUnload, { once: true });

  // Cancel if tab hidden for >2s
  let hiddenTimer = null;
  const onVisibilityChange = () => {
    if (document.hidden) {
      hiddenTimer = setTimeout(doCancel, 2000);
    } else {
      if (hiddenTimer) { clearTimeout(hiddenTimer); hiddenTimer = null; }
    }
  };
  document.addEventListener("visibilitychange", onVisibilityChange);

  // Cleanup visibility listener when toast resolves
  const origCleanup = cleanup;
  // Override cleanup to also remove visibility listener.
  function cleanupFull() {
    origCleanup();
    document.removeEventListener("visibilitychange", onVisibilityChange);
    window.removeEventListener("beforeunload", beforeUnload);
    if (hiddenTimer) clearTimeout(hiddenTimer);
  }
  // Patch: replace cleanup references inside closures.
  // Simpler: re-declare cleanup as cleanupFull from the start.
  // Restart with the correct pattern below:
  return { cancel: doCancel };
}
```

**Cleaner re-write** — replace the above body with the version that uses `cleanupFull` from the start (the intermediate version above has a logic gap):

```js
function showSwapConfirm({ text, onConfirm, onCancel }) {
  const el = document.createElement("div");
  el.className = "toast swap-confirm-toast";
  const msgSpan = document.createElement("span");
  msgSpan.className = "swap-confirm-msg";
  el.appendChild(msgSpan);
  const cancelBtn = document.createElement("button");
  cancelBtn.className = "swap-confirm-cancel";
  cancelBtn.textContent = "Cancel";
  el.appendChild(cancelBtn);
  document.getElementById("toast-container").appendChild(el);

  let remaining = 3;
  let done = false;
  let intervalId = null;
  let hiddenTimer = null;

  function updateLabel() {
    msgSpan.textContent = `${text} — Cancel (${remaining}…)`;
  }
  updateLabel();

  function teardown() {
    if (intervalId) { clearInterval(intervalId); intervalId = null; }
    if (hiddenTimer) { clearTimeout(hiddenTimer); hiddenTimer = null; }
    document.removeEventListener("visibilitychange", onVis);
    window.removeEventListener("beforeunload", onUnload);
    el.remove();
  }

  function doCancel() {
    if (done) return;
    done = true;
    teardown();
    onCancel();
  }

  function doConfirm() {
    if (done) return;
    done = true;
    teardown();
    toast(`${text} — swap in progress`, "info");
    onConfirm();
  }

  cancelBtn.addEventListener("click", doCancel);

  const onUnload = () => doCancel();
  window.addEventListener("beforeunload", onUnload, { once: true });

  const onVis = () => {
    if (document.hidden) {
      hiddenTimer = setTimeout(doCancel, 2000);
    } else {
      if (hiddenTimer) { clearTimeout(hiddenTimer); hiddenTimer = null; }
    }
  };
  document.addEventListener("visibilitychange", onVis);

  intervalId = setInterval(() => {
    remaining -= 1;
    if (remaining <= 0) {
      doConfirm();
    } else {
      updateLabel();
    }
  }, 1000);

  return { cancel: doCancel };
}
```

Write the final version (second block) directly into `app.js`. Delete the draft first block if you copied it — only the second block should be present.

- [ ] **Step 6: Add `.swap-confirm-toast` CSS to `style.css` after the `@keyframes slide-in` rule (around line 418)**

```css
/* ---- Swap-confirm interactive toast ---- */
.toast.swap-confirm-toast {
  display: flex;
  align-items: center;
  gap: 0.7rem;
  border-left-color: var(--warn, #f59e0b);
}
.swap-confirm-msg {
  flex: 1;
  font-size: 0.9rem;
}
.swap-confirm-cancel {
  background: transparent;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm, 4px);
  padding: 0.2rem 0.5rem;
  font-size: 0.82rem;
  cursor: pointer;
  color: var(--fg);
  white-space: nowrap;
}
.swap-confirm-cancel:hover { background: rgba(255,255,255,.08); }
```

- [ ] **Step 7: Run the full test suite**

```bash
cd multiace_web && pytest -v
```

Expected: all tests pass (including the new poller probe tests — adjust test if class name differs).

- [ ] **Step 8: Commit**

```bash
git add multiace_web/src/multiace_web/static/app.js \
        multiace_web/src/multiace_web/static/style.css \
        multiace_web/src/multiace_web/poller.py \
        multiace_web/src/multiace_web/state.py \
        multiace_web/tests/test_poller.py \
        multiace_web/tests/test_state.py
git -c user.name="Raul" -c user.email="raul@leadingbit.com" commit -m "feat(web): ops — swap-confirm toast helper + swap_park capability probe"
```

---

## Task 3: Head-state classification helper and gating predicate

Add `classifyHeadState(headIdx)` and `isChevronGated()` as pure helper functions. These are shared by the Unload item (Task 4) and the smart-swap (Task 5). Adding them first and testing their logic in isolation keeps Tasks 4 and 5 lean.

**Files:**
- Modify: `multiace_web/src/multiace_web/static/app.js`

These are pure JS functions — there are no server-side pytest tests for them. The Playwright e2e test in Task 7 verifies the observable behavior end-to-end.

- [ ] **Step 1: Add `classifyHeadState(headIdx)` after `lowestFreeHead()` (around line 1400)**

```js
/**
 * classifyHeadState — returns the head-state matrix category for headIdx.
 *
 * Returns one of:
 *   "empty"            — head_source[N] is null AND sensor is false
 *   "loaded_same_ace"  — head_source[N].ace === targetAce (caller provides targetAce),
 *                        parked falsy
 *   "loaded_cross_ace" — head_source[N].ace !== targetAce, parked falsy
 *   "parked"           — head_source[N].parked === true (regardless of ACE)
 *   "bookkeeping_empty"— head_source[N] is set but sensor[N] is false
 *
 * targetAce is the ace index of the slot whose chevron was clicked.
 * When called for Unload-item visibility (no specific target), pass targetAce=null
 * and only "empty" vs "loaded" distinction is needed.
 */
function classifyHeadState(headIdx, targetAce = null) {
  const src = state.head_source[headIdx];
  const sensor = !!state.sensors[headIdx];

  if (!src) {
    return "empty";
  }
  if (src.parked === true) {
    return "parked";
  }
  if (!sensor) {
    return "bookkeeping_empty";
  }
  if (targetAce !== null && src.ace !== targetAce) {
    return "loaded_cross_ace";
  }
  return "loaded_same_ace";
}

/**
 * isChevronGated — returns a gate-failure reason string, or null when clear.
 *
 * Three gates per spec §5:
 *   1. printState.state is "printing" or "paused"
 *   2. state.swap_in_progress === true
 *   3. state.smartSwapPending !== null
 */
function chevronGateReason() {
  const ps = printState.state;
  if (ps === "printing" || ps === "paused") {
    return `Print ${ps} — actions disabled`;
  }
  if (state.swap_in_progress) {
    return "Swap in progress — wait for completion";
  }
  if (state.smartSwapPending !== null) {
    const p = state.smartSwapPending;
    return `Smart-swap pending on ${tName(p.head)} leg ${p.leg} — wait for completion`;
  }
  return null;
}
```

- [ ] **Step 2: Hook `setView` to abort any pending swap-confirm toast on tab change**

The spec §4 says "Internal multiACE-web tab change — handled in the existing tab-router". The `showSwapConfirm` handle must be reachable from `setView`. Add a module-level variable `_pendingSwapConfirm` near the top of `app.js` (after the `state` literal, around line 22):

```js
let _pendingSwapConfirm = null;  // { cancel() } handle from showSwapConfirm, or null
```

Then patch `setView` to cancel on tab change:

```js
function setView(name) {
  // Abort any pending swap-confirm toast when the user navigates away.
  if (_pendingSwapConfirm) {
    _pendingSwapConfirm.cancel();
    _pendingSwapConfirm = null;
  }
  for (const tab of document.querySelectorAll(".tab")) {
    tab.classList.toggle("active", tab.dataset.view === name);
  }
  for (const view of document.querySelectorAll(".view")) {
    view.classList.toggle("active", view.dataset.view === name);
  }
  if (name === "hardware" && window.HardwareTwin) {
    window.HardwareTwin.mount(document.getElementById("htw-root"));
    window.HardwareTwin.render(state, printState, workflow);
  }
}
```

- [ ] **Step 3: Add a failure-toast helper for per-leg errors**

Add `showSwapFailure(head, leg, retryFn)` after `showSwapConfirm`. This is the retry/dismiss toast shown after a leg fails:

```js
/**
 * showSwapFailure — shows a persistent error toast with Retry and Dismiss.
 *
 * head: int — head index (for display)
 * leg: 1|2 — which leg failed
 * retryFn: async () => void — called when Retry is clicked
 * consecutiveFails: int — after 2 fails, adds "show recovery hints" link
 */
function showSwapFailure(head, leg, retryFn, consecutiveFails = 1) {
  const el = document.createElement("div");
  el.className = "toast error swap-failure-toast";

  const msg = document.createElement("span");
  msg.className = "swap-failure-msg";
  msg.textContent = `Swap ${tName(head)} leg ${leg} failed.`;
  el.appendChild(msg);

  const retryBtn = document.createElement("button");
  retryBtn.className = "swap-confirm-cancel";  // reuse same button style
  retryBtn.textContent = "Retry";
  el.appendChild(retryBtn);

  const dismissBtn = document.createElement("button");
  dismissBtn.className = "swap-confirm-cancel";
  dismissBtn.textContent = consecutiveFails >= 2 ? "Dismiss + hints" : "Dismiss";
  el.appendChild(dismissBtn);

  document.getElementById("toast-container").appendChild(el);

  retryBtn.addEventListener("click", () => {
    el.remove();
    retryFn();
  });

  dismissBtn.addEventListener("click", () => {
    el.remove();
    // Clear the cross-leg lock.
    state.smartSwapPending = null;
    if (consecutiveFails >= 2) {
      // Open the help modal focused on recovery.
      openHelp();
    }
  });
}
```

- [ ] **Step 4: Commit**

```bash
git add multiace_web/src/multiace_web/static/app.js
git -c user.name="Raul" -c user.email="raul@leadingbit.com" commit -m "feat(web): ops — classifyHeadState, chevronGateReason, setView abort hook, showSwapFailure"
```

---

## Task 4: Per-source-slot Unload menu item

Add `↗ Unload T<n>` to the `openHeadTargetMenu` chevron menu when the slot is the source of a loaded/parked head. Wire it to `ACEC__Unload_T<n>` with the same gate check as all other menu items.

**Files:**
- Modify: `multiace_web/src/multiace_web/static/app.js`

- [ ] **Step 1: Update `openHeadTargetMenu` to prepend the Unload item**

Replace the existing `openHeadTargetMenu` function body (lines ~1409–1444) with this version:

```js
function openHeadTargetMenu(anchor, ace, slotIdx) {
  document.querySelectorAll(".head-target-menu").forEach(el => el.remove());
  const menu = document.createElement("div");
  menu.className = "head-target-menu";

  const gateReason = chevronGateReason();

  // ---- Unload items: one per head that sources from this (ace, slot) ----
  for (let h = 0; h < 4; h++) {
    const src = state.head_source[h];
    if (!src || src.ace !== ace || src.slot !== slotIdx) continue;

    const hc = classifyHeadState(h);
    // Show unload item for: loaded_same_ace, loaded_cross_ace, parked, bookkeeping_empty
    if (hc === "empty") continue;

    const item = document.createElement("button");
    item.className = "head-target-menu-item";

    if (gateReason) {
      item.disabled = true;
      item.title = gateReason;
      item.textContent = `↗ Unload ${tName(h)} (gated)`;
    } else {
      item.textContent = `↗ Unload ${tName(h)}`;
      if (hc === "bookkeeping_empty") {
        item.title = `⚠ Sensor disagrees with bookkeeping. Unload may fail; recovery: ACE_MARK_HEAD_UNLOADED HEAD=${h} from gcode console.`;
      }
      item.addEventListener("click", async () => {
        menu.remove();
        seedSingleHeadWorkflow("unload_single", h, `Unload ${tName(h)}`);
        const ok = await sendCommand(`ACEC__Unload_T${h}`);
        if (!ok) {
          for (const s of workflow.steps) {
            if (s.status !== "done") { s.status = "failed"; s.error = "command rejected"; s.ended_at = _now(); }
          }
          renderWorkflow();
        }
      });
    }
    menu.appendChild(item);
  }

  // ---- Separator (only if at least one Unload item was added) ----
  if (menu.children.length > 0) {
    const sep = document.createElement("hr");
    sep.className = "head-target-menu-sep";
    menu.appendChild(sep);
  }

  // ---- Load items: one per head ----
  for (let h = 0; h < 4; h++) {
    const hc = classifyHeadState(h, ace);
    const item = document.createElement("button");
    item.className = "head-target-menu-item";

    if (gateReason) {
      item.disabled = true;
      item.title = gateReason;
      item.textContent = `→ ${tName(h)} (gated)`;
    } else {
      const label = hc === "empty"
        ? `→ ${tName(h)}`
        : `→ ${tName(h)} (swap)`;
      item.textContent = label;
      if (hc !== "empty") {
        // Label what will be displaced.
        const src = state.head_source[h];
        const srcAceLetter = String.fromCharCode(65 + src.ace);
        item.title = `Will displace ${srcAceLetter}${src.slot + 1} currently loaded in ${tName(h)}`;
      }
      item.addEventListener("click", async () => {
        menu.remove();
        await initiateSmartSwap(h, ace, slotIdx, hc);
      });
    }
    menu.appendChild(item);
  }

  // Position and dismiss logic (unchanged from original)
  const r = anchor.getBoundingClientRect();
  menu.style.position = "fixed";
  menu.style.top = `${r.bottom + 4}px`;
  menu.style.left = `${r.left}px`;
  menu.style.zIndex = "1000";
  document.body.appendChild(menu);

  setTimeout(() => {
    function onDocClick(ev) {
      if (!menu.contains(ev.target)) {
        menu.remove();
        document.removeEventListener("click", onDocClick);
      }
    }
    document.addEventListener("click", onDocClick);
  }, 0);
}
```

- [ ] **Step 2: Add `.head-target-menu-sep` CSS to `style.css` after `.head-target-menu-item[disabled]` rule**

```css
.head-target-menu-sep {
  border: 0;
  border-top: 1px solid var(--border, rgba(255,255,255,.12));
  margin: .2rem .25rem;
}
```

- [ ] **Step 3: Also render `parked` badge on the slot card when `src.parked === true`**

In `renderSlotCard` (around line 1447), add after the gate-status pill block (after `pill(head, "?")` else branch, around line 1470):

```js
  // Parked badge — shown when this slot's filament is parked in the bowden
  if (loadedToEntry) {
    const loadedSrc = state.head_source[loadedToHead];
    if (loadedSrc && loadedSrc.parked === true) {
      card.classList.add("parked");
      pill(head, "Parked", "parked-badge");
    }
  }
```

- [ ] **Step 4: Commit**

```bash
git add multiace_web/src/multiace_web/static/app.js \
        multiace_web/src/multiace_web/static/style.css
git -c user.name="Raul" -c user.email="raul@leadingbit.com" commit -m "feat(web): ops — Unload item in chevron menu, parked badge on slot card"
```

---

## Task 5: `initiateSmartSwap` — head-state matrix logic

Implement the core smart-swap function called by the Load items in `openHeadTargetMenu`. It handles the four head states, the toast countdown, the cross-leg lock, and retry on failure.

**Files:**
- Modify: `multiace_web/src/multiace_web/static/app.js`

- [ ] **Step 1: Add `initiateSmartSwap` after `openHeadTargetMenu`**

```js
/**
 * initiateSmartSwap — execute a load to targetHead from (targetAce, targetSlot),
 * branching per the head-state matrix (spec §UX2 and head-state matrix table).
 *
 * headState: return value of classifyHeadState(targetHead, targetAce)
 *
 * Head-state matrix:
 *   empty            → direct ACE_LOAD_HEAD, no toast
 *   loaded_same_ace  → toast + ACEC__Unload_T<n> → ACE_LOAD_HEAD
 *   loaded_cross_ace → if swapParkAvailable: toast + ACE_PARK_HEAD HEAD=<n> → ACE_LOAD_HEAD
 *                      else: same as loaded_same_ace (fallback)
 *   parked           → conservative v1: same as loaded_same_ace branch
 *   bookkeeping_empty→ treated as loaded (Unload shows; sensor disagreement noted in tooltip)
 */
async function initiateSmartSwap(targetHead, targetAce, targetSlot, headState) {
  // Gate check (defensive — menu should already be disabled if gated)
  const gate = chevronGateReason();
  if (gate) { toast(gate, "error"); return; }

  // ---- Empty head: direct load, no toast ----
  if (headState === "empty") {
    seedSingleHeadWorkflow("load_single", targetHead, `Load → ${tName(targetHead)}`);
    const ok = await sendScript(`ACE_LOAD_HEAD HEAD=${targetHead} ACE=${targetAce} SLOT=${targetSlot}`);
    if (!ok) {
      for (const s of workflow.steps) {
        if (s.status !== "done") { s.status = "failed"; s.error = "command rejected"; s.ended_at = _now(); }
      }
      renderWorkflow();
    }
    return;
  }

  // ---- Displacement swap: build toast text ----
  const src = state.head_source[targetHead];
  const srcAceLetter = String.fromCharCode(65 + src.ace);
  const srcSlotLabel = String(src.slot + 1);
  const dstAceLetter = String.fromCharCode(65 + targetAce);
  const dstSlotLabel = String(targetSlot + 1);
  const swapLabel = `${srcAceLetter}${srcSlotLabel} → ${dstAceLetter}${dstSlotLabel}`;

  const usePark = state.swapParkAvailable && headState === "loaded_cross_ace";
  const timeEst = usePark ? "~4 min" : "~6 min";
  const toastText = headState === "parked"
    ? `Swap (parked ${srcAceLetter}${srcSlotLabel}) → ${dstAceLetter}${dstSlotLabel} (${timeEst})`
    : `Swap ${swapLabel} (${timeEst})`;

  // ---- Show toast countdown, then execute on confirm ----
  _pendingSwapConfirm = showSwapConfirm({
    text: toastText,
    onConfirm: () => {
      _pendingSwapConfirm = null;
      _executeSmartSwapLeg1(targetHead, targetAce, targetSlot, usePark, 0);
    },
    onCancel: () => {
      _pendingSwapConfirm = null;
      toast(`Swap ${swapLabel} cancelled`, "info");
    },
  });
}

/**
 * Execute leg 1 of a smart-swap (park/unload), then leg 2 (load).
 * Sets smartSwapPending for the duration. Retries on failure.
 */
async function _executeSmartSwapLeg1(targetHead, targetAce, targetSlot, usePark, failCount) {
  state.smartSwapPending = { head: targetHead, leg: 1, startedAt: _now() };

  let leg1Ok;
  if (usePark) {
    // Cross-ACE with swap-park firmware: park the current source
    leg1Ok = await sendScript(`ACE_PARK_HEAD HEAD=${targetHead}`);
  } else {
    // Same-ACE, parked, or fallback: full unload
    seedSingleHeadWorkflow("unload_single", targetHead, `Unload ${tName(targetHead)}`);
    leg1Ok = await sendCommand(`ACEC__Unload_T${targetHead}`);
    if (!leg1Ok) {
      for (const s of workflow.steps) {
        if (s.status !== "done") { s.status = "failed"; s.error = "command rejected"; s.ended_at = _now(); }
      }
      renderWorkflow();
    }
  }

  if (!leg1Ok) {
    const newFailCount = failCount + 1;
    showSwapFailure(targetHead, 1,
      () => _executeSmartSwapLeg1(targetHead, targetAce, targetSlot, usePark, newFailCount),
      newFailCount
    );
    // smartSwapPending cleared by Dismiss button in showSwapFailure
    return;
  }

  // Leg 1 succeeded — proceed to leg 2
  _executeSmartSwapLeg2(targetHead, targetAce, targetSlot, 0);
}

async function _executeSmartSwapLeg2(targetHead, targetAce, targetSlot, failCount) {
  state.smartSwapPending = { head: targetHead, leg: 2, startedAt: _now() };
  seedSingleHeadWorkflow("load_single", targetHead, `Load → ${tName(targetHead)}`);
  const leg2Ok = await sendScript(`ACE_LOAD_HEAD HEAD=${targetHead} ACE=${targetAce} SLOT=${targetSlot}`);

  if (!leg2Ok) {
    for (const s of workflow.steps) {
      if (s.status !== "done") { s.status = "failed"; s.error = "command rejected"; s.ended_at = _now(); }
    }
    renderWorkflow();
    const newFailCount = failCount + 1;
    showSwapFailure(targetHead, 2,
      () => _executeSmartSwapLeg2(targetHead, targetAce, targetSlot, newFailCount),
      newFailCount
    );
    return;
  }

  // Both legs succeeded — clear cross-leg lock
  state.smartSwapPending = null;
}
```

- [ ] **Step 2: Remove the now-redundant `busy` guard from the old Load item in `openHeadTargetMenu`**

In Task 4 we replaced the entire `openHeadTargetMenu` body so this step is already done. Verify the new Load items use `classifyHeadState` and not the old `!!state.head_source[h] || !!state.sensors[h]` busy check. Do a search:

```bash
grep -n "const busy = " multiace_web/src/multiace_web/static/app.js
```

Expected: no hits inside `openHeadTargetMenu` (the old busy check is gone). If any hits remain from other callers, leave them — only the chevron menu change matters here.

- [ ] **Step 3: Run the full test suite**

```bash
cd multiace_web && pytest -v
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add multiace_web/src/multiace_web/static/app.js
git -c user.name="Raul" -c user.email="raul@leadingbit.com" commit -m "feat(web): ops — initiateSmartSwap with head-state matrix, leg1/2 execution, retry"
```

---

## Task 6: Gating — status banner and disabled-state tooltip

The spec (§Gating) requires all chevron menu items to be grayed with a tooltip explaining the active gate. Task 4 already gates individual items via `chevronGateReason()`. This task verifies the status banner also reflects `smartSwapPending` and that the `renderAll()` call flow is correct.

**Files:**
- Modify: `multiace_web/src/multiace_web/static/app.js`

- [ ] **Step 1: Extend `renderStatusBanner` to surface `smartSwapPending`**

The existing banner (around line 1225) checks `state.swap_in_progress || (workflow.active && !workflow.ended_at)`. Add `state.smartSwapPending` to that condition and branch:

```js
  if (state.swap_in_progress || state.smartSwapPending || (workflow.active && !workflow.ended_at)) {
    banner.classList.remove("hidden"); banner.classList.add("warn");
    if (state.smartSwapPending) {
      const p = state.smartSwapPending;
      setEl(banner, "strong", { textContent: `Smart-swap ${tName(p.head)} in progress` });
      setEl(banner, "span", { textContent: ` — leg ${p.leg} of 2. All chevron menus locked.` });
    } else if (workflow.active) {
      // ... existing workflow branch unchanged ...
```

Replace only the `if (state.swap_in_progress || ...)` condition line and add the `smartSwapPending` branch before the existing `if (workflow.active)` branch. Keep all existing workflow and swap_in_progress rendering unchanged.

- [ ] **Step 2: Ensure `renderStatusBanner()` is called when `smartSwapPending` changes**

`state.smartSwapPending` is mutated directly in JS (not via a WS update). After every mutation site (`_executeSmartSwapLeg1`, `_executeSmartSwapLeg2`, `showSwapFailure`'s Dismiss handler), call `renderStatusBanner()`:

In `_executeSmartSwapLeg1`, after setting `state.smartSwapPending = { head: ... }`:
```js
  state.smartSwapPending = { head: targetHead, leg: 1, startedAt: _now() };
  renderStatusBanner();
```

In `_executeSmartSwapLeg2`, after setting leg 2:
```js
  state.smartSwapPending = { head: targetHead, leg: 2, startedAt: _now() };
  renderStatusBanner();
```

In both success/clear sites (`state.smartSwapPending = null`):
```js
  state.smartSwapPending = null;
  renderStatusBanner();
```

In `showSwapFailure`'s Dismiss handler:
```js
  state.smartSwapPending = null;
  renderStatusBanner();
```

- [ ] **Step 3: Run the full test suite**

```bash
cd multiace_web && pytest -v
```

- [ ] **Step 4: Commit**

```bash
git add multiace_web/src/multiace_web/static/app.js
git -c user.name="Raul" -c user.email="raul@leadingbit.com" commit -m "feat(web): ops — status banner surfaces smartSwapPending, renderStatusBanner call sites"
```

---

## Task 7: Playwright structural e2e test script

Create `tools/e2e_operations.py` — a structural Playwright test that exercises all the menu states against a mock state injected via `page.evaluate`. No live hardware required; all assertions are against DOM structure.

**Files:**
- Create: `multiace_web/tools/e2e_operations.py`

- [ ] **Step 1: Create the file**

```python
"""Structural Playwright e2e for the multiACE Operations feature.

Injects synthetic state into the page via page.evaluate() to exercise all
four head-state matrix branches without real hardware.  Uses page.clock()
for the 3-second countdown so there is no wall-clock flake.

Must be run against a running instance of the web console (local dev or
live printer — but does NOT issue any real gcode commands because the mock
state has no Moonraker behind it; sendScript/sendCommand calls will bounce
with a 503 and the test only asserts UI structure).

Usage:
    pip install playwright && playwright install chromium
    # Start local dev server first:
    MULTIACE_LOG_DIR=/tmp/fake_logs uvicorn multiace_web.server:app --port 7126
    python tools/e2e_operations.py http://localhost:7126/

Pre-flight: NO print safety check required (no real gcode issued).
"""
from __future__ import annotations

import asyncio
import sys


async def inject_state(page, patch: dict) -> None:
    """Merge patch into the page's JS `state` object and re-render."""
    import json
    await page.evaluate(f"""
        Object.assign(window.state, {json.dumps(patch)});
        if (typeof window.renderAll === 'function') window.renderAll();
    """)


async def main(url: str) -> None:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("pip install playwright && playwright install chromium", file=sys.stderr)
        sys.exit(2)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await ctx.new_page()

        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        await page.goto(url, wait_until="networkidle")
        await page.wait_for_timeout(2000)

        # ---------------------------------------------------------------
        # Test 1: Empty head — chevron shows 4 load items, NO Unload item,
        # NO separator.
        # ---------------------------------------------------------------
        await inject_state(page, {
            "device_count": 2,
            "head_source": {"0": None, "1": None, "2": None, "3": None},
            "sensors": {"0": False, "1": False, "2": False, "3": False},
            "swap_in_progress": False,
            "smartSwapPending": None,
            "gate_status": [1, 1, 1, 1],
        })
        # Open chevron on ACE A (ace=0) slot 0
        await page.locator(
            '.ace-block[data-ace="0"] .card:nth-of-type(1) '
            '.slot-load-split > button:last-child'
        ).click()
        await page.locator(".head-target-menu").wait_for(state="visible", timeout=3000)
        item_count = await page.locator(".head-target-menu-item").count()
        assert item_count == 4, f"Test 1 (empty): expected 4 load items, got {item_count}"
        sep_count = await page.locator(".head-target-menu-sep").count()
        assert sep_count == 0, f"Test 1 (empty): expected no separator, got {sep_count}"
        print("Test 1 PASS: empty head — 4 load items, no Unload")
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(200)

        # ---------------------------------------------------------------
        # Test 2: Loaded same-ACE — chevron prepends Unload item + separator.
        # ---------------------------------------------------------------
        await inject_state(page, {
            "head_source": {
                "0": {"ace": 0, "slot": 0, "type": "PLA", "color": "ff0000"},
                "1": None, "2": None, "3": None,
            },
            "sensors": {"0": True, "1": False, "2": False, "3": False},
        })
        await page.locator(
            '.ace-block[data-ace="0"] .card:nth-of-type(1) '
            '.slot-load-split > button:last-child'
        ).click()
        await page.locator(".head-target-menu").wait_for(state="visible", timeout=3000)
        # First item should be "↗ Unload T1"
        first_item_text = await page.locator(".head-target-menu-item").nth(0).text_content()
        assert "Unload" in first_item_text, \
            f"Test 2 (loaded same-ACE): first item should be Unload, got: {first_item_text!r}"
        sep_count = await page.locator(".head-target-menu-sep").count()
        assert sep_count == 1, f"Test 2: expected 1 separator, got {sep_count}"
        total_items = await page.locator(".head-target-menu-item").count()
        assert total_items == 5, \
            f"Test 2: expected 1 Unload + 4 Load = 5 items, got {total_items}"
        print("Test 2 PASS: loaded same-ACE — Unload item prepended")
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(200)

        # ---------------------------------------------------------------
        # Test 3: Print state gating — all items disabled with tooltip.
        # ---------------------------------------------------------------
        await page.evaluate("window.printState = window.printState || {};")
        await page.evaluate("window.printState.state = 'printing';")
        await page.evaluate("if (typeof renderAll === 'function') renderAll();")
        await page.locator(
            '.ace-block[data-ace="0"] .card:nth-of-type(1) '
            '.slot-load-split > button:last-child'
        ).click()
        await page.locator(".head-target-menu").wait_for(state="visible", timeout=3000)
        disabled_count = await page.locator(".head-target-menu-item[disabled]").count()
        total_count = await page.locator(".head-target-menu-item").count()
        assert disabled_count == total_count, \
            f"Test 3 (printing gate): expected all {total_count} items disabled, got {disabled_count} disabled"
        print(f"Test 3 PASS: printing gate — all {disabled_count} items disabled")
        # Restore safe state
        await page.evaluate("window.printState.state = 'standby';")
        await page.evaluate("if (typeof renderAll === 'function') renderAll();")
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(200)

        # ---------------------------------------------------------------
        # Test 4: smartSwapPending gate — all items disabled.
        # ---------------------------------------------------------------
        await inject_state(page, {
            "smartSwapPending": {"head": 0, "leg": 1, "startedAt": 0},
        })
        await page.locator(
            '.ace-block[data-ace="0"] .card:nth-of-type(1) '
            '.slot-load-split > button:last-child'
        ).click()
        await page.locator(".head-target-menu").wait_for(state="visible", timeout=3000)
        disabled_count = await page.locator(".head-target-menu-item[disabled]").count()
        total_count = await page.locator(".head-target-menu-item").count()
        assert disabled_count == total_count, \
            f"Test 4 (smartSwapPending gate): expected all disabled, got {disabled_count}/{total_count}"
        print(f"Test 4 PASS: smartSwapPending gate — all {disabled_count} items disabled")
        await inject_state(page, {"smartSwapPending": None})
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(200)

        # ---------------------------------------------------------------
        # Test 5: Toast countdown and Cancel — use page.clock() to fast-forward.
        # ---------------------------------------------------------------
        # Reset to empty head so clicking Load items goes to swap path.
        # For the toast to show we need a loaded head, inject loaded_cross_ace state:
        await inject_state(page, {
            "head_source": {
                "0": {"ace": 0, "slot": 0, "type": "PLA", "color": "ff0000"},
                "1": None, "2": None, "3": None,
            },
            "sensors": {"0": True, "1": False, "2": False, "3": False},
            "swapParkAvailable": False,
        })
        # Install fake clock BEFORE the click that triggers the timer
        await ctx.route("**/api/command", lambda r: r.fulfill(status=200, body='{"ok":true}'))
        await page.clock.install()
        # Open chevron on ACE A slot 0 and click → T1 (swap) to trigger toast
        await page.locator(
            '.ace-block[data-ace="0"] .card:nth-of-type(1) '
            '.slot-load-split > button:last-child'
        ).click()
        await page.locator(".head-target-menu").wait_for(state="visible", timeout=3000)
        # Find T1 load item (index 1 — after the Unload separator): text contains "T1"
        load_items = page.locator(".head-target-menu-item")
        item_texts = await load_items.all_text_contents()
        # Find the "→ T1" item
        t1_idx = next((i for i, t in enumerate(item_texts) if "T1" in t and "Unload" not in t), None)
        assert t1_idx is not None, f"Test 5: could not find → T1 item. Items: {item_texts}"
        await load_items.nth(t1_idx).click()

        # Toast should appear within 500ms
        await page.wait_for_selector(".swap-confirm-toast", timeout=2000)
        toast_text = await page.locator(".swap-confirm-toast .swap-confirm-msg").text_content()
        assert "Swap" in toast_text, f"Test 5: expected Swap toast, got {toast_text!r}"
        assert "Cancel" in toast_text, f"Test 5: expected Cancel countdown in toast text"

        # Click Cancel button — toast disappears
        await page.locator(".swap-confirm-cancel").click()
        await page.wait_for_selector(".swap-confirm-toast", state="hidden", timeout=2000)
        print(f"Test 5 PASS: swap-confirm toast appears with Cancel; Cancel dismisses it")
        # Uninstall clock
        await page.clock.uninstall()

        # ---------------------------------------------------------------
        # Test 6: Toast navigates-away abort — switch tab during countdown.
        # ---------------------------------------------------------------
        await page.clock.install()
        await page.locator(
            '.ace-block[data-ace="0"] .card:nth-of-type(1) '
            '.slot-load-split > button:last-child'
        ).click()
        await page.locator(".head-target-menu").wait_for(state="visible", timeout=3000)
        item_texts = await page.locator(".head-target-menu-item").all_text_contents()
        t1_idx = next((i for i, t in enumerate(item_texts) if "T1" in t and "Unload" not in t), None)
        await page.locator(".head-target-menu-item").nth(t1_idx).click()
        await page.wait_for_selector(".swap-confirm-toast", timeout=2000)
        # Navigate to Activity tab — should abort toast
        await page.click('button[data-view="activity"]')
        await page.wait_for_selector(".swap-confirm-toast", state="hidden", timeout=2000)
        print("Test 6 PASS: navigate-away aborts swap-confirm toast")
        await page.clock.uninstall()

        # ---------------------------------------------------------------
        # Final summary
        # ---------------------------------------------------------------
        if errors:
            print(f"\nJS errors during test: {errors}", file=sys.stderr)
            sys.exit(1)
        print("\nAll e2e_operations tests PASSED")
        await browser.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    asyncio.run(main(sys.argv[1]))
```

- [ ] **Step 2: Commit**

```bash
git add multiace_web/tools/e2e_operations.py
git -c user.name="Raul" -c user.email="raul@leadingbit.com" commit -m "test(web): ops — Playwright structural e2e for operations menu/toast/gating"
```

---

## Task 8: Live smoke test (manual — hardware required)

This is the final verification step. Do NOT automate it — it involves real hardware and must pass before declaring the feature complete.

**Pre-flight:**
1. Confirm print state is safe: `curl -s http://<printer-ip>:7125/printer/objects/query?print_stats | jq -r '.result.status.print_stats.state'` — must be `standby`, `complete`, `cancelled`, or `error`.
2. Confirm `device_count=2` in the web console (both ACEs visible).
3. Confirm firmware is safe to touch (see global CLAUDE.md safety rules).

**Smoke matrix:**

- [ ] **Branch 1 — empty head:** All 4 heads unloaded. Open ACE A slot 1 chevron. Confirm: no Unload items, 4 load items, none disabled. Click `→ T1`. Confirm load completes in ~3 min and `head_source[0]` updates. Expected toast: "ACE_LOAD_HEAD sent".

- [ ] **Branch 2 — loaded same-ACE displacement:** T1 loaded from ACE A slot 1. Open ACE A slot 2 chevron. Confirm: `↗ Unload T1` present, `→ T1 (swap)` item present. Click `→ T1 (swap)`. Confirm swap-confirm toast shows `"Swap A1 → A2 (~6 min)"`. Wait 3 sec (do not cancel). Confirm unload then load sequence in Activity tab. Confirm `head_source[0]` updated to slot 2.

- [ ] **Branch 3 — cross-ACE displacement (without swap-park firmware):** T1 loaded from ACE A slot 1. Open ACE B slot 1 chevron. `swapParkAvailable` should be false (no park firmware). Click `→ T1 (swap)`. Confirm toast shows `"~6 min"` (fallback timing). Confirm full unload + cross-ACE load sequence. Total time ~6 min.

- [ ] **Branch 4 — parked head (requires swap-park firmware on `feat/swap-park` branch):** Run `ACE_PARK_HEAD HEAD=0` from gcode console. Confirm slot card shows dashed border + "Parked" badge. Open that slot's chevron. Confirm `↗ Unload T1` is present. Click it. Confirm full unload proceeds (conservative v1 path).

- [ ] **Branch 5 — gate: print running:** Start a test print. Confirm all chevron items are grayed with tooltip. After print completes, confirm items are re-enabled.

- [ ] **Branch 6 — Cancel within 3-sec window:** Initiate a displacement swap. Click Cancel within 3 sec. Confirm no gcode commands were issued (check Activity tab — no new events).

- [ ] **Branch 7 — navigate-away abort:** Initiate a displacement swap. Switch to Activity tab during the 3-sec countdown. Confirm toast disappears and no gcode issued.

- [ ] **Run visual regression after smoke:**

```bash
pip install playwright && playwright install chromium
export DAVINCI_U1_HOST=<printer-ip>
python multiace_web/tools/visual_regression.py http://$DAVINCI_U1_HOST/multiace/
```

Confirm no JS pageerrors in the output.

---

## Self-review

### Spec coverage check

| Spec section | Plan task |
|---|---|
| §UX1 Per-source-slot Unload menu item | Task 4 |
| §UX2 Smart-swap on chevron Load (4 head states) | Task 5 (`initiateSmartSwap`) |
| §UX3 Toast confirm with 3-sec cancel window | Task 2 (`showSwapConfirm`) |
| §UX4 Cancel + navigate-away semantics (beforeunload, visibilitychange, tab-change) | Tasks 2 and 3 |
| §UX5 UI-side cross-leg lock (`state.smartSwapPending`) | Tasks 2, 3, 5, 6 |
| §UX6 State-aware retry (leg 1 / leg 2) | Task 5 (`showSwapFailure`, `_executeSmartSwapLeg1/2`) |
| §UX7 Audit story (no new frontend work) | n/a — covered by existing Activity tab |
| §UX8 Parked-state visual | Task 1 (CSS) + Task 4 (badge in `renderSlotCard`) |
| §Firmware capability detection | Task 2 (poller probe + `state.swap_park_available`) |
| §Gating (3 gates) | Tasks 3 (`chevronGateReason`) + 6 (banner) |
| §Testing automated Playwright | Task 7 |
| §Testing live hardware smoke | Task 8 |
| Phase 1 / Phase 2 feature-flag | `state.swapParkAvailable` gate in `initiateSmartSwap` — activates when probe returns true |

**No gaps found.**

### Placeholder scan

- Task 2 Step 2 contains a multi-step revision of the probe design with an intermediate dead-end block that was immediately superseded. The final design (backend probe in `poller.py`) is unambiguous. The dead-end block is preceded by a "Wait —" callout and is clearly overridden by "Revise Step 2". Implementers should follow the "revised" instructions and ignore the dead-end draft.
- Task 7 Step 1 uses `ctx.route("**/api/command", ...)` to prevent real gcode from being issued. This only works if the test server's Moonraker is unreachable or the route mock fires. In a live-server run, `sendScript` POST will still reach the server but Moonraker will reject it — non-destructive. For a purely isolated test, run against a local dev server with no real Moonraker configured.

### Type / name consistency

- `state.smartSwapPending` — set in Tasks 2, 5, 6; read in Tasks 3, 6. Consistent.
- `state.swapParkAvailable` — set in Task 2 (JS camelCase); backend field `swap_park_available` (snake_case). Mapping added in Task 2 Step 4. Consistent.
- `classifyHeadState(h, targetAce)` — defined in Task 3; called in Tasks 4 and 5. Signature consistent.
- `chevronGateReason()` — defined in Task 3; called in Tasks 4 and 5. Consistent.
- `showSwapConfirm({ text, onConfirm, onCancel })` — defined Task 2; called Task 5. Consistent.
- `showSwapFailure(head, leg, retryFn, consecutiveFails)` — defined Task 3; called Task 5. Consistent.
- `_pendingSwapConfirm` — declared Task 3; set/cleared Task 5. Consistent.
- `tName(h)` — existing helper; used consistently throughout.
- `src.ace` (NOT `src.ace_index`) — used throughout. Consistent with existing codebase.

### `head_source` field note for poller test (Task 2 Step 3)

Before writing the poller probe tests, read `multiace_web/src/multiace_web/poller.py` to find the actual class name. The test uses `ACEHeadStatusPoller` as a placeholder — replace with the real class if different. The constructor may also need a `state` argument of a different type; adjust accordingly. The test logic itself (mock HTTP → assert boolean return) is correct regardless of the class name.
