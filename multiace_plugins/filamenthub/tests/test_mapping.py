# License: GPL-3.0
from filamenthub_plugin.mapping import (
    ace_state_row_to_override,
    material_split,
    normalize_color,
    spool_to_override,
    subtype_guess,
)

# Shape of decay71 GET /api/materials "db": material -> vendor -> [subtypes].
DB = {
    "PLA": {"Generic": ["Matte", "Silk"], "Snapmaker": ["SnapSpeed", "Wood"]},
    "PETG": {"Generic": ["HF"]},
    "TPU": {"Generic": ["95A HF", "95A"]},
    "PLA-CF": {"Generic": []},
}


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
    # Subtype is GUESSED (decay71 rule), never the raw spool name.
    assert ov == {"ace": 1, "slot": 2, "material": "PLA",
                  "brand": "Generic", "subtype": "",
                  "color": "#0000ff"}

def test_spool_to_override_handles_missing_fields():
    ov = spool_to_override({"spool_id": 1}, ace=0, slot=0)
    assert ov == {"ace": 0, "slot": 0, "material": "",
                  "brand": "", "subtype": "", "color": ""}


def test_ace_state_row_to_override_maps_fields_and_brand():
    row = {"ace": 1, "slot": 2, "spool_id": 42, "material": "PLA",
           "color": "00ff00", "name": "PolyTerra Green", "asserted_by": "user:assign"}
    out = ace_state_row_to_override(row, brand="PolyTerra")
    assert out == {"ace": 1, "slot": 2, "material": "PLA", "brand": "PolyTerra",
                   "subtype": "", "color": "#00ff00"}


def test_ace_state_row_to_override_blank_fields_default_to_empty_string():
    row = {"ace": 0, "slot": 0, "spool_id": 1, "material": None,
           "color": None, "name": None}
    out = ace_state_row_to_override(row, brand="")
    assert out == {"ace": 0, "slot": 0, "material": "", "brand": "",
                   "subtype": "", "color": ""}


# --- decay71 material split (main.py _spoolman_material_split) ---------------

def test_material_split_plus_becomes_plus_family():
    assert material_split("PLA+", DB) == ("PLA", "Plus")

def test_material_split_dash_family():
    assert material_split("PLA-HF", DB) == ("PLA", "HF")

def test_material_split_db_type_kept_with_db_spelling():
    assert material_split("pla-cf", DB) == ("PLA-CF", "")

def test_material_split_exact_case_insensitive():
    assert material_split("petg", DB) == ("PETG", "")

def test_material_split_unknown_base_unchanged():
    assert material_split("ASA+", DB) == ("ASA+", "")

def test_material_split_no_db_is_passthrough():
    assert material_split("PLA+", None) == ("PLA+", "")
    assert material_split("PLA+", {}) == ("PLA+", "")


# --- decay71 subtype guess (main.py _spoolman_subtype_guess) ------------------

def test_subtype_guess_known_subtype_anywhere_in_name():
    assert subtype_guess("Matte Black", "PLA", DB) == "Matte"

def test_subtype_guess_no_known_subtype_and_no_material_token():
    assert subtype_guess("Galaxy Blue", "PLA", DB) == ""

def test_subtype_guess_whole_word_only():
    assert subtype_guess("Silkyway Red", "PLA", DB) == ""

def test_subtype_guess_longest_first():
    assert subtype_guess("Flex 95A HF Black", "TPU", DB) == "95A HF"

def test_subtype_guess_returns_db_spelling():
    assert subtype_guess("matte white", "pla", DB) == "Matte"

def test_subtype_guess_fallback_text_after_material_token():
    # Unknown sub-type still picked up by the old rule (decay71 fallback).
    assert subtype_guess("PLA Glow Green", "PLA", DB) == "Glow Green"

def test_subtype_guess_fallback_truncated_to_32():
    out = subtype_guess("PLA " + "x" * 50, "PLA", DB)
    assert out == "x" * 32

def test_subtype_guess_db_unavailable_is_blank():
    assert subtype_guess("Matte Black", "PLA", None) == ""
    assert subtype_guess("Matte Black", "PLA", {}) == ""

def test_subtype_guess_empty_name():
    assert subtype_guess("", "PLA", DB) == ""


def test_spool_to_override_uses_guess_with_db():
    spool = {"name": "Matte Black", "material": "PLA", "vendor": "Generic",
             "color": "000000"}
    ov = spool_to_override(spool, ace=0, slot=1, materials_db=DB)
    assert ov["subtype"] == "Matte" and ov["material"] == "PLA"

def test_spool_to_override_splits_family_into_subtype():
    spool = {"name": "Black", "material": "PLA+", "vendor": "eSun",
             "color": "000000"}
    ov = spool_to_override(spool, ace=0, slot=1, materials_db=DB)
    assert ov["material"] == "PLA" and ov["subtype"] == "Plus"

def test_spool_to_override_guess_wins_over_family():
    spool = {"name": "Silk Gold", "material": "PLA+", "vendor": "X", "color": ""}
    ov = spool_to_override(spool, ace=0, slot=0, materials_db=DB)
    assert ov["material"] == "PLA" and ov["subtype"] == "Silk"

def test_ace_state_row_to_override_uses_guess_with_db():
    row = {"ace": 1, "slot": 3, "material": "PETG", "color": "ff0000",
           "name": "HF Red"}
    out = ace_state_row_to_override(row, brand="Generic", materials_db=DB)
    assert out["subtype"] == "HF" and out["material"] == "PETG"
