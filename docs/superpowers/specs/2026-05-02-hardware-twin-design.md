# Hardware Twin — Dashboard physical-twin redesign

**Status:** design (pre-implementation)
**Author:** raul + Claude Opus 4.7
**Date:** 2026-05-02
**Branch target:** `feat/multiace-web-console`
**Target version:** `0.6.0`

## 1. Goal

Add a new **Hardware** tab to the multiACE web console that visualizes the
ACE-Pro-to-Snapmaker-U1 system as a physical-twin SVG diagram. Filament
state, source mappings, load/unload activity, and per-toolhead status all
read from a single picture rather than a stack of cards.

The existing Dashboard tab is **not** modified. The new tab is additive.
If the redesign doesn't work out, the change is reverted by deleting one
file and one nav item — no Dashboard regressions possible.

## 2. Scope

In-scope:

- A new `Hardware` tab between Dashboard and Activity in the existing
  nav.
- A vertical stack of one ACE block per connected ACE (newest on top,
  oldest closest to the U1).
- A Snapmaker U1 block under all ACEs.
- Per-slot bowden tubes meeting at couplers, then a single tube per
  toolhead from the coupler into the U1.
- A thin status banner at the top of the tab.
- Per-slot Load/Unload buttons (one row under each ACE block) and
  per-toolhead Load/Unload buttons (under the U1 block).

Out-of-scope (deliberately):

- No backend changes. No new API endpoint, no new state field.
- No changes to the existing Dashboard, Activity, Dryer, Config, or
  Diag tabs.
- No replacement of the existing per-toolhead error banner that the
  Dashboard already provides.
- No new dependencies; no build step; no framework. Pure
  HTML/CSS/SVG/vanilla JS.

## 3. Decisions log

The visual decisions below were locked through brainstorming with the
visual companion. Each was an explicit option-pick rather than a default.

| Decision | Choice | Rationale |
|---|---|---|
| Where the new view lives | New "Hardware" tab; Dashboard untouched | Lowest risk, easy to revert, no Dashboard regressions possible |
| Multi-ACE layout | Vertical stack, newest ACE on top, oldest closest to U1 | Mirrors physical reality; reflows naturally as block elements |
| Illustration fidelity | Schematic SVG (clean rectangles, color = filament, dashed = empty) | Pure inline SVG, sharp at any DPI, no asset weight, readable on the 7" printer screen |
| ACE width vs U1 width | ACE block ≈ 70 % of U1 width, both centered | The U1 visually dominates; matches the user's brief |
| Load animation | Tube fill grows from ACE end to coupler/U1; source slot AND destination toolhead both pulse for entire op | Conveys direction; both endpoints flash so the user can pair them by sight |
| Unload animation | Tube fill recedes from U1 end back toward the ACE; same dual flash | Mirrors load; consistent visual language |
| Button placement | One row of context-aware Load/Unload buttons *below* each block | Matches the user's brief literally; doesn't fight the flash glow |
| Tab content | Thin status banner + the illustration; no side rail | Single line of context without duplicating Dashboard |
| Implementation approach | New module `static/hardware-twin.js` (separate file, IIFE) | Keeps app.js from drifting past 1500 lines; clean isolation |
| Tube topology | Each ACE slot has its own bowden; tubes meet at a per-toolhead coupler; one tube continues from coupler to U1 | Physically accurate to the multi-ACE setup; naturally explains why "only one ACE per coupler can be the active source" |

## 4. Topology and visual model

### 4.1 Physical reality (verified against the firmware)

`multiace/klipper/extras/ace.py:1604` — `ACE_LOAD_HEAD` defaults
`SLOT = HEAD`, and the UI macros
`multiace/config/extended/ace.cfg:217-235` are `ACEC__Load_Tn → ACE_LOAD_HEAD HEAD=n`
with no SLOT and no ACE override. So the standing-default mapping is
**slot N of the active ACE feeds toolhead N**.

For multi-ACE: every ACE has its own slot N tube. All matching-N tubes
from all ACEs meet at coupler N. From coupler N, one tube continues
into the U1 toolhead T_N. At any instant, exactly one ACE per coupler
is the active source (`state.head_source[N]` is a single
`{ace_index, slot}` value, not a list). Inactive ACEs can park
filament *up to but not past* the coupler.

### 4.2 Diagram model

For each ACE `d` ∈ [0..device_count):

- An ACE block (rectangle, 4 evenly-spaced slot sub-rectangles).
- A per-slot bowden tube. The tube runs from the slot's bottom edge
  straight down to that slot's coupler, **passing visually behind any
  lower ACE bodies via z-order** (tubes drawn first; ACE bodies drawn
  on top). The visible tube segments are between ACE bodies and
  below the bottom-most ACE.

