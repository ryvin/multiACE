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
