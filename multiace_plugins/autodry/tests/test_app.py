# License: GPL-3.0
from urllib.parse import parse_qs, urlparse

import httpx
import respx

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
