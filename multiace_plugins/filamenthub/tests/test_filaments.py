# License: GPL-3.0
import asyncio
import json
import re

import httpx
import pytest
import respx

from filamenthub_plugin.filaments import create_spool_from_filament, list_filaments

BASE = "http://fh.test"


def _filaments_body():
    return [
        {"id": 3, "name": "Matte Black", "material": "PLA", "color_hex": "000000",
         "vendor": {"id": 2, "name": "Sunlu"}, "settings_extruder_temp": 210,
         "settings_bed_temp": 60, "density": 1.24, "diameter": 1.75, "weight": 1000},
        {"id": 1, "name": "Galaxy Blue", "material": "PETG", "color_hex": "#0000ff",
         "vendor": {"id": 1, "name": "Elegoo"}, "settings_extruder_temp": 240,
         "settings_bed_temp": 80, "weight": 1000},
        {"id": 2, "name": "Basic", "material": "PLA"},  # no vendor, colour, temps
        {"id": 4, "name": "Alpha", "material": "ABS", "color_hex": "zzz",
         "vendor": {"id": 2, "name": "Sunlu"}},
    ]


@respx.mock
def test_list_filaments_maps_and_sorts_by_vendor_then_name():
    respx.get(f"{BASE}/api/v1/filament").mock(
        return_value=httpx.Response(200, json=_filaments_body()))
    out = asyncio.run(list_filaments(BASE))
    assert [f["filament_id"] for f in out] == [2, 1, 4, 3]
    assert out[1] == {"filament_id": 1, "name": "Galaxy Blue", "vendor": "Elegoo",
                      "material": "PETG", "color": "#0000ff",
                      "extruder_temp": 240, "bed_temp": 80}
    assert out[3]["color"] == "#000000"


@respx.mock
def test_list_filaments_robust_to_missing_vendor_and_color():
    respx.get(f"{BASE}/api/v1/filament").mock(
        return_value=httpx.Response(200, json=_filaments_body()))
    out = asyncio.run(list_filaments(BASE))
    basic = next(f for f in out if f["filament_id"] == 2)
    assert basic == {"filament_id": 2, "name": "Basic", "vendor": "",
                     "material": "PLA", "color": "", "extruder_temp": None,
                     "bed_temp": None}
    alpha = next(f for f in out if f["filament_id"] == 4)
    assert alpha["color"] == ""  # invalid hex is dropped, not passed through


@respx.mock
def test_list_filaments_raises_on_upstream_error():
    respx.get(f"{BASE}/api/v1/filament").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPError):
        asyncio.run(list_filaments(BASE))


@respx.mock
def test_create_spool_mirrors_clone_with_virtual_tag():
    route = respx.post(f"{BASE}/api/v1/spool").mock(
        return_value=httpx.Response(200, json={"id": 77}))
    spool_id = asyncio.run(create_spool_from_filament(
        BASE, {"id": 3, "name": "Matte Black", "weight": 750}))
    assert spool_id == 77
    sent = json.loads(route.calls.last.request.content)
    assert sent["filament_id"] == 3
    assert sent["initial_weight"] == 750
    assert sent["remaining_weight"] == 750
    uid = json.loads(sent["extra"]["card_uid"])
    assert re.fullmatch(r"VIRT-[0-9A-F]{8}", uid)
    fh = json.loads(json.loads(sent["extra"]["filamenthub"]))
    assert fh == {"schema": 1, "virtual_tag": True}


@respx.mock
def test_create_spool_defaults_weight_to_1000_and_accepts_filament_id_key():
    route = respx.post(f"{BASE}/api/v1/spool").mock(
        return_value=httpx.Response(200, json={"id": 5}))
    asyncio.run(create_spool_from_filament(BASE, {"filament_id": 9}))
    sent = json.loads(route.calls.last.request.content)
    assert sent["filament_id"] == 9
    assert sent["initial_weight"] == 1000 and sent["remaining_weight"] == 1000


@respx.mock
def test_create_spool_uids_are_unique():
    respx.post(f"{BASE}/api/v1/spool").mock(
        return_value=httpx.Response(200, json={"id": 5}))
    route = respx.routes[0]
    asyncio.run(create_spool_from_filament(BASE, {"id": 1}))
    asyncio.run(create_spool_from_filament(BASE, {"id": 1}))
    uids = {json.loads(c.request.content)["extra"]["card_uid"] for c in route.calls}
    assert len(uids) == 2


@respx.mock
def test_create_spool_raises_on_upstream_error():
    respx.post(f"{BASE}/api/v1/spool").mock(return_value=httpx.Response(400))
    with pytest.raises(httpx.HTTPError):
        asyncio.run(create_spool_from_filament(BASE, {"id": 1}))
