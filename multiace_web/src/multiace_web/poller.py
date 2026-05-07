"""Periodic ACE_HEAD_STATUS poll task.

multiACE writes to its state log only on actions; between actions, the latest
log line is stale. This poller fires ACE_HEAD_STATUS every N seconds so the UI
sees fresh state even when nothing's happening.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import TYPE_CHECKING

from .moonraker import MoonrakerClient, MoonrakerError

if TYPE_CHECKING:
    from .autodryer import AutoDryer

log = logging.getLogger(__name__)


class StatusPoller:
    """Periodically fires ACE_HEAD_STATUS to refresh state.

    Single-shot lifecycle: call `run()` exactly once. After `stop()` is called
    or `run()` returns, the instance cannot be restarted — create a new one.

    Errors are logged but do not stop the loop — Moonraker may be temporarily
    unreachable and we want to keep trying. The interval sleep happens after
    every iteration regardless of success/failure, so the loop never busy-loops.
    """

    def __init__(self, moonraker: MoonrakerClient, interval: float = 5.0) -> None:
        if interval <= 0:
            raise ValueError(f"interval must be positive, got {interval}")
        self._moonraker = moonraker
        self._interval = interval
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._moonraker.run_gcode("ACE_HEAD_STATUS")
            except MoonrakerError as e:
                log.debug("Poller: %s", e)
            except Exception:
                log.exception("Poller: unexpected error")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
                return
            except asyncio.TimeoutError:
                pass


class PrintStatePoller:
    """Refresh app.state.last_print on a server-side cadence so the autodry
    FSM sees live print/dryer/humidity data even when no UI client is connected.

    Single-shot lifecycle: call `run()` exactly once. Moonraker errors are
    swallowed (the cached `last_print` stays valid for one tick); unexpected
    errors are logged.
    """

    def __init__(self, fetcher, app_state, interval: float = 4.0) -> None:
        if interval <= 0:
            raise ValueError(f"interval must be positive, got {interval}")
        self._fetcher = fetcher  # async () -> dict
        self._app_state = app_state
        self._interval = interval
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                payload = await self._fetcher()
                # Dual writer: GET /api/print also writes here. Python attr
                # assignment is atomic so no torn reads; tick-vs-request
                # interleaving may produce a slightly older payload "winning"
                # by ms but is harmless for the FSM. The companion timestamp
                # below is consumed by autodry_inputs_fetcher to detect
                # staleness when Moonraker is unreachable.
                self._app_state.last_print = payload
                self._app_state.last_print_at = time.time()
            except MoonrakerError as e:
                log.debug("PrintStatePoller: %s", e)
            except Exception:
                log.exception("PrintStatePoller: unexpected error")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
                return
            except asyncio.TimeoutError:
                pass


class MultiAcePoller:
    """Round-robin between ACEs while idle; pin to the active ACE during prints.

    Behavior:
    - idle: switch to next ACE if needed, query [ace] state, tick that FSM.
    - printing: skip switch, query active ACE only, lock other FSMs.
    - 2 consecutive switch failures → mark target FSM unreachable.

    Optional ``last_ace_data`` dict: when provided, after each successful
    tick the poller snapshots ``{**ace_obj, "last_seen_ts": time.time()}``
    under the polled ACE index. The ``/api/print`` endpoint reads this cache
    to show per-ACE dryer/humidity data for inactive ACEs.
    """

    def __init__(
        self,
        moonraker: MoonrakerClient,
        autodry: "AutoDryer",
        device_count: int,
        period_s: float = 5.0,
        last_ace_data: "dict | None" = None,
    ) -> None:
        if device_count < 1:
            raise ValueError(f"device_count must be >= 1, got {device_count}")
        self._mr = moonraker
        self._autodry = autodry
        self._n = device_count
        self._period = period_s
        self._stop = asyncio.Event()
        # -1 sentinel: on first idle tick we query the current active ACE and
        # anchor last_polled to it, so round-robin starts on the NEXT ACE.
        self._last_polled = -1
        self._consecutive_switch_failures: dict[int, int] = {}
        self._last_ace_data = last_ace_data

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception:
                log.exception("MultiAcePoller tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._period)
                return
            except asyncio.TimeoutError:
                pass

    async def tick(self) -> None:
        ps_obj = await self._mr.query_objects(["print_stats"])
        state = (ps_obj.get("print_stats") or {}).get("state", "standby")
        if state == "printing":
            await self._tick_printing()
        else:
            await self._tick_idle()

    async def _tick_printing(self) -> None:
        ace_obj = (await self._mr.query_objects(["ace"])).get("ace") or {}
        active_idx = max(0, int(ace_obj.get("active_device", 1)) - 1)
        for i in range(self._n):
            self._autodry.manager.get(i).locked = (i != active_idx)
        await self._autodry.tick_one_ace(active_idx, now_ts=time.time())
        if self._last_ace_data is not None:
            self._last_ace_data[active_idx] = {**ace_obj, "last_seen_ts": time.time()}

    async def _tick_idle(self) -> None:
        # Round-robin OFF by default. Each idle-time ACE_SWITCH writes
        # swap_in_progress=True to multiace_state.log; the firmware does NOT
        # emit a clear-event for autodry-driven switches, so the web's
        # CurrentState.swap_in_progress sticks True between switches and
        # disables every Load button in the UI. Re-enable via env var once
        # either the firmware emits SWITCH_DONE or state.py learns to ignore
        # swap_in_progress for autodry swaps.
        if os.environ.get("MULTIACE_AUTODRY_ROUND_ROBIN", "").lower() not in ("1", "true", "yes"):
            return

        for i in range(self._n):
            self._autodry.manager.get(i).locked = False

        ace_obj = (await self._mr.query_objects(["ace"])).get("ace") or {}
        active_idx = max(0, int(ace_obj.get("active_device", 1)) - 1)

        # On first tick, anchor last_polled to the current active ACE so the
        # round-robin opens on the NEXT ACE rather than always on ACE 0.
        if self._last_polled == -1:
            self._last_polled = active_idx

        target = (self._last_polled + 1) % self._n

        if active_idx != target:
            try:
                await self._mr.run_gcode(f"ACE_SWITCH TARGET={target}")
                self._consecutive_switch_failures[target] = 0
                self._autodry.manager.get(target).unreachable = False
            except Exception as e:
                self._consecutive_switch_failures[target] = (
                    self._consecutive_switch_failures.get(target, 0) + 1
                )
                if self._consecutive_switch_failures[target] >= 2:
                    self._autodry.manager.get(target).unreachable = True
                log.warning(
                    "ACE_SWITCH TARGET=%d failed (%d consecutive): %s",
                    target, self._consecutive_switch_failures[target], e,
                )
                # Do NOT advance last_polled on failure so the next tick retries
                # the same target (enabling consecutive-failure tracking).
                return

        await self._autodry.tick_one_ace(target, now_ts=time.time())
        if self._last_ace_data is not None:
            self._last_ace_data[target] = {**ace_obj, "last_seen_ts": time.time()}
        self._last_polled = target
