import asyncio
from unittest.mock import AsyncMock

import pytest

from multiace_web.poller import StatusPoller


@pytest.mark.asyncio
async def test_poller_calls_run_gcode_at_interval():
    moonraker = AsyncMock()
    moonraker.run_gcode = AsyncMock(return_value="ok")
    poller = StatusPoller(moonraker, interval=0.1)
    task = asyncio.create_task(poller.run())
    await asyncio.sleep(0.35)
    poller.stop()
    await asyncio.wait_for(task, timeout=1.0)
    # Should have polled at least 2 times within 0.35s at 0.1s interval
    assert moonraker.run_gcode.await_count >= 2
    moonraker.run_gcode.assert_awaited_with("ACE_HEAD_STATUS")


@pytest.mark.asyncio
async def test_poller_continues_after_error():
    moonraker = AsyncMock()
    # First call fails, subsequent succeed
    moonraker.run_gcode = AsyncMock(
        side_effect=[Exception("network!"), "ok", "ok", "ok"]
    )
    poller = StatusPoller(moonraker, interval=0.1)
    task = asyncio.create_task(poller.run())
    await asyncio.sleep(0.45)
    poller.stop()
    await asyncio.wait_for(task, timeout=1.0)
    assert moonraker.run_gcode.await_count >= 3  # Recovered after first failure


@pytest.mark.asyncio
async def test_poller_stop_is_responsive():
    """stop() should cause run() to return within ~50ms even mid-sleep."""
    moonraker = AsyncMock()
    moonraker.run_gcode = AsyncMock(return_value="ok")
    poller = StatusPoller(moonraker, interval=10.0)  # long interval
    task = asyncio.create_task(poller.run())
    await asyncio.sleep(0.05)  # let it enter the sleep
    poller.stop()
    # Must complete quickly, not wait the full 10s
    await asyncio.wait_for(task, timeout=0.5)


@pytest.mark.asyncio
async def test_poller_stop_before_run_exits_cleanly():
    """If stop() is called before run() starts, run() should exit without polling."""
    moonraker = AsyncMock()
    moonraker.run_gcode = AsyncMock(return_value="ok")
    poller = StatusPoller(moonraker, interval=1.0)
    poller.stop()
    await asyncio.wait_for(poller.run(), timeout=0.5)
    assert moonraker.run_gcode.await_count == 0


def test_poller_rejects_invalid_interval():
    moonraker = AsyncMock()
    with pytest.raises(ValueError):
        StatusPoller(moonraker, interval=0)
    with pytest.raises(ValueError):
        StatusPoller(moonraker, interval=-1)
