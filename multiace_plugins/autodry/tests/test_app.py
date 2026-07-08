# License: GPL-3.0
import dataclasses
from urllib.parse import parse_qs, urlparse

import httpx
import respx
from fastapi.testclient import TestClient

from autodry_plugin.app import create_app
from autodry_plugin.fsm import Fault, FSMState
from autodry_plugin.persistence import load_manager


def test_manifest_shape(client):
    r = client.get("/integration-manifest")
    assert r.status_code == 200
    m = r.json()
    assert m["name"] == "autodry"
    assert m["label"] == "Auto-Dry"
    assert m["ui_url"] == "/"


@respx.mock
def test_status_reports_per_ace_humidity_and_defaults(client):
    respx.get(url__regex=r"http://mr\.test/printer/objects/query.*").mock(
        return_value=httpx.Response(200, json={"result": {"status": {
            "ace": {"device_count": 2, "units": [
                {"unit_index": 0, "environment": {"humidity_pct": 22.0, "has_humidity": True}},
                {"unit_index": 1, "environment": {"humidity_pct": 0.0, "has_humidity": False}},
            ]},
        }}}))
    r = client.get("/status")
    assert r.status_code == 200
    aces = r.json()["aces"]
    assert len(aces) == 2
    assert aces[0]["ace"] == 0
    assert aces[0]["humidity_pct"] == 22.0
    assert aces[0]["state"] == "IDLE"
    assert aces[0]["enabled"] is False
    assert aces[1]["humidity_pct"] is None


@respx.mock
def test_status_degrades_gracefully_when_moonraker_unreachable(client):
    respx.get(url__regex=r"http://mr\.test/printer/objects/query.*").mock(
        side_effect=httpx.ConnectError("refused"))
    r = client.get("/status")
    assert r.status_code == 200
    assert r.json()["aces"][0]["humidity_pct"] is None


def test_config_persists_round_trip(cfg, client):
    r = client.post("/config", json={"ace": 1, "target_pct": 20, "temp": 60,
                                      "duration_min": 300, "enabled": True})
    assert r.status_code == 200
    body = r.json()
    assert body["ace"] == 1
    assert body["config"] == {"enabled": True, "target_pct": 20, "temp_c": 60,
                               "duration_min": 300, "hysteresis_pp": 5}

    # Reload from disk independently of the running app to prove it was
    # actually written, not just held in the in-process manager.
    reloaded = load_manager(cfg.state_path)
    fsm1 = reloaded.get(1)
    assert fsm1.config.enabled is True
    assert fsm1.config.target_pct == 20
    assert fsm1.config.temp_c == 60
    assert fsm1.config.duration_min == 300


def test_config_partial_update_preserves_other_fields(client):
    client.post("/config", json={"ace": 0, "target_pct": 20, "temp": 60,
                                  "duration_min": 300, "enabled": True})
    r = client.post("/config", json={"ace": 0, "enabled": False})
    assert r.status_code == 200
    cfg_out = r.json()["config"]
    assert cfg_out["enabled"] is False
    assert cfg_out["target_pct"] == 20  # untouched by the partial update
    assert cfg_out["temp_c"] == 60


def test_config_rejects_bad_target_pct(client):
    r = client.post("/config", json={"ace": 0, "target_pct": 150})
    assert r.status_code == 400


def test_config_rejects_negative_ace(client):
    r = client.post("/config", json={"ace": -1, "target_pct": 10})
    assert r.status_code == 400


@respx.mock
def test_dry_proxies_correct_ace_dry_macro(client):
    respx.get(url__regex=r"http://mr\.test/printer/objects/query.*").mock(
        return_value=httpx.Response(200, json={"result": {"status": {"ace": {}}}}))
    gcode_route = respx.post(url__regex=r"http://mr\.test/printer/gcode/script.*").mock(
        return_value=httpx.Response(200, json={"result": "ok"}))

    client.post("/config", json={"ace": 0, "temp": 60, "duration_min": 200})
    r = client.post("/dry", json={"ace": 0})

    assert r.status_code == 200
    assert r.json() == {"ok": True, "ace": 0, "temp_c": 60, "duration_min": 200}
    sent_qs = parse_qs(urlparse(str(gcode_route.calls.last.request.url)).query)
    assert sent_qs["script"][0] == "ACE_DRY ACE=0 TEMP=60 DURATION=200"


