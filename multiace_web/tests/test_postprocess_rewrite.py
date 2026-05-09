"""Tests for multiace_postprocess.rewrite_gcode() and write_sidecar()."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import multiace_postprocess as pp

FIXTURE = Path(__file__).parent / "fixtures" / "sample_8color.gcode"


def _make_resolution(idx, ace, slot, spool_id, t, c, head=None):
    tm = pp.ToolMeta(index=idx, type=t, color=c)
    cand = pp.Candidate(ace=ace, slot=slot, spool_id=spool_id, spool_name=f"Spool-{spool_id}")
    r = pp.ToolResolution(tool=tm, match_quality="exact", candidates=[cand], resolved=cand)
    r.physical_head = head
    return r


def _full_resolutions():
    """8-tool resolution matching sample_8color.gcode header."""
    types  = ["PLA","PLA","PETG","TPU","PLA","PETG","TPU","PETG"]
    colors = ["ff0000","ffffff","0080ff","ffff00","000000","00ffff","ff00ff","80ff00"]
    return [_make_resolution(i, i // 4, i % 4, 100+i, types[i], colors[i], head=i % 4)
            for i in range(8)]


def test_rewrite_replaces_T4_through_T7_with_physical_heads():
    lines = FIXTURE.read_text().splitlines()
    resolutions = _full_resolutions()
    swaps = []
    out = pp.rewrite_gcode(lines, resolutions, swaps)
    out_text = "\n".join(out)
    for t in ["T4", "T5", "T6", "T7"]:
        assert t + "\n" not in out_text + "\n" or t not in [l.strip() for l in out if l.strip() == t], \
            f"Physical tool {t} still present after rewrite"


def test_rewrite_inserts_header_comment_block():
    lines = FIXTURE.read_text().splitlines()
    resolutions = _full_resolutions()
    out = pp.rewrite_gcode(lines, resolutions, [])
    header_lines = [l for l in out if l.startswith("; multiace.")]
    assert any("tool0" in l for l in header_lines)
    assert any("multiace.swaps:" in l for l in header_lines)
    assert any("multiace.status:" in l for l in header_lines)


def test_rewrite_inserts_park_and_load_at_swap_boundaries(tmp_path):
    """A swap event causes ACE_PARK_HEAD + ACE_LOAD_HEAD to appear before the T line."""
    lines = [
        "; filament_type = PLA;PETG",
        "; filament_colour = #FF0000;#0000FF",
        "G28",
        "T0",
        "G1 X10",
        "T1",
        "G1 X20",
    ]
    r0 = _make_resolution(0, 0, 0, 10, "PLA",  "ff0000", head=0)
    r1 = _make_resolution(1, 0, 1, 11, "PETG", "0000ff", head=1)
    swap = pp.SwapEvent(line=5, layer=1, head=0, from_ace=0, from_slot=0, to_ace=0, to_slot=1)
    out = pp.rewrite_gcode(lines, [r0, r1], [swap])
    joined = "\n".join(out)
    assert "ACE_PARK_HEAD HEAD=0" in joined
    assert "ACE_LOAD_HEAD HEAD=0 ACE=0 SLOT=1" in joined
    park_idx = next(i for i, l in enumerate(out) if "ACE_PARK_HEAD" in l)
    load_idx = next(i for i, l in enumerate(out) if "ACE_LOAD_HEAD" in l)
    assert park_idx < load_idx


def test_sidecar_round_trip(tmp_path):
    gcode_path = tmp_path / "test.gcode"
    gcode_path.write_text("G28\n")
    resolutions = _full_resolutions()
    pp.write_sidecar(gcode_path, resolutions, [], "ready", None)
    sidecar_path = tmp_path / "test.gcode.multiace.json"
    assert sidecar_path.exists()
    data = json.loads(sidecar_path.read_text())
    assert data["schema"] == 1
    assert data["status"] == "ready"
    assert data["reason"] is None
    assert "0" in data["tools"]
    assert data["tools"]["0"]["type"] == "PLA"
    assert data["tools"]["0"]["color"] == "ff0000"
    assert data["tools"]["0"]["match_quality"] == "exact"
    assert data["tools"]["0"]["resolved"]["ace"] == 0
    assert data["swaps"] == []


def test_sidecar_pending_has_reason(tmp_path):
    gcode_path = tmp_path / "test.gcode"
    gcode_path.write_text("G28\n")
    r = pp.ToolResolution(
        tool=pp.ToolMeta(index=0, type="ASA", color="aaaaaa"),
        match_quality="none", candidates=[], resolved=None,
    )
    pp.write_sidecar(gcode_path, [r], [], "pending", "missing_bindings")
    data = json.loads((tmp_path / "test.gcode.multiace.json").read_text())
    assert data["status"] == "pending"
    assert data["reason"] == "missing_bindings"
    assert data["tools"]["0"]["match_quality"] == "none"