For each toolhead `i` ∈ [0..3]:

- One coupler at a fixed x position aligned with slot `i`'s column.
- One short tube from the coupler down to T_i's input on the U1.

### 4.3 Color and state mapping per tube

| Slot state | Backing rail | Colored fill | Coupler→U1 stroke |
|---|---|---|---|
| Slot empty | Dashed grey rail (always visible) | None | Whatever the active source dictates |
| Slot has filament, NOT the active source for its toolhead ("parked") | Dashed grey rail | Solid filament-color stroke from slot bottom to **15 viewBox units short of the coupler** | Whatever the active source dictates |
| Slot has filament AND is the active source ("active") | Dashed grey rail | Solid filament-color stroke from slot bottom to coupler | Same color, full length to T_i |
| No active source for toolhead i | n/a | n/a | Grey (no fill) |

### 4.4 Animation states

- **Load**: tube fill grows from slot-bottom toward coupler over 2.2 s
  linear; source ACE slot rectangle and destination toolhead rectangle
  both pulse `htw-flashing` for the entire operation. When LOAD_HEAD
  fires, the tube transitions to "active" steady state and the toolhead
  rectangle becomes solid + filament color. Both stop pulsing.
- **Unload**: tube fill recedes from coupler/U1 end back toward slot
  over 2.2 s linear; same dual pulse. When UNLOAD_HEAD fires, the
  toolhead returns to empty (dashed border), the tube returns to "empty"
  (grey rail only), pulses stop.
- **Reduced motion**: all keyframes wrapped in
  `@media (prefers-reduced-motion: no-preference)`. The fallback
  replaces the pulse with a static `outline: 3px solid var(--htw-flash-glow)`
  and the tube fill with a 0.4 s `transition: stroke-dashoffset` (still
  directional, just instant-feeling).

## 5. Architecture

### 5.1 New file

`multiace_web/src/multiace_web/static/hardware-twin.js`

IIFE module that exports two methods on `window.HardwareTwin`:

```js
window.HardwareTwin = (function () {
  function mount(rootEl) { /* one-time SVG skeleton + button rows */ }
  function render(state, printState, workflow) { /* attribute mutations only */ }
  return { mount, render };
})();
```

The module never reads file-scope globals from `app.js`. State is passed
in by the caller every time. The module keeps a small `lastWorkflow`
snapshot (module-private) for change detection, nothing else.

### 5.2 Edits to `static/index.html`

1. Add a nav button before the help button:
   ```html
   <button data-view="hardware" class="tab">Hardware</button>
   ```
2. Add a view section after the Diag section:
   ```html
   <section data-view="hardware" class="view">
     <div id="htw-banner" class="htw-banner hidden"></div>
     <div id="htw-root"></div>
   </section>
   ```
3. Add a script tag after `app.js`:
   ```html
   <script defer src="static/hardware-twin.js?v=0.6.0"></script>
   ```
4. Bump cache-bust version on `style.css` and `app.js` to `?v=0.6.0`.

### 5.3 Edits to `static/app.js`

Total < 15 lines.

1. After `setView` is defined (`app.js:1374`): when name is `"hardware"`,
   call `window.HardwareTwin.mount(document.getElementById("htw-root"))`
   on first activation (idempotent — `mount` checks
   `rootEl.dataset.htwMounted`).
2. In `renderAll()` (`app.js:864`): append
   `if (window.HardwareTwin) window.HardwareTwin.render(state, printState, workflow);`
   as the last line.
3. In `fetchPrint()` (`app.js:271`): same `HardwareTwin.render` call so
   `printState.current_extruder` changes drive the toolhead "extruding"
   tint.

### 5.4 Edits to `static/style.css`

Append a `/* hardware-twin */` block at the bottom containing:

- New CSS variables under `:root`:
  `--htw-stroke`, `--htw-stroke-empty`, `--htw-tube-grey`,
  `--htw-flash-glow`, `--htw-extrude-glow`, `--htw-banner-bg`,
  `--htw-text-muted`.
- All selectors prefixed `htw-`. **Hard rule:** every selector and DOM
  id introduced by this module starts with `htw-`. Verified at PR
  review by `grep -E '^[^/].*\bhtw-' style.css | wc -l` matching the
  expected count.
- `@keyframes htwFlashGlow`, `@keyframes htwTubeLoad`,
  `@keyframes htwTubeUnload`, `@keyframes htwExtrudeGlow`. All keyframes
  inside `@media (prefers-reduced-motion: no-preference)`.

