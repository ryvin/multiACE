# Post-processor planner improvements — port `compute_optimal_remap` + `compute_layer_swap_plan` from decay71

**Status:** approved 2026-05-09 (in-chat); ready for implementation plan.
**Branch:** `feat/postprocessor-planner-improvements` (off `main` after this spec lands).
**Scope:** Add two opt-in planning algorithms to `multiace_web/tools/multiace_postprocess.py`, both ported from decay71's `multiace/tools/post_process_virtual_toolheads.py` with adaptations to ryvin's manifest-driven architecture. Plus a "needs N ACEs" diagnostic in the web Print queue tab.

## Goal

Today's `multiace_postprocess.py` uses one swap-planning algorithm: greedy-furthest-future eviction. It's correct (caught and fixed an inverted-direction bug at `9b59cf0`) but it's not optimal — it minimizes swaps locally, not globally. Decay71's post-processor has two algorithms that meaningfully improve on this:

- **`compute_optimal_remap`** — re-assigns the slicer's logical T-indices to physical heads/slots BEFORE planning swaps, choosing the assignment that minimizes total swap count globally. Often eliminates 1-3 swaps on complex prints.
- **`compute_layer_swap_plan`** — layer-aware planning that reasons about per-layer color usage. Emits an "aces_needed: N" diagnostic that tells the operator how many ACEs would be required to print swap-free.

This spec ports both as opt-in flags (`--optimize` and `--layer`) and surfaces the `aces_needed` diagnostic in the web Print queue tab as a status banner.

## Provenance

decay71's `multiace/tools/post_process_virtual_toolheads.py` (~809 lines) on `decay71/main` — the algorithms live at:
- `compute_optimal_remap` around line 372
- `compute_layer_swap_plan` around line 245

These aren't in the public ace.py firmware — they're operator-side gcode planning. Pure Python; portable.

## Architecture

Three components:

### 1. `multiace_postprocess.py` gains two new functions + CLI flags

- New: `compute_optimal_remap(tools, gcode_lines)` — given the slicer's filament metadata + gcode, return a permutation of `tools` that minimizes swap count when fed into `plan_swaps`.
- New: `compute_layer_swap_plan(resolutions, gcode_lines)` — return `{layers: [{layer: N, aces_needed: M, swaps: [...]}], aces_needed_max: M}`. The operator-visible value is `aces_needed_max`: "this print needs M ACEs to run swap-free; you have N."
- New CLI: `--optimize` runs `compute_optimal_remap` BEFORE `plan_swaps`. `--layer` switches to layer-aware planning. Both opt-in; default behavior unchanged.

### 2. Sidecar JSON gets new fields

Schema v2 (additive — old readers ignore unknown fields):
```json
{
  "schema": 2,
  ...,
  "planning": {
    "algorithm": "greedy" | "optimal" | "layer",
    "swap_count": 4,
    "aces_needed_max": 3,
    "remap_applied": true | false,
    "layer_breakdown": [{"layer": 12, "aces_used": 3, "swaps_at_layer": 1}, ...]
  }
}
```

The `aces_needed_max` field is the new operator value — surfaced in the web UI as a banner.

### 3. Web Print queue tab gains a "needs ACEs" status banner

When `sidecar.planning.aces_needed_max > device_count`, render a yellow banner above the resolution table:

> ⚠ This print needs **3 ACEs** for swap-free printing; you have **2**. Expect **4 swaps** during printing (~24 min added).

When `aces_needed_max <= device_count`, no banner (or a green "no swaps needed" badge if `aces_needed_max == max(used_tools_per_layer)`).

## Convention pinning

- `tools` parameter in both new functions is `list[ToolMeta]` (existing dataclass)
- `gcode_lines` parameter is `list[str]` (existing convention)
- New planning algorithms are NOT default — operator opts in via CLI flag
- Sidecar schema bumps to v2 (additive — old `multiace_postprocess` clients still read v1 fields fine)

## Components

### `multiace_web/tools/multiace_postprocess.py` — additions

**Functions to add** (after existing `plan_swaps`):

