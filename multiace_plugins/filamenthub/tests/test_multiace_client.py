# License: GPL-3.0
"""Unit tests for MultiAceClient (slot-override POST/DELETE against mocked multiACE)."""
import json

import httpx
import pytest
import respx

from filamenthub_plugin.multiace_client import MultiAceClient


@respx.mock
@pytest.mark.asyncio
async def test_get_state_returns_body():
    respx.get("http://ma.test/api/plugin-api/state").mock(
        return_value=httpx.Response(200, json={"aces": [{"idx": 0, "slots": []}]}))
    body = await MultiAceClient("http://ma.test").get_state()
    assert body["aces"][0]["idx"] == 0


@respx.mock
@pytest.mark.asyncio
async def test_set_override_posts_payload():
    route = respx.post("http://ma.test/api/slot-override").mock(
        return_value=httpx.Response(200, json={"ok": True, "key": "0_1"}))
    ma = MultiAceClient("http://ma.test")
    out = await ma.set_override(ace=0, slot=1, material="PLA",
                                brand="Generic", subtype="Blue", color="#0000ff")
    assert out == {"ok": True, "key": "0_1"}
    body = json.loads(route.calls.last.request.content)
    assert body == {"ace": 0, "slot": 1, "material": "PLA",
                    "brand": "Generic", "subtype": "Blue", "color": "#0000ff"}


@respx.mock
@pytest.mark.asyncio
async def test_clear_override_deletes():
    respx.delete("http://ma.test/api/slot-override/0/1").mock(
        return_value=httpx.Response(200, json={"ok": True}))
    ma = MultiAceClient("http://ma.test")
    out = await ma.clear_override(0, 1)
    assert out == {"ok": True}


@respx.mock
@pytest.mark.asyncio
async def test_set_override_raises_on_5xx():
    respx.post("http://ma.test/api/slot-override").mock(
        return_value=httpx.Response(502))
    ma = MultiAceClient("http://ma.test")
    with pytest.raises(httpx.HTTPStatusError):
        await ma.set_override(ace=0, slot=0, material="", brand="",
                              subtype="", color="")


@respx.mock
@pytest.mark.asyncio
async def test_list_overrides_returns_map():
    respx.get("http://ma.test/api/slot-override").mock(
        return_value=httpx.Response(200, json={"overrides": {
            "0_0": {"material": "PLA"}, "1_2": {"material": "PETG"}}}))
    ma = MultiAceClient("http://ma.test")
    out = await ma.list_overrides()
    assert set(out.keys()) == {"0_0", "1_2"}


@respx.mock
@pytest.mark.asyncio
async def test_list_overrides_missing_key_is_empty():
    respx.get("http://ma.test/api/slot-override").mock(
        return_value=httpx.Response(200, json={}))
    ma = MultiAceClient("http://ma.test")
    assert await ma.list_overrides() == {}


# --- WP3: materials DB --------------------------------------------------------

@respx.mock
@pytest.mark.asyncio
async def test_get_materials_returns_db():
    db = {"PLA": {"Generic": ["Matte"]}, "PETG": {"Generic": ["HF"]}}
    respx.get("http://ma.test/api/materials").mock(
        return_value=httpx.Response(200, json={"materials": ["PLA", "PETG"], "db": db}))
    assert await MultiAceClient("http://ma.test").get_materials() == db


@respx.mock
@pytest.mark.asyncio
async def test_get_materials_error_returns_empty(caplog):
    respx.get("http://ma.test/api/materials").mock(return_value=httpx.Response(500))
    assert await MultiAceClient("http://ma.test").get_materials() == {}
    assert any("materials" in r.message.lower() for r in caplog.records)


@respx.mock
@pytest.mark.asyncio
async def test_get_materials_unreachable_returns_empty():
    respx.get("http://ma.test/api/materials").mock(
        side_effect=httpx.ConnectError("refused"))
    assert await MultiAceClient("http://ma.test").get_materials() == {}


@respx.mock
@pytest.mark.asyncio
async def test_get_materials_malformed_db_returns_empty():
    respx.get("http://ma.test/api/materials").mock(
        return_value=httpx.Response(200, json={"materials": ["PLA"], "db": ["PLA"]}))
    assert await MultiAceClient("http://ma.test").get_materials() == {}


# --- WP4: MULTIACE_REFRESH_OVERRIDES after override writes --------------------

GCODE = "http://ma.test/api/plugin-api/gcode"


@respx.mock
@pytest.mark.asyncio
async def test_set_override_triggers_refresh_gcode():
    respx.post("http://ma.test/api/slot-override").mock(
        return_value=httpx.Response(200, json={"ok": True}))
    g = respx.post(GCODE).mock(return_value=httpx.Response(200, json={"ok": True}))
    await MultiAceClient("http://ma.test").set_override(
        ace=0, slot=1, material="PLA", brand="", subtype="", color="")
    assert g.call_count == 1
    assert json.loads(g.calls.last.request.content) == {
        "script": "MULTIACE_REFRESH_OVERRIDES"}


@respx.mock
@pytest.mark.asyncio
async def test_clear_override_triggers_refresh_gcode():
    respx.delete("http://ma.test/api/slot-override/0/1").mock(
        return_value=httpx.Response(200, json={"ok": True}))
    g = respx.post(GCODE).mock(return_value=httpx.Response(200, json={"ok": True}))
    await MultiAceClient("http://ma.test").clear_override(0, 1)
    assert g.call_count == 1
    assert json.loads(g.calls.last.request.content) == {
        "script": "MULTIACE_REFRESH_OVERRIDES"}


@respx.mock
@pytest.mark.asyncio
async def test_refresh_failure_is_logged_not_raised(caplog):
    respx.post("http://ma.test/api/slot-override").mock(
        return_value=httpx.Response(200, json={"ok": True, "key": "0_1"}))
    respx.post(GCODE).mock(return_value=httpx.Response(409, text="printer busy"))
    out = await MultiAceClient("http://ma.test").set_override(
        ace=0, slot=1, material="PLA", brand="", subtype="", color="")
    assert out == {"ok": True, "key": "0_1"}
    assert any("refresh" in r.message.lower() for r in caplog.records)


@respx.mock
@pytest.mark.asyncio
async def test_refresh_unreachable_is_not_raised():
    respx.delete("http://ma.test/api/slot-override/0/1").mock(
        return_value=httpx.Response(200, json={"ok": True}))
    respx.post(GCODE).mock(side_effect=httpx.ConnectError("refused"))
    assert await MultiAceClient("http://ma.test").clear_override(0, 1) == {"ok": True}


@respx.mock
@pytest.mark.asyncio
async def test_failed_override_write_does_not_refresh():
    respx.post("http://ma.test/api/slot-override").mock(
        return_value=httpx.Response(502))
    g = respx.post(GCODE).mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(httpx.HTTPStatusError):
        await MultiAceClient("http://ma.test").set_override(
            ace=0, slot=0, material="", brand="", subtype="", color="")
    assert not g.called
