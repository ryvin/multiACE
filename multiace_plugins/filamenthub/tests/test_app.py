# License: GPL-3.0
import json

import httpx
import respx


def test_manifest_shape(client):
    r = client.get("/integration-manifest")
    assert r.status_code == 200
    m = r.json()
    assert m["name"] == "filamenthub"
    assert m["label"] == "FilamentHub"
    assert m["ui_url"] == "/"


@respx.mock
def test_spools_endpoint_lists_inventory(client):
    respx.get("http://fh.test/api/v1/spool").mock(return_value=httpx.Response(
        200, json=[{"id": 7, "archived": False,
                    "remaining_weight": 800.0, "extra": {},
                    "filament": {"name": "Galaxy Blue", "material": "PLA",
                                 "color_hex": "0000ff",
                                 "vendor": {"name": "Generic"}}}]))
    r = client.get("/spools")
    assert r.status_code == 200
    spools = r.json()["spools"]
    assert spools[0]["spool_id"] == 7
    assert spools[0]["material"] == "PLA"
    assert spools[0]["vendor"] == "Generic"


def _spool_route():
    return respx.get("http://fh.test/api/v1/spool").mock(return_value=httpx.Response(
        200, json=[{"id": 7, "archived": False, "remaining_weight": 800.0,
                    "extra": {},
                    "filament": {"name": "Galaxy Blue", "material": "PLA",
                                 "color_hex": "0000ff",
                                 "vendor": {"name": "Generic"}}}]))


@respx.mock
def test_assign_writes_both_sides(client):
    _spool_route()
    respx.get("http://fh.test/api/v1/spool/7").mock(return_value=httpx.Response(
        200, json={"id": 7, "extra": {}}))
    respx.patch("http://fh.test/api/v1/spool/7").mock(return_value=httpx.Response(200))
    ov_route = respx.post("http://ma.test/api/slot-override").mock(
        return_value=httpx.Response(200, json={"ok": True, "key": "1_2"}))
    r = client.post("/assign", json={"spool_id": 7, "ace": 1, "slot": 2})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["location"] == {"printer": "davinci-u1", "ace": 1, "slot": 2}
    sent = json.loads(ov_route.calls.last.request.content)
    assert sent["color"] == "#0000ff" and sent["brand"] == "Generic"


@respx.mock
def test_assign_unknown_spool_404(client):
    _spool_route()
    r = client.post("/assign", json={"spool_id": 999, "ace": 0, "slot": 0})
    assert r.status_code == 404


@respx.mock
def test_assign_multiace_failure_502(client):
    _spool_route()
    respx.get("http://fh.test/api/v1/spool/7").mock(return_value=httpx.Response(
        200, json={"id": 7, "extra": {}}))
    respx.patch("http://fh.test/api/v1/spool/7").mock(return_value=httpx.Response(200))
    respx.post("http://ma.test/api/slot-override").mock(
        return_value=httpx.Response(502))
    r = client.post("/assign", json={"spool_id": 7, "ace": 1, "slot": 2})
    assert r.status_code == 502
    assert "multiace" in r.json()["detail"].lower()


@respx.mock
def test_assign_502_when_filamenthub_unreachable(client):
    respx.get("http://fh.test/api/v1/spool").mock(return_value=httpx.Response(500))
    r = client.post("/assign", json={"spool_id": 7, "ace": 1, "slot": 2})
    assert r.status_code == 502


@respx.mock
def test_assign_502_when_filamenthub_write_fails(client):
    _spool_route()
    respx.get("http://fh.test/api/v1/spool/7").mock(return_value=httpx.Response(
        200, json={"id": 7, "extra": {}}))
    respx.patch("http://fh.test/api/v1/spool/7").mock(return_value=httpx.Response(500))
    ov_route = respx.post("http://ma.test/api/slot-override").mock(
        return_value=httpx.Response(200, json={"ok": True, "key": "1_2"}))
    r = client.post("/assign", json={"spool_id": 7, "ace": 1, "slot": 2})
    assert r.status_code == 502
    assert not ov_route.called


@respx.mock
def test_unassign_502_when_filamenthub_unreachable_and_does_not_clear_multiace(client):
    respx.get("http://fh.test/api/v1/spool").mock(return_value=httpx.Response(500))
    delete_route = respx.delete("http://ma.test/api/slot-override/1/2").mock(
        return_value=httpx.Response(200, json={"ok": True}))
    r = client.post("/unassign", json={"ace": 1, "slot": 2})
    assert r.status_code == 502
    assert not delete_route.called


@respx.mock
def test_unassign_clears_both_sides(client):
    respx.get("http://fh.test/api/v1/spool").mock(return_value=httpx.Response(
        200, json=[{"id": 7, "archived": False, "remaining_weight": 800.0,
                    "extra": {"filamenthub": json.dumps(json.dumps(
                        {"schema": 1, "location":
                         {"printer": "davinci-u1", "ace": 1, "slot": 2}}))},
                    "filament": {"name": "Galaxy Blue", "material": "PLA",
                                 "color_hex": "0000ff",
                                 "vendor": {"name": "Generic"}}}]))
    respx.get("http://fh.test/api/v1/spool/7").mock(return_value=httpx.Response(
        200, json={"id": 7, "extra": {}}))
    respx.patch("http://fh.test/api/v1/spool/7").mock(return_value=httpx.Response(200))
    respx.delete("http://ma.test/api/slot-override/1/2").mock(
        return_value=httpx.Response(200, json={"ok": True}))
    r = client.post("/unassign", json={"ace": 1, "slot": 2})
    assert r.status_code == 200
    assert r.json()["cleared_spool_id"] == 7


