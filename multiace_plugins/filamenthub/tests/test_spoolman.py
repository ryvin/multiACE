# License: GPL-3.0
"""Unit tests for the vendored FilamentHub client (SpoolmanClient).

Exercises the real HTTP behaviour against a mocked Spoolman API (respx):
listing/parsing spools, location detection for this printer, the
double-encoded ``extra.filamenthub`` round-trip on assign, and unassign.
"""
import json

import httpx
import pytest
import respx

from filamenthub_plugin.spoolman import SpoolmanClient, _decode_fh, _encode_fh

PRINTER = "davinci-u1"


def _spool(spool_id, *, archived=False, location=None, name="Galaxy Blue",
           material="PLA", color="0000ff", vendor="Generic"):
    extra = {}
    if location is not None:
        extra["filamenthub"] = _encode_fh({"schema": 1, "location": location})
    return {
        "id": spool_id,
        "archived": archived,
        "remaining_weight": 800.0,
        "extra": extra,
        "filament": {"name": name, "material": material,
                     "color_hex": color, "vendor": {"name": vendor}},
    }


def _client():
    return SpoolmanClient("http://fh.test", PRINTER)


def test_encode_decode_round_trip():
    fh = {"schema": 1, "location": {"printer": PRINTER, "ace": 1, "slot": 2}}
    assert _decode_fh(_encode_fh(fh)) == fh


@respx.mock
@pytest.mark.asyncio
async def test_list_spools_parses_and_detects_location():
    respx.get("http://fh.test/api/v1/spool").mock(return_value=httpx.Response(
        200, json=[
            _spool(7, location={"printer": PRINTER, "ace": 1, "slot": 2}),
            _spool(8, location={"printer": "other-printer", "ace": 0, "slot": 0}),
            _spool(9, archived=True),
        ]))
    spools = await _client().list_spools()
    by_id = {s["spool_id"]: s for s in spools}
    assert 9 not in by_id                       # archived skipped
    assert by_id[7]["location"] == {"ace": 1, "slot": 2}   # bound here
    assert by_id[7]["material"] == "PLA"
    assert by_id[7]["vendor"] == "Generic"
    assert by_id[8]["location"] is None         # bound to a different printer


@respx.mock
@pytest.mark.asyncio
async def test_list_spools_empty_on_error():
    respx.get("http://fh.test/api/v1/spool").mock(return_value=httpx.Response(500))
    assert await _client().list_spools() == []


@respx.mock
@pytest.mark.asyncio
async def test_assign_spool_patches_double_encoded_location():
    respx.get("http://fh.test/api/v1/spool/7").mock(return_value=httpx.Response(
        200, json={"id": 7, "extra": {}}))
    patch_route = respx.patch("http://fh.test/api/v1/spool/7").mock(
        return_value=httpx.Response(200))
    location = await _client().assign_spool(7, ace=1, slot=2)
    assert location == {"printer": PRINTER, "ace": 1, "slot": 2}
    sent = json.loads(patch_route.calls.last.request.content)
    # extra.filamenthub is double-encoded: decode back to the object
    assert _decode_fh(sent["extra"]["filamenthub"])["location"] == location


@respx.mock
@pytest.mark.asyncio
async def test_unassign_slot_clears_bound_spool():
    respx.get("http://fh.test/api/v1/spool").mock(return_value=httpx.Response(
        200, json=[_spool(7, location={"printer": PRINTER, "ace": 1, "slot": 2})]))
    respx.get("http://fh.test/api/v1/spool/7").mock(return_value=httpx.Response(
        200, json={"id": 7, "extra": {}}))
    patch_route = respx.patch("http://fh.test/api/v1/spool/7").mock(
        return_value=httpx.Response(200))
    cleared = await _client().unassign_slot(ace=1, slot=2)
    assert cleared == 7
    sent = json.loads(patch_route.calls.last.request.content)
    assert _decode_fh(sent["extra"]["filamenthub"])["location"] is None


@respx.mock
@pytest.mark.asyncio
async def test_unassign_slot_none_when_empty():
    respx.get("http://fh.test/api/v1/spool").mock(return_value=httpx.Response(
        200, json=[_spool(7, location={"printer": PRINTER, "ace": 0, "slot": 0})]))
    assert await _client().unassign_slot(ace=3, slot=3) is None
