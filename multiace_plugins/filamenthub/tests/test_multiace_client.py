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
