import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from multiace_web.server import create_app


@pytest.fixture
def app(tmp_path, monkeypatch):
    """App configured against tmp paths so tests don't touch real printer state."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    cfg_path = tmp_path / "ace.cfg"
    cfg_path.write_text(
        "[ace]\nfeed_speed: 80\nretract_speed: 30\nload_length: 880\n"
    )
    static_dir = Path(__file__).resolve().parent.parent / "static"
    monkeypatch.setenv("MULTIACE_LOG_DIR", str(log_dir))
    monkeypatch.setenv("MULTIACE_CONFIG", str(cfg_path))
    monkeypatch.setenv("MOONRAKER_URL", "http://printer:7125")
    monkeypatch.setenv("MULTIACE_AUTODRY_STATE_PATH", str(tmp_path / "autodry.json"))
    monkeypatch.delenv("MULTIACE_TOKEN", raising=False)

    # Patch MoonrakerClient so lifespan uses a mock instance instead of real HTTP client.
    mock_moonraker_class = MagicMock()
    mock_instance = MagicMock()
    mock_instance.close = AsyncMock()
    mock_instance.run_gcode = AsyncMock(return_value="ok")
    mock_instance.get_logs = AsyncMock(return_value=[])
    mock_moonraker_class.return_value = mock_instance
    monkeypatch.setattr("multiace_web.server.MoonrakerClient", mock_moonraker_class)

    return create_app(static_dir=static_dir, start_background_tasks=False)


def test_bootstrap_state_from_log_when_log_has_recent_entry(tmp_path, monkeypatch):
    """After a service restart, the LogTailer starts at EOF — but the multiACE
    state should pre-populate from the most recent STATE entry in the existing
    log. Otherwise /api/state stays blank until the next real event.
    """
    log_dir = tmp_path / "logs"; log_dir.mkdir()
    state_log = log_dir / "multiace_state.log"
    cfg_path = tmp_path / "ace.cfg"
    cfg_path.write_text("[ace]\nfeed_speed: 80\n")
    static_dir = Path(__file__).resolve().parent.parent / "static"

    # Seed the log with a realistic recent STATE entry showing 4 toolheads loaded.
    state_log.write_text(
        '2026-04-29 13:30:22 STATE {"action": "SWITCH_AUTO_PASSIVE", "params": {"head": 1, "target_ace": 0, "target_slot": 1, "action": "same_ace_noop"}, '
        '"active_device": 0, "device_count": 1, "connected": true, '
        '"serial": "/dev/serial/by-path/test", "mode": "multi", '
        '"swap_in_progress": false, "auto_feed": false, "feed_assist": 1, '
        '"gate_status": [1, 1, 1, 1], '
        '"head_source": {"0": {"ace": 0, "slot": 0, "type": "PLA", "color": "FF0000"}, '
        '"1": {"ace": 0, "slot": 1, "type": "PLA", "color": "00FF00"}, '
        '"2": null, "3": null}, '
        '"sensors": {"0": true, "1": true, "2": false, "3": false}, '
        '"print_task_config": {"0": {"type": "PLA", "color": 4294506524, "vendor": "Snapmaker"}, '
        '"1": {"type": "PLA", "color": 4293340957, "vendor": "Generic"}, '
        '"2": {"type": "NONE", "color": 4294967295, "vendor": "NONE"}, '
        '"3": {"type": "NONE", "color": 4294967295, "vendor": "NONE"}}}\n'
    )

    monkeypatch.setenv("MULTIACE_LOG_DIR", str(log_dir))
    monkeypatch.setenv("MULTIACE_CONFIG", str(cfg_path))
    monkeypatch.setenv("MOONRAKER_URL", "http://printer:7125")
    monkeypatch.setenv("MULTIACE_AUTODRY_STATE_PATH", str(tmp_path / "autodry.json"))

    mock_class = MagicMock()
    mock_instance = MagicMock()
    mock_instance.close = AsyncMock()
    mock_class.return_value = mock_instance
    monkeypatch.setattr("multiace_web.server.MoonrakerClient", mock_class)

    fresh_app = create_app(static_dir=static_dir, start_background_tasks=False)
    with TestClient(fresh_app) as client:
        resp = client.get("/api/state")
    body = resp.json()
    assert body["device_count"] == 1
    assert body["gate_status"] == [1, 1, 1, 1]
    assert body["head_source"]["0"]["slot"] == 0
    assert body["last_action_at"] == "2026-04-29 13:30:22"


def test_bootstrap_state_handles_missing_log(tmp_path, monkeypatch):
    """If the log file doesn't exist yet (fresh install), don't crash."""
    log_dir = tmp_path / "logs"; log_dir.mkdir()
    cfg_path = tmp_path / "ace.cfg"
    cfg_path.write_text("[ace]\nfeed_speed: 80\n")
    static_dir = Path(__file__).resolve().parent.parent / "static"
    monkeypatch.setenv("MULTIACE_LOG_DIR", str(log_dir))
    monkeypatch.setenv("MULTIACE_CONFIG", str(cfg_path))
    monkeypatch.setenv("MOONRAKER_URL", "http://printer:7125")
    monkeypatch.setenv("MULTIACE_AUTODRY_STATE_PATH", str(tmp_path / "autodry.json"))
    mock_class = MagicMock()
    mock_instance = MagicMock(); mock_instance.close = AsyncMock()
    mock_class.return_value = mock_instance
    monkeypatch.setattr("multiace_web.server.MoonrakerClient", mock_class)

    fresh_app = create_app(static_dir=static_dir, start_background_tasks=False)
    with TestClient(fresh_app) as client:
        resp = client.get("/api/state")
    assert resp.status_code == 200
    assert resp.json()["device_count"] == 0  # default


def test_health_endpoint(app):
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_state_endpoint_returns_default_state(app):
    with TestClient(app) as client:
        resp = client.get("/api/state")
    assert resp.status_code == 200
    body = resp.json()
    assert "active_device" in body
    assert "gate_status" in body
    assert body["gate_status"] == [0, 0, 0, 0]


def test_events_endpoint_empty_initially(app):
    with TestClient(app) as client:
        resp = client.get("/api/events")
    assert resp.status_code == 200
    assert resp.json() == {"events": []}


def test_config_get_returns_current_values(app):
    with TestClient(app) as client:
        resp = client.get("/api/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["values"]["feed_speed"] == "80"
    assert body["values"]["load_length"] == "880"


def test_command_endpoint_proxies_to_moonraker(app):
    with TestClient(app) as client:
        app.state.moonraker.run_gcode = AsyncMock(return_value="ok")
        resp = client.post("/api/command", json={"macro": "ACEC__Load_T1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["result"] == "ok"
    app.state.moonraker.run_gcode.assert_awaited_with("ACEC__Load_T1")


def test_command_endpoint_rejects_missing_macro(app):
    with TestClient(app) as client:
        resp = client.post("/api/command", json={})
    assert resp.status_code == 422  # Pydantic validation


def test_command_endpoint_returns_502_on_moonraker_error(app):
    from multiace_web.moonraker import MoonrakerError
    with TestClient(app) as client:
        app.state.moonraker.run_gcode = AsyncMock(side_effect=MoonrakerError("timeout"))
        resp = client.post("/api/command", json={"macro": "ACEC__Load_T1"})
    assert resp.status_code == 502
    assert "timeout" in resp.json()["detail"]


def test_config_put_writes_and_restarts(app):
    # Need 'feed_speed' to already exist in the test ace.cfg (it does)
    with TestClient(app) as client:
        app.state.moonraker.run_gcode = AsyncMock(return_value="ok")
        resp = client.put("/api/config", json={"values": {"feed_speed": "100"}})
    assert resp.status_code == 200
    assert resp.json()["restarted"] is True
    app.state.moonraker.run_gcode.assert_awaited_with("RESTART")
    text = app.state.config_path.read_text()
    assert "feed_speed: 100" in text


def test_config_put_rejects_empty_body(app):
    with TestClient(app) as client:
        resp = client.put("/api/config", json={})
    assert resp.status_code == 422


def test_logs_klippy_returns_lines(app):
    with TestClient(app) as client:
        app.state.moonraker.get_logs = AsyncMock(return_value=["line 1", "line 2"])
        resp = client.get("/api/logs/klippy")
    assert resp.status_code == 200
    assert resp.json()["lines"] == ["line 1", "line 2"]


def test_logs_unknown_kind_returns_400(app):
    with TestClient(app) as client:
        resp = client.get("/api/logs/nonsense")
    assert resp.status_code == 400


def test_command_endpoint_rejects_lowercase_macro(app):
    with TestClient(app) as client:
        resp = client.post("/api/command", json={"macro": "lowercase_bad"})
    assert resp.status_code == 422


def test_command_endpoint_rejects_special_chars_in_macro(app):
    with TestClient(app) as client:
        resp = client.post("/api/command", json={"macro": "ACE; rm -rf /"})
    assert resp.status_code == 422


def test_config_put_rejects_unknown_key(app):
    """Keys must already exist in ace.cfg — prevents injection of new keys."""
    with TestClient(app) as client:
        resp = client.put("/api/config", json={"values": {"evil_new_key": "1"}})
    assert resp.status_code == 400
    assert "unknown" in resp.json()["detail"].lower()


def test_config_put_rejects_value_with_newline(app):
    """Newlines in values would inject extra config lines."""
    with TestClient(app) as client:
        resp = client.put("/api/config", json={"values": {"feed_speed": "100\nrestart_method: hard"}})
    assert resp.status_code == 422


def test_config_put_rejects_value_with_hash(app):
    """# would be interpreted as a Klipper comment, mismatching read/write."""
    with TestClient(app) as client:
        resp = client.put("/api/config", json={"values": {"feed_speed": "100 # malicious"}})
    assert resp.status_code == 422