```python
def compute_optimal_remap(tools: list[ToolMeta],
                          gcode_lines: list[str]) -> list[ToolMeta]:
    """Return a permutation of `tools` that minimizes total swap count.

    Algorithm sketch (port from decay71 post_process_virtual_toolheads.py:372):
      1. Build a "co-occurrence graph" — for each pair of tool indices,
         count how often they appear in the same layer.
      2. Tools that frequently co-occur should be assigned to different physical
         heads (avoids swaps when both are used in the same layer).
      3. Tools that never co-occur can share a head (swap once between layer
         boundaries).
      4. Greedy bipartite matching: assign tools to head slots minimizing
         expected swap count given the gcode's actual usage pattern.

    Returns the input list with .index fields renumbered to a different order.
    The original tool semantics (type, color) are preserved; only the index
    changes.
    """
    ...


def compute_layer_swap_plan(resolutions: list[ToolResolution],
                            gcode_lines: list[str]
                            ) -> dict:
    """Layer-aware swap planning.

    Returns:
      {
        "layers": [{"layer": int, "aces_used": int, "swaps_at_layer": int}],
        "aces_needed_max": int,   # max aces_used across all layers
        "swaps": [SwapEvent, ...] # same shape as plan_swaps output
      }
    """
    ...
```

**CLI surface** in `main()`:

```python
parser.add_argument('--optimize', action='store_true',
                    help='Re-assign T-indices to minimize swap count before planning.')
parser.add_argument('--layer', action='store_true',
                    help='Use layer-aware planner; emit aces_needed diagnostic.')
```

**Wire-up in `main()`**:

```python
if args.optimize:
    tools = compute_optimal_remap(tools, lines)
    # resolutions need to be re-built per the new tool order
    resolutions = match_tools(tools, slots_resp)

if args.layer:
    plan = compute_layer_swap_plan(resolutions, lines)
    swaps = plan["swaps"]
    aces_needed_max = plan["aces_needed_max"]
    layer_breakdown = plan["layers"]
else:
    swaps = plan_swaps(resolutions, lines)
    aces_needed_max = None
    layer_breakdown = None

# write_sidecar gets the new planning metadata
write_sidecar(gcode_path, resolutions, swaps, status, reason,
              planning={
                  "algorithm": "layer" if args.layer else ("optimal" if args.optimize else "greedy"),
                  "swap_count": len(swaps),
                  "aces_needed_max": aces_needed_max,
                  "remap_applied": args.optimize,
                  "layer_breakdown": layer_breakdown,
              })
```

### `write_sidecar` signature update

