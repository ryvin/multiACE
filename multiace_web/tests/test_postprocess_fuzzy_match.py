"""Tests for the tiered/fuzzy color matcher in multiace_postprocess.

Exact (type + hex) matching is the default and unchanged. When the slicer's
filament_colour hex does not byte-match a spool's RFID hex, an *opt-in* tiered
fallback (fuzzy=True) resolves within the same material via, in order:
  exact_hex -> name_exact -> name_canon -> fuzzy (RGB distance <= threshold)
This mirrors decay71 0.98b's post_process_virtual_toolheads matcher, grafted
onto our per-tool match_tools() API.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import multiace_postprocess as pp


def _slots_resp(bindings: list[dict]) -> dict:
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


# --- color helpers ---------------------------------------------------------

def test_approx_color_name_known_hexes():
    assert pp.approx_color_name("#e02020") == "Red"
    assert pp.approx_color_name("e02020") == "Red"
    assert pp.approx_color_name("ffffff") == "White"
    assert pp.approx_color_name("00008b") == "DarkBlue"


def test_approx_color_name_unparseable_passthrough():
    assert pp.approx_color_name("xyz") == "xyz"     # too short to parse
    assert pp.approx_color_name("") == "?"          # empty


def test_canonical_color_name_qualifier_and_synonym():
    assert pp._canonical_color_name("DarkRed") == "Red"
    assert pp._canonical_color_name("LightGray") == "Gray"
    assert pp._canonical_color_name("Silver") == "Gray"
    assert pp._canonical_color_name("Gold") == "Yellow"
    assert pp._canonical_color_name("Blue") == "Blue"


def test_color_distance_basic():
    assert pp.color_distance("ff0000", "ff0000") == 0.0
    # opposite corners of the cube
    assert round(pp.color_distance("000000", "ffffff"), 1) == 441.7
    assert pp.color_distance("zzz", "ff0000") is None


# --- match_tools: fallback is OFF by default (regression guard) -------------

def test_near_miss_is_none_when_fuzzy_off():
    tools = [{"type": "PLA", "color": "ff0303"}]
    bindings = [{"ace": 0, "slot": 0, "material": "PLA", "color": "ff0000", "id": 10}]
    res = pp.match_tools(tools, _slots_resp(bindings))
    assert res[0].match_quality == "none"
    assert res[0].resolved is None


def test_exact_sets_tier_exact_hex():
    tools = [{"type": "PLA", "color": "ff0000"}]
    bindings = [{"ace": 0, "slot": 0, "material": "PLA", "color": "ff0000", "id": 10}]
    res = pp.match_tools(tools, _slots_resp(bindings), fuzzy=True)
    assert res[0].match_quality == "exact"
    assert res[0].tier == "exact_hex"


# --- match_tools: fuzzy fallback ON ----------------------------------------

def test_near_miss_resolves_when_fuzzy_on():
    tools = [{"type": "PLA", "color": "ff0505"}]
    bindings = [{"ace": 0, "slot": 1, "material": "PLA", "color": "ff0000", "id": 11}]
    res = pp.match_tools(tools, _slots_resp(bindings), fuzzy=True)
    assert res[0].match_quality == "approx"
    assert res[0].resolved.slot == 1
    assert res[0].resolved.spool_id == 11


def test_name_exact_tier_matches_far_hex_same_name():
    # ff4040 and e02020 are >40 apart in RGB but both nearest "Red".
    tools = [{"type": "PLA", "color": "ff4040"}]
    bindings = [{"ace": 0, "slot": 0, "material": "PLA", "color": "e02020", "id": 10}]
    res = pp.match_tools(tools, _slots_resp(bindings), fuzzy=True)
    assert res[0].match_quality == "approx"
    assert res[0].tier == "name_exact"


def test_name_canon_tier_matches_gold_to_yellow():
    # e0a020 -> Gold -> (synonym) Yellow ; ffe020 -> Yellow. Distance ~71 (>40),
    # so only the canonical-name tier can match.
    tools = [{"type": "PLA", "color": "e0a020"}]
    bindings = [{"ace": 0, "slot": 0, "material": "PLA", "color": "ffe020", "id": 10}]
    res = pp.match_tools(tools, _slots_resp(bindings), fuzzy=True)
    assert res[0].match_quality == "approx"
    assert res[0].tier == "name_canon"


def test_fuzzy_tier_matches_within_threshold_only():
    # e0a020 (Gold/Yellow) vs ff8c00 (Orange): different name AND canonical,
    # distance ~48.8. Misses with default threshold, hits at 60.
    tools = [{"type": "PLA", "color": "e0a020"}]
    bindings = [{"ace": 0, "slot": 2, "material": "PLA", "color": "ff8c00", "id": 7}]
    assert pp.match_tools(tools, _slots_resp(bindings), fuzzy=True)[0].match_quality == "none"
    res = pp.match_tools(tools, _slots_resp(bindings), fuzzy=True, fuzzy_max_distance=60)
    assert res[0].match_quality == "approx"
    assert res[0].tier == "fuzzy"
    assert res[0].resolved.spool_id == 7


def test_fuzzy_never_crosses_material():
    tools = [{"type": "ABS", "color": "ff0000"}]
    bindings = [{"ace": 0, "slot": 0, "material": "PLA", "color": "ff0000", "id": 10}]
    res = pp.match_tools(tools, _slots_resp(bindings), fuzzy=True)
    assert res[0].match_quality == "none"


def test_fuzzy_far_color_stays_none():
    tools = [{"type": "PLA", "color": "ff0000"}]  # Red
    bindings = [{"ace": 0, "slot": 0, "material": "PLA", "color": "0000ff", "id": 10}]  # Blue
    res = pp.match_tools(tools, _slots_resp(bindings), fuzzy=True)
    assert res[0].match_quality == "none"


def test_exact_preferred_over_fuzzy_candidate():
    tools = [{"type": "PLA", "color": "ff0000"}]
    bindings = [
        {"ace": 0, "slot": 0, "material": "PLA", "color": "ff0505", "id": 11},  # near
        {"ace": 0, "slot": 3, "material": "PLA", "color": "ff0000", "id": 10},  # exact
    ]
    res = pp.match_tools(tools, _slots_resp(bindings), fuzzy=True)
    assert res[0].match_quality == "exact"
    assert res[0].tier == "exact_hex"
    assert res[0].resolved.spool_id == 10
