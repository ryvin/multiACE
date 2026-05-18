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
