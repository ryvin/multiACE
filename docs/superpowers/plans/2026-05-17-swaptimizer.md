# Swaptimizer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two opt-in optimization flags (`--optimize` for Tn aliasing, `--layer` for pre-layer reload of ≤4-color layers) to `multiace_web/tools/multiace_postprocess.py`. Existing pipeline unchanged when flags absent.

**Architecture:** Two pure functions added to `multiace_postprocess.py` (`optimize_aliases`, `prelayer_reload`), each returning `(rewritten_lines, decisions)`. A new `main()` with argparse wires the flags into the existing parse→query→match→plan→rewrite→sidecar pipeline. Sidecar gains two new sections (`optimize:` and `layer:`) for operator verification.

**Tech Stack:** Python 3.8+ stdlib only (no pip deps — script must run in any Python env). Existing dataclasses (`ToolMeta`, `Candidate`, `ToolResolution`, `SwapEvent`) reused; one new dataclass per pass for decision records.

**Spec:** `docs/superpowers/specs/2026-05-17-swaptimizer-design.md`

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `multiace_web/tools/multiace_postprocess.py` | Modify | Add `optimize_aliases()`, `prelayer_reload()`, `AliasDecision`, `LayerDecision`, `main()`, sidecar additions |
| `multiace_web/tests/test_postprocess_optimize.py` | Create | 7 tests for `--optimize` path |
| `multiace_web/tests/test_postprocess_layer.py` | Create | 7 tests for `--layer` path |
| `multiace_web/tests/test_postprocess_integration.py` | Create | 2-3 tests combining both flags + end-to-end |

Total new code: ~250-350 lines in `multiace_postprocess.py`; ~400 lines of tests.

---

## Task 1: `AliasDecision` + `LayerDecision` dataclasses

**Files:**
- Modify: `multiace_web/tools/multiace_postprocess.py` — insert after line 107 (end of existing dataclasses block, before `parse_header`)

- [ ] **Step 1: Add the two dataclasses**

In `multiace_web/tools/multiace_postprocess.py`, after the existing `@dataclass class SwapEvent` block, add:

```python
@dataclass
class AliasDecision:
    """One Tn → Tm rewrite recorded by optimize_aliases() for the sidecar."""
    line: int                # 0-based line index in the original gcode
    layer: Optional[int]     # layer N from preceding `; --- layer N ---`, or None
    original_tool: int       # Tn (the slicer-emitted index that got rewritten)
    alias_tool: int          # Tm (the existing loaded tool that absorbed it)
    reason: str              # human-readable, e.g. "color+type match, T0 already loaded"


@dataclass
class LayerDecision:
    """One layer's pre-load plan recorded by prelayer_reload() for the sidecar."""
    layer: int               # layer N (0-based)
    distinct_tools: list[int]  # tool indices that appear within the layer
    preloads: list[dict]     # [{"head": h, "ace": a, "slot": s, "tool": n}, ...]
    skipped: bool            # True if layer was >4 distinct tools and skipped
    skip_reason: Optional[str]
```

- [ ] **Step 2: Verify the file still imports cleanly**

Run: `cd /mnt/e/Code/multiACE/multiace_web && .venv/bin/python -c "import sys; sys.path.insert(0, 'tools'); import multiace_postprocess; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add multiace_web/tools/multiace_postprocess.py
git commit -m "feat(postprocess): add AliasDecision + LayerDecision dataclasses (#66)"
```

---

## Task 2: `optimize_aliases()` — basic happy path test + implementation

**Files:**
- Create: `multiace_web/tests/test_postprocess_optimize.py`
- Modify: `multiace_web/tools/multiace_postprocess.py` — insert `optimize_aliases()` after the existing `plan_swaps()` function (around line 365)

- [ ] **Step 1: Write the failing test**

Create `multiace_web/tests/test_postprocess_optimize.py`:

```python
"""Tests for multiace_postprocess.optimize_aliases()."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import multiace_postprocess as pp


def _res(idx, ace, slot, color="ff0000", type_="PLA", quality="exact"):
    tm = pp.ToolMeta(index=idx, type=type_, color=color)
    c = pp.Candidate(ace=ace, slot=slot, spool_id=10 + idx, spool_name="")
    return pp.ToolResolution(tool=tm, match_quality=quality,
                              candidates=[c], resolved=c)


def test_aliases_same_color_type_tool_to_already_loaded():
    """T0 and T5 both PLA red. T0 loaded first, T5 appears later → rewrite T5→T0."""
    resolutions = [
        _res(0, 0, 0, color="ff0000"),
        _res(5, 0, 1, color="ff0000"),  # same color, different slot
    ]
    lines = ["T0", "G1 X10", "T5", "G1 X20"]
    out, decisions = pp.optimize_aliases(lines, resolutions)
    assert out == ["T0", "G1 X10", "T0", "G1 X20"]
    assert len(decisions) == 1
    assert decisions[0].original_tool == 5
    assert decisions[0].alias_tool == 0
    assert decisions[0].line == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/e/Code/multiACE/multiace_web && .venv/bin/python -m pytest tests/test_postprocess_optimize.py::test_aliases_same_color_type_tool_to_already_loaded -v`
