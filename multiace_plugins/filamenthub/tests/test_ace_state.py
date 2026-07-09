# License: GPL-3.0
"""Unit tests for the FilamentHub ace-state read-seam client."""
import httpx
import pytest
import respx

from filamenthub_plugin.ace_state import (
    AceStateClient,
    AceStateSeamDisabled,
    AceStateProviderError,
    AceStateBadRequest,
    AceStateUnreachable,
)

URL = "http://fh.test/fleet/api/ace-state"


@respx.mock
@pytest.mark.asyncio
async def test_get_ace_state_returns_envelope():
    body = {
        "schema": 1, "printer": "davinci-u1", "ts": 123,
        "slots": [{"ace": 0, "slot": 0, "spool_id": 42, "material": "PLA",
                   "color": "#00ff00", "name": "PolyTerra Green",
                   "asserted_by": "user:assign", "asserted_at": "2026-07-09T00:00:00Z"}],
        "disputed": [{"ace": 0, "slot": 1, "spool_id": 7, "material": "PETG",
                      "color": "#ff0000", "name": "X", "asserted_by": "watcher:rfid",
                      "asserted_at": "2026-07-08T00:00:00Z", "winner_spool_id": 99}],
    }
    respx.get(url__startswith=URL).mock(return_value=httpx.Response(200, json=body))
    out = await AceStateClient(URL).get_ace_state("davinci-u1")
    assert out["slots"][0]["spool_id"] == 42
    assert out["disputed"][0]["winner_spool_id"] == 99


@respx.mock
@pytest.mark.asyncio
async def test_503_raises_seam_disabled():
    respx.get(url__startswith=URL).mock(return_value=httpx.Response(503))
    with pytest.raises(AceStateSeamDisabled):
        await AceStateClient(URL).get_ace_state("davinci-u1")


@respx.mock
@pytest.mark.asyncio
async def test_502_raises_provider_error():
    respx.get(url__startswith=URL).mock(return_value=httpx.Response(502))
    with pytest.raises(AceStateProviderError):
        await AceStateClient(URL).get_ace_state("davinci-u1")


@respx.mock
@pytest.mark.asyncio
async def test_400_raises_bad_request():
    respx.get(url__startswith=URL).mock(return_value=httpx.Response(400))
    with pytest.raises(AceStateBadRequest):
        await AceStateClient(URL).get_ace_state("davinci-u1")


@respx.mock
@pytest.mark.asyncio
async def test_network_error_raises_unreachable():
    respx.get(url__startswith=URL).mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(AceStateUnreachable):
        await AceStateClient(URL).get_ace_state("davinci-u1")


@respx.mock
@pytest.mark.asyncio
async def test_schema_mismatch_still_returns_but_warns(caplog):
    body = {"schema": 999, "printer": "davinci-u1", "ts": 1, "slots": [], "disputed": []}
    respx.get(url__startswith=URL).mock(return_value=httpx.Response(200, json=body))
    out = await AceStateClient(URL).get_ace_state("davinci-u1")
    assert out["slots"] == []
    assert any("schema" in r.message.lower() for r in caplog.records)
