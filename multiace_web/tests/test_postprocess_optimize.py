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
