# License: GPL-3.0
import json
from filamenthub_plugin.desired_store import load_desired, save_desired


def test_missing_file_returns_empty(tmp_path):
    assert load_desired(str(tmp_path / "nope.json")) == {}


def test_roundtrip(tmp_path):
    p = str(tmp_path / "d.json")
    slots = {"0_2": {"ace": 0, "slot": 2, "spool_id": 110, "material": "PLA",
                     "brand": "Snapmaker", "subtype": "SnapSpeed Red", "color": "#FF0000"}}
    save_desired(p, "u1-1", slots)
    assert load_desired(p) == slots
    with open(p) as f:
        assert json.load(f)["printer"] == "u1-1"


def test_corrupt_file_returns_empty(tmp_path):
    p = tmp_path / "d.json"
    p.write_text("{not json")
    assert load_desired(str(p)) == {}
