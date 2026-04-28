import os
from pathlib import Path

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
