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
