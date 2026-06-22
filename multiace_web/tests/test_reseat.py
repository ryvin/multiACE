"""Tests for the slot reseat endpoint.

Reseat nudges an empty-reading slot (filament physically present but the ACE's
inlet sensor reads empty) with a small ACE_RETRACT to drag the tail back onto
the sensor, then reads gate_status back live from the `ace` Klipper object to
report whether the ACE now detects filament. Only valid on the active ACE,
since ACE_RETRACT acts on the active device and gate_status reflects it.
"""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import multiace_web.server as server
from multiace_web.server import create_app
from multiace_web.moonraker import MoonrakerError

_STATIC = Path(__file__).resolve().parent.parent / "src" / "multiace_web" / "static"


@pytest.fixture(autouse=True)
def _no_settle(monkeypatch):
    # Skip the post-retract heartbeat wait so tests don't sleep.
    monkeypatch.setattr(server, "RESEAT_SETTLE_S", 0)


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
    inst.query_objects = AsyncMock(return_value={"ace": {"gate_status": [1, 1, 1, 0]}})
    mcls.return_value = inst
    monkeypatch.setattr("multiace_web.server.MoonrakerClient", mcls)
    return create_app(static_dir=_STATIC, start_background_tasks=False)


def _set_active(app, idx):
    app.state.state.active_device = idx


def test_reseat_detected_when_gate_flips(app):
    # active ACE 1 (B), slot 3 empty -> after retract the ace object reports it filled
    with TestClient(app) as c:
        _set_active(app, 1)
        app.state.moonraker.query_objects = AsyncMock(
            return_value={"ace": {"gate_status": [1, 1, 1, 1]}})
        r = c.post("/api/slots/1/3/reseat")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["detected"] is True
    assert body["gate_status"] == 1
    # issued a bounded retract on the right slot
    gcode = app.state.moonraker.run_gcode.await_args.args[0]
    assert gcode.startswith("ACE_RETRACT INDEX=3 ")
    assert "LENGTH=" in gcode and "SPEED=" in gcode


def test_reseat_not_detected_when_still_empty(app):
    # gate stays 0 for slot 3 -> filament past the wheel, hand-reseat needed
    with TestClient(app) as c:
        _set_active(app, 1)
        app.state.moonraker.query_objects = AsyncMock(
            return_value={"ace": {"gate_status": [1, 1, 1, 0]}})
        r = c.post("/api/slots/1/3/reseat")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["detected"] is False
    assert body["gate_status"] == 0


def test_reseat_updates_state_gate_status_for_chip_refresh(app):
    # The endpoint must write the read-back array into CurrentState so the slot
    # chip refreshes (no STATE audit line is emitted by the reseat).
    with TestClient(app) as c:
        _set_active(app, 1)
        app.state.state.gate_status = [1, 1, 1, 0]
        app.state.moonraker.query_objects = AsyncMock(
            return_value={"ace": {"gate_status": [1, 1, 1, 1]}})
        r = c.post("/api/slots/1/3/reseat")
        assert r.status_code == 200
        assert app.state.state.gate_status == [1, 1, 1, 1]


def test_reseat_409_when_ace_not_active(app):
    with TestClient(app) as c:
        _set_active(app, 0)            # ACE A active
        r = c.post("/api/slots/1/3/reseat")  # but reseating ACE B
    assert r.status_code == 409
    app.state.moonraker.run_gcode.assert_not_awaited()


def test_reseat_422_on_bad_slot(app):
    with TestClient(app) as c:
        _set_active(app, 1)
        r = c.post("/api/slots/1/9/reseat")
    assert r.status_code == 422
    app.state.moonraker.run_gcode.assert_not_awaited()


def test_reseat_422_on_bad_ace(app):
    with TestClient(app) as c:
        _set_active(app, 1)
        r = c.post("/api/slots/7/3/reseat")
    assert r.status_code == 422


def test_reseat_502_when_retract_fails(app):
    with TestClient(app) as c:
        _set_active(app, 1)
        app.state.moonraker.run_gcode = AsyncMock(side_effect=MoonrakerError("boom"))
        r = c.post("/api/slots/1/3/reseat")
    assert r.status_code == 502


def test_reseat_detected_none_when_readback_fails(app):
    # retract succeeds but the gate read-back errors -> ok with detected None
    with TestClient(app) as c:
        _set_active(app, 1)
        app.state.moonraker.query_objects = AsyncMock(
            side_effect=MoonrakerError("query down"))
        r = c.post("/api/slots/1/3/reseat")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["detected"] is None
