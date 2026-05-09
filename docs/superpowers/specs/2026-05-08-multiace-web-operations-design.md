# multiACE web — per-head Operations (Load/Unload + smart-swap)

**Status:** approved 2026-05-08 (in-chat); ready for implementation plan.
**Branch (web work):** `feat/multiace-web-operations` (off `main`, after this spec lands).
**Branch (firmware dependency):** `feat/swap-park` (firmware-side ACE_PARK_HEAD; spec at `docs/superpowers/specs/2026-05-07-swap-park-design.md`).
**Scope:** Web UI changes only. Firmware changes (ACE_PARK_HEAD) live in their own spec/plan.

## Goal

Add per-head Unload to the slot-row chevron menu, and make the existing `→ T<n>` Load smart: when the target head is loaded or parked, auto-chain the appropriate retract + load, with explicit handling of all four head states.

## Architecture

Single-file frontend change in `multiace_web/src/multiace_web/static/app.js` + small CSS addition in `style.css`. No backend changes. All multi-step ops sequenced via Moonraker `gcode/script` from the frontend. Firmware capability detection happens at app init.

**Phased shipping:** Phase 1 ships option (a) — Unload + same-ACE smart-swap via full unload — plus a feature-flagged option-(b) path. Phase 2 activates option (b) (cross-ACE park-then-load) when the swap-park firmware verb is detected at runtime. Both phases are the same web PR; the option-(b) path activates automatically on next page load after firmware deploys.

## Convention pinning

Throughout this spec, slot/ACE labels are derived as in the FilamentHub deep-link picker:
- ACE letter: `String.fromCharCode(65 + ace_index)` (0→A, 1→B, …)
- Slot label: `printers.json slot_labels[slot_index]` if available, else `String(slot_index + 1)` (1-based)

Example: `head_source[1] = {ace_index: 1, slot: 0}` renders as `B1`.

## Head-state matrix

Four head states. Smart-swap and Unload behavior is defined per state:

| State | Definition | Unload menu | Smart-swap target action |
|---|---|---|---|
| **empty** | `head_source[N] === null`, sensor=False | n/a — no source slot to host the menu item | direct `ACE_LOAD_HEAD` (no chain) |
| **loaded same-ACE** | `head_source[N].ace_index === target.ace`, `parked` falsy | source slot shows `↗ Unload T<n>` | chain `ACEC__Unload_T<n>` → `ACE_LOAD_HEAD` |
| **loaded cross-ACE** | `head_source[N].ace_index !== target.ace`, `parked` falsy | source slot shows `↗ Unload T<n>` | If swap-park available: chain `ACE_PARK_HEAD HEAD=<n>` → `ACE_LOAD_HEAD`. Else: same as loaded same-ACE (full unload). |
| **parked** | `head_source[N].parked === true` | source slot shows `↗ Unload T<n>` (full retract from park to gate) | Conservative v1: treat as **loaded same-ACE** branch — chain `ACEC__Unload_T<n>` → `ACE_LOAD_HEAD` regardless of source/target ACE relationship. Park-aware swap-back is a v1.5 follow-up requiring a firmware un-park verb. |

Edge case — **bookkeeping-empty**: `head_source[N]` is set but `e<N>_filament` reads False. Treated visually as "loaded" (the menu item shows) but the slot row gets a `⚠` tooltip "Sensor disagrees with bookkeeping. Unload may fail; recovery: `ACE_MARK_HEAD_UNLOADED HEAD=<n>` from gcode console." Operator can still click Unload — failure is non-destructive and the audit log captures the error.

## UX

### 1. Per-source-slot Unload menu item

- Chevron menu prepends `↗ Unload T<n>` only when the slot is the source of head N (any of: loaded same-ACE, loaded cross-ACE, parked, or bookkeeping-empty per matrix above).
- Single-tap → `ACEC__Unload_T<n>`. UI-side lock during execution; on completion the item disappears on next state poll.

### 2. Smart-swap on chevron-menu Load

`→ T<n>` click → branch per the head-state matrix above.

### 3. Toast confirm — only when displacing

