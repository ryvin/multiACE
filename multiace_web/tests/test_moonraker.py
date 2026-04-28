import httpx
import pytest
import respx

from multiace_web.moonraker import MoonrakerClient, MoonrakerError


@pytest.mark.asyncio
async def test_get_printer_info_returns_state():
    async with respx.mock(base_url="http://printer:7125") as mock:
        mock.get("/printer/info").respond(
            200, json={"result": {"state": "ready", "state_message": "Printer is ready"}}
        )
        client = MoonrakerClient("http://printer:7125")
        info = await client.printer_info()
        assert info["state"] == "ready"
        await client.close()


@pytest.mark.asyncio
async def test_run_gcode_posts_script():
    async with respx.mock(base_url="http://printer:7125") as mock:
        route = mock.post("/printer/gcode/script").respond(200, json={"result": "ok"})
        client = MoonrakerClient("http://printer:7125")
        result = await client.run_gcode("ACEC__Load_T1")
        assert result == "ok"
        assert route.called
        # Verify the script was URL-encoded into the query string
        assert "ACEC__Load_T1" in str(route.calls[0].request.url)
        await client.close()


@pytest.mark.asyncio
async def test_run_gcode_raises_on_4xx():
    async with respx.mock(base_url="http://printer:7125") as mock:
        mock.post("/printer/gcode/script").respond(
            400, json={"error": {"message": "extruder[1] timeout"}}
        )
        client = MoonrakerClient("http://printer:7125")
        with pytest.raises(MoonrakerError) as excinfo:
            await client.run_gcode("ACEC__Load_T1")
        assert "timeout" in str(excinfo.value)
        await client.close()


@pytest.mark.asyncio
async def test_run_gcode_raises_on_connection_error():
    async with respx.mock(base_url="http://printer:7125") as mock:
        mock.post("/printer/gcode/script").mock(side_effect=httpx.ConnectError("nope"))
        client = MoonrakerClient("http://printer:7125")
        with pytest.raises(MoonrakerError):
            await client.run_gcode("ACEC__Load_T1")
        await client.close()


@pytest.mark.asyncio
async def test_get_logs_returns_last_n_lines():
    async with respx.mock(base_url="http://printer:7125") as mock:
        body = "\n".join(f"line {i}" for i in range(1, 11)) + "\n"
        mock.get("/server/files/logs/klippy.log").respond(200, content=body)
        client = MoonrakerClient("http://printer:7125")
        lines = await client.get_logs(kind="klippy", lines=3)
        assert lines == ["line 8", "line 9", "line 10"]
        await client.close()


@pytest.mark.asyncio
async def test_get_logs_handles_empty_file():
    async with respx.mock(base_url="http://printer:7125") as mock:
        mock.get("/server/files/logs/klippy.log").respond(200, content="")
        client = MoonrakerClient("http://printer:7125")
        lines = await client.get_logs(kind="klippy", lines=10)
        assert lines == []
        await client.close()


@pytest.mark.asyncio
async def test_get_logs_raises_on_404():
    async with respx.mock(base_url="http://printer:7125") as mock:
        mock.get("/server/files/logs/missing.log").respond(404)
        client = MoonrakerClient("http://printer:7125")
        with pytest.raises(MoonrakerError):
            await client.get_logs(kind="missing")
        await client.close()


@pytest.mark.asyncio
async def test_get_logs_rejects_path_traversal():
    client = MoonrakerClient("http://printer:7125")
    with pytest.raises(MoonrakerError, match="invalid log kind"):
        await client.get_logs(kind="../etc/passwd")
    await client.close()
