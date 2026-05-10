"""Tests for /api/print_queue and /api/print_queue/{gcode}/revalidate."""
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from multiace_web.server import create_app


FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def app_with_sidecars(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    cfg_path = tmp_path / "ace.cfg"
    cfg_path.write_text("[ace]\nfeed_speed: 80\n")
    gcode_dir = tmp_path / "gcodes"
    gcode_dir.mkdir()

    # Write a ready sidecar
    sidecar_ready = {
        "schema": 1,
        "generated_at": "2026-05-08T12:00:00Z",
        "gcode_path": str(gcode_dir / "demo.gcode"),
        "status": "ready",
        "reason": None,
        "tools": {"0": {"type": "PLA", "color": "#ff0000", "match_quality": "exact",
                        "candidates": [{"ace": 0, "slot": 0, "spool_id": 10, "spool_name": "PLA Red"}],
                        "resolved": {"ace": 0, "slot": 0, "spool_id": 10}, "physical_head": 0}},
        "swaps": [],
        "errors": [],
    }
    (gcode_dir / "demo.gcode").write_text("G28\n")
    (gcode_dir / "demo.gcode.multiace.json").write_text(json.dumps(sidecar_ready))

    # Write a pending sidecar
    sidecar_pending = {**sidecar_ready, "status": "pending", "reason": "missing_bindings",
                       "generated_at": "2026-05-08T11:00:00Z",
                       "gcode_path": str(gcode_dir / "pending.gcode")}
    (gcode_dir / "pending.gcode").write_text("G28\n")
    (gcode_dir / "pending.gcode.multiace.json").write_text(json.dumps(sidecar_pending))

    monkeypatch.setenv("MULTIACE_LOG_DIR", str(log_dir))
    monkeypatch.setenv("MULTIACE_CONFIG", str(cfg_path))
    monkeypatch.setenv("MOONRAKER_URL", "http://printer:7125")
    monkeypatch.setenv("MULTIACE_GCODE_DIR", str(gcode_dir))
    monkeypatch.delenv("MULTIACE_TOKEN", raising=False)

    mock_mr_class = MagicMock()
    mock_mr = MagicMock()
    mock_mr.close = AsyncMock()
    mock_mr.run_gcode = AsyncMock(return_value="ok")
    mock_mr.start_print = AsyncMock(return_value="ok")
    mock_mr.get_logs = AsyncMock(return_value=[])
    # list_gcode_files returns filenames as Moonraker would
    mock_mr.list_gcode_files = AsyncMock(return_value=[
        {"filename": "demo.gcode", "modified": 1715000000.0, "size": 100},
        {"filename": "demo.gcode.multiace.json", "modified": 1715000001.0, "size": 500},
        {"filename": "pending.gcode", "modified": 1714900000.0, "size": 100},
        {"filename": "pending.gcode.multiace.json", "modified": 1714900001.0, "size": 500},
    ])
    mock_mr_class.return_value = mock_mr

    monkeypatch.setattr("multiace_web.server.MoonrakerClient", mock_mr_class)

    static_dir = Path(__file__).resolve().parent.parent / "static"
    app = create_app(static_dir=static_dir, start_background_tasks=False)
    app.state.gcode_dir = gcode_dir
    return app


def test_print_queue_returns_list(app_with_sidecars):
    with TestClient(app_with_sidecars) as c:
        resp = c.get("/api/print_queue")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    # Should have 2 entries (demo.gcode and pending.gcode have sidecars)
    assert len(data["items"]) == 2


def test_print_queue_sorted_by_generated_at_desc(app_with_sidecars):
    with TestClient(app_with_sidecars) as c:
        resp = c.get("/api/print_queue")
    items = resp.json()["items"]
    # demo (12:00) should come before pending (11:00)
    assert items[0]["filename"] == "demo.gcode"
    assert items[0]["status"] == "ready"
    assert items[1]["filename"] == "pending.gcode"
    assert items[1]["status"] == "pending"


def test_print_queue_item_shape(app_with_sidecars):
    with TestClient(app_with_sidecars) as c:
        resp = c.get("/api/print_queue")
    item = resp.json()["items"][0]
    assert "filename" in item
    assert "status" in item
    assert "reason" in item
    assert "generated_at" in item
    assert "tools" in item
    assert "swaps" in item


def test_revalidate_returns_updated_sidecar(app_with_sidecars, monkeypatch):
    """Re-validate re-reads the gcode file + current /api/slots and rewrites sidecar."""
    # Patch the module-level _revalidate_gcode function
    monkeypatch.setattr(
        "multiace_web.server._revalidate_gcode",
        AsyncMock(return_value={"status": "pending", "reason": "missing_bindings", "tools": {}, "swaps": []}),
    )
    with TestClient(app_with_sidecars) as c:
        resp = c.post("/api/print_queue/demo.gcode/revalidate")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data


def test_revalidate_404_when_no_sidecar(app_with_sidecars, tmp_path):
    with TestClient(app_with_sidecars) as c:
        resp = c.post("/api/print_queue/nonexistent.gcode/revalidate")
    assert resp.status_code == 404


def test_revalidate_409_when_swap_in_progress(app_with_sidecars):
    """If swap_in_progress is True on the state, revalidate must return 409."""
    with TestClient(app_with_sidecars) as c:
        app_with_sidecars.state.state.swap_in_progress = True
        resp = c.post("/api/print_queue/demo.gcode/revalidate")
        app_with_sidecars.state.state.swap_in_progress = False
    assert resp.status_code == 409


# --- /api/print_queue/{filename}/print ---

def test_print_starts_when_sidecar_ready(app_with_sidecars):
    """POST /print on a ready sidecar should call moonraker.start_print and return 200."""
    with TestClient(app_with_sidecars) as c:
        resp = c.post("/api/print_queue/demo.gcode/print")
    assert resp.status_code == 200
    data = resp.json()
    assert data["started"] == "demo.gcode"
    # Verify moonraker.start_print was called with the right filename
    mr = app_with_sidecars.state.moonraker
    mr.start_print.assert_awaited_once_with("demo.gcode")


def test_print_409_when_sidecar_pending(app_with_sidecars):
    """POST /print on a pending sidecar must refuse with 409 + reason."""
    with TestClient(app_with_sidecars) as c:
        resp = c.post("/api/print_queue/pending.gcode/print")
    assert resp.status_code == 409
    detail = resp.json().get("detail", "")
    assert "pending" in detail
    # And moonraker.start_print should NOT have been called
    mr = app_with_sidecars.state.moonraker
    mr.start_print.assert_not_awaited()


def test_print_404_when_no_sidecar(app_with_sidecars):
    """POST /print without a sidecar must 404 with a message about re-validating."""
    with TestClient(app_with_sidecars) as c:
        resp = c.post("/api/print_queue/nonexistent.gcode/print")
    assert resp.status_code == 404
    assert "re-validate" in resp.json().get("detail", "")


def test_print_409_when_swap_in_progress(app_with_sidecars):
    """POST /print must refuse with 409 if swap_in_progress is True."""
    with TestClient(app_with_sidecars) as c:
        app_with_sidecars.state.state.swap_in_progress = True
        resp = c.post("/api/print_queue/demo.gcode/print")
        app_with_sidecars.state.state.swap_in_progress = False
    assert resp.status_code == 409
    mr = app_with_sidecars.state.moonraker
    mr.start_print.assert_not_awaited()


def test_print_502_when_moonraker_rejects(app_with_sidecars):
    """If Moonraker raises MoonrakerError, the endpoint should return 502."""
    from multiace_web.moonraker import MoonrakerError
    with TestClient(app_with_sidecars) as c:
        # Override start_print AFTER lifespan startup so app.state.moonraker exists
        app_with_sidecars.state.moonraker.start_print = AsyncMock(
            side_effect=MoonrakerError("file not found")
        )
        resp = c.post("/api/print_queue/demo.gcode/print")
    assert resp.status_code == 502
    assert "file not found" in resp.json().get("detail", "")
