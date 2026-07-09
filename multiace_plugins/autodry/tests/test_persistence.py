# License: GPL-3.0
import json

from autodry_plugin.fsm import AutodryManager
from autodry_plugin.persistence import load_manager, save_manager


def test_load_manager_missing_file_returns_empty(tmp_path):
    mgr = load_manager(tmp_path / "missing.json")
    assert mgr.fsms == {}


def test_load_manager_corrupt_json_returns_empty(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("{not valid json")
    mgr = load_manager(p)
    assert mgr.fsms == {}


def test_load_manager_non_dict_json_returns_empty(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("[1, 2, 3]")
    mgr = load_manager(p)
    assert mgr.fsms == {}


def test_save_then_load_round_trip(tmp_path):
    p = tmp_path / "state.json"
    mgr = AutodryManager()
    mgr.get(0).config.enabled = True
    mgr.get(0).config.target_pct = 25
    mgr.get(1).config.temp_c = 60

    save_manager(p, mgr)
    reloaded = load_manager(p)

    assert reloaded.get(0).config.enabled is True
    assert reloaded.get(0).config.target_pct == 25
    assert reloaded.get(1).config.temp_c == 60
    assert json.loads(p.read_text())["schema"] == 2


def test_save_manager_creates_parent_dirs(tmp_path):
    p = tmp_path / "nested" / "dir" / "state.json"
    save_manager(p, AutodryManager())
    assert p.exists()
