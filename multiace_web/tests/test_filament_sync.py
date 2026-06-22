"""Tests for FilamentHub -> printer filament sync.

The ACE reads no RFID for 3rd-party spools, so Klipper's print_task_config
stays blank and the printer reports filament 'not recognized' at print start.
Sync pushes the FilamentHub-designated material/color/vendor for each loaded
head into print_task_config via SET_PRINT_FILAMENT_CONFIG, mapping
head_source[head] -> (ace, slot) -> the spool bound there.
"""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from multiace_web.server import create_app, _resolve_head_filaments
from multiace_web.spoolman import SpoolBinding
from multiace_web.moonraker import MoonrakerError

_STATIC = Path(__file__).resolve().parent.parent / "src" / "multiace_web" / "static"


def _binding(spool_id, material, color, vendor):
    return SpoolBinding(spool_id=spool_id, name=f"sp{spool_id}", material=material,
                        color=color, weight_remaining_g=1000.0, vendor=vendor)


@pytest.fixture
def app(tmp_path, monkeypatch):
    (tmp_path / "logs").mkdir()
    (tmp_path / "ace.cfg").write_text("[ace]\nfeed_speed: 80\n")
    monkeypatch.setenv("MULTIACE_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MULTIACE_CONFIG", str(tmp_path / "ace.cfg"))
    monkeypatch.setenv("MOONRAKER_URL", "http://printer:7125")
    monkeypatch.delenv("MULTIACE_TOKEN", raising=False)
    mcls = MagicMock()
    inst = MagicMock()
    inst.close = AsyncMock()
    inst.run_gcode = AsyncMock(return_value="ok")
    inst.get_logs = AsyncMock(return_value=[])
    mcls.return_value = inst
    monkeypatch.setattr("multiace_web.server.MoonrakerClient", mcls)
    return create_app(static_dir=_STATIC, start_background_tasks=False)


# ---- unit: head_source x bindings resolution -----------------------------

def test_resolve_maps_head_to_bound_spool():
    state = MagicMock()
    state.head_source = {
        0: {"ace": 1, "slot": 0}, 1: {"ace": 0, "slot": 1},
        2: None, 3: {"ace": 0, "slot": 3},
    }
    cache = {
        0: {1: _binding(40, "PLA", "1A1A1A", "Jayo"),
            3: _binding(107, "PLA", "FFFFFF", "Comgrow")},
        1: {0: _binding(102, "PLA", "808080", "Snapmaker")},
    }
    out = _resolve_head_filaments(state, cache)
    assert set(out) == {0, 1, 3}              # head 2 has no source -> skipped
    assert out[0].spool_id == 102             # head 0 <- ace1/slot0
    assert out[1].vendor == "Jayo"
    assert out[3].color == "FFFFFF"


def test_resolve_skips_heads_without_binding():
    state = MagicMock()
    state.head_source = {0: {"ace": 1, "slot": 2}}   # no binding at ace1/slot2
    out = _resolve_head_filaments(state, {1: {}})
    assert out == {}


# ---- endpoint: POST /api/filament/sync -----------------------------------

def test_sync_pushes_config_per_loaded_head(app):
    with TestClient(app) as c:
        app.state.state.head_source = {
            0: {"ace": 1, "slot": 0}, 1: {"ace": 0, "slot": 1},
        }
        app.state.spool_cache = {
            0: {1: _binding(40, "PLA", "1A1A1A", "Jayo")},
            1: {0: _binding(102, "PETG", "808080", "Snapmaker")},
        }
        r = c.post("/api/filament/sync")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["count"] == 2
    calls = [ca.args[0] for ca in app.state.moonraker.run_gcode.await_args_list]
    joined = "\n".join(calls)
    assert 'SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=0 FILAMENT_TYPE="PETG"' in joined
    assert "FILAMENT_COLOR_RGBA=808080" in joined
    assert 'VENDOR="Snapmaker"' in joined
    assert 'CONFIG_EXTRUDER=1 FILAMENT_TYPE="PLA"' in joined


def test_sync_no_bindings_returns_zero(app):
    with TestClient(app) as c:
        app.state.state.head_source = {0: None, 1: None, 2: None, 3: None}
        app.state.spool_cache = {}
        r = c.post("/api/filament/sync")
    assert r.status_code == 200
    assert r.json()["count"] == 0
    app.state.moonraker.run_gcode.assert_not_awaited()


def test_sync_reports_per_head_failure(app):
    with TestClient(app) as c:
        app.state.state.head_source = {0: {"ace": 0, "slot": 0}, 1: {"ace": 0, "slot": 1}}
        app.state.spool_cache = {0: {
            0: _binding(1, "PLA", "FF0000", "A"),
            1: _binding(2, "PLA", "00FF00", "B"),
        }}
        # first push ok, second fails
        app.state.moonraker.run_gcode = AsyncMock(side_effect=["ok", MoonrakerError("boom")])
        r = c.post("/api/filament/sync")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["count"] == 1
    assert len(body["failed"]) == 1
    assert body["failed"][0]["head"] == 1


def test_sync_color_normalized_to_six_hex(app):
    with TestClient(app) as c:
        app.state.state.head_source = {0: {"ace": 0, "slot": 0}}
        app.state.spool_cache = {0: {0: _binding(1, "PLA", "#AABBCCFF", "V")}}
        r = c.post("/api/filament/sync")
    assert r.status_code == 200
    g = app.state.moonraker.run_gcode.await_args.args[0]
    assert "FILAMENT_COLOR_RGBA=AABBCC " in g    # '#' stripped, 6 hex only


# ---- auto-sync on assign -------------------------------------------------

def test_assign_pushes_config_for_loaded_head(app):
    sm = MagicMock()
    sm.assign_spool = AsyncMock(return_value={"printer": "u1", "ace": 0, "slot": 1})
    sm.list_all_bindings = AsyncMock(return_value={
        0: {1: _binding(40, "PLA", "1A1A1A", "Jayo")}})
    with TestClient(app) as c:
        app.state.spoolman = sm
        # head 2 is loaded from ace0/slot1 -> assigning that slot should push head 2
        app.state.state.head_source = {2: {"ace": 0, "slot": 1}}
        r = c.post("/api/slots/0/1/assign", json={"spool_id": 40})
    assert r.status_code == 200
    calls = [ca.args[0] for ca in app.state.moonraker.run_gcode.await_args_list]
    assert any("SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=2" in g for g in calls)
