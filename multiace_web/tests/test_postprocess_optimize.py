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
