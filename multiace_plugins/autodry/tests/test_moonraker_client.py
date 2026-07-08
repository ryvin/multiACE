# License: GPL-3.0
"""Unit tests for MoonrakerClient + the ace-object parsing helpers."""
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from autodry_plugin.moonraker_client import (
    MoonrakerClient,
    MoonrakerError,
    active_device_0based,
    parse_ace_object,
)


@respx.mock
@pytest.mark.asyncio
async def test_run_gcode_sends_exact_script():
    route = respx.post(url__regex=r"http://mr\.test/printer/gcode/script.*").mock(
        return_value=httpx.Response(200, json={"result": "ok"}))
    mr = MoonrakerClient("http://mr.test")
    out = await mr.run_gcode("ACE_DRY ACE=1 TEMP=55 DURATION=240")
    await mr.close()
    assert out == "ok"
    sent_qs = parse_qs(urlparse(str(route.calls.last.request.url)).query)
    assert sent_qs["script"][0] == "ACE_DRY ACE=1 TEMP=55 DURATION=240"


@respx.mock
@pytest.mark.asyncio
async def test_run_gcode_raises_on_error_status():
    respx.post(url__regex=r"http://mr\.test/printer/gcode/script.*").mock(
        return_value=httpx.Response(500))
    mr = MoonrakerClient("http://mr.test")
    with pytest.raises(MoonrakerError):
        await mr.run_gcode("ACE_DRY ACE=0")
    await mr.close()


@respx.mock
@pytest.mark.asyncio
async def test_run_gcode_raises_on_connection_error():
    respx.post(url__regex=r"http://mr\.test/printer/gcode/script.*").mock(
        side_effect=httpx.ConnectError("refused"))
    mr = MoonrakerClient("http://mr.test")
    with pytest.raises(MoonrakerError):
        await mr.run_gcode("ACE_DRY ACE=0")
    await mr.close()


@respx.mock
@pytest.mark.asyncio
async def test_query_objects_returns_status_dict():
    respx.get(url__regex=r"http://mr\.test/printer/objects/query.*").mock(
        return_value=httpx.Response(200, json={"result": {"status": {"ace": {"device_count": 2}}}}))
    mr = MoonrakerClient("http://mr.test")
    out = await mr.query_objects(["ace"])
    await mr.close()
    assert out["ace"]["device_count"] == 2


@pytest.mark.asyncio
async def test_query_objects_empty_list_short_circuits():
    mr = MoonrakerClient("http://mr.test")
    out = await mr.query_objects([])
    await mr.close()
    assert out == {}


def test_parse_ace_object_units_shape_gives_independent_per_ace_humidity():
    obj = {"device_count": 2, "units": [
        {"unit_index": 0, "environment": {"humidity_pct": 12.5, "has_humidity": True}},
        {"unit_index": 1, "environment": {"humidity_pct": 0.0, "has_humidity": False}},
    ]}
    snaps = parse_ace_object(obj)
    assert snaps[0].humidity_ok is True
    assert snaps[0].humidity_pct == 12.5
    assert snaps[1].humidity_ok is False


def test_parse_ace_object_legacy_shape_only_active_reported_and_unknown_humidity():
    obj = {"active_device": 2, "dryer_status": {"status": "stop"}}
    snaps = parse_ace_object(obj)
    assert list(snaps.keys()) == [1]  # active_device 2 (1-based) -> index 1
    assert snaps[1].humidity_ok is False


def test_parse_ace_object_empty_object_returns_empty():
    assert parse_ace_object({}) == {}


def test_active_device_0based_conversions():
    assert active_device_0based({"active_device": 1}) == 0
    assert active_device_0based({"active_device": 0}) is None  # 0 == "no active device"
    assert active_device_0based({}) is None
    assert active_device_0based({"active_unit": 3}) == 3
    assert active_device_0based({"active_device": "not-a-number"}) is None
