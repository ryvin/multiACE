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