"""Tests for multiace_postprocess.match_tools()."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import multiace_postprocess as pp


def _slots_resp(bindings: list[dict]) -> dict:
    """Build a fake /api/slots response from a list of (ace, slot, material, color, id) tuples."""
    aces: dict[int, dict] = {}
    for b in bindings:
        ace, slot = b["ace"], b["slot"]
        aces.setdefault(ace, {"index": ace, "is_active": ace == 0, "slots": []})
        aces[ace]["slots"].append({
            "slot": slot,
            "gate_status": 1,
            "spool": {
                "spool_id": b["id"],
                "name": b.get("name", f"Spool-{b['id']}"),
                "material": b["material"],
                "color": b["color"],
                "weight_remaining_g": 800.0,
            },
        })
    return {"aces": list(aces.values())}


TOOLS_2COLOR = [
    {"type": "PLA", "color": "ff0000"},
    {"type": "PLA", "color": "ffffff"},
]

BINDINGS_2COLOR = [
    {"ace": 0, "slot": 0, "material": "PLA", "color": "ff0000", "id": 10},
    {"ace": 0, "slot": 1, "material": "PLA", "color": "ffffff", "id": 11},
]


def test_match_exact_two_tools():
    resolutions = pp.match_tools(TOOLS_2COLOR, _slots_resp(BINDINGS_2COLOR))
    assert len(resolutions) == 2
    assert resolutions[0].match_quality == "exact"
    assert resolutions[0].resolved.ace == 0
    assert resolutions[0].resolved.slot == 0
    assert resolutions[0].resolved.spool_id == 10
    assert resolutions[1].match_quality == "exact"
    assert resolutions[1].resolved.spool_id == 11


def test_match_none_when_no_binding():
    tools = [{"type": "ASA", "color": "aaaaaa"}]
    resolutions = pp.match_tools(tools, _slots_resp(BINDINGS_2COLOR))
    assert resolutions[0].match_quality == "none"
    assert resolutions[0].resolved is None
    assert resolutions[0].candidates == []


def test_match_ambiguous_two_same_filament():
    tools = [{"type": "PLA", "color": "ff0000"}]
    bindings = [
        {"ace": 0, "slot": 0, "material": "PLA", "color": "ff0000", "id": 10},
        {"ace": 0, "slot": 2, "material": "PLA", "color": "ff0000", "id": 20},
    ]
    resolutions = pp.match_tools(tools, _slots_resp(bindings))
    assert resolutions[0].match_quality == "ambiguous"
    assert resolutions[0].resolved is None
    assert len(resolutions[0].candidates) == 2


def test_match_across_two_aces():
    tools = [
        {"type": "PLA",  "color": "ff0000"},
        {"type": "PETG", "color": "0000ff"},
    ]
    bindings = [
        {"ace": 0, "slot": 0, "material": "PLA",  "color": "ff0000", "id": 10},
        {"ace": 1, "slot": 2, "material": "PETG", "color": "0000ff", "id": 42},
    ]
    resolutions = pp.match_tools(tools, _slots_resp(bindings))
    assert resolutions[0].match_quality == "exact"
    assert resolutions[0].resolved.ace == 0
    assert resolutions[1].match_quality == "exact"
    assert resolutions[1].resolved.ace == 1
    assert resolutions[1].resolved.slot == 2


def test_match_color_is_case_insensitive_from_spoolman():
    """Spoolman stores hex without # and may be uppercase; match must be lowercase-normalized."""
    tools = [{"type": "PLA", "color": "ff0000"}]
    bindings = [
        {"ace": 0, "slot": 0, "material": "PLA", "color": "FF0000", "id": 10},
    ]
    resolutions = pp.match_tools(tools, _slots_resp(bindings))
    assert resolutions[0].match_quality == "exact"


def test_match_skips_empty_slots():
    tools = [{"type": "PLA", "color": "ff0000"}]
    slots_resp = {
        "aces": [{"index": 0, "is_active": True, "slots": [
            {"slot": 0, "gate_status": 0, "spool": None},
            {"slot": 1, "gate_status": 1, "spool": {"spool_id": 5, "name": "PLA Red",
                "material": "PLA", "color": "ff0000", "weight_remaining_g": 900.0}},
        ]}]
    }
    resolutions = pp.match_tools(tools, slots_resp)
    assert resolutions[0].match_quality == "exact"
    assert resolutions[0].resolved.slot == 1
