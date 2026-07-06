# License: GPL-3.0
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
    import json
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