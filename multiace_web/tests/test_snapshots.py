"""Tests for loadout snapshots — capture a named head->slot loadout and later
re-apply it, with a plan + mismatch warnings against current slot bindings."""
from multiace_web import snapshots as sn


def _slots(bindings):
    """Build an /api/slots-shaped response. bindings: list of
    (ace, slot, material, color, name)."""
    aces = {}
    for ace, slot, mat, col, name in bindings:
        aces.setdefault(ace, {"index": ace, "slots": []})
        aces[ace]["slots"].append({
            "slot": slot,
            "spool": {"spool_id": 1, "name": name, "material": mat, "color": col},
        })
    return {"aces": list(aces.values())}


# --- name sanitization (path-traversal guard) ------------------------------

def test_sanitize_accepts_plain_names():
    assert sn._sanitize_name("PLA set") == "PLA set"
    assert sn._sanitize_name("multi_color-2") == "multi_color-2"


def test_sanitize_rejects_traversal_and_separators():
    assert sn._sanitize_name("../secret") is None
    assert sn._sanitize_name("a/b") is None
    assert sn._sanitize_name("..") is None
    assert sn._sanitize_name("") is None
    assert sn._sanitize_name("   ") is None


# --- SnapshotStore round-trip ----------------------------------------------

def test_store_save_load_list_delete(tmp_path):
    store = sn.SnapshotStore(tmp_path)
    assert store.list() == []
    store.save("my set", {"created_at": "2026-06-21T00:00:00Z",
                          "heads": {"0": {"ace": 0, "slot": 0, "material": "PLA",
                                          "color": "ff0000", "spool_name": "Red"}}})
    listing = store.list()
    assert len(listing) == 1
    assert listing[0]["name"] == "my set"
    assert listing[0]["created_at"] == "2026-06-21T00:00:00Z"
    loaded = store.load("my set")
    assert loaded["heads"]["0"]["material"] == "PLA"
    assert store.delete("my set") is True
    assert store.list() == []


def test_store_load_missing_is_none_delete_missing_false(tmp_path):
    store = sn.SnapshotStore(tmp_path)
    assert store.load("nope") is None
    assert store.delete("nope") is False


def test_store_rejects_bad_name(tmp_path):
    store = sn.SnapshotStore(tmp_path)
    import pytest
    with pytest.raises(ValueError):
        store.save("../evil", {"heads": {}})
    assert store.load("../evil") is None


# --- capture_loadout -------------------------------------------------------

def test_capture_pulls_material_color_from_bindings():
    head_source = {0: {"ace": 0, "slot": 0}, 1: None,
                   2: {"ace": 1, "slot": 2}, 3: None}
    slots = _slots([(0, 0, "PLA", "ff0000", "Red"),
                    (1, 2, "PETG", "0000ff", "Blue")])
    snap = sn.capture_loadout(head_source, slots)
    assert set(snap["heads"].keys()) == {"0", "2"}    # empty heads skipped
    assert snap["heads"]["0"] == {"ace": 0, "slot": 0, "material": "PLA",
                                  "color": "ff0000", "spool_name": "Red"}
    assert snap["heads"]["2"]["material"] == "PETG"


def test_capture_falls_back_to_head_source_when_no_binding():
    head_source = {0: {"ace": 0, "slot": 0, "type": "ABS", "color": "112233"}}
    snap = sn.capture_loadout(head_source, _slots([]))   # no bindings
    assert snap["heads"]["0"]["material"] == "ABS"
    assert snap["heads"]["0"]["color"] == "112233"


# --- plan_apply ------------------------------------------------------------

def _snap(heads):
    return {"heads": heads}


def test_plan_emits_load_head_actions_in_head_order():
    snap = _snap({
        "2": {"ace": 1, "slot": 2, "material": "PETG"},
        "0": {"ace": 0, "slot": 0, "material": "PLA"},
    })
    cur = _slots([(0, 0, "PLA", "ff0000", "Red"), (1, 2, "PETG", "0000ff", "Blue")])
    plan = sn.plan_apply(snap, cur)
    assert [a["head"] for a in plan["actions"]] == [0, 2]   # sorted by head
    assert plan["actions"][0]["gcode"] == "ACE_LOAD_HEAD HEAD=0 ACE=0 SLOT=0"
    assert plan["warnings"] == []