Expected: FAIL with `AttributeError: module 'multiace_postprocess' has no attribute 'optimize_aliases'`

- [ ] **Step 3: Implement `optimize_aliases()`**

In `multiace_web/tools/multiace_postprocess.py`, after the `plan_swaps()` function (search for `def plan_swaps`, find its closing line — currently ends ~line 365), add:

```python
# ---------------------------------------------------------------------------
# Optimization passes (--optimize, --layer)
# ---------------------------------------------------------------------------

_TOOL_RE_OPTIM = re.compile(r"^T(\d+)\b")
_LAYER_RE_OPTIM = re.compile(r"^;\s*---\s*layer\s+(\d+)\s*---")


def optimize_aliases(
    lines: list[str],
    resolutions: list,
) -> tuple[list[str], list]:
    """Single linear pass over gcode. When a Tn line appears and a different
    Tm (already encountered) shares the same (color, type), rewrite Tn → Tm.

    See docs/superpowers/specs/2026-05-17-swaptimizer-design.md for the
    full algorithm and correctness assumptions.

    Returns (rewritten_lines, list[AliasDecision]).
    Unresolved tools (match_quality='none') are skipped — no color to alias against.
    """
    # Build tool index → (color, type) map, skipping unresolved
    tool_meta: dict = {}
    for r in resolutions:
        if r.match_quality == "none":
            continue
        tool_meta[r.tool.index] = (r.tool.color, r.tool.type)

    seen_tools: set = set()       # tools we've already issued (proxy for "loaded somewhere")
    out_lines = list(lines)
    decisions: list = []
    current_layer = None

    for line_idx, line in enumerate(lines):
        m_layer = _LAYER_RE_OPTIM.match(line)
        if m_layer:
            current_layer = int(m_layer.group(1))
            continue
        m_tool = _TOOL_RE_OPTIM.match(line)
        if not m_tool:
            continue
        n = int(m_tool.group(1))
        if n not in tool_meta:
            continue   # unresolved — skip
        n_color, n_type = tool_meta[n]
        alias = None
        for m in seen_tools:
            if m == n:
                continue
            m_color, m_type = tool_meta[m]
            if m_color == n_color and m_type == n_type:
                alias = m
                break
        if alias is not None:
            # Rewrite the Tn keyword (preserve any trailing args after T<digits>)
            out_lines[line_idx] = f"T{alias}" + line[m_tool.end():]
            decisions.append(AliasDecision(
                line=line_idx,
                layer=current_layer,
                original_tool=n,
                alias_tool=alias,
                reason=f"color+type match, T{alias} already loaded",
            ))
        else:
            seen_tools.add(n)
    return out_lines, decisions
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /mnt/e/Code/multiACE/multiace_web && .venv/bin/python -m pytest tests/test_postprocess_optimize.py::test_aliases_same_color_type_tool_to_already_loaded -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add multiace_web/tools/multiace_postprocess.py multiace_web/tests/test_postprocess_optimize.py
git commit -m "feat(postprocess): optimize_aliases() core algorithm + first test (#66)"
```

---

## Task 3: `optimize_aliases()` — no-rewrite edge cases

**Files:**
- Modify: `multiace_web/tests/test_postprocess_optimize.py`

- [ ] **Step 1: Add three edge-case tests**

Append to `multiace_web/tests/test_postprocess_optimize.py`:

```python
def test_no_rewrite_when_colors_differ():
    """T0 red, T5 blue → no rewrite."""
    resolutions = [
        _res(0, 0, 0, color="ff0000"),
        _res(5, 0, 1, color="0000ff"),
    ]
    lines = ["T0", "G1 X10", "T5", "G1 X20"]
    out, decisions = pp.optimize_aliases(lines, resolutions)
    assert out == lines
    assert decisions == []


def test_no_rewrite_when_types_differ():
    """T0 black PLA, T5 black PETG → no rewrite (type mismatch)."""
    resolutions = [
        _res(0, 0, 0, color="000000", type_="PLA"),
        _res(5, 0, 1, color="000000", type_="PETG"),
    ]
    lines = ["T0", "G1 X10", "T5", "G1 X20"]
    out, decisions = pp.optimize_aliases(lines, resolutions)
    assert out == lines
    assert decisions == []


def test_unresolved_tool_is_skipped():
    """Tool with match_quality='none' is never aliased and never an alias target."""
    res0 = _res(0, 0, 0, color="ff0000")
    res5_unresolved = pp.ToolResolution(
        tool=pp.ToolMeta(index=5, type="PLA", color="ff0000"),
        match_quality="none", candidates=[], resolved=None,
    )
    lines = ["T0", "G1 X10", "T5", "G1 X20"]
    out, decisions = pp.optimize_aliases(lines, [res0, res5_unresolved])
    assert out == lines
    assert decisions == []
```