Position: **uses the existing multiACE web toast/notification system** if present (the autodry feature has one — to be verified by implementer). If no system exists, the implementation plan adds one as a small dependency, defaulting bottom-center on mobile (390×844) and bottom-right on desktop.

Toast text and timing per scenario:
- **Same-ACE or fallback path**: `"Swap A2 → B1 (~6 min) — Cancel (3..2..1)"`
- **Cross-ACE with swap-park**: `"Swap A2 → B1 (~4 min) — Cancel (3..2..1)"`
- **Parked target**: `"Swap (parked A2) → B1 (~6 min) — Cancel (3..2..1)"`

3-sec cancel window. Empty-target Load skips the toast (existing fire-and-go preserved).

### 4. Cancel and "navigate away" semantics

The 3-sec cancel window aborts the swap if any of these fire:
- Cancel button click
- Tab close (`window.beforeunload` listener installed for the duration of the toast)
- Tab/window hidden for >2 sec (`document.visibilitychange` observation)
- Internal multiACE-web tab change (Dashboard → Activity, etc.) — handled in the existing tab-router

After the 3-sec window passes, the toast switches to "Swap in progress — abort not available" and the gcode chain begins. Cancel and navigate-away no longer abort.

### 5. UI-side smart-swap lock

```js
state.smartSwapPending = {
  head: N,
  leg: 1 | 2,
  startedAt: <ts>,
} | null
```

Set when the toast countdown completes and leg 1 issues. Persists across both legs. Cleared on success of leg 2 or on final failure (after Retry exhausted). All chevron menus across all heads gate on:
- `print_stats.state in {'standby','complete','cancelled','error'}`
- `state.swap_in_progress === false` (firmware-side per-call flag)
- `state.smartSwapPending === null` (web-side cross-leg lock)

Disabled state: grayed item + tooltip explaining which gate is failing.

### 6. State-aware retry

Toast on per-leg failure shows:
- `Retry leg <n>` button (re-issues only the failed leg)
- `Dismiss` button (clears `smartSwapPending` and lets operator manually recover via gcode console)

If leg 1 (park/unload) failed: clicking Retry re-issues leg 1.
If leg 2 (load) failed: clicking Retry re-issues only the load.

After 2 consecutive Retry failures on the same leg, the toast offers `Dismiss + show recovery hints` (links to ACE_CLEAR_HEADS / ACE_MARK_HEAD_UNLOADED docs).

### 7. Audit story

No new firmware audit events. No client-side "swap" chip in v1 — adjacent same-head Activity entries aren't reliably a swap (could be unrelated user actions hours apart). Operators read the two events sequentially. Future correlation (timestamp delta + action sequence) is a v1.5 polish.

### 8. Parked-state visual

Independent of this spec but coupled enough to mention: the dashboard's per-slot row needs a "parked" visual when `head_source[N].parked === true`. The swap-park spec (`docs/superpowers/specs/2026-05-07-swap-park-design.md` §2) declares the dashed-border + "parked" label rendering for the source slot. **This spec depends on that visual existing** — implementer should verify it's in place; if not, this spec's plan adds it as a small backfill.

## Firmware capability detection

At app init (or on first `openAceLoad` call, whichever is implemented), the web probes Moonraker for `ACE_PARK_HEAD` availability:

```javascript
async function detectSwapParkAvailable(printerId) {
  try {
    const r = await fetch(`/printer/${printerId}/printer/gcode/help`);
    if (!r.ok) return false;
    const data = await r.json();
    const cmds = data?.result || {};
    return 'ACE_PARK_HEAD' in cmds;
  } catch { return false; }
}
state.swapParkAvailable = await detectSwapParkAvailable(printerId);
```

Cached per session. `state.swapParkAvailable` gates the cross-ACE-with-park branch in the head-state matrix. Cache invalidated on Klipper-restart events (firmware-side `notify_klippy_ready` WebSocket signal — already observed by the multiACE web).

If `/printer/gcode/help` doesn't enumerate ACE_PARK_HEAD reliably (some Klipper versions only list registered macros, not core commands), the implementation plan picks an alternative probe: query `/printer/objects/list` for `gcode_macro ACEC__Park_T0` (the convenience macro added in the swap-park spec).

