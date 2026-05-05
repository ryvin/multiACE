"""Periodic ACE_HEAD_STATUS poll task.

multiACE writes to its state log only on actions; between actions, the latest
log line is stale. This poller fires ACE_HEAD_STATUS every N seconds so the UI
sees fresh state even when nothing's happening.
"""
from __future__ import annotations

import asyncio
import logging

from .moonraker import MoonrakerClient, MoonrakerError

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
                self._app_state.last_print = payload
            except MoonrakerError as e:
                log.debug("PrintStatePoller: %s", e)
            except Exception:
                log.exception("PrintStatePoller: unexpected error")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
                return
            except asyncio.TimeoutError:
                pass