- [ ] **Step 2: Run all four tests, expect PASS**

Run: `cd /mnt/e/Code/multiACE/multiace_web && .venv/bin/python -m pytest tests/test_postprocess_optimize.py -v`
Expected: 4 passed (1 existing + 3 new)

- [ ] **Step 3: Commit**

```bash
git add multiace_web/tests/test_postprocess_optimize.py
git commit -m "test(postprocess): optimize_aliases() no-rewrite edge cases (#66)"
```

---

## Task 4: `optimize_aliases()` — multi-alias + repeated Tn cases

**Files:**
- Modify: `multiace_web/tests/test_postprocess_optimize.py`

- [ ] **Step 1: Add three more tests**

Append:

```python
def test_repeated_tn_only_first_seen_is_loaded():
    """T0 T0 T0 T0 T5 (all same color) → T5 aliases to T0 once."""
    resolutions = [
        _res(0, 0, 0, color="ff0000"),
        _res(5, 0, 1, color="ff0000"),
    ]
    lines = ["T0", "T0", "T0", "T0", "T5"]
    out, decisions = pp.optimize_aliases(lines, resolutions)
    assert out == ["T0", "T0", "T0", "T0", "T0"]
    assert len(decisions) == 1
    assert decisions[0].original_tool == 5


def test_no_tn_lines_is_noop():
    """gcode without any T-commands round-trips unchanged."""
    resolutions = [_res(0, 0, 0)]
    lines = ["G28", "G1 X10", "G1 Y20", "M104 S200"]
    out, decisions = pp.optimize_aliases(lines, resolutions)
    assert out == lines
    assert decisions == []


def test_alias_preserves_trailing_args_on_tn_line():
    """T5 A0 → T0 A0 (the 'A0' suffix Snapmaker uses must survive the rewrite)."""
    resolutions = [
        _res(0, 0, 0, color="ff0000"),
        _res(5, 0, 1, color="ff0000"),
    ]
    lines = ["T0", "T5 A0"]
    out, decisions = pp.optimize_aliases(lines, resolutions)
    assert out == ["T0", "T0 A0"]
    assert len(decisions) == 1
```

- [ ] **Step 2: Run tests, expect PASS**

Run: `cd /mnt/e/Code/multiACE/multiace_web && .venv/bin/python -m pytest tests/test_postprocess_optimize.py -v`
Expected: 7 passed

- [ ] **Step 3: Commit**

```bash
git add multiace_web/tests/test_postprocess_optimize.py
git commit -m "test(postprocess): optimize_aliases() repeated-Tn + trailing-args (#66)"
```

---

## Task 5: `prelayer_reload()` — happy path test + implementation

**Files:**
- Create: `multiace_web/tests/test_postprocess_layer.py`
- Modify: `multiace_web/tools/multiace_postprocess.py` — add `prelayer_reload()` after `optimize_aliases()`

- [ ] **Step 1: Write the failing test**

Create `multiace_web/tests/test_postprocess_layer.py`:

```python
"""Tests for multiace_postprocess.prelayer_reload()."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import multiace_postprocess as pp


def _res(idx, ace, slot, color="ff0000", type_="PLA"):
    tm = pp.ToolMeta(index=idx, type=type_, color=color)
    c = pp.Candidate(ace=ace, slot=slot, spool_id=10 + idx, spool_name="")
    return pp.ToolResolution(tool=tm, match_quality="exact",
                              candidates=[c], resolved=c)


def test_prelayer_inserts_ace_load_head_for_each_distinct_tool_in_layer():
    """A single layer with 4 distinct tools → 4 ACE_LOAD_HEAD inserts at layer start."""
    resolutions = [_res(i, 0, i) for i in range(4)]
    lines = [
        "G28",
        "; --- layer 0 ---",
        "T0", "G1 X10",
        "T1", "G1 X20",
        "T2", "G1 X30",
        "T3", "G1 X40",
    ]
    out, decisions = pp.prelayer_reload(lines, resolutions)
    # Expect 4 ACE_LOAD_HEAD inserts right after the layer marker
    assert "ACE_LOAD_HEAD HEAD=0 ACE=0 SLOT=0" in out
    assert "ACE_LOAD_HEAD HEAD=1 ACE=0 SLOT=1" in out
    assert "ACE_LOAD_HEAD HEAD=2 ACE=0 SLOT=2" in out
    assert "ACE_LOAD_HEAD HEAD=3 ACE=0 SLOT=3" in out
    # The layer marker should precede the inserts
    marker_idx = out.index("; --- layer 0 ---")
    first_load_idx = next(i for i, ln in enumerate(out) if ln.startswith("ACE_LOAD_HEAD"))
    assert first_load_idx > marker_idx
    # One LayerDecision recorded with skipped=False and 4 preloads
    assert len(decisions) == 1
    assert decisions[0].layer == 0
    assert decisions[0].skipped is False
    assert len(decisions[0].preloads) == 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/e/Code/multiACE/multiace_web && .venv/bin/python -m pytest tests/test_postprocess_layer.py::test_prelayer_inserts_ace_load_head_for_each_distinct_tool_in_layer -v`