## Gating

All actions disabled when:
- `print_stats.state` is `printing` or `paused`
- Firmware `swap_in_progress === true`
- `state.smartSwapPending !== null` (cross-leg lock per §5)

Disabled state: grayed menu item + tooltip identifying the active gate.

## Out of scope (explicit)

- New "Operations" tab (option c — future scope)
- Head-first action surface (right-click toolhead tile)
- ACE_MARK_HEAD_LOADED / ACE_CLEAR_HEADS / ACE_SWITCH UI exposure
- Per-leg confirmation between park/unload and load (one toast covers the whole swap)
- Retroactive swap-correlation chips on historical Activity entries
- Park-aware swap-back (firmware un-park verb required first; v1.5)
- Mobile-specific UX polish — desktop-first; mobile is best-effort, no broken layouts at 390×844
- **Mid-print swap operations** — paused-state actions remain disabled in v1; supporting M0-pause-driven workflows is a follow-up that depends on this spec
- Operations against multiACE web pre-autodry deploys without a toast system — implementation plan adds toast dependency only if absent today

## Testing

Per project's e2e Playwright rule:

**Automated (Playwright):**
- Chevron menu shows Unload only on source slots (state-driven assertions across head-state matrix)
- Toast appears with correct text per scenario; Cancel button aborts; navigate-away aborts
- 3-sec timing tested with `page.clock()` to avoid flake
- UI-side lock prevents second swap from initiating mid-chain
- Disabled-state gating across the three gates (print state, firmware flag, web lock)

**Live hardware (manual smoke):**
- Each branch of the head-state matrix (empty, loaded same-ACE, loaded cross-ACE, parked) on Davinci-U1 with both ACEs online
- Cross-ACE smart-swap with swap-park available: timing measurement (~4 min target)
- Fallback path with swap-park firmware not deployed: timing measurement (~6 min target)
- Mid-swap browser close → reopen → state poll reflects half-swap correctly

**Pre-flight:** Davinci-U1 must report `device_count=2`. Print state must be safe.

## Risks and follow-ups

1. **Park-aware swap-back** — v1.5 requires a firmware un-park verb. Until then, parked target falls through to full-unload path (slower than necessary).
2. **Bookkeeping-empty recovery** — Tier-1 (`ACE_MARK_HEAD_UNLOADED`) and Tier-2 are gcode-console-only in this spec. Recovery tab is future scope.
3. **Toast subsystem** — if absent today, implementation plan adds it. Avoid bloating scope by reusing the existing notification rendering (autodry has one — to be verified).
4. **swap-park firmware PR sequencing** — if web Phase 1 ships before swap-park firmware merges, all cross-ACE swaps run the slower fallback path. Acceptable; users see correct behavior, just longer timing.
5. **Multi-tab race** — two browser tabs open to multiACE web could each issue a swap simultaneously. UI-side lock is per-tab; firmware `swap_in_progress` catches the second on the call. Toast on the second tab shows "swap in progress" gate. No data corruption; UX surprise.
6. **Mid-print swap UX gap** — explicitly out of scope for v1 (see Out of Scope). 8-color slicer integration needs to either avoid mid-print swap UI dependence (post-processor injects ACE_PARK/ACE_LOAD into the print stream — runs as part of `printing` state, no UI gesture) or motivate a follow-up spec for paused-state swap actions. Deferred to a separate slicer-integration spec.

## Spec self-review

**Placeholder scan:** No "TBD"/"TODO"/"fill in later." The detection mechanism is two named candidates with the implementation plan picking one — concrete enough.

**Internal consistency:** Head-state matrix is the source of truth; UX1 (Unload), UX2 (smart-swap), UX6 (retry), Gating all reference it consistently.

**Scope check:** Single feature, single PR (web side); firmware dependency is its own existing spec/branch.

**Ambiguity check:** "Navigate away" disambiguated. "Retry" semantics state-aware. Time estimate scenario-branched. Detection mechanism named with a fallback.