### 5.5 Edits to `multiace_web/__init__.py`

Bump `__version__` to `"0.6.0"`.

### 5.6 Edits to `multiace_web/README.md`

One paragraph under "Features" describing the Hardware tab. One line in
the changelog describing the tab.

### 5.7 Backend

No changes. The existing `/api/state`, `/api/events`, `/api/command`,
`/api/print`, and the WebSocket `/ws` endpoints already supply
everything the Hardware tab needs. The pytest suite is untouched.

## 6. State → visual mapping

All inputs come via the `render(state, printState, workflow)` call.

### 6.1 ACE block (per device `d` ∈ [0..device_count))

| Visual | Input | Rule |
|---|---|---|
| Block label | `d` | `String.fromCharCode(65 + d)` → "ACE A", "ACE B", … |
| Active marker | `state.active_device` | `htw-ace-active` class when `d === active_device` |
| Stack order | `device_count` | DOM order: device 0 last, device N first (newest on top) |

### 6.2 Slot section (4 per ACE)

| Visual | Input | Rule |
|---|---|---|
| Filled, active ACE | `state.gate_status[i]` | `1` filled; `0` empty (dashed) |
| Filled, non-active ACE | derived from `state.head_source` | Filled iff some `head_source[h] === {ace: d, slot: i}`; otherwise unknown (dashed) |
| Color | `print_task_config[H].color` via `rgbFromUint(...)` | `H` = the toolhead currently sourced from `(d, i)`; null → no fill |
| Flash | `workflow` step in `running` state targeting this slot's mapped toolhead | `htw-flashing` class while running |

### 6.3 Tube (per ACE per slot)

| Visual | Input | Rule |
|---|---|---|
| Backing rail | always drawn | Dashed grey from slot bottom to coupler |
| Empty fill | slot empty | No colored stroke |
| Parked fill | slot filled, this ACE NOT the head_source for its toolhead | Solid stroke from slot bottom to `coupler_y - 15` |
| Active fill | slot filled, this ACE IS the head_source for its toolhead | Solid stroke from slot bottom to `coupler_y` |
| Loading anim | workflow step running, kind starts with `load_*`, target = this slot's mapped head | `htw-tube-loading` (animates 100→0 dashoffset) |
| Unloading anim | workflow step running, kind starts with `unload_*`, target head's source = `(d, i)` | `htw-tube-unloading` (animates 0→100 dashoffset) |

### 6.4 Coupler→U1 tube (per toolhead i)

The post-coupler tube is a thin vertical stroke from coupler i down to
T_i's input strip. It is **not animated independently**. It mirrors the
state of the active source tube above it:

| Visual | Input | Rule |
|---|---|---|
| Empty | `state.head_source[i] == null` | Grey stroke |
| Loaded | source set, `sensors[i] === true` | Filament color from `print_task_config[i]` |
| In-flight (loading or unloading) | step running with target i | Same color as the active source tube above; no separate stroke-dashoffset animation — the visual narrative is "the source tube is filling/draining; this short downstream segment just shares the color." |

### 6.5 Toolhead block (per i)

| Visual | Input | Rule |
|---|---|---|
| Filled vs empty | `state.sensors[i]` | true → solid; false → dashed empty |
| Color | `rgbFromUint(state.print_task_config[i].color)` | null → grey |
| Source label | `state.head_source[i]` | "ACE A · Slot 3" or hidden |
| Flash | workflow step running, `step.head === i` | `htw-flashing` class |
| Extruding tint | `printState.state === "printing" && printState.current_extruder === i` | `htw-extruding` class — subtler than flash |
| Error | `state.last_error?.head === i` | `htw-error` class + tooltip from `last_error.error` |

### 6.6 Buttons (under each block)

All buttons are HTML `<button>` siblings of the SVG inside the relevant
block's container, populated/relabeled per render. They use
`data-cmd="ACEC__Load_Tn"` / `data-cmd="ACEC__Unload_Tn"` and rely on
the existing global `[data-cmd]` listener at `app.js:1393-1410`. **No
new sendCommand path.**

| Button | Visibility | data-cmd | Disabled when |
|---|---|---|---|
| Slot Load | `state.gate_status[i] === 1` and slot not currently sourcing any head | `ACEC__Load_T<i>` | `state.swap_in_progress` (the existing convention) |
| Slot Unload | this slot IS sourcing head H | `ACEC__Unload_T<H>` | `state.swap_in_progress` |
| Toolhead Load | always rendered | `ACEC__Load_T<i>` | `state.swap_in_progress` |
| Toolhead Unload | `state.head_source[i] != null` | `ACEC__Unload_T<i>` | `state.swap_in_progress` |