Expected: FAIL with `AttributeError: ... has no attribute 'prelayer_reload'`

- [ ] **Step 3: Implement `prelayer_reload()`**

In `multiace_web/tools/multiace_postprocess.py`, after the `optimize_aliases()` function added in Task 2, add:

```python
def prelayer_reload(
    lines: list[str],
    resolutions: list,
) -> tuple[list[str], list]:
    """Insert ACE_LOAD_HEAD lines at layer start for each layer using ≤4 distinct
    tool indices. Layers using >4 distinct tools are skipped (no improvement
    possible from pre-load alone).

    See docs/superpowers/specs/2026-05-17-swaptimizer-design.md for algorithm.

    Returns (rewritten_lines, list[LayerDecision]).
    """
    # Tool index → resolved (ace, slot)
    tool_resolved: dict = {}
    for r in resolutions:
        if r.resolved is not None:
            tool_resolved[r.tool.index] = (r.resolved.ace, r.resolved.slot)

    # Parse layer boundaries: list of (marker_line_idx, layer_num)
    layers: list = []
    for i, ln in enumerate(lines):
        m = _LAYER_RE_OPTIM.match(ln)
        if m:
            layers.append((i, int(m.group(1))))

    if not layers:
        # No layer markers — can't do per-layer optimization
        return list(lines), []

    out_lines = list(lines)
    decisions: list = []
    inserts: list = []  # [(insert_at, [lines_to_insert])]

    for i, (marker_idx, layer_num) in enumerate(layers):
        end_idx = layers[i + 1][0] if i + 1 < len(layers) else len(lines)
        distinct = []
        seen = set()
        for j in range(marker_idx + 1, end_idx):
            m_tool = _TOOL_RE_OPTIM.match(lines[j])
            if m_tool:
                n = int(m_tool.group(1))
                if n not in seen and n in tool_resolved:
                    seen.add(n)
                    distinct.append(n)

        if not distinct:
            decisions.append(LayerDecision(
                layer=layer_num, distinct_tools=[],
                preloads=[], skipped=False,
                skip_reason=None,
            ))
            continue

        if len(distinct) > 4:
            decisions.append(LayerDecision(
                layer=layer_num, distinct_tools=distinct,
                preloads=[], skipped=True,
                skip_reason=f"{len(distinct)} distinct tools > 4",
            ))
            continue

        # Assign each distinct tool to a head 0..3. Lowest-head-index first
        # (matches plan_swaps' greedy first-fit tie-breaking).
        preloads = []
        for head_idx, tool_idx in enumerate(distinct):
            ace, slot = tool_resolved[tool_idx]
            preloads.append({
                "head": head_idx, "ace": ace, "slot": slot, "tool": tool_idx,
            })

        lines_to_insert = [
            f"ACE_LOAD_HEAD HEAD={p['head']} ACE={p['ace']} SLOT={p['slot']}"
            for p in preloads
        ]
        inserts.append((marker_idx + 1, lines_to_insert))

        decisions.append(LayerDecision(
            layer=layer_num, distinct_tools=distinct,
            preloads=preloads, skipped=False, skip_reason=None,
        ))

    # Apply inserts in reverse so earlier indices stay valid
    for insert_at, new_lines in reversed(inserts):
        out_lines[insert_at:insert_at] = new_lines

    return out_lines, decisions
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /mnt/e/Code/multiACE/multiace_web && .venv/bin/python -m pytest tests/test_postprocess_layer.py::test_prelayer_inserts_ace_load_head_for_each_distinct_tool_in_layer -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add multiace_web/tools/multiace_postprocess.py multiace_web/tests/test_postprocess_layer.py
git commit -m "feat(postprocess): prelayer_reload() core algorithm + first test (#66)"
```

---

## Task 6: `prelayer_reload()` — edge cases

**Files:**
- Modify: `multiace_web/tests/test_postprocess_layer.py`

- [ ] **Step 1: Add four edge-case tests**

Append:

