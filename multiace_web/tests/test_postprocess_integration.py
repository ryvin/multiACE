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