def test_root_serves_ui(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "FilamentHub" in r.text


def _ace_state_body():
    return {"schema": 1, "printer": "davinci-u1", "ts": 1,
            "slots": [{"ace": 0, "slot": 0, "spool_id": 42, "material": "PLA",
                       "color": "#00ff00", "name": "PolyTerra Green",
                       "asserted_by": "user:assign", "asserted_at": "z"}],
            "disputed": [{"ace": 0, "slot": 1, "spool_id": 7, "material": "PETG",
                          "color": "#ff0000", "name": "X",
                          "asserted_by": "watcher:rfid", "asserted_at": "y",
                          "winner_spool_id": 42}]}


def _spools_body():
    return [{"id": 42, "archived": False,
             "filament": {"name": "PolyTerra Green", "material": "PLA",
                          "color_hex": "00ff00", "vendor": {"name": "PolyTerra"}},
             "remaining_weight": 800,
             "extra": {"filamenthub": '"{\\"location\\": {\\"printer\\": \\"davinci-u1\\", \\"ace\\": 0, \\"slot\\": 0}}"'}}]


@respx.mock
def test_pull_applies_winner_and_clears_vacated(client):
    respx.get("http://fh.test/fleet/api/ace-state").mock(
        return_value=httpx.Response(200, json=_ace_state_body()))
    respx.get("http://fh.test/api/v1/spool").mock(
        return_value=httpx.Response(200, json=_spools_body()))
    # multiACE currently labels: (0,0) still-winner, (0,1) disputed, and (0,2)
    # genuinely vacated (not a winner, not disputed). Only (0,2) may be cleared;
    # the disputed (0,1) must be preserved.
    respx.get("http://ma.test/api/slot-override").mock(
        return_value=httpx.Response(200,
            json={"overrides": {"0_0": {}, "0_1": {}, "0_2": {}}}))
    post = respx.post("http://ma.test/api/slot-override").mock(
        return_value=httpx.Response(200, json={"ok": True, "key": "0_0"}))
    delete = respx.delete(url__regex=r"http://ma\.test/api/slot-override/\d+/\d+").mock(
        return_value=httpx.Response(200, json={"ok": True}))

    r = client.post("/pull")
    assert r.status_code == 200
    data = r.json()
    assert data["applied"] == [{"ace": 0, "slot": 0, "material": "PLA",
                                "brand": "PolyTerra", "subtype": "PolyTerra Green",
                                "color": "#00ff00"}]
    assert data["cleared"] == [{"ace": 0, "slot": 2}]
    assert data["disputed"][0]["winner_spool_id"] == 42
    assert data["errors"] == []
    assert post.called and delete.called


@respx.mock
def test_pull_prune_false_is_additive_only(client):
    # Passive auto-pull-on-open sends prune=false: it applies/updates labels but
    # NEVER deletes. Guards against churn when the live seam transiently drops a
    # slot (a destructive clear on every tab-open would delete valid labels).
    respx.get("http://fh.test/fleet/api/ace-state").mock(
        return_value=httpx.Response(200, json=_ace_state_body()))
    respx.get("http://fh.test/api/v1/spool").mock(
        return_value=httpx.Response(200, json=_spools_body()))
    respx.get("http://ma.test/api/slot-override").mock(
        return_value=httpx.Response(200,
            json={"overrides": {"0_0": {}, "0_2": {}}}))
    post = respx.post("http://ma.test/api/slot-override").mock(
        return_value=httpx.Response(200, json={"ok": True, "key": "0_0"}))
    delete = respx.delete(url__regex=r"http://ma\.test/api/slot-override/\d+/\d+").mock(
        return_value=httpx.Response(200, json={"ok": True}))

    r = client.post("/pull", json={"prune": False})
    assert r.status_code == 200
    data = r.json()
    assert data["applied"] == [{"ace": 0, "slot": 0, "material": "PLA",
                                "brand": "PolyTerra", "subtype": "PolyTerra Green",
                                "color": "#00ff00"}]
    assert data["cleared"] == []          # nothing deleted
    assert data["stale"] == [{"ace": 0, "slot": 2}]  # reported, not acted on
    assert post.called and not delete.called


@respx.mock
def test_pull_collects_partial_errors(client):
    respx.get("http://fh.test/fleet/api/ace-state").mock(
        return_value=httpx.Response(200, json=_ace_state_body()))
    respx.get("http://fh.test/api/v1/spool").mock(
        return_value=httpx.Response(200, json=_spools_body()))
    respx.get("http://ma.test/api/slot-override").mock(
        return_value=httpx.Response(200, json={"overrides": {}}))
    respx.post("http://ma.test/api/slot-override").mock(
        return_value=httpx.Response(502))

    r = client.post("/pull")
    assert r.status_code == 200
    data = r.json()
    assert data["applied"] == []
    assert len(data["errors"]) == 1
    assert data["errors"][0]["action"] == "apply"
    assert data["errors"][0]["ace"] == 0 and data["errors"][0]["slot"] == 0


@respx.mock
def test_pull_maps_seam_disabled_to_502(client):
    respx.get("http://fh.test/fleet/api/ace-state").mock(
        return_value=httpx.Response(503))
    r = client.post("/pull")
    assert r.status_code == 502
    assert "not enabled" in r.json()["detail"]


@respx.mock
def test_get_ace_state_passthrough(client):
    respx.get("http://fh.test/fleet/api/ace-state").mock(
        return_value=httpx.Response(200, json=_ace_state_body()))
    r = client.get("/ace-state")
    assert r.status_code == 200
    assert r.json()["slots"][0]["spool_id"] == 42