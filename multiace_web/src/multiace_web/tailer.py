"""Async log tailer with rotation and missing-file handling.

Uses inode-stat polling instead of inotify so it works on the U1's busybox
userland without extra dependencies.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Awaitable, Callable, Optional, Union

LineCallback = Callable[[str], Union[None, Awaitable[None]]]
log = logging.getLogger(__name__)


class LogTailer:
    """Tails a file by polling, detecting rotation/truncation/recreation.

    Calls `on_line(line)` for each new line. The callback may be sync or async.
    Tailer starts at end-of-file (does not replay history).
    """

    def __init__(
        self,
        path: Union[str, Path],
        on_line: LineCallback,
        poll_interval: float = 0.5,
    ) -> None:
        self.path = Path(path)
        self.on_line = on_line
        self.poll_interval = poll_interval
        self._stop = asyncio.Event()
        self._inode: Optional[int] = None
        self._fh = None

    def stop(self) -> None:
        self._stop.set()

    def _open(self, seek_end: bool = True) -> None:
        try:
            self._fh = open(self.path, "r", encoding="utf-8", errors="replace")
            self._inode = os.stat(self.path).st_ino
            if seek_end:
                self._fh.seek(0, 2)  # SEEK_END
        except FileNotFoundError:
            self._fh = None
            self._inode = None
        except OSError as e:
            log.warning("Tailer open(%s) failed: %s", self.path, e)
            self._fh = None
            self._inode = None

    def _check_rotation(self) -> bool:
        """Return True if file was rotated, truncated, or recreated."""
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return self._inode is not None
        except OSError:
            return False
        if self._inode is None:
            return False
        if st.st_ino != self._inode:
            return True  # rotation
        # Truncation: file size shrank below our read position
        if self._fh is not None:
            try:
                pos = self._fh.tell()
            except (OSError, ValueError):
                return False
            if st.st_size < pos:
                return True
        return False

    async def _emit(self, line: str) -> None:
        try:
            result = self.on_line(line)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            log.exception("on_line callback raised")

    async def run(self) -> None:
        """Tail loop. Returns when stop() is called."""
        self._open(seek_end=True)
        while not self._stop.is_set():
            if self._fh is None:
                # File missing, retry after interval
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval)
                    return
                except asyncio.TimeoutError:
                    pass
                self._open(seek_end=False)  # if it appears, read from start
                continue

            line = self._fh.readline()
            if line:
                if line.endswith("\n"):
                    await self._emit(line.rstrip("\n"))
                else:
                    # Partial line — re-seek and wait
                    self._fh.seek(self._fh.tell() - len(line.encode("utf-8")))
                    try:
                        await asyncio.wait_for(self._stop.wait(),
                                               timeout=self.poll_interval)
                        return
                    except asyncio.TimeoutError:
                        pass
                continue

            # No data; check rotation before sleeping
            if self._check_rotation():
                log.info("Tailer detected rotation on %s", self.path)
                if self._fh:
                    self._fh.close()
                self._open(seek_end=False)
                continue

            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval)
                return
            except asyncio.TimeoutError:
                pass

        if self._fh:
            self._fh.close()