```python
def test_layer_with_more_than_4_distinct_is_skipped():
    """5 distinct tools in one layer → skipped, no inserts."""
    resolutions = [_res(i, 0, i) for i in range(5)]
    lines = [
        "; --- layer 0 ---",
        "T0", "T1", "T2", "T3", "T4",
    ]
    out, decisions = pp.prelayer_reload(lines, resolutions)
    assert not any(ln.startswith("ACE_LOAD_HEAD") for ln in out)
    assert len(decisions) == 1
    assert decisions[0].skipped is True
    assert "5 distinct tools" in decisions[0].skip_reason


def test_layer_with_no_tn_lines_records_empty_decision():
    """A layer with only G-moves and comments → empty decision, no inserts."""
    resolutions = [_res(0, 0, 0)]
    lines = [
        "; --- layer 0 ---",
        "G1 X10", "G1 Y20", "; comment",
    ]
    out, decisions = pp.prelayer_reload(lines, resolutions)
    assert not any(ln.startswith("ACE_LOAD_HEAD") for ln in out)
    assert len(decisions) == 1
    assert decisions[0].distinct_tools == []
    assert decisions[0].preloads == []
    assert decisions[0].skipped is False


def test_file_with_no_layer_markers_is_noop():
    """Gcode without any '; --- layer N ---' comments → unchanged, no decisions."""
    resolutions = [_res(0, 0, 0), _res(1, 0, 1)]
    lines = ["G28", "T0", "G1 X10", "T1", "G1 X20"]
    out, decisions = pp.prelayer_reload(lines, resolutions)
    assert out == lines
    assert decisions == []


def test_multiple_layers_handled_independently():
    """Two layers each with their own distinct sets → two LayerDecisions, inserts at each marker."""
    resolutions = [_res(i, 0, i) for i in range(4)]
    lines = [
        "; --- layer 0 ---",
        "T0", "T1",
        "; --- layer 1 ---",
        "T2", "T3",
    ]
    out, decisions = pp.prelayer_reload(lines, resolutions)
    inserts = [ln for ln in out if ln.startswith("ACE_LOAD_HEAD")]
    assert len(inserts) == 4
    assert len(decisions) == 2
    assert decisions[0].layer == 0
    assert decisions[1].layer == 1
    assert decisions[0].distinct_tools == [0, 1]
    assert decisions[1].distinct_tools == [2, 3]
```

- [ ] **Step 2: Run all tests, expect PASS**

Run: `cd /mnt/e/Code/multiACE/multiace_web && .venv/bin/python -m pytest tests/test_postprocess_layer.py -v`
Expected: 5 passed (1 existing + 4 new)

- [ ] **Step 3: Commit**

```bash
git add multiace_web/tests/test_postprocess_layer.py
git commit -m "test(postprocess): prelayer_reload() edge cases (skip, no-Tn, no-markers, multi-layer) (#66)"
```

---

## Task 7: `prelayer_reload()` — pre-existing ACE_LOAD_HEAD detection

**Files:**
- Modify: `multiace_web/tools/multiace_postprocess.py` — extend `prelayer_reload()` to detect pre-existing inserts
- Modify: `multiace_web/tests/test_postprocess_layer.py` — add test

- [ ] **Step 1: Write the failing test**

Append to `multiace_web/tests/test_postprocess_layer.py`:

```python
def test_does_not_double_emit_when_load_head_already_present():
    """If the slicer or a prior pass already inserted ACE_LOAD_HEAD for a
    (head, ace, slot) at layer start, we don't emit a duplicate."""
    resolutions = [_res(i, 0, i) for i in range(2)]
    lines = [
        "; --- layer 0 ---",
        "ACE_LOAD_HEAD HEAD=0 ACE=0 SLOT=0",   # pre-existing
        "T0", "T1",
    ]
    out, decisions = pp.prelayer_reload(lines, resolutions)
    head1_inserts = [ln for ln in out if ln == "ACE_LOAD_HEAD HEAD=1 ACE=0 SLOT=1"]
    head0_inserts = [ln for ln in out if ln == "ACE_LOAD_HEAD HEAD=0 ACE=0 SLOT=0"]
    assert len(head1_inserts) == 1
    assert len(head0_inserts) == 1, "must not double-emit the pre-existing one"
    assert len(decisions[0].preloads) == 1
    assert decisions[0].preloads[0]["head"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/e/Code/multiACE/multiace_web && .venv/bin/python -m pytest tests/test_postprocess_layer.py::test_does_not_double_emit_when_load_head_already_present -v`
Expected: FAIL (current implementation emits HEAD=0 even though pre-existing)

- [ ] **Step 3: Update `prelayer_reload()` to detect pre-existing inserts**

In `multiace_web/tools/multiace_postprocess.py`, find the section in `prelayer_reload()` where `preloads` is built. Replace the `# Assign each distinct tool ...` block with:

