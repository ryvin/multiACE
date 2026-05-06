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


import pytest
from unittest.mock import AsyncMock, MagicMock

from multiace_web.poller import MultiAcePoller


class _FakeMoonraker:
    """Minimal stand-in for MoonrakerClient supporting query_objects + run_gcode."""
    def __init__(self) -> None:
        self.gcodes_run: list[str] = []
        self.print_state = "standby"
        self.active_idx = 0  # 0-indexed; firmware exposes +1
        self.switch_should_fail = False

    async def run_gcode(self, script: str) -> str:
        if script.startswith("ACE_SWITCH") and self.switch_should_fail:
            raise RuntimeError("usb gone")
        self.gcodes_run.append(script)
        if script.startswith("ACE_SWITCH TARGET="):
            self.active_idx = int(script.split("TARGET=")[1])
        return "ok"

    async def query_objects(self, objects):
        out = {}
        if "print_stats" in objects:
            out["print_stats"] = {"state": self.print_state}
        if "ace" in objects:
            out["ace"] = {
                "active_device": self.active_idx + 1,  # 1-indexed
                "status": "ready",
                "humidity": 22.0,
                "dryer_status": {"status": "stop"},
            }
        return out


def _make_autodry_mock(device_count: int) -> MagicMock:
    """Returns a MagicMock-shaped object that mimics AutoDryer enough for the poller."""
    autodry = MagicMock()
    autodry.tick_one_ace = AsyncMock()
    fsms = {i: MagicMock(unreachable=False, locked=False) for i in range(device_count)}
    autodry._manager = MagicMock()
    autodry._manager.get.side_effect = lambda i: fsms[i]
    autodry.manager = autodry._manager
    autodry._fsms = fsms  # easy access for assertions
    return autodry


@pytest.mark.asyncio
async def test_multi_ace_poller_idle_alternates_between_aces() -> None:
    fake_mr = _FakeMoonraker()
    autodry = _make_autodry_mock(2)
    poller = MultiAcePoller(moonraker=fake_mr, autodry=autodry, device_count=2, period_s=0.0)
    await poller.tick()
    assert fake_mr.active_idx == 1   # switched 0 → 1
    await poller.tick()
    assert fake_mr.active_idx == 0   # back to 0
    assert autodry.tick_one_ace.await_count == 2


@pytest.mark.asyncio
async def test_multi_ace_poller_printing_pins_active_ace_and_locks_others() -> None:
    fake_mr = _FakeMoonraker()
    fake_mr.print_state = "printing"
    fake_mr.active_idx = 1
    autodry = _make_autodry_mock(2)
    poller = MultiAcePoller(moonraker=fake_mr, autodry=autodry, device_count=2, period_s=0.0)
    await poller.tick()
    # No ACE_SWITCH issued during a print
    assert not any(s.startswith("ACE_SWITCH") for s in fake_mr.gcodes_run)
    # FSM 0 locked, FSM 1 free
    assert autodry._fsms[0].locked is True
    assert autodry._fsms[1].locked is False
    # tick_one_ace called for active ACE only
    autodry.tick_one_ace.assert_awaited_once()
    call_kwargs = autodry.tick_one_ace.await_args.kwargs
    assert autodry.tick_one_ace.await_args.args[0] == 1 or call_kwargs.get("ace_idx") == 1


@pytest.mark.asyncio
async def test_multi_ace_poller_two_consecutive_switch_failures_mark_unreachable() -> None:
    fake_mr = _FakeMoonraker()
    fake_mr.switch_should_fail = True
    autodry = _make_autodry_mock(2)
    poller = MultiAcePoller(moonraker=fake_mr, autodry=autodry, device_count=2, period_s=0.0)
    # First failure (target=1, switch fails) — not yet unreachable
    await poller.tick()
    assert autodry._fsms[1].unreachable is False
    # Second failure on same target — flips unreachable
    await poller.tick()
    assert autodry._fsms[1].unreachable is True


@pytest.mark.asyncio
async def test_multi_ace_poller_idle_skips_switch_when_already_active() -> None:
    """If round-robin target equals current active ACE, no ACE_SWITCH is issued."""
    fake_mr = _FakeMoonraker()
    fake_mr.active_idx = 1
    autodry = _make_autodry_mock(2)
    poller = MultiAcePoller(moonraker=fake_mr, autodry=autodry, device_count=2, period_s=0.0)
    # last_polled starts at -1 → target = (−1+1) % 2 = 0
    # active is already 1, target is 0, so it WILL switch from 1→0
    await poller.tick()
    assert "ACE_SWITCH TARGET=0" in fake_mr.gcodes_run
    fake_mr.gcodes_run.clear()
    # Now last_polled=0; next target = 1. active is now 0 → must switch to 1
    await poller.tick()
    assert "ACE_SWITCH TARGET=1" in fake_mr.gcodes_run


@pytest.mark.asyncio
async def test_multi_ace_poller_successful_switch_resets_unreachable_flag() -> None:
    fake_mr = _FakeMoonraker()
    autodry = _make_autodry_mock(2)
    autodry._fsms[1].unreachable = True   # pretend earlier failures marked it
    poller = MultiAcePoller(moonraker=fake_mr, autodry=autodry, device_count=2, period_s=0.0)
    await poller.tick()  # target=1; switch should succeed → resets
    assert autodry._fsms[1].unreachable is False


@pytest.mark.asyncio
async def test_multi_ace_poller_writes_last_ace_data_on_tick() -> None:
    fake_mr = _FakeMoonraker()
    autodry = _make_autodry_mock(2)
    cache: dict[int, dict] = {}
    poller = MultiAcePoller(moonraker=fake_mr, autodry=autodry, device_count=2,
                            period_s=0.0, last_ace_data=cache)
    await poller.tick()
    # Whatever ACE was just polled should have an entry in cache
    assert len(cache) == 1
    polled_idx = next(iter(cache))
    assert "last_seen_ts" in cache[polled_idx]


@pytest.mark.asyncio
async def test_multi_ace_poller_writes_last_ace_data_during_print() -> None:
    fake_mr = _FakeMoonraker()
    fake_mr.print_state = "printing"
    fake_mr.active_idx = 0
    autodry = _make_autodry_mock(2)
    cache: dict[int, dict] = {}
    poller = MultiAcePoller(moonraker=fake_mr, autodry=autodry, device_count=2,
                            period_s=0.0, last_ace_data=cache)
    await poller.tick()
    # The active ACE should be cached during a print
    assert 0 in cache
    assert "last_seen_ts" in cache[0]


@pytest.mark.asyncio
async def test_multi_ace_poller_no_last_ace_data_kwarg_is_noop() -> None:
    """When last_ace_data is not passed (default None), the poller just skips caching."""
    fake_mr = _FakeMoonraker()
    autodry = _make_autodry_mock(2)
    poller = MultiAcePoller(moonraker=fake_mr, autodry=autodry, device_count=2, period_s=0.0)
    # Should complete without raising
    await poller.tick()
    assert poller._last_ace_data is None
