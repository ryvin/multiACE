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

    Errors are logged but do not stop the loop — Moonraker may be temporarily
    unreachable and we want to keep trying.
    """

    def __init__(self, moonraker: MoonrakerClient, interval: float = 5.0) -> None:
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