```python
        # Scan the layer's existing lines for ACE_LOAD_HEAD entries already
        # present (slicer or prior pass) so we don't double-emit.
        _LOAD_RE = re.compile(r"^ACE_LOAD_HEAD\s+HEAD=(\d+)\s+ACE=(\d+)\s+SLOT=(\d+)")
        already_loaded: set = set()
        for j in range(marker_idx + 1, end_idx):
            m = _LOAD_RE.match(lines[j])
            if m:
                already_loaded.add((int(m.group(1)), int(m.group(2)), int(m.group(3))))

        # Assign each distinct tool to a head 0..3. Lowest-head-index first
        # (matches plan_swaps' greedy first-fit tie-breaking). Skip preloads
        # already present in the layer.
        preloads = []
        for head_idx, tool_idx in enumerate(distinct):
            ace, slot = tool_resolved[tool_idx]
            if (head_idx, ace, slot) in already_loaded:
                continue
            preloads.append({
                "head": head_idx, "ace": ace, "slot": slot, "tool": tool_idx,
            })
```

- [ ] **Step 4: Run all tests, expect PASS**

Run: `cd /mnt/e/Code/multiACE/multiace_web && .venv/bin/python -m pytest tests/test_postprocess_layer.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add multiace_web/tools/multiace_postprocess.py multiace_web/tests/test_postprocess_layer.py
git commit -m "feat(postprocess): prelayer_reload() detects pre-existing ACE_LOAD_HEAD (#66)"
```

---

## Task 8: Wire optimization decisions into the sidecar

**Files:**
- Create: `multiace_web/tests/test_postprocess_integration.py`
- Modify: `multiace_web/tools/multiace_postprocess.py` — extend `write_sidecar()` signature + body

- [ ] **Step 1: Add a test for sidecar shape**

Create `multiace_web/tests/test_postprocess_integration.py`:

```python
"""Integration tests for multiace_postprocess optimization passes + sidecar."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import multiace_postprocess as pp


def _res(idx, ace, slot, color="ff0000", type_="PLA"):
    tm = pp.ToolMeta(index=idx, type=type_, color=color)
    c = pp.Candidate(ace=ace, slot=slot, spool_id=10 + idx, spool_name="")
    return pp.ToolResolution(tool=tm, match_quality="exact",
                              candidates=[c], resolved=c)


def test_sidecar_includes_optimize_and_layer_sections():
    """When alias/layer decisions are passed to write_sidecar, both appear
    in the JSON sidecar's 'optimize' and 'layer' sections."""
    resolutions = [_res(0, 0, 0)]
    swaps = []
    aliases = [pp.AliasDecision(
        line=10, layer=0, original_tool=5, alias_tool=0,
        reason="color+type match, T0 already loaded",
    )]
    layers = [pp.LayerDecision(
        layer=0, distinct_tools=[0, 1, 2, 3],
        preloads=[{"head": 1, "ace": 0, "slot": 1, "tool": 1}],
        skipped=False, skip_reason=None,
    )]
    with tempfile.TemporaryDirectory() as tmpdir:
        gpath = Path(tmpdir) / "test.gcode"
        gpath.write_text("dummy\n")
        pp.write_sidecar(
            gpath, resolutions, swaps, status="ok",
            optimize_decisions=aliases, layer_decisions=layers,
        )
        sidecar = json.loads((Path(str(gpath) + ".multiace.json")).read_text())
    assert "optimize" in sidecar
    assert sidecar["optimize"]["count"] == 1
    assert sidecar["optimize"]["aliases"][0]["original_tool"] == 5
    assert "layer" in sidecar
    assert sidecar["layer"]["count"] == 1
    assert sidecar["layer"]["layers"][0]["layer"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/e/Code/multiACE/multiace_web && .venv/bin/python -m pytest tests/test_postprocess_integration.py::test_sidecar_includes_optimize_and_layer_sections -v`