@respx.mock
def test_dry_rejects_when_already_drying(client):
    respx.get(url__regex=r"http://mr\.test/printer/objects/query.*").mock(
        return_value=httpx.Response(200, json={"result": {"status": {"ace": {}}}}))
    respx.post(url__regex=r"http://mr\.test/printer/gcode/script.*").mock(
        return_value=httpx.Response(200, json={"result": "ok"}))

    r1 = client.post("/dry", json={"ace": 2})
    assert r1.status_code == 200
    r2 = client.post("/dry", json={"ace": 2})
    assert r2.status_code == 409


@respx.mock
def test_dry_502_when_moonraker_gcode_fails(client):
    respx.get(url__regex=r"http://mr\.test/printer/objects/query.*").mock(
        return_value=httpx.Response(200, json={"result": {"status": {"ace": {}}}}))
    respx.post(url__regex=r"http://mr\.test/printer/gcode/script.*").mock(
        return_value=httpx.Response(500))
    r = client.post("/dry", json={"ace": 0})
    assert r.status_code == 502
    assert "moonraker" in r.json()["detail"].lower()


def test_reset_fault_clears_faulted_state(client):
    app = client.app
    fsm = app.state.manager.get(3)
    fsm.snapshot.state = FSMState.FAULTED
    fsm.snapshot.fault = Fault(code="FAILED_LIMIT", since_ts=0.0, msg="test fault")

    r = client.post("/reset-fault", json={"ace": 3})
    assert r.status_code == 200
    assert r.json()["state"] == "IDLE"
    assert app.state.manager.get(3).snapshot.fault is None


