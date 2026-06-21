"""Tests for the FilamentHub picker endpoints: list available spools and
assign one to a slot (binds it in FilamentHub, no RFID scan needed)."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from multiace_web.server import create_app

_STATIC = Path(__file__).resolve().parent.parent / "src" / "multiace_web" / "static"


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


def test_list_spools_when_not_configured(app):
    with TestClient(app) as c:
        app.state.spoolman = None
        r = c.get("/api/filamenthub/spools")
    assert r.status_code == 200
    assert r.json() == {"configured": False, "spools": []}


def test_list_spools_proxies_client(app):
    sm = MagicMock()
    sm.list_spools = AsyncMock(return_value=[
        {"spool_id": 21, "name": "Ivory", "material": "PLA", "color": "FFFFF0",
         "vendor": "Snapmaker", "weight_remaining_g": 126.4, "location": None},
    ])
    with TestClient(app) as c:
        app.state.spoolman = sm
        r = c.get("/api/filamenthub/spools")
    body = r.json()
    assert body["configured"] is True
    assert body["spools"][0]["spool_id"] == 21
    assert body["spools"][0]["vendor"] == "Snapmaker"


def test_assign_calls_client_and_refreshes_cache(app):
    sm = MagicMock()
    sm.assign_spool = AsyncMock(return_value={"printer": "u1-ace", "ace": 0, "slot": 3})
    sm.list_all_bindings = AsyncMock(return_value={})
    with TestClient(app) as c:
        app.state.spoolman = sm
        r = c.post("/api/slots/0/3/assign", json={"spool_id": 21})
    assert r.status_code == 200
    assert r.json()["location"]["slot"] == 3
    sm.assign_spool.assert_awaited_once_with(21, 0, 3)
    sm.list_all_bindings.assert_awaited()   # cache refreshed after assign


def test_assign_503_when_not_configured(app):
    with TestClient(app) as c:
        app.state.spoolman = None
        r = c.post("/api/slots/0/0/assign", json={"spool_id": 1})
    assert r.status_code == 503


def test_assign_502_on_client_failure(app):
    sm = MagicMock()
    sm.assign_spool = AsyncMock(side_effect=RuntimeError("boom"))
    with TestClient(app) as c:
        app.state.spoolman = sm
        r = c.post("/api/slots/0/0/assign", json={"spool_id": 1})
    assert r.status_code == 502


def test_assign_422_on_bad_slot(app):
    sm = MagicMock()
    sm.assign_spool = AsyncMock()
    with TestClient(app) as c:
        app.state.spoolman = sm
        r = c.post("/api/slots/0/9/assign", json={"spool_id": 1})
    assert r.status_code == 422
    sm.assign_spool.assert_not_awaited()