def test_plan_warns_on_material_mismatch():
    snap = _snap({"0": {"ace": 0, "slot": 0, "material": "PLA", "spool_name": "Red"}})
    cur = _slots([(0, 0, "PETG", "0000ff", "Blue")])   # slot now holds PETG
    plan = sn.plan_apply(snap, cur)
    assert len(plan["actions"]) == 1
    assert any("PETG" in w and "PLA" in w for w in plan["warnings"])


def test_plan_warns_when_slot_now_empty():
    snap = _snap({"0": {"ace": 0, "slot": 0, "material": "PLA", "spool_name": "Red"}})
    cur = _slots([])   # nothing bound
    plan = sn.plan_apply(snap, cur)
    assert len(plan["actions"]) == 1
    assert any("no bound spool" in w.lower() for w in plan["warnings"])


# --- /api/snapshots endpoints ----------------------------------------------

import pytest  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest.mock import AsyncMock, MagicMock  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from multiace_web.server import create_app  # noqa: E402
from multiace_web.spoolman import SpoolBinding  # noqa: E402


@pytest.fixture
def app(tmp_path, monkeypatch):
    (tmp_path / "logs").mkdir()
    (tmp_path / "ace.cfg").write_text("[ace]\nfeed_speed: 80\n")
    static_dir = Path(__file__).resolve().parent.parent / "src" / "multiace_web" / "static"
    monkeypatch.setenv("MULTIACE_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MULTIACE_CONFIG", str(tmp_path / "ace.cfg"))
    monkeypatch.setenv("MOONRAKER_URL", "http://printer:7125")
    monkeypatch.setenv("MULTIACE_SNAPSHOTS_DIR", str(tmp_path / "snapshots"))
    monkeypatch.delenv("MULTIACE_TOKEN", raising=False)
    mock_cls = MagicMock()
    inst = MagicMock()
    inst.close = AsyncMock()
    inst.run_gcode = AsyncMock(return_value="ok")
    inst.get_logs = AsyncMock(return_value=[])
    mock_cls.return_value = inst
    monkeypatch.setattr("multiace_web.server.MoonrakerClient", mock_cls)
    return create_app(static_dir=static_dir, start_background_tasks=False)


def _load_state(app):
    app.state.state.device_count = 1
    app.state.state.active_device = 0
    app.state.state.gate_status = [1, 1, 0, 0]
    app.state.state.head_source = {0: {"ace": 0, "slot": 0}, 1: None, 2: None, 3: None}
    app.state.spool_cache = {0: {0: SpoolBinding(
        spool_id=10, name="Red", material="PLA", color="ff0000",
        weight_remaining_g=900.0)}}


def test_endpoint_save_list_apply_delete_roundtrip(app):
    with TestClient(app) as c:
        _load_state(app)
        body = c.post("/api/snapshots/my-set").json()
        assert body["saved"] == "my-set"
        assert body["heads"]["0"]["material"] == "PLA"
        assert body["created_at"]
        names = [s["name"] for s in c.get("/api/snapshots").json()["snapshots"]]
        assert "my-set" in names
        plan = c.post("/api/snapshots/my-set/apply").json()
        assert plan["actions"][0]["gcode"] == "ACE_LOAD_HEAD HEAD=0 ACE=0 SLOT=0"
        assert plan["warnings"] == []
        assert c.delete("/api/snapshots/my-set").status_code == 200
        assert c.get("/api/snapshots").json()["snapshots"] == []


def test_endpoint_apply_warns_when_slot_changed(app):
    with TestClient(app) as c:
        _load_state(app)
        c.post("/api/snapshots/set1")
        app.state.spool_cache = {0: {0: SpoolBinding(
            spool_id=11, name="Blue", material="PETG", color="0000ff",
            weight_remaining_g=900.0)}}
        plan = c.post("/api/snapshots/set1/apply").json()
        assert any("PETG" in w for w in plan["warnings"])


def test_endpoint_apply_unknown_is_404(app):
    with TestClient(app) as c:
        assert c.post("/api/snapshots/nope/apply").status_code == 404
