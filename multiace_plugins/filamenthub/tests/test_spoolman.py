# License: GPL-3.0
"""Unit tests for the vendored FilamentHub client (SpoolmanClient).

Exercises the real HTTP behaviour against a mocked Spoolman API (respx):
listing/parsing spools, location detection for this printer, the
double-encoded ``extra.filamenthub`` round-trip on assign, and unassign.
"""
import json
from datetime import datetime, timedelta, timezone

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
async def test_list_spools_raises_when_requested():
    respx.get("http://fh.test/api/v1/spool").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPError):
        await _client().list_spools(raise_on_error=True)


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
    written = _decode_fh(sent["extra"]["filamenthub"])["location"]
    assert {k: written[k] for k in ("printer", "ace", "slot")} == location


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


# --- WP1: write hardening -------------------------------------------------
# Spoolman PATCH may replace `extra` wholesale (unverified on 0.23; see plan),
# so every write must carry ALL pre-existing extra keys, not only `filamenthub`.

_RICH_FH = {"schema": 1, "td": 3.4, "color_measured": "FF00AA",
            "virtual_tag": False, "location": None}


def _rich_extra(fh=None):
    return {
        "card_uid": json.dumps("04:AB:CD:EF"),
        "tags": json.dumps(json.dumps(["dry", "favourite"])),
        "pressure_advance_matrix": json.dumps(json.dumps({"0.4": 0.035})),
        "filamenthub": _encode_fh(_RICH_FH if fh is None else fh),
    }


def _mock_single(spool_id, extra):
    respx.get(f"http://fh.test/api/v1/spool/{spool_id}").mock(
        return_value=httpx.Response(200, json={"id": spool_id, "extra": extra}))
    return respx.patch(f"http://fh.test/api/v1/spool/{spool_id}").mock(
        return_value=httpx.Response(200))


def _sent_body(route):
    return json.loads(route.calls.last.request.content)


@respx.mock
@pytest.mark.asyncio
async def test_assign_preserves_all_other_extra_keys_verbatim():
    extra = _rich_extra()
    patch_route = _mock_single(7, extra)
    await _client().assign_spool(7, ace=1, slot=2)
    sent = _sent_body(patch_route)["extra"]
    assert set(sent) == set(extra)
    for key in ("card_uid", "tags", "pressure_advance_matrix"):
        assert sent[key] == extra[key]          # untouched, byte-for-byte


@respx.mock
@pytest.mark.asyncio
async def test_assign_preserves_other_filamenthub_fields():
    patch_route = _mock_single(7, _rich_extra())
    await _client().assign_spool(7, ace=1, slot=2)
    fh = _decode_fh(_sent_body(patch_route)["extra"]["filamenthub"])
    assert fh["td"] == 3.4
    assert fh["color_measured"] == "FF00AA"
    assert fh["virtual_tag"] is False
    assert fh["schema"] == 1


@respx.mock
@pytest.mark.asyncio
async def test_assign_writes_asserted_by_and_utc_iso_asserted_at():
    patch_route = _mock_single(7, _rich_extra())
    before = datetime.now(timezone.utc)
    await _client().assign_spool(7, ace=1, slot=2)
    loc = _decode_fh(_sent_body(patch_route)["extra"]["filamenthub"])["location"]
    assert {k: loc[k] for k in ("printer", "ace", "slot")} == {
        "printer": PRINTER, "ace": 1, "slot": 2}
    assert loc["asserted_by"] == "user:assign"
    ts = datetime.fromisoformat(loc["asserted_at"])
    assert ts.utcoffset() == timedelta(0)       # timezone-aware UTC
    assert before - timedelta(seconds=1) <= ts <= datetime.now(timezone.utc)


@respx.mock
@pytest.mark.asyncio
async def test_assign_keeps_double_encoding():
    patch_route = _mock_single(7, _rich_extra())
    await _client().assign_spool(7, ace=1, slot=2)
    once = json.loads(_sent_body(patch_route)["extra"]["filamenthub"])
    assert isinstance(once, str)                # wire value decodes to a string...
    assert isinstance(json.loads(once), dict)   # ...whose content is the object


@pytest.mark.parametrize("ace,slot,expected", [
    (1, 2, f"{PRINTER}/ace1 T2"),
    (3, 0, f"{PRINTER}/ace3 T0"),
    (0, 2, f"{PRINTER} T2"),
])
@respx.mock
@pytest.mark.asyncio
async def test_assign_sets_standard_location_string(ace, slot, expected):
    patch_route = _mock_single(7, _rich_extra())
    await _client().assign_spool(7, ace=ace, slot=slot)
    assert _sent_body(patch_route)["location"] == expected


@respx.mock
@pytest.mark.asyncio
async def test_unassign_preserves_extra_and_clears_both_locations():
    bound = dict(_RICH_FH, location={"printer": PRINTER, "ace": 1, "slot": 2})
    extra = _rich_extra(bound)
    respx.get("http://fh.test/api/v1/spool").mock(return_value=httpx.Response(
        200, json=[{"id": 7, "extra": extra, "filament": {}}]))
    patch_route = _mock_single(7, extra)
    assert await _client().unassign_slot(ace=1, slot=2) == 7
    body = _sent_body(patch_route)
    assert body["location"] == ""
    sent = body["extra"]
    assert set(sent) == set(extra)
    for key in ("card_uid", "tags", "pressure_advance_matrix"):
        assert sent[key] == extra[key]
    fh = _decode_fh(sent["filamenthub"])
    assert fh["location"] is None
    assert fh["td"] == 3.4 and fh["color_measured"] == "FF00AA"


@respx.mock
@pytest.mark.asyncio
async def test_assign_does_not_mutate_get_response_extra():
    extra = _rich_extra()
    snapshot = json.loads(json.dumps(extra))
    _mock_single(7, extra)
    await _client().assign_spool(7, ace=1, slot=2)
    assert extra == snapshot
