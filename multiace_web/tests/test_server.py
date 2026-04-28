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
    assert resp.status_code == 400


def test_command_endpoint_returns_502_on_moonraker_error(app):
    from multiace_web.moonraker import MoonrakerError
    with TestClient(app) as client:
        app.state.moonraker.run_gcode = AsyncMock(side_effect=MoonrakerError("timeout"))
        resp = client.post("/api/command", json={"macro": "ACEC__Load_T1"})
    assert resp.status_code == 502
    assert "timeout" in resp.json()["detail"]


def test_config_put_writes_and_restarts(app):
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
    assert resp.status_code == 400


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