Expected: FAIL (write_sidecar doesn't accept `optimize_decisions` / `layer_decisions` yet)

- [ ] **Step 3: Update `write_sidecar()` signature + body**

In `multiace_web/tools/multiace_postprocess.py`, replace the existing `def write_sidecar(...)` definition and body with:

```python
def write_sidecar(gcode_path: Path, resolutions: list,
                  swaps: list, status: str,
                  reason: Optional[str] = None, errors: Optional[list] = None,
                  optimize_decisions: Optional[list] = None,
                  layer_decisions: Optional[list] = None) -> None:
    """Write <gcode_path>.multiace.json atomically.

    optimize_decisions / layer_decisions are appended as separate sections
    when provided (Phase 4 swaptimizer).
    """
    tools_dict: dict = {}
    for r in resolutions:
        cand = r.resolved
        tools_dict[str(r.tool.index)] = {
            "type": r.tool.type,
            "color": r.tool.color,
            "match_quality": r.match_quality,
            "candidates": [
                {"ace": c.ace, "slot": c.slot, "spool_id": c.spool_id, "spool_name": c.spool_name}
                for c in r.candidates
            ],
            "resolved": (
                {"ace": cand.ace, "slot": cand.slot, "spool_id": cand.spool_id}
                if cand else None
            ),
            "physical_head": r.physical_head,
        }
    swaps_list = [
        {
            "line": s.line, "layer": s.layer, "head": s.head,
            "from": {"ace": s.from_ace, "slot": s.from_slot},
            "to": {"ace": s.to_ace, "slot": s.to_slot},
        }
        for s in swaps
    ]
    data = {
        "schema": 2,   # bumped: now includes optimize / layer sections
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gcode_path": str(gcode_path),
        "status": status,
        "reason": reason,
        "tools": tools_dict,
        "swaps": swaps_list,
        "errors": errors or [],
    }
    if optimize_decisions is not None:
        data["optimize"] = {
            "count": len(optimize_decisions),
            "aliases": [
                {
                    "line": d.line, "layer": d.layer,
                    "original_tool": d.original_tool,
                    "alias_tool": d.alias_tool,
                    "reason": d.reason,
                }
                for d in optimize_decisions
            ],
        }
    if layer_decisions is not None:
        data["layer"] = {
            "count": len(layer_decisions),
            "layers": [
                {
                    "layer": d.layer,
                    "distinct_tools": d.distinct_tools,
                    "preloads": d.preloads,
                    "skipped": d.skipped,
                    "skip_reason": d.skip_reason,
                }
                for d in layer_decisions
            ],
        }
    sidecar_path = Path(str(gcode_path) + ".multiace.json")
    _atomic_write_json(sidecar_path, data)
    _info(f"Sidecar written: {sidecar_path}")
```

- [ ] **Step 4: Run new test + verify no regression in existing tests**

Run: `cd /mnt/e/Code/multiACE/multiace_web && .venv/bin/python -m pytest tests/test_postprocess_*.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add multiace_web/tools/multiace_postprocess.py multiace_web/tests/test_postprocess_integration.py
git commit -m "feat(postprocess): write_sidecar() optimize/layer sections + schema v2 (#66)"
```

---

## Task 9: `main()` with argparse + flag wiring

**Files:**
- Modify: `multiace_web/tools/multiace_postprocess.py` — add `main()` at end of file

- [ ] **Step 1: Add `main()` at the end of the file**

After the existing `write_sidecar()` function in `multiace_web/tools/multiace_postprocess.py`, append:

```python
# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------

def main(argv: Optional[list] = None) -> int:
    """Process a gcode file end-to-end.

    Usage (Orca Slicer "Post-processing scripts" field):
        python3 /path/to/multiace_postprocess.py [--optimize] [--layer] {output_filepath}

    Returns 0 on success, non-zero on error.
    """
    import argparse
    parser = argparse.ArgumentParser(
        description="multiACE / Snapmaker U1 8-color gcode post-processor",
    )
    parser.add_argument(
        "--optimize", action="store_true",
        help=("Tn aliasing: rewrite Tn->Tm when (color, type) match and Tm "
              "is already loaded. Assumes spool interchangeability; users "
              "requiring per-spool identity (compliance, batch matching) "
              "should leave this off. Default: off."),
    )
    parser.add_argument(
        "--layer", action="store_true",
        help=("Pre-layer reload: for each layer using <=4 distinct tools, "
              "insert ACE_LOAD_HEAD at layer start so the printer doesn't "
              "pause for slot-change mid-extrusion. Layers with >4 tools "
              "are skipped. Default: off."),
    )
    parser.add_argument(
        "gcode_path",
        help="Path to the gcode file to process (in-place modification).",
    )
    args = parser.parse_args(argv)

    gcode_path = Path(args.gcode_path)
    if not gcode_path.exists():
        _warn(f"gcode file not found: {gcode_path}")
        return 1

    lines = gcode_path.read_text().splitlines()
    tools = parse_header(lines)
    if tools is None:
        _warn("no Orca filament header found; nothing to do")
        write_sidecar(gcode_path, [], [], status="skipped", reason="no_header")
        return 0

    printer_url = os.environ.get("DAVINCI_U1_HOST", "")
    slots_response = query_slots(f"http://{printer_url}") if printer_url else {"slots": []}
    resolutions = match_tools(tools, slots_response)

    # Phase 4 optimizations (opt-in). Run order matters: --optimize first
    # (may collapse a 5-distinct-tool layer to 4), then --layer operates on
    # the aliased gcode.
    alias_decisions = None
    if args.optimize:
        lines, alias_decisions = optimize_aliases(lines, resolutions)

    layer_decisions = None
    if args.layer:
        lines, layer_decisions = prelayer_reload(lines, resolutions)

    swaps = plan_swaps(resolutions, lines)
    lines = rewrite_gcode(lines, resolutions, swaps)

    gcode_path.write_text("\n".join(lines) + "\n")
    write_sidecar(
        gcode_path, resolutions, swaps, status="ok",
        optimize_decisions=alias_decisions,
        layer_decisions=layer_decisions,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Verify the file still imports cleanly**

Run: `cd /mnt/e/Code/multiACE/multiace_web && .venv/bin/python -c "import sys; sys.path.insert(0, 'tools'); import multiace_postprocess; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Verify CLI help**

Run: `cd /mnt/e/Code/multiACE/multiace_web && .venv/bin/python tools/multiace_postprocess.py --help`
Expected: argparse help showing `--optimize` and `--layer` flags

- [ ] **Step 4: Commit**

```bash
git add multiace_web/tools/multiace_postprocess.py
git commit -m "feat(postprocess): main() with --optimize and --layer argparse flags (#66)"
```

---

## Task 10: Integration test — both flags on a synthetic full-pipeline run

**Files:**
- Modify: `multiace_web/tests/test_postprocess_integration.py`

- [ ] **Step 1: Add the combined integration test**

Append to `multiace_web/tests/test_postprocess_integration.py`:

```python
def test_optimize_and_layer_compose_on_5_color_layer():
    """A layer with 5 distinct tools where two share (color, type):
       after --optimize collapses it to 4 distinct, --layer can pre-load."""
    resolutions = [
        _res(0, 0, 0, color="ff0000"),    # red
        _res(1, 0, 1, color="00ff00"),    # green
        _res(2, 0, 2, color="0000ff"),    # blue
        _res(3, 0, 3, color="ffff00"),    # yellow
        _res(5, 1, 0, color="ff0000"),    # red again (alias target T0)
    ]
    lines = [
        "; --- layer 0 ---",
        "T0", "T1", "T2", "T3", "T5",
    ]
    # --optimize first
    lines, alias_decisions = pp.optimize_aliases(lines, resolutions)
    assert "T5" not in lines
    assert len(alias_decisions) == 1
    # --layer second
    lines, layer_decisions = pp.prelayer_reload(lines, resolutions)
    assert len(layer_decisions) == 1
    assert layer_decisions[0].skipped is False
    assert len(layer_decisions[0].preloads) == 4
    inserts = [ln for ln in lines if ln.startswith("ACE_LOAD_HEAD")]
    assert len(inserts) == 4
```

- [ ] **Step 2: Run, expect PASS**

Run: `cd /mnt/e/Code/multiACE/multiace_web && .venv/bin/python -m pytest tests/test_postprocess_integration.py -v`
Expected: 2 passed

- [ ] **Step 3: Run the full test suite and verify nothing else regressed**

Run: `cd /mnt/e/Code/multiACE/multiace_web && .venv/bin/python -m pytest -q --deselect tests/test_poller.py::test_poller_calls_run_gcode_at_interval --deselect tests/test_poller.py::test_poller_continues_after_error 2>&1 | tail -3`
Expected: all pass (count should be 333 baseline + ~15 new = ~348)

- [ ] **Step 4: Commit**

```bash
git add multiace_web/tests/test_postprocess_integration.py
git commit -m "test(postprocess): integration test for --optimize + --layer composition (#66)"
```

---

## Task 11: Final self-review + optional PR

- [ ] **Step 1: Re-read the spec and confirm each section has a corresponding task**

Read: `docs/superpowers/specs/2026-05-17-swaptimizer-design.md`
Walk through each section:
- `--optimize` algorithm → Task 2 + Tasks 3-4 edge cases ✓
- `--layer` algorithm → Task 5 + Tasks 6-7 edge cases ✓
- Sidecar additions → Task 8 ✓
- CLI / main() → Task 9 ✓
- Combined run order → Task 10 ✓
- Out-of-scope items → spec only, no tasks ✓

- [ ] **Step 2: Push the branch + open a PR (optional, operator's call)**

```bash
git push -u origin HEAD
gh pr create --title "feat(postprocess): Phase 4 Swaptimizer (#66)" --body "Implements docs/superpowers/specs/2026-05-17-swaptimizer-design.md. See task #66."
```

---

## Notes for the implementer

- The pipeline order is fixed:
  1. `parse_header`
  2. `query_slots`
  3. `match_tools`
  4. **`optimize_aliases`** (if `--optimize`) ← new
  5. **`prelayer_reload`** (if `--layer`) ← new
  6. `plan_swaps`
  7. `rewrite_gcode`
  8. `write_sidecar` (now includes optimize/layer sections)
- Both new functions are PURE — they take inputs and return outputs, no side effects. Easy to unit test.
- Stdlib-only. No new dependencies.
- All new tests use the same `_res()` helper pattern as the existing test files for consistency.
- After all tasks land, run the full suite one more time and verify the count went up by exactly the number of new tests (no accidental regressions in existing tests).