Add `planning: dict | None = None` keyword arg. If provided, embed under `data["planning"]`. Existing callers (web's revalidate endpoint) pass `None` and get v1 sidecars; the post-processor passes the new dict and gets v2.

### `multiace_web/src/multiace_web/server.py` — `/api/print_queue/{gcode}/revalidate`

When the revalidate endpoint re-runs planning, it should respect the original sidecar's `planning.algorithm` (re-validate uses the same flavor). If the original sidecar is v1 (no planning field), default to `greedy`. If v2 with `algorithm: "optimal"`, re-run with optimal; same for `layer`.

This means the server-side `_revalidate_gcode` helper needs to accept an `algorithm` parameter and pass it through. Small change.

### Web Print queue tab — `multiace_web/src/multiace_web/static/app.js`

In `renderPrintQueue`, after rendering the resolution table, add a planning banner when `item.planning && item.planning.aces_needed_max > device_count`:

```javascript
if (item.planning && typeof item.planning.aces_needed_max === 'number') {
  const need = item.planning.aces_needed_max;
  const have = state.device_count || 1;
  if (need > have) {
    // Render yellow warning banner
    return `<div class="pq-warn-banner">
      ⚠ This print needs <strong>${need} ACEs</strong> for swap-free printing;
      you have <strong>${have}</strong>. Expect <strong>${item.planning.swap_count}
      swaps</strong> during printing (~${item.planning.swap_count * 6} min added).
    </div>`;
  } else if (item.planning.swap_count === 0) {
    // Green "no swaps" badge
    return `<span class="pq-no-swaps-badge">✓ No swaps required</span>`;
  }
}
```

CSS additions (small):

```css
.pq-warn-banner { background: var(--warn-bg, #fff3cd); color: var(--warn-fg, #856404);
  border-left: 3px solid var(--warn, #f59e0b); padding: 8px 12px; margin: 6px 0;
  border-radius: 4px; font-size: 0.88em; }
.pq-no-swaps-badge { background: var(--ok-bg, #d4edda); color: var(--ok-fg, #155724);
  padding: 2px 8px; border-radius: 10px; font-size: 0.82em; font-weight: 600;
  margin-left: 8px; }
```

### Tests

**Pytest:** add `multiace_web/tests/test_postprocess_optimal_remap.py` and `test_postprocess_layer_plan.py`. Each ~6-8 tests covering:

For `compute_optimal_remap`:
- Identity case (already optimal): no remap needed
- Two tools with high co-occurrence: assigned to different heads
- Tools with no co-occurrence: can share a head
- 8-tool stress test against a synthetic gcode with known optimal answer
- Result invariant: returned list is a permutation (same length, same `(type, color)` set)

For `compute_layer_swap_plan`:
- Single-layer print: aces_needed = number of distinct tools used
- Multi-layer print, color reveal pattern: aces_needed_max correctly captures peak
- Layer comments parsed correctly (`; --- layer N ---`)
- Empty gcode: returns sensible defaults

**Snapshot tests:** add fixtures for known-good optimal-remap output on the existing 8-color sample gcode.

**Integration test:** `--optimize` + `--layer` flags both set should run cleanly (no conflict; layer planning consumes the remapped tool order).

## Out of scope

- Changing the default planner from greedy. Both new algorithms are opt-in via CLI flag.
- Multi-printer planning (e.g. "split this print across two printers"). Single-printer scope only.
- Mid-print planning re-evaluation. The plan is generated once at slicing time.
- Auto-detecting which algorithm to use. Operator picks via CLI flag.
- Re-running optimal-remap if loadout changes (re-validate uses the original algorithm choice).

## Testing

**Automated (pytest):**
- `compute_optimal_remap` correctness via permutation + co-occurrence assertions
- `compute_layer_swap_plan` correctness via layer-by-layer aces_used assertions
- Sidecar schema v2 round-trips through write/read
- `_revalidate_gcode` honors the original sidecar's algorithm choice

**Live hardware (manual smoke):**
- Slice an 8-color print with `--optimize` flag — sidecar shows `algorithm: "optimal"` and `swap_count` lower than the same print without the flag
- Slice with `--layer` flag — sidecar shows `aces_needed_max` matching the eyeball count from the slicer's preview
- Web Print queue: banner appears when `aces_needed_max > device_count`; disappears when device_count is increased (e.g. ACE B comes online)
- Re-validate preserves the algorithm choice

## Risks and follow-ups

1. **Algorithm equivalence with decay71** — they may have ported a different reference algorithm; if our re-implementation produces different swap counts on the same input, it suggests a real algorithmic difference worth investigating. Cross-test against decay71's output on a shared gcode fixture (their post-processor is invocable via Python; we can run both and compare).

2. **Planning algorithm convergence** — if `--optimize` proves universally better than greedy, consider making it the default in v2. Don't ship that without operator-side data showing it's reliably better.

3. **Layer breakdown size** — for prints with hundreds of layers, the sidecar's `layer_breakdown` array could be large. Consider compressing (only emit layers where swaps occur or where `aces_used` changes).

4. **`compute_optimal_remap` complexity** — bipartite matching is polynomial but could be slow on very tool-heavy prints (>16 tools). Decay71's print scale is similar to ryvin's (8 tools max), so this is unlikely to bite, but worth measuring.

5. **Cross-fork sync** — if decay71 evolves their algorithms after we port, we'd want to track changes. Add a `# Source: decay71/multiACE post_process_virtual_toolheads.py:LINE` comment at each ported function.

## Self-review

**Placeholder scan:** No "TBD". The actual algorithm bodies are sketched (port from decay71); the implementation plan will read the source and produce concrete code.

**Internal consistency:** Sidecar schema v2 referenced consistently; backward-compat with v1 readers preserved. CLI flag names match across post-processor + revalidate endpoint.

**Scope check:** Two algorithms + one CLI surface + one web banner. Single feature; single PR-tree. Could ship as two commits (algorithms first, web banner second).

**Ambiguity check:** "Greedy" vs "optimal" vs "layer" algorithm choices defined. Banner trigger condition `aces_needed_max > device_count` is concrete. Re-validate algorithm preservation defined.
