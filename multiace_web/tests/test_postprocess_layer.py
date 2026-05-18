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
    assert "ACE_LOAD_HEAD HEAD=0 ACE=0 SLOT=0" in out
    assert "ACE_LOAD_HEAD HEAD=1 ACE=0 SLOT=1" in out
    assert "ACE_LOAD_HEAD HEAD=2 ACE=0 SLOT=2" in out
    assert "ACE_LOAD_HEAD HEAD=3 ACE=0 SLOT=3" in out
    marker_idx = out.index("; --- layer 0 ---")
    first_load_idx = next(i for i, ln in enumerate(out) if ln.startswith("ACE_LOAD_HEAD"))
    assert first_load_idx > marker_idx
    assert len(decisions) == 1
    assert decisions[0].layer == 0
    assert decisions[0].skipped is False
    assert len(decisions[0].preloads) == 4
