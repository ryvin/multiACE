# Swaptimizer (Phase 4) — Design Spec

**Date:** 2026-05-17
**Task:** #66
**Branch (to be created):** `feat/swaptimizer`
**Target file:** `multiace_web/tools/multiace_postprocess.py`

## Goal

Reduce the number of physical filament-swap operations that the
Snapmaker U1 has to perform when printing a multi-color G-code job
produced by Orca Slicer. Two opt-in optimizations, each independently
useful, designed to compose:

- `--optimize`: Tn aliasing. When two tool indices share `(color, type)`
  and one is already loaded, rewrite occurrences of the other.
- `--layer`: pre-layer reload. For each layer using ≤4 distinct tool
  indices, pre-load the 4 toolheads at the layer boundary so no
  mid-layer load is needed.

Default behavior unchanged when flags absent. Existing
`multiace_postprocess.py` pipeline (parse Orca header → query printer
for slot mapping → `match_tools` → `plan_swaps` → `rewrite_gcode` →
sidecar `.multiace_map`) is preserved end-to-end.

## Cost model (critical to get right)

The Snapmaker U1 has **4 dedicated extruders** (T0-T3), one per
physical toolhead. Tool change between two loaded heads activates a
different physical hotend — **no purge tower, no filament waste**,
just gantry motion to engage the new head.

The expensive operations are:

- **Slot change on a loaded head** (~20-30s): retract current filament
  back to the ACE, ACE_SWITCH if cross-ACE, feed new filament from ACE
  through bowden, heat to print temperature.
- **`ACE_LOAD_HEAD` at layer start**: same cost, just executed before
  the layer's first extrusion instead of mid-layer.

Tool change between two already-loaded heads is essentially free
(activation only, no filament motion).

`--optimize` reduces the number of slot-change events.
`--layer` shifts unavoidable slot-changes from mid-layer (which pauses
extrusion) to between-layer (where the printer is already in a
non-extruding state for the layer transition G0 move). Net wall-clock
savings depend on print structure: prints with stable color sets
across many layers benefit most; prints with rapidly-changing palettes
of >4 distinct tools per layer benefit least.

## `--optimize` (Tn aliasing)

### Algorithm

