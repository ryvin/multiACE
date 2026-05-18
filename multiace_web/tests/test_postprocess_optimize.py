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
