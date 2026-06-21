import json

import httpx
import pytest
import respx

from multiace_web.spoolman import SpoolBinding, SpoolmanClient


@pytest.mark.asyncio
async def test_list_all_bindings_groups_by_ace_then_slot() -> None:
    """Spoolman returns a flat list of spools; client groups them
    {ace: {slot: SpoolBinding}} for the configured printer only."""
    base_url = "http://fh.local"
    spools = [
        # bound to ace=1 slot=0 on this printer — included
        {"id": 142, "filament": {"name": "PLA Black", "material": "PLA", "color_hex": "000000"},
         "remaining_weight": 920.0,
         "extra": {"filamenthub": '{"schema":1,"location":{"printer":"u1-1","ace":1,"slot":0}}'}},
        # bound to ace=0 slot=2 — included
        {"id": 208, "filament": {"name": "TPU Blue", "material": "TPU", "color_hex": "1133ff"},
         "remaining_weight": 410.0,
         "extra": {"filamenthub": '{"schema":1,"location":{"printer":"u1-1","ace":0,"slot":2}}'}},
        # bound to a different printer — excluded
        {"id": 311, "filament": {"name": "ABS White", "material": "ABS", "color_hex": "ffffff"},
         "remaining_weight": 730.0,
         "extra": {"filamenthub": '{"schema":1,"location":{"printer":"kobra","ace":0,"slot":0}}'}},
        # legacy: missing ace field — defaults to ace=0
        {"id": 412, "filament": {"name": "PETG Red", "material": "PETG", "color_hex": "ff0000"},
         "remaining_weight": 600.0,
         "extra": {"filamenthub": '{"schema":1,"location":{"printer":"u1-1","slot":3}}'}},
    ]
    async with respx.mock(base_url=base_url) as mock:
        mock.get("/api/v1/spool").respond(200, json=spools)
        client = SpoolmanClient(base_url=base_url, printer_id="u1-1")
        bindings = await client.list_all_bindings()

    assert set(bindings.keys()) == {0, 1}
    assert bindings[1][0].spool_id == 142
    assert bindings[1][0].material == "PLA"
    assert bindings[0][2].spool_id == 208
    assert bindings[0][3].spool_id == 412   # legacy, defaulted
    # the kobra spool is excluded
    all_ids = {b.spool_id for slots in bindings.values() for b in slots.values()}
    assert 311 not in all_ids


@pytest.mark.asyncio
async def test_list_all_bindings_handles_timeout_returns_empty() -> None:
    """Timeout → empty dict. Caller decides cache aging."""
    async with respx.mock(base_url="http://fh.local") as mock:
        mock.get("/api/v1/spool").mock(side_effect=httpx.TimeoutException("slow"))
        client = SpoolmanClient(base_url="http://fh.local", printer_id="u1-1", timeout_s=0.1)
        bindings = await client.list_all_bindings()
    assert bindings == {}


@pytest.mark.asyncio
async def test_list_all_bindings_handles_5xx_returns_empty() -> None:
    async with respx.mock(base_url="http://fh.local") as mock:
        mock.get("/api/v1/spool").respond(503)
        client = SpoolmanClient(base_url="http://fh.local", printer_id="u1-1")
        bindings = await client.list_all_bindings()
    assert bindings == {}


# --- list_spools (the FilamentHub picker source) ---------------------------

@pytest.mark.asyncio
async def test_list_spools_returns_all_with_placement() -> None:
    spools = [
        {"id": 21, "filament": {"name": "Matte Ivory", "material": "PLA",
                                "color_hex": "FFFFF0", "vendor": {"name": "Snapmaker"}},
         "remaining_weight": 126.4,
         "extra": {"filamenthub": '{"schema":1,"td":3.3,"location":null}'}},
        {"id": 42, "filament": {"name": "PETG Blue", "material": "PETG",
                                "color_hex": "0000ff", "vendor": {"name": "Jayo"}},
         "remaining_weight": 800.0,
         "extra": {"filamenthub": '{"schema":1,"location":{"printer":"u1-ace","ace":1,"slot":2}}'}},
        # placed on a different printer — listed, but no local location
        {"id": 99, "filament": {"name": "TPU Red", "material": "TPU", "color_hex": "ff0000"},
         "remaining_weight": 300.0,
         "extra": {"filamenthub": '{"schema":1,"location":{"printer":"other","ace":0,"slot":0}}'}},
        # archived — excluded
        {"id": 7, "archived": True, "filament": {"name": "old", "material": "PLA"}},
    ]
    async with respx.mock(base_url="http://fh.local") as mock:
        mock.get("/api/v1/spool").respond(200, json=spools)
        client = SpoolmanClient(base_url="http://fh.local", printer_id="u1-ace")
        out = await client.list_spools()
    by_id = {s["spool_id"]: s for s in out}
    assert set(by_id) == {21, 42, 99}                 # archived dropped
    assert by_id[21]["vendor"] == "Snapmaker"
    assert by_id[21]["material"] == "PLA" and by_id[21]["color"] == "FFFFF0"
    assert by_id[21]["location"] is None              # location null
    assert by_id[42]["location"] == {"ace": 1, "slot": 2}   # placed on this printer
    assert by_id[99]["location"] is None              # placed elsewhere -> not here


# --- assign_spool (bind to a slot, like a scan) ----------------------------

@pytest.mark.asyncio
async def test_assign_spool_patches_location_preserving_extra() -> None:
    captured = {}

    def _patch_side_effect(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": 21})

    async with respx.mock(base_url="http://fh.local") as mock:
        mock.get("/api/v1/spool/21").respond(200, json={
            "id": 21,
            "extra": {"filamenthub": '{"schema":1,"td":3.3,"location":null}'},
        })
        mock.patch("/api/v1/spool/21").mock(side_effect=_patch_side_effect)
        client = SpoolmanClient(base_url="http://fh.local", printer_id="u1-ace")
        loc = await client.assign_spool(21, ace=0, slot=3)

    assert loc == {"printer": "u1-ace", "ace": 0, "slot": 3}
    fh = json.loads(captured["body"]["extra"]["filamenthub"])
    assert fh["location"] == {"printer": "u1-ace", "ace": 0, "slot": 3}
    assert fh["td"] == 3.3 and fh["schema"] == 1       # other fields preserved


@pytest.mark.asyncio
async def test_assign_spool_handles_missing_extra() -> None:
    captured = {}
    async with respx.mock(base_url="http://fh.local") as mock:
        mock.get("/api/v1/spool/5").respond(200, json={"id": 5, "extra": {}})
        mock.patch("/api/v1/spool/5").mock(
            side_effect=lambda r: (captured.__setitem__("b", json.loads(r.content)),
                                   httpx.Response(200, json={}))[1])
        client = SpoolmanClient(base_url="http://fh.local", printer_id="u1-ace")
        await client.assign_spool(5, ace=1, slot=1)
    fh = json.loads(captured["b"]["extra"]["filamenthub"])
    assert fh["location"]["slot"] == 1 and fh["location"]["ace"] == 1