def test_config_put_partial_success_when_restart_fails(app):
    """File written successfully but Moonraker RESTART failed → 502."""
    from multiace_web.moonraker import MoonrakerError
    with TestClient(app) as client:
        app.state.moonraker.run_gcode = AsyncMock(side_effect=MoonrakerError("klipper down"))
        resp = client.put("/api/config", json={"values": {"feed_speed": "100"}})
    assert resp.status_code == 502
    assert "saved but RESTART failed" in resp.json()["detail"]
    # File was still written
    text = app.state.config_path.read_text()
    assert "feed_speed: 100" in text


def test_websocket_sends_initial_state_on_connect(app):
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
    assert msg["type"] == "state"
    assert "gate_status" in msg["payload"]


def test_websocket_responds_to_ping(app):
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # initial state
            ws.send_text("ping")
            assert ws.receive_text() == "pong"


def test_websocket_rejects_missing_token_when_configured(monkeypatch, tmp_path):
    """When MULTIACE_TOKEN is set, WS without ?token= or Bearer header is rejected."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    cfg_path = tmp_path / "ace.cfg"
    cfg_path.write_text("[ace]\nfeed_speed: 80\n")
    static_dir = Path(__file__).resolve().parent.parent / "static"
    monkeypatch.setenv("MULTIACE_LOG_DIR", str(log_dir))
    monkeypatch.setenv("MULTIACE_CONFIG", str(cfg_path))
    monkeypatch.setenv("MOONRAKER_URL", "http://printer:7125")
    monkeypatch.setenv("MULTIACE_TOKEN", "secret")
    monkeypatch.setenv("MULTIACE_AUTODRY_STATE_PATH", str(tmp_path / "autodry.json"))

    # Patch MoonrakerClient so lifespan can construct a mock
    from unittest.mock import AsyncMock, MagicMock
    mock_class = MagicMock()
    mock_instance = AsyncMock()
    mock_instance.close = AsyncMock()
    mock_class.return_value = mock_instance
    monkeypatch.setattr("multiace_web.server.MoonrakerClient", mock_class)

    from multiace_web.server import create_app
    secured_app = create_app(static_dir=static_dir, start_background_tasks=False)
    with TestClient(secured_app) as client:
        with pytest.raises(Exception):  # WS handshake will fail with close code
            with client.websocket_connect("/ws") as ws:
                ws.receive_json()


# =====================================================================
# /api/print — proxies Moonraker print_stats / virtual_sdcard / toolhead
# / ace / temperature_sensor cavity, and includes external humidity.
# =====================================================================

def _print_query_payload(**overrides):
    """Default Moonraker query_objects() return shape; tweak with overrides."""
    payload = {
        "print_stats": {
            "state": "printing",
            "filename": "1234-abc_plate_1.gcode",
            "print_duration": 1000.0,
            "total_duration": 1100.0,
            "info": {"current_layer": 50, "total_layer": 200},
            "exception": {},
            "message": "",
        },
        "virtual_sdcard": {"progress": 0.25},
        "toolhead": {"extruder": "extruder2"},
        "ace": {
            "dryer_status": {
                "status": "drying",
                "target_temp": 50,
                "duration": 240,        # MINUTES (Klipper convention)
                "remain_time": 14400,   # SECONDS (Klipper convention)
            }
        },
        "temperature_sensor cavity": {"temperature": 46.5},
    }
    payload.update(overrides)
    return payload


def test_print_endpoint_summarizes_state(app):
    with TestClient(app) as client:
        app.state.moonraker.query_objects = AsyncMock(return_value=_print_query_payload())
        resp = client.get("/api/print")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "printing"
    # Filename returned verbatim (frontend strips slicer prefix)
    assert body["filename"] == "1234-abc_plate_1.gcode"
    assert body["progress"] == 0.25
    assert body["layer"] == 50
    assert body["total_layer"] == 200


def test_print_endpoint_eta_extrapolation(app):
    """ETA = (print_duration / progress) - print_duration when progress > 0."""
    payload = _print_query_payload()
    payload["print_stats"]["print_duration"] = 1000.0
    payload["virtual_sdcard"]["progress"] = 0.25
    with TestClient(app) as client:
        app.state.moonraker.query_objects = AsyncMock(return_value=payload)
        resp = client.get("/api/print")
    body = resp.json()
    assert body["eta_sec"] == pytest.approx(3000.0)


def test_print_endpoint_eta_none_when_no_progress(app):
    """No ETA when virtual_sdcard.progress is ~0 (avoids division blow-up)."""
    payload = _print_query_payload()
    payload["virtual_sdcard"]["progress"] = 0.0
    with TestClient(app) as client:
        app.state.moonraker.query_objects = AsyncMock(return_value=payload)
        resp = client.get("/api/print")
    assert resp.json()["eta_sec"] is None


@pytest.mark.parametrize("ext_name,expected", [
    ("extruder", 0),
    ("extruder1", 1),
    ("extruder2", 2),
    ("extruder3", 3),
    ("", None),
    (None, None),
    ("extruder_weird", None),
])
def test_print_endpoint_maps_extruder_name_to_head_index(app, ext_name, expected):
    """Klipper uses 'extruder' (T0) and 'extruderN' for the rest."""
    payload = _print_query_payload()
    payload["toolhead"]["extruder"] = ext_name
    with TestClient(app) as client:
        app.state.moonraker.query_objects = AsyncMock(return_value=payload)
        resp = client.get("/api/print")
    assert resp.json()["current_extruder"] == expected


def test_print_endpoint_normalizes_empty_exception_to_none(app):
    """Klipper sometimes returns exception={}; treat as None for the dashboard."""
    payload = _print_query_payload()
    payload["print_stats"]["exception"] = {}
    with TestClient(app) as client:
        app.state.moonraker.query_objects = AsyncMock(return_value=payload)
        resp = client.get("/api/print")
    assert resp.json()["exception"] is None


def test_print_endpoint_passes_real_exception_through(app):
    payload = _print_query_payload()
    payload["print_stats"]["state"] = "paused"
    payload["print_stats"]["exception"] = {
        "code": 45, "message": "Extruder pickup failed", "level": 2,
    }
    with TestClient(app) as client:
        app.state.moonraker.query_objects = AsyncMock(return_value=payload)
        resp = client.get("/api/print")
    body = resp.json()
    assert body["state"] == "paused"
    assert body["exception"]["code"] == 45
    assert "pickup failed" in body["exception"]["message"]


def test_print_endpoint_dryer_unit_conversion(app):
    """duration is minutes; remain_time is seconds; backend normalizes both to minutes."""
    payload = _print_query_payload()
    payload["ace"]["dryer_status"] = {
        "status": "drying",
        "target_temp": 50,
        "duration": 240,        # 4h in minutes
        "remain_time": 14353,   # ~239 minutes in seconds
    }
    with TestClient(app) as client:
        app.state.moonraker.query_objects = AsyncMock(return_value=payload)
        resp = client.get("/api/print")
    dryer = resp.json()["dryer"]
    assert dryer["status"] == "drying"
    assert dryer["target_temp"] == 50
    assert dryer["duration_min"] == 240
    assert dryer["remain_min"] == 239   # rounded from 14353/60
    assert dryer["remain_sec"] == 14353


def test_print_endpoint_dryer_idle_when_stop(app):
    payload = _print_query_payload()
    payload["ace"]["dryer_status"] = {
        "status": "stop", "target_temp": 0, "duration": 0, "remain_time": 0,
    }
    with TestClient(app) as client:
        app.state.moonraker.query_objects = AsyncMock(return_value=payload)
        resp = client.get("/api/print")
    assert resp.json()["dryer"]["status"] == "stop"


def test_print_endpoint_includes_cavity_temp(app):
    payload = _print_query_payload()
    payload["temperature_sensor cavity"] = {"temperature": 47.2}
    with TestClient(app) as client:
        app.state.moonraker.query_objects = AsyncMock(return_value=payload)
        resp = client.get("/api/print")
    assert resp.json()["cavity_temp_c"] == pytest.approx(47.2)


def test_print_endpoint_humidity_unconfigured_when_no_url(app, monkeypatch):
    monkeypatch.delenv("MULTIACE_HUMIDITY_URL", raising=False)
    with TestClient(app) as client:
        app.state.moonraker.query_objects = AsyncMock(return_value=_print_query_payload())
        resp = client.get("/api/print")
    assert resp.json()["humidity"] == {"configured": False}


def test_print_endpoint_502_when_moonraker_fails(app):
    from multiace_web.moonraker import MoonrakerError
    with TestClient(app) as client:
        app.state.moonraker.query_objects = AsyncMock(side_effect=MoonrakerError("dead"))
        resp = client.get("/api/print")
    assert resp.status_code == 502


def test_print_endpoint_handles_missing_optional_objects(app):
    """If Klipper hasn't created an object yet (e.g. cavity sensor offline),
    the endpoint should still return cleanly with None where applicable."""
    payload = _print_query_payload()
    del payload["temperature_sensor cavity"]
    del payload["ace"]
    with TestClient(app) as client:
        app.state.moonraker.query_objects = AsyncMock(return_value=payload)
        resp = client.get("/api/print")
    body = resp.json()
    assert body["cavity_temp_c"] is None
    assert body["dryer"]["status"] == "stop"  # default


# =====================================================================
# /api/dry — Pydantic-validated wrapper around ACE_DRY ACE=N TEMP=T DURATION=D
# =====================================================================

def test_dry_endpoint_emits_correct_gcode(app):
    with TestClient(app) as client:
        app.state.moonraker.run_gcode = AsyncMock(return_value="ok")
        resp = client.post("/api/dry", json={
            "ace": 0, "temp_c": 50, "duration_min": 240,
        })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["gcode"] == "ACE_DRY ACE=0 TEMP=50 DURATION=240"
    app.state.moonraker.run_gcode.assert_awaited_with("ACE_DRY ACE=0 TEMP=50 DURATION=240")


@pytest.mark.parametrize("payload,reason", [
    ({"ace": -1, "temp_c": 50, "duration_min": 240}, "ace below range"),
    ({"ace": 8,  "temp_c": 50, "duration_min": 240}, "ace above range"),
    ({"ace": 0,  "temp_c": 25, "duration_min": 240}, "temp too low"),
    ({"ace": 0,  "temp_c": 130,"duration_min": 240}, "temp too high"),
    ({"ace": 0,  "temp_c": 50, "duration_min": 0},   "duration too short"),
    ({"ace": 0,  "temp_c": 50, "duration_min": 3000},"duration too long (>48h)"),
    ({"ace": 0,  "temp_c": 50},                       "duration missing"),
    ({"temp_c": 50, "duration_min": 240},             "ace missing"),
])
def test_dry_endpoint_validation(app, payload, reason):
    with TestClient(app) as client:
        resp = client.post("/api/dry", json=payload)
    assert resp.status_code == 422, f"expected 422 for {reason} but got {resp.status_code}"


def test_dry_endpoint_502_on_moonraker_failure(app):
    from multiace_web.moonraker import MoonrakerError
    with TestClient(app) as client:
        app.state.moonraker.run_gcode = AsyncMock(side_effect=MoonrakerError("klipper down"))
        resp = client.post("/api/dry", json={
            "ace": 0, "temp_c": 50, "duration_min": 240,
        })
    assert resp.status_code == 502


def test_websocket_accepts_correct_token_via_query(monkeypatch, tmp_path):
    """WS with ?token=secret should connect when MULTIACE_TOKEN is configured."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    cfg_path = tmp_path / "ace.cfg"
    cfg_path.write_text("[ace]\nfeed_speed: 80\n")
    static_dir = Path(__file__).resolve().parent.parent / "static"
    monkeypatch.setenv("MULTIACE_LOG_DIR", str(log_dir))
    monkeypatch.setenv("MULTIACE_CONFIG", str(cfg_path))
    monkeypatch.setenv("MOONRAKER_URL", "http://printer:7125")
    monkeypatch.setenv("MULTIACE_TOKEN", "secret")
    monkeypatch.setenv("MULTIACE_AUTODRY_STATE_PATH", str(tmp_path / "autodry.json"))

    from unittest.mock import AsyncMock, MagicMock
    mock_class = MagicMock()
    mock_instance = AsyncMock()
    mock_instance.close = AsyncMock()
    mock_class.return_value = mock_instance
    monkeypatch.setattr("multiace_web.server.MoonrakerClient", mock_class)

    from multiace_web.server import create_app
    secured_app = create_app(static_dir=static_dir, start_background_tasks=False)
    with TestClient(secured_app) as client:
        with client.websocket_connect("/ws?token=secret") as ws:
            msg = ws.receive_json()
    assert msg["type"] == "state"