Print-in-progress safety: the existing app already gates Dashboard
Load/Unload on `state.swap_in_progress`. Hardware tab uses the exact
same signal. No new gating layer.

### 6.7 Status banner (`#htw-banner`)

- `workflow.active` → workflow.label + active step description.
- Else `state.last_error` → error message; clickable ✕ to dismiss
  (sets a local "ack" flag — banner stays hidden until `last_error`
  changes again).
- Else `state.connected === false` → "ACE disconnected".
- Else hidden.

A tiny formatter helper is factored out of `app.js`'s existing
`renderStatusBanner` and shared with the Hardware tab so both banners
read the same content.

### 6.8 Step → slot resolution

Workflow steps know `head` only. To pick which slot's tube animates:

- Unload step → snapshot `state.head_source[step.head]` *before* the
  UNLOAD_HEAD event clears it. That's the slot the tube drains from.
- Load step → before LOAD_HEAD fires, `head_source[step.head]` is
  null. Source slot defaults to `step.head` of `state.active_device`
  (matches the `ACEC__Load_Tn → ACE_LOAD_HEAD HEAD=n` macro chain).
  After LOAD_HEAD, `head_source[step.head]` is authoritative — re-read
  and snap the tube's loaded final destination from there.

## 7. Render lifecycle

`mount(rootEl)` runs once per session and is idempotent.

1. Sets `rootEl.dataset.htwMounted = "1"` (idempotency guard).
2. Builds the **container scaffold only**: the U1 SVG, the four
   couplers, the four post-coupler stroke paths, and the per-toolhead
   button row. Per-ACE SVGs and per-slot tubes are NOT built here — they
   are added by `render` when `state.device_count > 0`.
3. Stable IDs on every node mutated by `render`: `htw-coupler-${i}`,
   `htw-postcoupler-${i}`, `htw-tool-${i}`, `htw-tool-source-${i}`,
   `htw-banner`. ACE-block IDs (added later) are
   `htw-ace-${d}`, `htw-ace-${d}-slot-${i}`, `htw-tube-${d}-${i}`.
4. Assigns `data-cmd` attributes on the toolhead button row. The actual
   click handling is delegated to the existing global listener at
   `app.js:1393-1410` — no per-button listener needed.

`render(state, printState, workflow)` runs on every state push, every
print-state poll, and every workflow change. It does **only**:

- Attribute mutations: `class`, `data-*`, `aria-*`, `fill`, `stroke`,
  `stroke-dasharray`, `stroke-dashoffset`.
- Text-node updates inside the existing `<text>` and source-label
  elements.
- Button label/disabled state updates.
- Workflow-diff to add/remove animation classes.

It **never** calls `innerHTML`, `replaceChildren`, or removes a child
node. Animations are CSS-driven on persistent nodes and survive
mid-render mutations.

**Initial block creation and runtime resizing.** On every `render`, the
module compares the number of ACE blocks currently in the DOM against
`state.device_count`. If they differ:

- Missing blocks are appended at the **top** of the stack (newest on
  top), each with its own SVG, four slot rectangles, four backing-rail
  tubes, four colored-fill tube placeholders, and a button row.
- Excess blocks (e.g. one ACE was removed) are detached entirely from
  the DOM.

Existing blocks are not touched; their nodes remain stable across
state pushes. This same routine handles the initial mount transition
from "0 blocks" to "device_count blocks".

If `state.device_count === 0` (initial load before discovery, or all
ACEs disconnected), the tab body shows a centered "Waiting for ACE…"
message and no SVG content under `htw-root`.

## 8. Edge cases