def test_root_serves_ui(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Auto-Dry" in r.text


@respx.mock
def test_status_humidity_source_is_bridge_when_bridge_configured(cfg):
    bridge_cfg = dataclasses.replace(cfg, humidity_url="http://govee.test/sensor")
    bridge_client = TestClient(create_app(bridge_cfg))
    respx.get(url__regex=r"http://mr\.test/printer/objects/query.*").mock(
        return_value=httpx.Response(200, json={"result": {"status": {
            "ace": {"device_count": 1},
        }}}))
    respx.get("http://govee.test/sensors").mock(
        return_value=httpx.Response(200, json={"AA:BB": {"humidity": 44.0, "temperature": 21.0}}))

    r = bridge_client.get("/status")
    assert r.status_code == 200
    aces = r.json()["aces"]
    assert aces[0]["humidity_pct"] == 44.0
    assert aces[0]["humidity_source"] == "bridge"


@respx.mock
def test_status_humidity_source_is_none_when_bridge_unconfigured(client):
    respx.get(url__regex=r"http://mr\.test/printer/objects/query.*").mock(
        return_value=httpx.Response(200, json={"result": {"status": {
            "ace": {"device_count": 1},
        }}}))
    r = client.get("/status")
    assert r.status_code == 200
    aces = r.json()["aces"]
    assert aces[0]["humidity_pct"] is None
    assert aces[0]["humidity_source"] == "none"


@respx.mock
def test_status_falls_back_to_ace_object_humidity_when_bridge_has_no_reading(client):
    # No bridge configured (base `cfg` fixture) — the legacy ACE-object
    # humidity (units[] shape) still surfaces for display continuity, but
    # is tagged humidity_source="none" (only "bridge" reads count as
    # humidity_source="bridge"). This is the same fixture/assertion shape as
    # test_status_reports_per_ace_humidity_and_defaults, isolated here to
    # pin the humidity_source field specifically.
    respx.get(url__regex=r"http://mr\.test/printer/objects/query.*").mock(
        return_value=httpx.Response(200, json={"result": {"status": {
            "ace": {"device_count": 1, "units": [
                {"unit_index": 0, "environment": {"humidity_pct": 22.0, "has_humidity": True}},
            ]},
        }}}))
    r = client.get("/status")
    aces = r.json()["aces"]
    assert aces[0]["humidity_pct"] == 22.0
    assert aces[0]["humidity_source"] == "none"


def _mock_tick_dependencies(humidity_pct: float):
    """Register the Moonraker + multiACE + Govee-bridge respx routes a
    single _tick_once() call needs. Returns the gcode route so callers can
    assert on ACE_DRY calls."""
    respx.get(url__regex=r"http://mr\.test/printer/objects/query.*").mock(
        return_value=httpx.Response(200, json={"result": {"status": {
            "ace": {"device_count": 1},
            "print_stats": {"state": "standby"},
        }}}))
    respx.get(url__regex=r"http://ma\.test/api/state").mock(
        return_value=httpx.Response(200, json={"swap_in_progress": False, "device_count": 1}))
    respx.get("http://govee.test/sensors").mock(
        return_value=httpx.Response(200, json={
            "AA:BB:CC:DD:EE:01": {"humidity": humidity_pct, "temperature": 22.0},
        }))
    return respx.post(url__regex=r"http://mr\.test/printer/gcode/script.*").mock(
        return_value=httpx.Response(200, json={"result": "ok"}))


@respx.mock
async def test_tick_loop_fires_ace_dry_when_bridge_humidity_above_wake_threshold(cfg):
    # target_pct(15, from cfg fixture default) + hysteresis_pp(5) = wake at 20.
    bridge_cfg = dataclasses.replace(cfg, humidity_url="http://govee.test/sensor")
    app = create_app(bridge_cfg)
    tc = TestClient(app)
    tc.post("/config", json={"ace": 0, "target_pct": 15, "temp": 55,
                              "duration_min": 240, "enabled": True})

    gcode_route = _mock_tick_dependencies(humidity_pct=40.0)

    # debounce_required=3 (cfg default): tick1 IDLE->WATCHING (no observe),
    # ticks 2-4 accumulate 3 above-threshold observations to trigger.
    for _ in range(4):
        await app.state.tick_once()

    assert gcode_route.called
    sent_qs = parse_qs(urlparse(str(gcode_route.calls.last.request.url)).query)
    assert sent_qs["script"][0] == "ACE_DRY ACE=0 TEMP=55 DURATION=240"

    fsm = app.state.manager.get(0)
    assert fsm.snapshot.state == FSMState.DRYING


@respx.mock
async def test_tick_loop_does_not_fire_ace_dry_when_bridge_humidity_below_wake_threshold(cfg):
    bridge_cfg = dataclasses.replace(cfg, humidity_url="http://govee.test/sensor")
    app = create_app(bridge_cfg)
    tc = TestClient(app)
    tc.post("/config", json={"ace": 0, "target_pct": 15, "temp": 55,
                              "duration_min": 240, "enabled": True})

    gcode_route = _mock_tick_dependencies(humidity_pct=10.0)

    for _ in range(6):
        await app.state.tick_once()

    assert not gcode_route.called
    fsm = app.state.manager.get(0)
    assert fsm.snapshot.state in (FSMState.IDLE, FSMState.WATCHING)
    assert fsm.snapshot.state != FSMState.DRYING


@respx.mock
async def test_tick_loop_stays_inert_when_bridge_unconfigured(cfg):
    # cfg fixture has no humidity_url set — bridge disabled, so humidity_ok
    # must stay False every tick (auto-trigger inert) per the bridge
    # contract, even though the `ace` object query itself succeeds.
    app = create_app(cfg)
    tc = TestClient(app)
    tc.post("/config", json={"ace": 0, "target_pct": 15, "temp": 55,
                              "duration_min": 240, "enabled": True})

    respx.get(url__regex=r"http://mr\.test/printer/objects/query.*").mock(
        return_value=httpx.Response(200, json={"result": {"status": {
            "ace": {"device_count": 1},
            "print_stats": {"state": "standby"},
        }}}))
    respx.get(url__regex=r"http://ma\.test/api/state").mock(
        return_value=httpx.Response(200, json={"swap_in_progress": False, "device_count": 1}))
    gcode_route = respx.post(url__regex=r"http://mr\.test/printer/gcode/script.*").mock(
        return_value=httpx.Response(200, json={"result": "ok"}))

    for _ in range(6):
        await app.state.tick_once()

    assert not gcode_route.called
    fsm = app.state.manager.get(0)
    assert fsm.snapshot.state == FSMState.IDLE