Single linear scan of the gcode lines. Maintain
`loaded: dict[head_idx → tool_idx]` representing which tool index is
currently considered "loaded" on each head (initialized from the first
4 distinct resolved tools, matching `plan_swaps`'s greedy semantics).

For each `Tn` line encountered:

1. Look up `(color, type)` of tool `n` from the resolved tools list.
2. Search `loaded` for any `Tm` with `m != n` and matching `(color, type)`.
3. If found: rewrite the line `Tn` → `Tm` in-place; record alias in
   sidecar.
4. If not found: let `plan_swaps`'s existing logic decide which head
   gets reassigned to tool `n`; update `loaded[that_head] = n`.

Aliasing changes nothing about which physical slot is loaded; it only
changes which slicer-emitted Tn label resolves to that slot. From the
printer's perspective, fewer slot changes happen.

### Correctness assumptions

- Spools matching `(color, type)` are interchangeable for print
  quality purposes. Each toolhead has its own dedicated hotend so
  there's no color cross-contamination concern; the assumption is
  about user intent (whether the user cares which specific spool
  prints which segments).
- The optimizer does NOT compare spool_id, vendor, lot number, or
  RFID metadata. Users requiring per-spool identity (regulatory
  compliance, color batch matching for large prints) should leave
  this flag off.

This assumption is documented in the CLI help and the
`.multiace_map` sidecar.

### Unresolved tools

Tools with `match_quality == "none"` are skipped — they have no
`color`/`type` to alias against. Aliasing only applies between fully-
resolved tools. Matches the existing `plan_swaps` behavior for
unresolved tools.

### Output

- Rewritten gcode (in-place).
- Sidecar `.multiace_map` gets a new `optimize:` section listing each
  alias as `Tn@line_L → Tm` so operators can verify.

## `--layer` (pre-layer reload, ≤4 colors)

### Algorithm

1. Parse layer boundaries from `; --- layer N ---` comments
   (already supported by `plan_swaps`).
2. For each layer (range of lines):
   a. Collect `distinct_tools`: the set of tool indices appearing in
      `Tn` lines within the layer.
   b. If `len(distinct_tools) > 4`: skip — existing `plan_swaps`
      output is unchanged for this layer.
   c. If `len(distinct_tools) ≤ 4`: compute head→tool assignment:
      - Tools already loaded on the right head: keep.
      - Tools needed but not loaded: assign to a head whose currently-
        loaded tool isn't in `distinct_tools` (i.e., evictable).
      - Tools needed and already loaded on the wrong head: leave them
        (no reassignment needed; the existing rewriter handles it
        when the Tn appears).
   d. Emit `ACE_LOAD_HEAD HEAD=h ACE=a SLOT=s` lines just after the
      layer marker for each new assignment.
   e. Mark those `(head, tool)` pairs as "pre-loaded for this layer"
      so the existing `rewrite_gcode` step suppresses redundant
      mid-layer `ACE_LOAD_HEAD` insertions for the same pair.
3. Mid-layer `Tn` keywords are **left in place** — they just activate
   the (already-correctly-loaded) head.

### Edge cases

- **Single-layer file (no layer markers)**: no-op; layer boundaries
  not detectable.
- **Layer with no Tn lines**: no-op for that layer.
- **Slicer pre-loads ahead of us**: detect by looking for
  `ACE_LOAD_HEAD` already present at the layer boundary; don't double-
  emit.
- **Exactly 4 distinct + 4 heads, all currently correct**: no inserts;
  the optimizer reports "layer L: optimal already".
- **Just-over-4 distinct**: skip the layer (no benefit from partial
  pre-load when we'd still need a mid-layer swap).

### Output

- Modified gcode with `ACE_LOAD_HEAD` inserts at layer starts.
- Sidecar `.multiace_map` `layer:` section lists each layer's
  pre-loads as `layer L: head h ← (ACE a slot s)`.

## Interaction (both flags together)

Run order: `--optimize` first, then `--layer` operates on the post-
aliased gcode. Rationale:

- Aliasing can collapse a 5-distinct-tool layer to 4, enabling
  `--layer` to optimize it.
- Aliasing is purely a Tn-label rewrite; it doesn't change which
  physical slots are loaded, so it can't interfere with `--layer`'s
  pre-load decisions.

When both flags are present, both passes run and both sidecar sections
are populated.

## CLI

Existing entry signature follows Orca Slicer convention:
```
python3 multiace_postprocess.py;{output_filepath}
```

New flags inserted before the semicolon-separated args:
```
python3 multiace_postprocess.py --optimize --layer;{output_filepath}
```

Either flag alone, both, or neither. `--help` lists both with one-
sentence descriptions plus a link to this spec.

## Sidecar `.multiace_map` schema (additions)

Existing sidecar gets two new sections appended at end:

```
optimize: <n> aliases applied
  Tn@line_L → Tm  ; reason: color+type match, Tm already loaded on head h
  ...

layer: <n> layers pre-loaded ([m] already-optimal, [k] skipped >4 distinct)
  layer L: head h ← (ACE a slot s)
  ...
```

Both sections are present when the corresponding flag was passed,
even if zero changes were applied (so operators can confirm the pass
ran).

## Testing

Two new test files alongside the existing four:

### `tests/test_postprocess_optimize.py`

- Same-color alias: T0 black PLA on head 0, T5 black PLA appears →
  rewrite T5 → T0.
- Different colors: no rewrite.
- Different types: no rewrite (e.g., black PLA vs black PETG).
- Multi-alias chain: T5 → T0 → T5 → T0 alternation in input only
  produces one alias decision.
- Unresolved tool: skip without error.
- No Tn lines: no-op.
- Aliasing through head reassignment: T4 first appears, replaces T0
  on head 0; then T0 reappears later, becomes the alias target.

### `tests/test_postprocess_layer.py`

- 4-distinct-tool layer with partial pre-load: 2 inserts.
- 4-distinct-tool layer with all-correct loads: 0 inserts, reported
  as "optimal".
- 5-distinct-tool layer: 0 inserts, layer reported as skipped.
- Single-layer file: no inserts.
- No-Tn layer (e.g., wipe-only layer): no inserts.
- Slicer pre-emptively inserts `ACE_LOAD_HEAD`: don't double-emit.
- Layer boundary edge cases: empty layer, layer with only G0 moves.

### Combined integration tests

- `--optimize` + `--layer`: 5-tool layer collapses to 4 via aliasing,
  then pre-loads.
- Real-shape gcode fixture (anonymized slice of a multi-color test
  print): full pipeline including header parse, slot resolution,
  optimization, rewrite, sidecar.

Total: 20-30 tests including 3-5 integration cases. Preserve all
existing 4 test files' behavior.

## Out of scope (explicit follow-ups)

- **Approach B (lookahead slot reassignment)**: not picked. Big gains
  per the original plan estimate but bigger algorithmic and test
  surface.
- **Belady eviction (Approach E)**: not picked. Would help layers
  with >4 distinct tools by minimizing mid-layer churn. Defer until
  we see real prints where `--layer` reports many skipped layers.
- **Cross-print optimization** (cache analysis across queued jobs):
  out of scope for Phase 4.

## Hardware verification

Both flags are gcode rewrites — verifiable by diff of input vs
output gcode + sidecar inspection. No runtime / printer verification
required for landing the code. Real-world value verification
(actual wall-clock savings) is observation, not test.

## Risk register

| Risk | Mitigation |
|---|---|
| Aliasing silently changes which spool prints which segments | Default off; doc note in CLI help and sidecar |
| Pre-load at layer 0 causes startup delay | Documented in sidecar; user can compare time-to-first-extrusion before/after |
| Edge cases in layer-boundary detection (unusual slicer outputs) | Skip layers we can't parse cleanly; never emit broken gcode |
| Interaction bug between `--optimize` and `--layer` | Sequential passes; aliasing doesn't change loaded-slot state; integration tests cover combined runs |
