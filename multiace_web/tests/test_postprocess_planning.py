"""Tests for multiace_postprocess.plan_swaps()."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import multiace_postprocess as pp


def _res(idx, ace, slot, spool_id=10):
    """Helper: build a fully-resolved ToolResolution."""
    tm = pp.ToolMeta(index=idx, type="PLA", color="ff0000")
    c = pp.Candidate(ace=ace, slot=slot, spool_id=spool_id, spool_name="")
    r = pp.ToolResolution(tool=tm, match_quality="exact", candidates=[c], resolved=c)
    return r


def test_no_swap_when_all_tools_fit_in_4_heads():
    """T0-T3 each resolve to distinct slots — no swaps needed."""
    resolutions = [_res(i, 0, i, 10+i) for i in range(4)]
    lines = [
        "G28",
        "T0", "G1 X10", "T1", "G1 X20", "T2", "G1 X30", "T3", "G1 X40",
    ]
    swaps = pp.plan_swaps(resolutions, lines)
    assert swaps == []


def test_one_swap_with_5th_color():
    """T4 forces a swap at the physical head that's done with T0 (greedy: first head free)."""
    resolutions = [_res(i, 0, i, 10+i) for i in range(5)]
    lines = [
        "G28",
        "T0", "G1 X10",
        "T1", "G1 X20",
        "T2", "G1 X30",
        "T3", "G1 X40",
        "T4", "G1 X50",
    ]
    swaps = pp.plan_swaps(resolutions, lines)
    assert len(swaps) == 1
    assert swaps[0].head == 0
    assert swaps[0].to_ace == resolutions[4].resolved.ace
    assert swaps[0].to_slot == resolutions[4].resolved.slot


def test_swap_count_minimized_not_per_change():
    """A T0→T1→T0 sequence reusing same physical head has 0 swaps (head not reassigned)."""
    resolutions = [_res(i, 0, i, 10+i) for i in range(4)]
    lines = [
        "G28",
        "T0", "G1 X10",
        "T1", "G1 X20",
        "T0", "G1 X30",
    ]
    swaps = pp.plan_swaps(resolutions, lines)
    assert swaps == []


def test_unresolved_tool_is_skipped_in_planning():
    """An unresolved tool (match_quality=none) produces no swap; it stays None."""
    res0 = _res(0, 0, 0)
    res_unresolved = pp.ToolResolution(
        tool=pp.ToolMeta(index=1, type="ASA", color="aaaaaa"),
        match_quality="none", candidates=[], resolved=None,
    )
    lines = ["T0", "G1 X10", "T1", "G1 X20"]
    swaps = pp.plan_swaps([res0, res_unresolved], lines)
    assert all(s.head != 1 for s in swaps)


def test_layer_number_recorded_in_swap():
    """plan_swaps must record the current layer from '; --- layer N ---' comments."""
    resolutions = [_res(i, 0, i, 10+i) for i in range(5)]
    lines = [
        "G28",
        "T0", "G1 X10",
        "T1", "G1 X20",
        "T2", "G1 X30",
        "T3", "G1 X40",
        "; --- layer 12 ---",
        "T4", "G1 X50",
    ]
    swaps = pp.plan_swaps(resolutions, lines)
    assert len(swaps) == 1
    assert swaps[0].layer == 12


def test_eviction_picks_furthest_future_use_when_all_heads_busy():
    """When all 4 heads have future uses, evict the head whose tool is used
    LATEST (least urgent to keep). With T0 used at line 30 and T1/T2/T3 used
    at lines 6/7/8, the 5th tool T4 should evict T0's head (furthest future use)
    — NOT T1's head (nearest future use)."""
    resolutions = [_res(i, 0, i, 10+i) for i in range(5)]
    lines = [
        "G28",
        "T0", "G1 X1",
        "T1", "G1 X2",
        "T2", "G1 X3",
        "T3", "G1 X4",
        # All 4 heads now busy with T0-T3.
        "T4", "G1 X5",   # ← swap point. Evict head whose next-use is furthest.
        # Future uses after the swap point:
        "T1", "G1 X6",   # T1 used very soon — must keep
        "T2", "G1 X7",
        "T3", "G1 X8",
        # ... many more lines ...
        "G1 X9", "G1 X10", "G1 X11", "G1 X12", "G1 X13",
        "G1 X14", "G1 X15", "G1 X16", "G1 X17", "G1 X18",
        "G1 X19", "G1 X20", "G1 X21", "G1 X22", "G1 X23",
        "T0", "G1 X24",  # T0 used much later — fine to evict
    ]
    swaps = pp.plan_swaps(resolutions, lines)
    assert len(swaps) >= 1
    # The first swap (at T4) should evict head 0 (T0's head — furthest next use)
    assert swaps[0].head == 0, (
        f"Expected eviction of head 0 (T0, furthest next-use), got head {swaps[0].head}. "
        "If head 1 was evicted, the algorithm picked NEAREST next use (wrong direction)."
    )
