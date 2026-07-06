# License: GPL-3.0
from filamenthub_plugin.mapping import normalize_color, spool_to_override


def test_normalize_color_adds_hash():
    assert normalize_color("0000ff") == "#0000ff"

def test_normalize_color_keeps_hash():
    assert normalize_color("#00ff00") == "#00ff00"

def test_normalize_color_blank_on_none():
    assert normalize_color(None) == ""
    assert normalize_color("") == ""

def test_normalize_color_rejects_non_hex_name():
    assert normalize_color("green") == ""

def test_normalize_color_rejects_too_short():
    assert normalize_color("0ff") == ""

def test_normalize_color_keeps_hash_uppercase():
    assert normalize_color("#00FF00") == "#00FF00"

def test_spool_to_override_maps_fields():
    spool = {"spool_id": 7, "name": "Galaxy Blue", "material": "PLA",
             "color": "0000ff", "vendor": "Generic",
             "weight_remaining_g": 812.0, "location": None}
    ov = spool_to_override(spool, ace=1, slot=2)
    assert ov == {"ace": 1, "slot": 2, "material": "PLA",
                  "brand": "Generic", "subtype": "Galaxy Blue",
                  "color": "#0000ff"}

def test_spool_to_override_handles_missing_fields():
    ov = spool_to_override({"spool_id": 1}, ace=0, slot=0)
    assert ov == {"ace": 0, "slot": 0, "material": "",
                  "brand": "", "subtype": "", "color": ""}