# ---------------------------------------------------------------------------
# Auto-dry endpoints (/api/autodry)
# ---------------------------------------------------------------------------


@pytest.fixture
def autodry_test_client(tmp_path, monkeypatch):
    """Spin up an app with autodry state persisted to a temp dir."""
    monkeypatch.setenv("MULTIACE_AUTODRY_STATE_PATH", str(tmp_path / "autodry.json"))
    monkeypatch.setenv("MULTIACE_AUTODRY_MODE", "off")
    app = create_app(start_background_tasks=False)
    with TestClient(app) as c:
        yield c


def test_get_autodry_returns_defaults(autodry_test_client):
    r = autodry_test_client.get("/api/autodry")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "off"
    assert body["target_ace"] == 0
    assert body["target_pct"] == 15
    assert body["hysteresis_pp"] == 5
    assert body["fsm"]["state"] == "IDLE"


def test_post_autodry_set_mode_persists(autodry_test_client):
    r = autodry_test_client.post("/api/autodry",
                                 json={"action": "set_mode", "value": "log"})
    assert r.status_code == 200
    assert r.json()["mode"] == "log"
    # GET reflects it
    r2 = autodry_test_client.get("/api/autodry")
    assert r2.json()["mode"] == "log"


def test_post_autodry_set_target_validation(autodry_test_client):
    r_ok = autodry_test_client.post("/api/autodry",
                                    json={"action": "set_target", "value": 12})
    assert r_ok.status_code == 200
    assert r_ok.json()["target_pct"] == 12

    r_bad_low = autodry_test_client.post("/api/autodry",
                                         json={"action": "set_target", "value": 1})
    assert r_bad_low.status_code == 400

    r_bad_high = autodry_test_client.post("/api/autodry",
                                          json={"action": "set_target", "value": 99})
    assert r_bad_high.status_code == 400


def test_post_autodry_set_hysteresis_validation(autodry_test_client):
    r = autodry_test_client.post("/api/autodry",
                                 json={"action": "set_hysteresis", "value": 3})
    assert r.status_code == 200
    assert r.json()["hysteresis_pp"] == 3
    r_bad = autodry_test_client.post("/api/autodry",
                                     json={"action": "set_hysteresis", "value": 99})
    assert r_bad.status_code == 400


def test_post_autodry_unknown_action_400(autodry_test_client):
    r = autodry_test_client.post("/api/autodry",
                                 json={"action": "do_something_weird"})
    assert r.status_code == 400


def test_post_autodry_invalid_mode_400(autodry_test_client):
    r = autodry_test_client.post("/api/autodry",
                                 json={"action": "set_mode", "value": "bogus"})
    assert r.status_code == 400