| Case | Detection | Behavior |
|---|---|---|
| Print in progress, user clicks Load | `state.swap_in_progress` becomes true via existing flow | Buttons grey out via `disabled`. Same gate as Dashboard. |
| ACE disconnected (`state.connected === false`) | per render | Whole tab gets `htw-disconnected` class; centered "ACE disconnected" pill over the illustration; all buttons disabled. |
| `swap_in_progress === true` but `workflow.active === false` | possible if a swap was already running before page load | Banner shows "Tool change in progress"; faint pulse on U1 chassis (target unknown); buttons disabled. |
| Filament loaded but no color (RFID didn't read) | `print_task_config[h].color === 0` → `rgbFromUint` returns null | Slot/toolhead rendered with neutral grey + `?` glyph in the corner; tube uses neutral grey. |
| `last_error` exists | `state.last_error != null` | Affected toolhead gets `htw-error` border tint + tooltip. Banner shows error text + dismiss ✕. |
| Multi-ACE, non-active ACE has slot whose status we can't see | only `gate_status` for active ACE in state | Non-active blocks render at `opacity: 0.7`; their slots are "unknown" (dashed) unless they appear in `head_source`, in which case we know the slot is filled and can derive its color. |
| User switches tabs mid-animation | `setView("dashboard")` hides Hardware view | CSS animations continue running while hidden (per spec). On tab return the user sees whatever state the animation has reached. No pause/resume logic; this is acceptable because the animation duration (2.2 s) is short relative to typical tab dwell time. |
| `prefers-reduced-motion: reduce` | media query | All keyframes replaced with static accents; tube fill becomes a 0.4 s transition. |
| `device_count === 0` | initial / disconnected state | Tab body shows a centered "Waiting for ACE…" message; no SVG built. |

## 9. Testing

- **Backend (pytest)**: zero changes. The Hardware tab adds no new
  endpoint, no new state field, no new event type.
- **Visual regression**: append `"hardware"` to the
  `READ_ONLY_TABS = ["dashboard", "activity", "dryer", "config", "diag"]`
  list at `multiace_web/tools/visual_regression.py:25`. The script
  already runs read-only at 1280×900 and 390×844 viewports; the new
  tab is captured in both per run. Per the project's read-only rule
  the script does NOT click any Load/Unload, /api/dry, or Save &
  Restart action. Also update the docstring at line 3 to include
  "Hardware" in the captured-tab list.
- **Manual e2e (Playwright, browser)**: open `/multiace/#hardware`,
  verify the SVG renders, verify the button `data-cmd` attributes
  match macros, verify the status banner reflects state. Per the
  project's e2e rule, exercise the golden path against a real printer
  with the live state log before declaring complete.
- **Per-PR check**: a "before / after" Dashboard-tab visual regression
  comparison to confirm the change is additive (no Dashboard pixel
  drift).
- **JS unit testing**: not added. The project has no JS test framework;
  introducing one for one tab is unjustified scope. The diff/animation
  lifecycle in `hardware-twin.js` is small enough to validate by hand
  against the visual regression snapshots.

## 10. Versioning and rollout

- Version: `0.6.0` (next minor — new user-visible feature). Cache-bust
  query params on every static asset bumped to `?v=0.6.0`.
- Rollout: install the new release per the standard
  `bash /tmp/multiace_web/install/install_web.sh` flow. The tab
  appears immediately on next page load. Watchdog and init.d unchanged.
- Rollback: revert by deleting `static/hardware-twin.js`, reverting the
  three `index.html` lines, the three `app.js` lines, and the
  `style.css` block. Dashboard is unaffected end to end.

## 11. Known limitations

- **Inactive ACE slot fill is unknown.** `gate_status` is per the
  active ACE only. For non-active ACEs we infer fill state only from
  `head_source` references, which means a filled-but-unmapped slot in
  a non-active ACE renders as "unknown". This is an existing
  data-model limitation, not a Hardware-tab regression.
- **Default slot↔toolhead mapping assumed.** `ACE_LOAD_HEAD HEAD=n
  SLOT=m` with `m ≠ n` is technically possible but unsurfaced in any
  UI. The Hardware tab follows the standing default
  (slot N → toolhead N) and would need a small extension to visualize
  arbitrary mappings if those ever ship.
- **Print-in-progress button gating uses `state.swap_in_progress` only**,
  matching the existing Dashboard. If a future change tightens that gate
  to also consider `printState.state`, both tabs gain the protection
  together.

## 12. Resolved details

These were left as judgment calls and are now committed:

- **Tube routing for 4 ACEs**: tubes from each ACE's slot N go straight
  down to coupler N at the same x. Higher ACEs' tubes pass visually
  behind lower ACE bodies via z-order. No lateral offset. If 4-ACE
  setups feel cramped during real-world testing, a future patch can
  introduce a small per-ACE x-offset; the spec does not pre-commit to
  one.
- **Source sub-label inside the toolhead rectangle**: hidden when
  viewport width < 480 px (i.e., the printer-screen layout shows just
  the T1/T2/T3/T4 label; desktop shows "ACE A · Slot 3" beneath).
- **"Extruding" green tint during a print**: applied to the toolhead
  rectangle only, not the active source's tube. The tube color is
  reserved for filament identity; tube state is driven by load/unload
  events, not by the print extrusion clock.
