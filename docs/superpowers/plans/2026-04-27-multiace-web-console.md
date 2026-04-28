# multiACE Web Console v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a self-hosted web console for full management of Anycubic ACE Pro filament changers on a Snapmaker U1 with multiACE installed.

**Architecture:** Python FastAPI backend running as a systemd service on the printer (port 7126), with vanilla HTML/JS/CSS frontend served by nginx reverse-proxy at `http://<printer-ip>/multiace/`. Backend tails multiace state logs, polls Moonraker for current state, and proxies command POSTs back through Moonraker. WebSocket pushes live updates to browsers; auto-reconnect handles drops.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn[standard], httpx, pydantic v2, pytest (dev), vanilla HTML/JS/CSS (no framework, no build step).

**Repo location:** `multiace_web/` directory at the root of the multiACE repo, sibling to `multiace/`.

---

## File Structure

```
multiace_web/
├── pyproject.toml                # Project metadata + deps
├── README.md                     # User docs
├── src/
│   └── multiace_web/
│       ├── __init__.py
│       ├── server.py             # FastAPI app, routes, lifespan, WS broadcaster
│       ├── state.py              # CurrentState, EventBuffer
│       ├── tailer.py             # Async log tailers
│       ├── poller.py             # ACE_HEAD_STATUS poller
│       ├── moonraker.py          # Async Moonraker client
│       ├── auth.py               # Bearer token middleware
│       └── config_io.py          # ace.cfg reader/writer
├── static/
│   ├── index.html                # Single-page shell
│   ├── app.js                    # Vanilla JS, ~500 LoC
│   └── style.css                 # CSS Grid + custom properties
├── tests/
│   ├── conftest.py
│   ├── test_state.py
│   ├── test_tailer.py
│   ├── test_moonraker.py
│   ├── test_config_io.py
│   ├── test_auth.py
│   └── test_server.py
└── install/
    ├── multiace-web.service      # systemd unit
    ├── nginx-multiace.conf       # nginx location block
    ├── install_web.sh            # printer-side install
    └── uninstall_web.sh          # rollback
```

---

## Task 1: Scaffold project structure

**Files:**
- Create: `multiace_web/pyproject.toml`
- Create: `multiace_web/README.md`
- Create: `multiace_web/src/multiace_web/__init__.py`
- Create: `multiace_web/tests/__init__.py`
- Create: `multiace_web/tests/conftest.py`

- [ ] **Step 1: Create `multiace_web/pyproject.toml`**

```toml
[project]
name = "multiace-web"
version = "0.1.0"
description = "Web console for multiACE on Snapmaker U1"
requires-python = ">=3.11"
license = {text = "GPL-3.0"}
dependencies = [
    "fastapi>=0.111",
    "uvicorn[standard]>=0.30",
    "httpx>=0.27",
    "pydantic>=2.7",
    "websockets>=12.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "pytest-mock>=3.12",
    "respx>=0.21",
]

[project.scripts]
multiace-web = "multiace_web.server:main"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: Create `multiace_web/README.md`**

```markdown
# multiACE Web Console

Web console for managing Anycubic ACE Pro filament changers on a Snapmaker U1 running multiACE.

## Local development

```bash
cd multiace_web
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest
uvicorn multiace_web.server:app --reload --port 7126
```

Then open http://localhost:7126/.

## Install on printer

The parent multiACE installer (`install_multiace.sh`) runs `install/install_web.sh` after copying the multiACE files. See that script for printer-side details.

## License

GPL-3.0
```

- [ ] **Step 3: Create empty `__init__.py` files**

`multiace_web/src/multiace_web/__init__.py`:
```python
__version__ = "0.1.0"
```

`multiace_web/tests/__init__.py`: (empty file)

- [ ] **Step 4: Create `multiace_web/tests/conftest.py`**

```python
import pytest


@pytest.fixture
def sample_state_event():
    """One realistic line from multiace_state.log, parsed."""
    return {
        "action": "LOAD_HEAD",
        "params": {"head": 1, "ace": 0, "slot": 1},
        "active_device": 0,
        "device_count": 1,
        "connected": True,
        "serial": "/dev/serial/by-path/example",
        "mode": "multi",
        "swap_in_progress": False,
        "auto_feed": False,
        "feed_assist": 1,
        "gate_status": [1, 1, 1, 1],
        "head_source": {
            "0": {"ace": 0, "slot": 0, "type": "", "color": "000000"},
            "1": {"ace": 0, "slot": 1, "type": "", "color": "000000"},
            "2": None,
            "3": None,
        },
        "sensors": {"0": True, "1": True, "2": False, "3": False},
        "print_task_config": {
            "0": {"type": "NONE", "color": 4294967295, "vendor": "NONE"},
            "1": {"type": "", "color": 4278190080, "vendor": "Generic"},
            "2": {"type": "", "color": 4278190080, "vendor": "Generic"},
            "3": {"type": "", "color": 4278190080, "vendor": "Generic"},
        },
    }


@pytest.fixture
def sample_state_log_line():
    """Raw line as it appears in multiace_state.log (timestamp + STATE + JSON)."""
    return (
        "2026-04-27 23:40:52 STATE "
        '{"action": "LOAD_HEAD", "params": {"head": 1, "ace": 0, "slot": 1}, '
        '"active_device": 0, "device_count": 1, "connected": true, '
        '"serial": "/dev/serial/by-path/example", "mode": "multi", '
        '"swap_in_progress": false, "auto_feed": false, "feed_assist": 1, '
        '"gate_status": [1, 1, 1, 1], '
        '"head_source": {"0": {"ace": 0, "slot": 0, "type": "", "color": "000000"}, '
        '"1": {"ace": 0, "slot": 1, "type": "", "color": "000000"}, "2": null, "3": null}, '
        '"sensors": {"0": true, "1": true, "2": false, "3": false}, '
        '"print_task_config": {"0": {"type": "NONE", "color": 4294967295, "vendor": "NONE"}, '
        '"1": {"type": "", "color": 4278190080, "vendor": "Generic"}, '
        '"2": {"type": "", "color": 4278190080, "vendor": "Generic"}, '
        '"3": {"type": "", "color": 4278190080, "vendor": "Generic"}}}\n'
    )
```

- [ ] **Step 5: Set up venv and verify project loads**

```bash
cd multiace_web
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest --collect-only
```

Expected: `pytest` collects 0 tests (no test files yet) without import errors.

- [ ] **Step 6: Commit**

```bash
git add multiace_web/
git commit -m "feat(web): scaffold multiace_web project structure"
```

---

## Task 2: State models — CurrentState and EventBuffer

**Files:**
- Create: `multiace_web/src/multiace_web/state.py`
- Create: `multiace_web/tests/test_state.py`

- [ ] **Step 1: Write failing test for parsing a state log line**

Create `multiace_web/tests/test_state.py`:

```python
from multiace_web.state import CurrentState, EventBuffer, parse_state_log_line


def test_parse_state_log_line_returns_timestamp_and_data(sample_state_log_line):
    ts, data = parse_state_log_line(sample_state_log_line)
    assert ts == "2026-04-27 23:40:52"
    assert data["action"] == "LOAD_HEAD"
    assert data["active_device"] == 0
    assert data["sensors"]["1"] is True


def test_parse_state_log_line_returns_none_on_malformed():
    result = parse_state_log_line("not a state log line\n")
    assert result is None


def test_current_state_initial_values():
    state = CurrentState()
    assert state.active_device is None
    assert state.connected is False
    assert state.swap_in_progress is False
    assert state.gate_status == [0, 0, 0, 0]
    assert state.head_source == {0: None, 1: None, 2: None, 3: None}
    assert state.sensors == {0: False, 1: False, 2: False, 3: False}
    assert state.last_error is None


def test_current_state_apply_event_updates_fields(sample_state_event):
    state = CurrentState()
    state.apply_event(sample_state_event)
    assert state.active_device == 0
    assert state.connected is True
    assert state.gate_status == [1, 1, 1, 1]
    assert state.head_source[0] == {"ace": 0, "slot": 0, "type": "", "color": "000000"}
    assert state.sensors[1] is True
    assert state.mode == "multi"


def test_current_state_load_head_failed_sets_last_error():
    state = CurrentState()
    state.apply_event({
        "action": "LOAD_HEAD_FAILED",
        "params": {"head": 1, "ace": 0, "slot": 1, "reason": "feed_auto_error",
                   "error": "extruder[1]: timeout!"},
        "active_device": 0,
        "connected": True,
        "swap_in_progress": False,
        "gate_status": [1, 1, 1, 1],
        "head_source": {"0": None, "1": None, "2": None, "3": None},
        "sensors": {"0": False, "1": False, "2": False, "3": False},
    })
    assert state.last_error is not None
    assert state.last_error["head"] == 1
    assert "timeout" in state.last_error["error"]


def test_event_buffer_append_and_read():
    buf = EventBuffer(maxlen=3)
    buf.append({"action": "A"})
    buf.append({"action": "B"})
    buf.append({"action": "C"})
    buf.append({"action": "D"})  # evicts A
    events = buf.recent(limit=10)
    actions = [e["action"] for e in events]
    assert actions == ["B", "C", "D"]


def test_event_buffer_since_id_returns_only_newer():
    buf = EventBuffer(maxlen=10)
    e1 = buf.append({"action": "A"})
    e2 = buf.append({"action": "B"})
    e3 = buf.append({"action": "C"})
    new = buf.since(e1)
    assert [e["action"] for e in new] == ["B", "C"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_state.py -v
```

Expected: FAIL — `multiace_web.state` doesn't exist yet.

- [ ] **Step 3: Implement `state.py`**

Create `multiace_web/src/multiace_web/state.py`:

```python
"""State models for multiACE web console."""
from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from itertools import count
from typing import Any, Optional


def parse_state_log_line(line: str) -> Optional[tuple[str, dict[str, Any]]]:
    """Parse one line of multiace_state.log.

    Format: "<YYYY-MM-DD HH:MM:SS> STATE <json>"
    Returns (timestamp, data) or None on malformed input.
    """
    line = line.rstrip("\n")
    marker = " STATE "
    idx = line.find(marker)
    if idx < 0:
        return None
    ts = line[:idx]
    body = line[idx + len(marker):]
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    return ts, data


@dataclass
class CurrentState:
    """Aggregated live state of the multiACE system. Single source of truth
    for what to push to clients."""

    active_device: Optional[int] = None
    device_count: int = 0
    connected: bool = False
    serial: Optional[str] = None
    mode: str = "multi"
    swap_in_progress: bool = False
    auto_feed: bool = False
    feed_assist: int = -1
    gate_status: list[int] = field(default_factory=lambda: [0, 0, 0, 0])
    head_source: dict[int, Optional[dict]] = field(
        default_factory=lambda: {0: None, 1: None, 2: None, 3: None}
    )
    sensors: dict[int, bool] = field(
        default_factory=lambda: {0: False, 1: False, 2: False, 3: False}
    )
    print_task_config: dict[int, dict] = field(
        default_factory=lambda: {
            i: {"type": "NONE", "color": 0xFFFFFFFF, "vendor": "NONE"}
            for i in range(4)
        }
    )
    last_error: Optional[dict] = None
    last_action_at: Optional[str] = None

    def apply_event(self, event: dict[str, Any]) -> None:
        """Update state from a multiace_state.log event payload."""
        for field_name in (
            "active_device", "device_count", "connected", "serial",
            "mode", "swap_in_progress", "auto_feed", "feed_assist",
            "gate_status",
        ):
            if field_name in event:
                setattr(self, field_name, event[field_name])

        if "head_source" in event:
            self.head_source = {int(k): v for k, v in event["head_source"].items()}
        if "sensors" in event:
            self.sensors = {int(k): bool(v) for k, v in event["sensors"].items()}
        if "print_task_config" in event:
            self.print_task_config = {
                int(k): v for k, v in event["print_task_config"].items()
            }

        action = event.get("action", "")
        if action.endswith("_FAILED"):
            self.last_error = {
                "action": action,
                "head": event.get("params", {}).get("head"),
                "slot": event.get("params", {}).get("slot"),
                "ace": event.get("params", {}).get("ace"),
                "reason": event.get("params", {}).get("reason"),
                "error": event.get("params", {}).get("error", ""),
            }
        elif action == "LOAD_HEAD":
            head = event.get("params", {}).get("head")
            if self.last_error and self.last_error.get("head") == head:
                self.last_error = None  # cleared by successful retry

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable snapshot."""
        return {
            "active_device": self.active_device,
            "device_count": self.device_count,
            "connected": self.connected,
            "serial": self.serial,
            "mode": self.mode,
            "swap_in_progress": self.swap_in_progress,
            "auto_feed": self.auto_feed,
            "feed_assist": self.feed_assist,
            "gate_status": self.gate_status,
            "head_source": self.head_source,
            "sensors": self.sensors,
            "print_task_config": self.print_task_config,
            "last_error": self.last_error,
            "last_action_at": self.last_action_at,
        }


class EventBuffer:
    """Ring buffer of recent state-log events with monotonic IDs."""

    def __init__(self, maxlen: int = 200) -> None:
        self._buf: deque[dict] = deque(maxlen=maxlen)
        self._counter = count(1)

    def append(self, event: dict[str, Any]) -> int:
        eid = next(self._counter)
        entry = {"id": eid, **event}
        self._buf.append(entry)
        return eid

    def recent(self, limit: int = 50) -> list[dict]:
        return list(self._buf)[-limit:]

    def since(self, last_id: int) -> list[dict]:
        return [e for e in self._buf if e["id"] > last_id]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_state.py -v
```

Expected: PASS — all 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add multiace_web/src/multiace_web/state.py multiace_web/tests/test_state.py
git commit -m "feat(web): add CurrentState and EventBuffer with parser"
```

---

## Task 3: Moonraker client wrapper

**Files:**
- Create: `multiace_web/src/multiace_web/moonraker.py`
- Create: `multiace_web/tests/test_moonraker.py`

- [ ] **Step 1: Write failing tests using respx for HTTP mocking**

Create `multiace_web/tests/test_moonraker.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_moonraker.py -v
```

Expected: FAIL — `multiace_web.moonraker` doesn't exist.

- [ ] **Step 3: Implement `moonraker.py`**

Create `multiace_web/src/multiace_web/moonraker.py`:

```python
"""Async client wrapping Moonraker's HTTP API."""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx


class MoonrakerError(Exception):
    """Raised when a Moonraker call fails (HTTP error or connection error)."""


class MoonrakerClient:
    """Single-purpose async client for Moonraker.

    Centralizes timeouts and error translation. Caller is responsible for
    closing via `await client.close()` (typically tied to FastAPI lifespan).
    """

    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    async def printer_info(self) -> dict[str, Any]:
        """GET /printer/info → returns the result dict."""
        try:
            resp = await self._client.get("/printer/info")
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise MoonrakerError(f"printer_info failed: {e}") from e
        return resp.json()["result"]

    async def run_gcode(self, script: str) -> str:
        """POST /printer/gcode/script?script=<encoded> → returns result string."""
        url = f"/printer/gcode/script?script={quote(script)}"
        try:
            resp = await self._client.post(url)
        except httpx.HTTPError as e:
            raise MoonrakerError(f"run_gcode {script!r} connection error: {e}") from e
        if resp.status_code >= 400:
            try:
                err = resp.json().get("error", {}).get("message", resp.text)
            except Exception:
                err = resp.text
            raise MoonrakerError(f"run_gcode {script!r} failed: {err}")
        return resp.json().get("result", "ok")

    async def get_logs(self, kind: str = "klippy", lines: int = 200) -> list[str]:
        """Fetch a slice of klippy.log via Moonraker's file API."""
        path = f"/server/files/logs/{kind}.log"
        try:
            resp = await self._client.get(path)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise MoonrakerError(f"get_logs {kind} failed: {e}") from e
        text = resp.text
        return text.splitlines()[-lines:]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_moonraker.py -v
```

Expected: PASS — all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add multiace_web/src/multiace_web/moonraker.py multiace_web/tests/test_moonraker.py
git commit -m "feat(web): add Moonraker async client wrapper"
```

---

## Task 4: Log tailer with rotation detection

**Files:**
- Create: `multiace_web/src/multiace_web/tailer.py`
- Create: `multiace_web/tests/test_tailer.py`

- [ ] **Step 1: Write failing tests**

Create `multiace_web/tests/test_tailer.py`:

```python
import asyncio
import os
from pathlib import Path

import pytest

from multiace_web.tailer import LogTailer


@pytest.mark.asyncio
async def test_tailer_reads_new_lines(tmp_path: Path):
    log_path = tmp_path / "test.log"
    log_path.write_text("existing line 1\nexisting line 2\n")

    received: list[str] = []
    tailer = LogTailer(log_path, on_line=lambda line: received.append(line))
    task = asyncio.create_task(tailer.run())
    await asyncio.sleep(0.2)  # let it start tailing from end-of-file

    with open(log_path, "a") as f:
        f.write("new line A\n")
        f.flush()
    await asyncio.sleep(0.5)

    with open(log_path, "a") as f:
        f.write("new line B\n")
        f.flush()
    await asyncio.sleep(0.5)

    tailer.stop()
    await asyncio.wait_for(task, timeout=2.0)

    assert "new line A" in received
    assert "new line B" in received
    assert "existing line 1" not in received  # tailer starts from end


@pytest.mark.asyncio
async def test_tailer_handles_rotation(tmp_path: Path):
    log_path = tmp_path / "test.log"
    log_path.write_text("original\n")

    received: list[str] = []
    tailer = LogTailer(log_path, on_line=lambda line: received.append(line),
                       poll_interval=0.1)
    task = asyncio.create_task(tailer.run())
    await asyncio.sleep(0.3)

    # Simulate rotation: rename old file, create new one
    rotated = tmp_path / "test.log.1"
    log_path.rename(rotated)
    log_path.write_text("rotated start\n")
    await asyncio.sleep(0.5)

    with open(log_path, "a") as f:
        f.write("after rotation\n")
        f.flush()
    await asyncio.sleep(0.5)

    tailer.stop()
    await asyncio.wait_for(task, timeout=2.0)

    assert "after rotation" in received


@pytest.mark.asyncio
async def test_tailer_recovers_from_missing_file(tmp_path: Path):
    log_path = tmp_path / "test.log"  # doesn't exist yet

    received: list[str] = []
    tailer = LogTailer(log_path, on_line=lambda line: received.append(line),
                       poll_interval=0.1)
    task = asyncio.create_task(tailer.run())
    await asyncio.sleep(0.3)

    log_path.write_text("first line after creation\n")
    await asyncio.sleep(0.5)

    tailer.stop()
    await asyncio.wait_for(task, timeout=2.0)

    assert "first line after creation" in received
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_tailer.py -v
```

Expected: FAIL — `multiace_web.tailer` doesn't exist.

- [ ] **Step 3: Implement `tailer.py`**

Create `multiace_web/src/multiace_web/tailer.py`:

```python
"""Async log tailer with rotation and missing-file handling.

Uses inode-stat polling instead of inotify so it works on the U1's busybox
userland without extra dependencies.
"""
from __future__ import annotations

import asyncio
import logging
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
        """Return True if file was rotated/truncated/recreated."""
        try:
            current_inode = os.stat(self.path).st_ino
        except FileNotFoundError:
            return self._inode is not None
        except OSError:
            return False
        return self._inode is not None and current_inode != self._inode

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
                    self._fh.seek(self._fh.tell() - len(line))
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


# Imported here to avoid top-level os usage before it's needed
import os  # noqa: E402
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_tailer.py -v
```

Expected: PASS — all 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add multiace_web/src/multiace_web/tailer.py multiace_web/tests/test_tailer.py
git commit -m "feat(web): add async log tailer with rotation detection"
```

---

## Task 5: Config IO (read/write ace.cfg)

**Files:**
- Create: `multiace_web/src/multiace_web/config_io.py`
- Create: `multiace_web/tests/test_config_io.py`

- [ ] **Step 1: Write failing tests**

Create `multiace_web/tests/test_config_io.py`:

```python
from pathlib import Path

from multiace_web.config_io import read_ace_config, write_ace_config


SAMPLE_CFG = """
[save_variables]
filename: /home/lava/printer_data/config/extended/multiace/ace_vars.cfg

[ace]

# ace_device_count: 3

# Logging
state_debug: true
usb_debug: true

# Serial
baud: 115200

# Feed/retract
feed_speed: 80
retract_speed: 30
retract_length: 700        # 80cm tubes
load_length: 1500          # bumped from 880

# Per-toolhead overrides
# load_length_0: 2100
# load_length_1: 2050

dryer_temp: 55
dryer_duration: 240
"""


def test_read_ace_config_returns_known_keys(tmp_path: Path):
    cfg = tmp_path / "ace.cfg"
    cfg.write_text(SAMPLE_CFG)
    values = read_ace_config(cfg)
    assert values["feed_speed"] == "80"
    assert values["retract_speed"] == "30"
    assert values["retract_length"] == "700"
    assert values["load_length"] == "1500"
    assert values["dryer_temp"] == "55"
    assert values["state_debug"] == "true"
    # Commented-out lines should not appear
    assert "ace_device_count" not in values
    assert "load_length_0" not in values


def test_write_ace_config_updates_only_specified_keys(tmp_path: Path):
    cfg = tmp_path / "ace.cfg"
    cfg.write_text(SAMPLE_CFG)
    write_ace_config(cfg, {"load_length": "2000", "feed_speed": "100"})
    text = cfg.read_text()
    assert "load_length: 2000" in text
    assert "feed_speed: 100" in text
    # Other keys unchanged
    assert "retract_speed: 30" in text
    assert "dryer_temp: 55" in text
    # Comments preserved
    assert "# 80cm tubes" in text or "# bumped" in text


def test_write_ace_config_creates_atomic_backup(tmp_path: Path):
    cfg = tmp_path / "ace.cfg"
    cfg.write_text(SAMPLE_CFG)
    write_ace_config(cfg, {"feed_speed": "100"})
    backup = cfg.with_suffix(".cfg.bak")
    assert backup.exists()
    assert "feed_speed: 80" in backup.read_text()


def test_write_ace_config_appends_unknown_key(tmp_path: Path):
    cfg = tmp_path / "ace.cfg"
    cfg.write_text(SAMPLE_CFG)
    write_ace_config(cfg, {"new_per_tool_setting": "42"})
    text = cfg.read_text()
    assert "new_per_tool_setting: 42" in text
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_config_io.py -v
```

Expected: FAIL — `multiace_web.config_io` doesn't exist.

- [ ] **Step 3: Implement `config_io.py`**

Create `multiace_web/src/multiace_web/config_io.py`:

```python
"""Read/write ace.cfg preserving comments and formatting.

ace.cfg is a Klipper-style INI file with `key: value` lines (note the colon, not
equals). We preserve all formatting except for the specific keys we update.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path

# Matches a non-commented `key: value` line under [ace], capturing key and value.
# Allows leading whitespace, optional inline comment after the value.
_KV_RE = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*([^#\n]*?)\s*(?:#.*)?$")


def read_ace_config(path: Path) -> dict[str, str]:
    """Return all uncommented key/value pairs from ace.cfg as a flat dict."""
    text = path.read_text()
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        # Skip comment-only lines
        if raw_line.lstrip().startswith("#"):
            continue
        m = _KV_RE.match(raw_line.strip())
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if val == "":
            continue
        values[key] = val
    return values


def write_ace_config(path: Path, updates: dict[str, str]) -> None:
    """Update specified keys in ace.cfg, preserving formatting and comments.

    Writes atomically: writes to tmp file, fsyncs, renames over original.
    Saves a .bak copy of the previous content first.
    """
    text = path.read_text()
    backup_path = path.with_suffix(path.suffix + ".bak")
    backup_path.write_text(text)

    lines = text.splitlines(keepends=True)
    keys_seen: set[str] = set()
    new_lines: list[str] = []
    for raw_line in lines:
        if raw_line.lstrip().startswith("#"):
            new_lines.append(raw_line)
            continue
        stripped = raw_line.strip()
        m = _KV_RE.match(stripped)
        if not m or m.group(1) not in updates:
            new_lines.append(raw_line)
            continue
        key = m.group(1)
        new_val = updates[key]
        keys_seen.add(key)
        # Preserve leading whitespace and trailing comment if any
        leading_ws = raw_line[: len(raw_line) - len(raw_line.lstrip())]
        comment_match = re.search(r"#.*$", raw_line)
        trailing_comment = (" " + comment_match.group(0)) if comment_match else ""
        new_lines.append(f"{leading_ws}{key}: {new_val}{trailing_comment}\n")

    # Append any updates that didn't match an existing line
    for key, val in updates.items():
        if key not in keys_seen:
            new_lines.append(f"{key}: {val}\n")

    fd, tmp_path_str = tempfile.mkstemp(
        dir=path.parent, prefix=path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path_str, path)
    except Exception:
        try:
            os.unlink(tmp_path_str)
        except OSError:
            pass
        raise
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_config_io.py -v
```

Expected: PASS — all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add multiace_web/src/multiace_web/config_io.py multiace_web/tests/test_config_io.py
git commit -m "feat(web): add ace.cfg read/write with comment preservation"
```

---

## Task 6: Auth middleware (optional bearer token)

**Files:**
- Create: `multiace_web/src/multiace_web/auth.py`
- Create: `multiace_web/tests/test_auth.py`

- [ ] **Step 1: Write failing tests**

Create `multiace_web/tests/test_auth.py`:

```python
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from multiace_web.auth import TokenAuth


def make_app(token: str | None) -> FastAPI:
    app = FastAPI()
    app.add_middleware(TokenAuth, token=token)

    @app.get("/api/foo")
    async def foo():
        return {"ok": True}

    @app.get("/health")
    async def health():
        return {"ok": True}

    return app


def test_no_token_configured_allows_all_traffic():
    client = TestClient(make_app(token=None))
    assert client.get("/api/foo").status_code == 200


def test_token_configured_requires_authorization():
    client = TestClient(make_app(token="secret"))
    assert client.get("/api/foo").status_code == 401


def test_token_configured_accepts_correct_bearer():
    client = TestClient(make_app(token="secret"))
    resp = client.get("/api/foo", headers={"Authorization": "Bearer secret"})
    assert resp.status_code == 200


def test_token_configured_rejects_wrong_bearer():
    client = TestClient(make_app(token="secret"))
    resp = client.get("/api/foo", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


def test_token_does_not_block_non_api_routes():
    """The /health and /static/* paths shouldn't require auth — only /api/* and /ws."""
    client = TestClient(make_app(token="secret"))
    assert client.get("/health").status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_auth.py -v
```

Expected: FAIL — `multiace_web.auth` doesn't exist.

- [ ] **Step 3: Implement `auth.py`**

Create `multiace_web/src/multiace_web/auth.py`:

```python
"""Bearer-token middleware. Optional — only enforces if a token is configured."""
from __future__ import annotations

from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


PROTECTED_PREFIXES = ("/api/", "/ws")


class TokenAuth(BaseHTTPMiddleware):
    def __init__(self, app, token: Optional[str] = None) -> None:
        super().__init__(app)
        self._token = token

    async def dispatch(self, request: Request, call_next) -> Response:
        if not self._token:
            return await call_next(request)
        path = request.url.path
        if not any(path.startswith(p) for p in PROTECTED_PREFIXES):
            return await call_next(request)
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        provided = auth_header[len("Bearer "):]
        if provided != self._token:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_auth.py -v
```

Expected: PASS — all 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add multiace_web/src/multiace_web/auth.py multiace_web/tests/test_auth.py
git commit -m "feat(web): add optional bearer token auth middleware"
```

---

## Task 7: Poller — periodic ACE_HEAD_STATUS

**Files:**
- Create: `multiace_web/src/multiace_web/poller.py`
- Create: `multiace_web/tests/test_poller.py`

- [ ] **Step 1: Write failing tests**

Create `multiace_web/tests/test_poller.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_poller.py -v
```

Expected: FAIL — `multiace_web.poller` doesn't exist.

- [ ] **Step 3: Implement `poller.py`**

Create `multiace_web/src/multiace_web/poller.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_poller.py -v
```

Expected: PASS — both tests pass.

- [ ] **Step 5: Commit**

```bash
git add multiace_web/src/multiace_web/poller.py multiace_web/tests/test_poller.py
git commit -m "feat(web): add periodic ACE_HEAD_STATUS poller"
```

---

## Task 8: FastAPI server skeleton + lifespan

**Files:**
- Create: `multiace_web/src/multiace_web/server.py`
- Create: `multiace_web/tests/test_server.py`

- [ ] **Step 1: Write failing tests**

Create `multiace_web/tests/test_server.py`:

```python
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from multiace_web.server import create_app


@pytest.fixture
def app(tmp_path, monkeypatch):
    """App configured against tmp paths so tests don't touch real printer state."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    cfg_path = tmp_path / "ace.cfg"
    cfg_path.write_text(
        "[ace]\nfeed_speed: 80\nretract_speed: 30\nload_length: 880\n"
    )
    static_dir = Path(__file__).resolve().parent.parent / "static"
    monkeypatch.setenv("MULTIACE_LOG_DIR", str(log_dir))
    monkeypatch.setenv("MULTIACE_CONFIG", str(cfg_path))
    monkeypatch.setenv("MOONRAKER_URL", "http://printer:7125")
    monkeypatch.delenv("MULTIACE_TOKEN", raising=False)
    return create_app(static_dir=static_dir, start_background_tasks=False)


def test_health_endpoint(app):
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_state_endpoint_returns_default_state(app):
    with TestClient(app) as client:
        resp = client.get("/api/state")
    assert resp.status_code == 200
    body = resp.json()
    assert "active_device" in body
    assert "gate_status" in body
    assert body["gate_status"] == [0, 0, 0, 0]


def test_events_endpoint_empty_initially(app):
    with TestClient(app) as client:
        resp = client.get("/api/events")
    assert resp.status_code == 200
    assert resp.json() == {"events": []}


def test_config_get_returns_current_values(app):
    with TestClient(app) as client:
        resp = client.get("/api/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["values"]["feed_speed"] == "80"
    assert body["values"]["load_length"] == "880"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_server.py -v
```

Expected: FAIL — `multiace_web.server` doesn't exist.

- [ ] **Step 3: Implement `server.py` skeleton**

Create `multiace_web/src/multiace_web/server.py`:

```python
"""FastAPI server entrypoint."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .auth import TokenAuth
from .config_io import read_ace_config, write_ace_config
from .moonraker import MoonrakerClient, MoonrakerError
from .poller import StatusPoller
from .state import CurrentState, EventBuffer, parse_state_log_line
from .tailer import LogTailer

log = logging.getLogger(__name__)


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Wire up background tasks + Moonraker client."""
    moonraker_url = _env("MOONRAKER_URL", "http://127.0.0.1:7125")
    log_dir = Path(_env("MULTIACE_LOG_DIR", "/home/lava/printer_data/logs"))

    state = CurrentState()
    events = EventBuffer(maxlen=200)
    moonraker = MoonrakerClient(moonraker_url)
    ws_clients: set = set()

    app.state.state = state
    app.state.events = events
    app.state.moonraker = moonraker
    app.state.ws_clients = ws_clients

    async def on_state_line(line: str) -> None:
        parsed = parse_state_log_line(line + "\n")
        if not parsed:
            return
        ts, data = parsed
        state.last_action_at = ts
        state.apply_event(data)
        eid = events.append({"ts": ts, **data})
        msg = json.dumps({"type": "event", "id": eid, "ts": ts, "payload": data})
        await _broadcast(ws_clients, msg)
        snap = json.dumps({"type": "state", "payload": state.to_dict()})
        await _broadcast(ws_clients, snap)

    state_log = log_dir / "multiace_state.log"
    usb_log = log_dir / "multiace_usb.log"

    state_tailer = LogTailer(state_log, on_line=on_state_line)
    usb_tailer = LogTailer(usb_log, on_line=lambda l: None)  # reserved for v1.x
    poller = StatusPoller(moonraker, interval=5.0)

    tasks: list[asyncio.Task] = []
    if app.state.start_background_tasks:
        tasks = [
            asyncio.create_task(state_tailer.run()),
            asyncio.create_task(usb_tailer.run()),
            asyncio.create_task(poller.run()),
        ]
        app.state.background_tasks = tasks
    else:
        app.state.background_tasks = []

    try:
        yield
    finally:
        state_tailer.stop()
        usb_tailer.stop()
        poller.stop()
        for t in tasks:
            try:
                await asyncio.wait_for(t, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                t.cancel()
        await moonraker.close()


async def _broadcast(clients: set, message: str) -> None:
    """Send `message` to every WS client; drop dead ones."""
    dead = []
    for ws in list(clients):
        try:
            await ws.send_text(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)


def create_app(
    static_dir: Optional[Path] = None,
    start_background_tasks: bool = True,
) -> FastAPI:
    app = FastAPI(title="multiACE Web Console", version=__version__, lifespan=lifespan)
    app.state.start_background_tasks = start_background_tasks
    app.state.config_path = Path(_env("MULTIACE_CONFIG",
                                       "/home/lava/printer_data/config/extended/ace.cfg"))

    token = os.environ.get("MULTIACE_TOKEN")
    app.add_middleware(TokenAuth, token=token)

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    @app.get("/api/state")
    async def get_state(request: Request) -> dict:
        return request.app.state.state.to_dict()

    @app.get("/api/events")
    async def get_events(request: Request, since: int = 0, limit: int = 50) -> dict:
        buf = request.app.state.events
        items = buf.since(since) if since > 0 else buf.recent(limit=limit)
        return {"events": items}

    @app.get("/api/config")
    async def get_config(request: Request) -> dict:
        try:
            values = read_ace_config(request.app.state.config_path)
        except OSError as e:
            raise HTTPException(500, f"read config failed: {e}")
        return {"values": values}

    @app.put("/api/config")
    async def put_config(request: Request, body: dict) -> dict:
        updates = body.get("values") or {}
        if not isinstance(updates, dict) or not updates:
            raise HTTPException(400, "body must include non-empty 'values' dict")
        try:
            write_ace_config(request.app.state.config_path, {k: str(v) for k, v in updates.items()})
        except OSError as e:
            raise HTTPException(500, f"write config failed: {e}")
        try:
            await request.app.state.moonraker.run_gcode("RESTART")
        except MoonrakerError as e:
            raise HTTPException(502, f"saved but RESTART failed: {e}")
        return {"ok": True, "restarted": True}

    @app.post("/api/command")
    async def post_command(request: Request, body: dict) -> dict:
        macro = body.get("macro")
        if not macro or not isinstance(macro, str):
            raise HTTPException(400, "body must include 'macro' string")
        try:
            result = await request.app.state.moonraker.run_gcode(macro)
        except MoonrakerError as e:
            raise HTTPException(502, str(e))
        return {"ok": True, "result": result}

    @app.get("/api/logs/{kind}")
    async def get_logs(request: Request, kind: str, lines: int = 200) -> dict:
        if kind not in ("klippy",):
            raise HTTPException(400, f"unknown log kind: {kind}")
        try:
            content = await request.app.state.moonraker.get_logs(kind=kind, lines=lines)
        except MoonrakerError as e:
            raise HTTPException(502, str(e))
        return {"lines": content}

    if static_dir and static_dir.exists():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

        @app.get("/")
        async def root():
            return FileResponse(static_dir / "index.html")

    return app


def main() -> None:
    """Entry point for `multiace-web` script."""
    import uvicorn
    static_dir = Path(__file__).resolve().parent.parent.parent / "static"
    uvicorn.run(
        "multiace_web.server:create_app",
        factory=True,
        host="0.0.0.0",
        port=int(_env("MULTIACE_WEB_PORT", "7126")),
        log_level="info",
    )


# Module-level app for `uvicorn multiace_web.server:app`
app = create_app(
    static_dir=Path(__file__).resolve().parent.parent.parent / "static",
)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_server.py -v
```

Expected: PASS — all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add multiace_web/src/multiace_web/server.py multiace_web/tests/test_server.py
git commit -m "feat(web): add FastAPI server with state/events/config/command endpoints"
```

---

## Task 9: Command and logs endpoints — wire to Moonraker

**Files:**
- Modify: `multiace_web/tests/test_server.py`

- [ ] **Step 1: Add tests for /api/command and /api/logs/klippy with mocked Moonraker**

Append to `multiace_web/tests/test_server.py`:

```python
from unittest.mock import AsyncMock


def test_command_endpoint_proxies_to_moonraker(app):
    app.state.moonraker.run_gcode = AsyncMock(return_value="ok")
    with TestClient(app) as client:
        resp = client.post("/api/command", json={"macro": "ACEC__Load_T1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["result"] == "ok"
    app.state.moonraker.run_gcode.assert_awaited_with("ACEC__Load_T1")


def test_command_endpoint_rejects_missing_macro(app):
    with TestClient(app) as client:
        resp = client.post("/api/command", json={})
    assert resp.status_code == 400


def test_command_endpoint_returns_502_on_moonraker_error(app):
    from multiace_web.moonraker import MoonrakerError
    app.state.moonraker.run_gcode = AsyncMock(side_effect=MoonrakerError("timeout"))
    with TestClient(app) as client:
        resp = client.post("/api/command", json={"macro": "ACEC__Load_T1"})
    assert resp.status_code == 502
    assert "timeout" in resp.json()["detail"]


def test_config_put_writes_and_restarts(app, monkeypatch):
    app.state.moonraker.run_gcode = AsyncMock(return_value="ok")
    with TestClient(app) as client:
        resp = client.put("/api/config", json={"values": {"feed_speed": "100"}})
    assert resp.status_code == 200
    assert resp.json()["restarted"] is True
    app.state.moonraker.run_gcode.assert_awaited_with("RESTART")
    text = app.state.config_path.read_text()
    assert "feed_speed: 100" in text


def test_logs_klippy_returns_lines(app):
    app.state.moonraker.get_logs = AsyncMock(return_value=["line 1", "line 2"])
    with TestClient(app) as client:
        resp = client.get("/api/logs/klippy")
    assert resp.status_code == 200
    assert resp.json()["lines"] == ["line 1", "line 2"]


def test_logs_unknown_kind_returns_400(app):
    with TestClient(app) as client:
        resp = client.get("/api/logs/nonsense")
    assert resp.status_code == 400
```

Note: `config_path` is set on `app.state` already in `create_app`. The fixture monkeypatches the env vars before `create_app` is called. To make the config-write test work against the tmp path, ensure `app.state.config_path` ties to the tmp path. Adjust the `app` fixture in `test_server.py`:

```python
# In the existing 'app' fixture, after create_app(...), add:
app_instance = create_app(static_dir=static_dir, start_background_tasks=False)
app_instance.state.config_path = cfg_path  # Override for tests
return app_instance
```

(Replace the existing return statement with the two lines above.)

- [ ] **Step 2: Run tests to verify they pass**

```bash
pytest tests/test_server.py -v
```

Expected: PASS — original 4 tests + 6 new = 10 tests pass.

- [ ] **Step 3: Commit**

```bash
git add multiace_web/tests/test_server.py
git commit -m "test(web): cover /api/command, /api/logs, /api/config PUT"
```

---

## Task 10: WebSocket endpoint

**Files:**
- Modify: `multiace_web/src/multiace_web/server.py`
- Modify: `multiace_web/tests/test_server.py`

- [ ] **Step 1: Add WS endpoint to `server.py`**

Inside `create_app`, add the WebSocket route. Add this import at the top of `server.py`:

```python
from fastapi import WebSocket, WebSocketDisconnect
```

Then inside `create_app`, before `if static_dir`, add:

```python
    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        # Token check. Browsers can't set Authorization headers on WS, so also
        # accept ?token=... query param.
        if app.state.token:
            auth_header = ws.headers.get("authorization", "")
            header_token = auth_header[7:] if auth_header.startswith("Bearer ") else ""
            query_token = ws.query_params.get("token", "")
            provided = header_token or query_token
            if provided != app.state.token:
                await ws.close(code=4401)
                return
        await ws.accept()
        ws_clients = ws.app.state.ws_clients
        ws_clients.add(ws)
        try:
            # Send initial state on connect
            snap = json.dumps({"type": "state", "payload": ws.app.state.state.to_dict()})
            await ws.send_text(snap)
            while True:
                msg = await ws.receive_text()
                # Heartbeat: client sends "ping", server replies "pong"
                if msg == "ping":
                    await ws.send_text("pong")
        except WebSocketDisconnect:
            pass
        finally:
            ws_clients.discard(ws)
```

Also at the top of `create_app`, after middleware add, store the token on app state:

```python
    app.state.token = token
```

- [ ] **Step 2: Add WS test using `TestClient.websocket_connect`**

Append to `multiace_web/tests/test_server.py`:

```python
def test_websocket_sends_initial_state_on_connect(app):
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
    assert msg["type"] == "state"
    assert "gate_status" in msg["payload"]


def test_websocket_responds_to_ping(app):
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # initial state
            ws.send_text("ping")
            assert ws.receive_text() == "pong"
```

- [ ] **Step 3: Run tests to verify they pass**

```bash
pytest tests/test_server.py -v
```

Expected: PASS — all tests pass including the 2 new WS tests.

- [ ] **Step 4: Commit**

```bash
git add multiace_web/src/multiace_web/server.py multiace_web/tests/test_server.py
git commit -m "feat(web): add WebSocket endpoint with initial state and ping/pong"
```

---

## Task 11: Frontend HTML shell

**Files:**
- Create: `multiace_web/static/index.html`

- [ ] **Step 1: Write `index.html`**

Create `multiace_web/static/index.html`:

```html
<!doctype html>
<html lang="en" data-theme="auto">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <meta name="theme-color" content="#0d1117" />
  <title>multiACE</title>
  <link rel="stylesheet" href="/static/style.css?v=0.1.0" />
  <script defer src="/static/app.js?v=0.1.0"></script>
</head>
<body>
  <header class="topbar">
    <div class="brand">multiACE</div>
    <div class="status">
      <span class="dot" id="conn-dot" data-state="disconnected"></span>
      <span id="conn-label">Connecting…</span>
    </div>
    <div class="active-ace">
      <span class="muted">Active:</span>
      <strong id="active-ace-label">—</strong>
    </div>
  </header>

  <nav class="tabs" role="tablist">
    <button data-view="slots" class="tab active">Slots</button>
    <button data-view="toolheads" class="tab">Toolheads</button>
    <button data-view="activity" class="tab">Activity</button>
    <button data-view="dryer" class="tab">Dryer</button>
    <button data-view="config" class="tab">Config</button>
    <button data-view="diag" class="tab">Diag</button>
  </nav>

  <main class="content">
    <section data-view="slots" class="view active">
      <h2>Slots <small id="slots-active-ace">(ACE 0)</small></h2>
      <div id="slots-grid" class="grid grid-4"></div>
    </section>

    <section data-view="toolheads" class="view">
      <h2>Toolheads</h2>
      <div id="toolheads-grid" class="grid grid-2x2"></div>
    </section>

    <section data-view="activity" class="view">
      <h2>Activity</h2>
      <ul id="activity-list" class="activity"></ul>
    </section>

    <section data-view="dryer" class="view">
      <h2>Dryer</h2>
      <div id="dryer-panel" class="dryer"></div>
    </section>

    <section data-view="config" class="view">
      <h2>Config</h2>
      <form id="config-form" class="config">
        <p class="muted">Values from <code>ace.cfg</code>. Saving triggers <code>RESTART</code>.</p>
        <div id="config-fields"></div>
        <button type="submit">Save &amp; Restart</button>
      </form>
    </section>

    <section data-view="diag" class="view">
      <h2>Diagnostics</h2>
      <div class="diag-actions">
        <button data-cmd="ACE_HEAD_STATUS">Run ACE_HEAD_STATUS</button>
        <button data-cmd="ACE_LIST">Run ACE_LIST</button>
        <button data-cmd="ACE_CLEAR_HEADS" data-confirm="Reset all head sources?">ACE_CLEAR_HEADS</button>
      </div>
      <h3>State JSON</h3>
      <pre id="diag-state"></pre>
      <h3>klippy.log tail</h3>
      <pre id="diag-klippy">Loading…</pre>
    </section>
  </main>

  <footer class="actionbar">
    <button data-cmd="ACEC__Unload_All" data-confirm="Unload all toolheads?">Unload All</button>
    <button id="autofeed-toggle">Auto-feed: …</button>
    <button id="mode-toggle">Mode: …</button>
  </footer>

  <div id="toast-container" class="toast-container"></div>
  <div id="confirm-modal" class="modal hidden">
    <div class="modal-card">
      <p id="confirm-text"></p>
      <div class="modal-actions">
        <button id="confirm-cancel">Cancel</button>
        <button id="confirm-ok" class="primary">Confirm</button>
      </div>
    </div>
  </div>
</body>
</html>
```

- [ ] **Step 2: Verify it loads when served**

For local dev, you can verify the HTML serves without 500 errors:

```bash
cd multiace_web
uvicorn multiace_web.server:app --port 7126 &
curl -s http://localhost:7126/ | head -20
kill %1
```

Expected: HTML output starting with `<!doctype html>`.

- [ ] **Step 3: Commit**

```bash
git add multiace_web/static/index.html
git commit -m "feat(web): add HTML shell with semantic structure"
```

---

## Task 12: Frontend CSS — responsive layout, dark mode, touch-friendly

**Files:**
- Create: `multiace_web/static/style.css`

- [ ] **Step 1: Write `style.css`**

Create `multiace_web/static/style.css`:

```css
:root {
  --bg: #ffffff;
  --bg-elev: #f3f4f6;
  --fg: #0d1117;
  --fg-muted: #57606a;
  --accent: #2f81f7;
  --good: #2ea043;
  --warn: #d29922;
  --bad: #cf222e;
  --border: #d0d7de;
  --radius: 10px;
  --gap: 0.75rem;
  --pad: 1rem;
  --touch: 44px;
}

@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0d1117;
    --bg-elev: #161b22;
    --fg: #e6edf3;
    --fg-muted: #7d8590;
    --border: #30363d;
  }
}

* { box-sizing: border-box; }
html, body {
  margin: 0; padding: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
               "Helvetica Neue", Arial, sans-serif;
  background: var(--bg); color: var(--fg);
  font-size: 16px;
  min-height: 100vh;
}

button {
  min-height: var(--touch); min-width: var(--touch);
  padding: 0.5rem 1rem;
  border: 1px solid var(--border);
  background: var(--bg-elev); color: var(--fg);
  border-radius: var(--radius);
  font-size: 1rem; cursor: pointer;
}
button:hover { background: var(--border); }
button[disabled] { opacity: 0.5; cursor: not-allowed; }
button.primary { background: var(--accent); color: white; border-color: var(--accent); }
button.danger { background: var(--bad); color: white; border-color: var(--bad); }

.muted { color: var(--fg-muted); }
.hidden { display: none !important; }

.topbar {
  display: flex; align-items: center; gap: var(--gap);
  padding: var(--pad);
  border-bottom: 1px solid var(--border);
  background: var(--bg-elev);
}
.brand { font-weight: 700; }
.status { display: flex; align-items: center; gap: 0.4rem; }
.dot {
  display: inline-block; width: 10px; height: 10px;
  border-radius: 50%; background: var(--fg-muted);
}
.dot[data-state="connected"] { background: var(--good); }
.dot[data-state="reconnecting"] { background: var(--warn); }
.dot[data-state="disconnected"] { background: var(--bad); }
.active-ace { margin-left: auto; }

.tabs {
  display: flex; overflow-x: auto;
  border-bottom: 1px solid var(--border);
  background: var(--bg-elev);
}
.tab {
  flex-shrink: 0; border: none; border-bottom: 2px solid transparent;
  border-radius: 0; background: transparent;
}
.tab.active { border-bottom-color: var(--accent); color: var(--accent); }

.content { padding: var(--pad); padding-bottom: 6rem; }
.view { display: none; }
.view.active { display: block; }

.grid { display: grid; gap: var(--gap); }
.grid-4 { grid-template-columns: 1fr; }
.grid-2x2 { grid-template-columns: 1fr; }

.card {
  background: var(--bg-elev);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: var(--pad);
}
.card.error { border-color: var(--bad); }
.card.error .err-msg { color: var(--bad); font-size: 0.9rem; margin: 0.4rem 0; }

.card h3 { margin: 0 0 0.4rem 0; }
.card .row { display: flex; justify-content: space-between; gap: var(--gap); margin: 0.3rem 0; }
.card .actions { display: flex; gap: var(--gap); margin-top: var(--gap); flex-wrap: wrap; }

.activity { list-style: none; padding: 0; margin: 0; }
.activity li {
  padding: 0.5rem;
  border-bottom: 1px solid var(--border);
  font-family: monospace; font-size: 0.9rem;
}
.activity li.fail { color: var(--bad); }
.activity li.ok { color: var(--good); }

.actionbar {
  position: fixed; bottom: 0; left: 0; right: 0;
  display: flex; gap: var(--gap); padding: var(--pad);
  background: var(--bg-elev); border-top: 1px solid var(--border);
  z-index: 10;
}

.toast-container {
  position: fixed; top: 1rem; right: 1rem; z-index: 100;
  display: flex; flex-direction: column; gap: 0.5rem;
}
.toast {
  background: var(--bg-elev); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 0.75rem 1rem;
  box-shadow: 0 4px 12px rgba(0,0,0,0.2);
}
.toast.error { border-color: var(--bad); }
.toast.success { border-color: var(--good); }

.modal {
  position: fixed; inset: 0;
  background: rgba(0,0,0,0.5);
  display: flex; align-items: center; justify-content: center;
  z-index: 50;
}
.modal-card {
  background: var(--bg); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 1.5rem;
  max-width: 400px; width: 90%;
}
.modal-actions { display: flex; gap: var(--gap); justify-content: flex-end; margin-top: 1rem; }

pre {
  background: var(--bg-elev); padding: var(--pad);
  border-radius: var(--radius); overflow-x: auto;
  font-size: 0.85rem;
  max-height: 400px;
}

.config { display: flex; flex-direction: column; gap: var(--gap); }
.config label { display: flex; flex-direction: column; gap: 0.3rem; }
.config input {
  min-height: var(--touch); padding: 0.5rem;
  border: 1px solid var(--border); border-radius: var(--radius);
  background: var(--bg); color: var(--fg);
  font-size: 1rem;
}

@media (min-width: 1024px) {
  .grid-4 { grid-template-columns: repeat(4, 1fr); }
  .grid-2x2 { grid-template-columns: repeat(2, 1fr); }
  body { display: grid;
    grid-template-areas: "top top" "tabs main" "tabs main" "actionbar main";
    grid-template-columns: 200px 1fr; grid-template-rows: auto 1fr;
    min-height: 100vh; }
  .topbar { grid-area: top; }
  .tabs { grid-area: tabs; flex-direction: column; border-right: 1px solid var(--border); border-bottom: none; }
  .tab { border-bottom: none; border-left: 2px solid transparent; text-align: left; }
  .tab.active { border-left-color: var(--accent); border-bottom-color: transparent; }
  .content { grid-area: main; padding-bottom: var(--pad); }
  .actionbar { grid-area: actionbar; position: static; flex-direction: column; }
}
```

- [ ] **Step 2: Open the page locally and verify layout**

```bash
cd multiace_web
uvicorn multiace_web.server:app --port 7126
```

Open `http://localhost:7126/` in a browser. Expected: HTML renders with topbar, tabs (Slots, Toolheads, Activity, Dryer, Config, Diag), empty content sections, action bar at bottom. Resize browser to ≥1024px width and verify desktop layout (sidebar tabs, persistent action bar in sidebar).

- [ ] **Step 3: Commit**

```bash
git add multiace_web/static/style.css
git commit -m "feat(web): add responsive CSS with mobile-first + desktop sidebar"
```

---

## Task 13: Frontend JS — state management + WS connection

**Files:**
- Create: `multiace_web/static/app.js`

- [ ] **Step 1: Write the WS/state portion**

Create `multiace_web/static/app.js`:

```javascript
// multiACE Web Console - frontend
// Vanilla JS, no framework, no build step.

const state = {
  active_device: null,
  device_count: 0,
  connected: false,
  swap_in_progress: false,
  auto_feed: false,
  feed_assist: -1,
  mode: "multi",
  gate_status: [0, 0, 0, 0],
  head_source: { 0: null, 1: null, 2: null, 3: null },
  sensors: { 0: false, 1: false, 2: false, 3: false },
  print_task_config: {},
  last_error: null,
};
const events = []; // last 200 activity entries
const ws = { sock: null, retry: 0, alive: false };

const TOKEN = localStorage.getItem("multiace_token") || null;
const authHeader = () => (TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {});

function setConnState(label, dotState) {
  document.getElementById("conn-label").textContent = label;
  document.getElementById("conn-dot").dataset.state = dotState;
}

function toast(msg, kind = "info") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = msg;
  document.getElementById("toast-container").appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

async function fetchState() {
  try {
    const resp = await fetch("/api/state", { headers: authHeader() });
    if (!resp.ok) throw new Error(`status ${resp.status}`);
    const body = await resp.json();
    Object.assign(state, body);
    renderAll();
  } catch (e) {
    console.error("fetchState", e);
    toast(`Failed to fetch state: ${e.message}`, "error");
  }
}

async function fetchEvents() {
  try {
    const resp = await fetch("/api/events?limit=200", { headers: authHeader() });
    if (!resp.ok) throw new Error(`status ${resp.status}`);
    const body = await resp.json();
    events.length = 0;
    events.push(...body.events);
    renderActivity();
  } catch (e) {
    console.error("fetchEvents", e);
  }
}

function connectWS() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const tokenQuery = TOKEN ? `?token=${encodeURIComponent(TOKEN)}` : "";
  const url = `${proto}//${location.host}${location.pathname.replace(/\/$/, "")}/ws${tokenQuery}`;
  setConnState("Connecting…", "reconnecting");
  ws.sock = new WebSocket(url);
  let pingTimer = null;

  ws.sock.onopen = async () => {
    ws.alive = true;
    ws.retry = 0;
    setConnState("Connected", "connected");
    await fetchState();
    await fetchEvents();
    pingTimer = setInterval(() => {
      try { ws.sock.send("ping"); } catch (_) {}
    }, 30000);
  };

  ws.sock.onmessage = (ev) => {
    if (ev.data === "pong") return;
    let msg;
    try { msg = JSON.parse(ev.data); } catch (_) { return; }
    if (msg.type === "state") {
      Object.assign(state, msg.payload);
      renderAll();
    } else if (msg.type === "event") {
      events.unshift({ id: msg.id, ts: msg.ts, ...msg.payload });
      if (events.length > 200) events.length = 200;
      renderActivity();
    }
  };

  ws.sock.onclose = () => {
    if (pingTimer) clearInterval(pingTimer);
    ws.alive = false;
    setConnState("Reconnecting…", "reconnecting");
    const delay = Math.min(30000, 1000 * 2 ** ws.retry);
    ws.retry += 1;
    setTimeout(connectWS, delay);
  };

  ws.sock.onerror = () => {
    setConnState("Disconnected", "disconnected");
  };
}

async function sendCommand(macro) {
  try {
    const resp = await fetch("/api/command", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeader() },
      body: JSON.stringify({ macro }),
    });
    const body = await resp.json();
    if (!resp.ok) {
      toast(`${macro} failed: ${body.detail || resp.statusText}`, "error");
      return false;
    }
    toast(`${macro} sent`, "success");
    return true;
  } catch (e) {
    toast(`${macro} failed: ${e.message}`, "error");
    return false;
  }
}

function confirmDialog(text) {
  return new Promise((resolve) => {
    document.getElementById("confirm-text").textContent = text;
    const modal = document.getElementById("confirm-modal");
    modal.classList.remove("hidden");
    const ok = () => { cleanup(); resolve(true); };
    const cancel = () => { cleanup(); resolve(false); };
    function cleanup() {
      modal.classList.add("hidden");
      document.getElementById("confirm-ok").removeEventListener("click", ok);
      document.getElementById("confirm-cancel").removeEventListener("click", cancel);
    }
    document.getElementById("confirm-ok").addEventListener("click", ok);
    document.getElementById("confirm-cancel").addEventListener("click", cancel);
  });
}

// Render functions are declared in subsequent tasks; placeholder for now
function renderAll() {
  renderTopbar();
  renderSlots();
  renderToolheads();
  renderActivity();
  renderActionBar();
  renderDiag();
}
function renderTopbar() {
  document.getElementById("active-ace-label").textContent =
    state.active_device !== null ? `ACE ${state.active_device}` : "—";
  document.getElementById("slots-active-ace").textContent =
    state.active_device !== null ? `(ACE ${state.active_device})` : "(none)";
}
function renderSlots() { /* impl in Task 14 */ }
function renderToolheads() { /* impl in Task 14 */ }
function renderActivity() { /* impl in Task 15 */ }
function renderActionBar() { /* impl in Task 16 */ }
function renderDiag() { /* impl in Task 17 */ }

// View switching (tabs)
function setView(name) {
  for (const tab of document.querySelectorAll(".tab")) {
    tab.classList.toggle("active", tab.dataset.view === name);
  }
  for (const view of document.querySelectorAll(".view")) {
    view.classList.toggle("active", view.dataset.view === name);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  for (const tab of document.querySelectorAll(".tab")) {
    tab.addEventListener("click", () => setView(tab.dataset.view));
  }
  // Bind any data-cmd buttons (action bar, diag panel)
  document.body.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("[data-cmd]");
    if (!btn) return;
    const macro = btn.dataset.cmd;
    const confirm = btn.dataset.confirm;
    if (confirm && !(await confirmDialog(confirm))) return;
    btn.disabled = true;
    await sendCommand(macro);
    btn.disabled = false;
  });
  connectWS();
});
```

- [ ] **Step 2: Smoke test in a browser**

```bash
cd multiace_web
uvicorn multiace_web.server:app --port 7126
```

Open `http://localhost:7126/`. Expected: page loads, top bar shows "Connecting..." then "Connected" (assuming Moonraker is reachable), tabs work, switching between them changes the active section. WebSocket should connect.

- [ ] **Step 3: Commit**

```bash
git add multiace_web/static/app.js
git commit -m "feat(web): add frontend JS with WebSocket + state management"
```

---

## Task 14: Frontend renderers — Slots and Toolheads

**Files:**
- Modify: `multiace_web/static/app.js`

- [ ] **Step 1: Replace placeholder `renderSlots` and `renderToolheads`**

Replace the two stub functions in `app.js` with:

```javascript
function slotIcon(filled) {
  return filled ? "●" : "○";
}

function rgbFromUint(packed) {
  // ACE color is uint32 0xAARRGGBB or similar; treat low 24 bits as RGB
  const r = (packed >> 16) & 0xff;
  const g = (packed >> 8) & 0xff;
  const b = packed & 0xff;
  return `rgb(${r},${g},${b})`;
}

function renderSlots() {
  const grid = document.getElementById("slots-grid");
  grid.innerHTML = "";
  const ace = state.active_device;
  for (let i = 0; i < 4; i++) {
    const filled = state.gate_status[i] === 1;
    const loadedTo = Object.entries(state.head_source).find(
      ([, src]) => src && src.ace === ace && src.slot === i
    );
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `
      <h3>Slot ${i} ${slotIcon(filled)}</h3>
      <div class="row"><span>Gate:</span><span>${filled ? "filled" : "empty"}</span></div>
      <div class="row"><span>Loaded to:</span><span>${loadedTo ? `T${loadedTo[0]}` : "—"}</span></div>
      <div class="actions">
        <button data-cmd="ACEC__Load_T${i}" ${!filled || state.swap_in_progress ? "disabled" : ""}>Load → T${i}</button>
        <button data-cmd="ACEC__Unload_T${i}" data-confirm="Unload T${i}?" ${!loadedTo || state.swap_in_progress ? "disabled" : ""}>Unload T${i}</button>
      </div>
    `;
    grid.appendChild(card);
  }
}

function renderToolheads() {
  const grid = document.getElementById("toolheads-grid");
  grid.innerHTML = "";
  for (let i = 0; i < 4; i++) {
    const src = state.head_source[i];
    const sensor = state.sensors[i];
    const err = state.last_error && state.last_error.head === i ? state.last_error : null;
    const cfg = state.print_task_config[i] || {};
    const card = document.createElement("div");
    card.className = `card ${err ? "error" : ""}`;
    const colorSwatch = cfg.color && cfg.color !== 4294967295
      ? `<span style="display:inline-block;width:14px;height:14px;border-radius:50%;background:${rgbFromUint(cfg.color)};vertical-align:middle"></span>`
      : "";
    card.innerHTML = `
      <h3>T${i} ${colorSwatch}</h3>
      <div class="row"><span>Loaded:</span><span>${src ? `ACE ${src.ace} slot ${src.slot}` : "—"}</span></div>
      <div class="row"><span>Sensor:</span><span>${sensor ? "filament present" : "empty"}</span></div>
      <div class="row"><span>Vendor:</span><span class="muted">${cfg.vendor || "—"}</span></div>
      ${err ? `<div class="err-msg">⚠ ${err.action}: ${err.error || err.reason || ""}</div>` : ""}
      <div class="actions">
        <button data-cmd="ACEC__Load_T${i}" ${state.swap_in_progress ? "disabled" : ""}>Load</button>
        <button data-cmd="ACEC__Unload_T${i}" data-confirm="Unload T${i}?" ${!src || state.swap_in_progress ? "disabled" : ""}>Unload</button>
      </div>
    `;
    grid.appendChild(card);
  }
}
```

- [ ] **Step 2: Smoke test**

```bash
cd multiace_web
MULTIACE_LOG_DIR=/path/to/synthetic/logs uvicorn multiace_web.server:app --port 7126
```

Then in another terminal, append a synthetic event:

```bash
cat <<EOF >> /path/to/synthetic/logs/multiace_state.log
2026-04-27 23:40:52 STATE {"action":"LOAD_HEAD","params":{"head":1,"ace":0,"slot":1},"active_device":0,"device_count":1,"connected":true,"mode":"multi","swap_in_progress":false,"auto_feed":false,"feed_assist":1,"gate_status":[1,1,1,1],"head_source":{"0":{"ace":0,"slot":0,"type":"","color":"000000"},"1":{"ace":0,"slot":1,"type":"","color":"000000"},"2":null,"3":null},"sensors":{"0":true,"1":true,"2":false,"3":false},"print_task_config":{"0":{"type":"NONE","color":4294967295,"vendor":"NONE"},"1":{"type":"","color":4278190080,"vendor":"Generic"},"2":{"type":"","color":4278190080,"vendor":"Generic"},"3":{"type":"","color":4278190080,"vendor":"Generic"}}}
EOF
```

Expected: browser updates within ~1s. Slots view shows 4 filled slots with "Loaded to T0/T1/—/—". Toolheads view shows T0 and T1 loaded.

- [ ] **Step 3: Commit**

```bash
git add multiace_web/static/app.js
git commit -m "feat(web): render Slots and Toolheads cards"
```

---

## Task 15: Frontend renderer — Activity feed

**Files:**
- Modify: `multiace_web/static/app.js`

- [ ] **Step 1: Replace `renderActivity` placeholder**

Replace the stub:

```javascript
function renderActivity() {
  const list = document.getElementById("activity-list");
  list.innerHTML = "";
  const recent = events.slice(0, 50);
  for (const ev of recent) {
    const li = document.createElement("li");
    const isFail = (ev.action || "").endsWith("_FAILED");
    const isOk = !isFail && ["LOAD_HEAD", "UNLOAD_HEAD", "UNLOAD_ALL", "ACE_SWITCH"]
      .some((a) => (ev.action || "").startsWith(a));
    if (isFail) li.classList.add("fail");
    else if (isOk) li.classList.add("ok");
    const params = ev.params ? JSON.stringify(ev.params) : "";
    li.textContent = `${ev.ts || ""} ${ev.action || "?"} ${params}`;
    list.appendChild(li);
  }
}
```

- [ ] **Step 2: Smoke test**

Append more events to the log file (in a second terminal):

```bash
cat <<EOF >> /path/to/synthetic/logs/multiace_state.log
2026-04-27 23:42:00 STATE {"action":"LOAD_HEAD_FAILED","params":{"head":2,"ace":0,"slot":2,"reason":"feed_auto_error","error":"timeout!"},"active_device":0,"connected":true,"swap_in_progress":false,"gate_status":[1,1,1,1],"head_source":{"0":{"ace":0,"slot":0},"1":{"ace":0,"slot":1},"2":null,"3":null},"sensors":{"0":true,"1":true,"2":false,"3":false},"print_task_config":{"0":{"type":"NONE","color":4294967295,"vendor":"NONE"},"1":{"type":"","color":4278190080,"vendor":"Generic"},"2":{"type":"","color":4278190080,"vendor":"Generic"},"3":{"type":"","color":4278190080,"vendor":"Generic"}}}
EOF
```

Expected: Activity tab shows the new entry highlighted in red (fail class). Toolhead T2 card flips red with the error message.

- [ ] **Step 3: Commit**

```bash
git add multiace_web/static/app.js
git commit -m "feat(web): render Activity feed with fail/ok styling"
```

---

## Task 16: Frontend — Action bar (Unload All, Auto-feed, Mode), ACE switcher

**Files:**
- Modify: `multiace_web/static/app.js`

- [ ] **Step 1: Replace `renderActionBar` and add ACE switch buttons**

Add a function and update `renderActionBar`:

```javascript
function renderActionBar() {
  const af = document.getElementById("autofeed-toggle");
  af.textContent = `Auto-feed: ${state.auto_feed ? "ON" : "OFF"}`;
  af.dataset.cmd = state.auto_feed ? "ACEE__Autofeed_Off" : "ACEE__Autofeed_On";
  af.removeAttribute("data-confirm");
  const mt = document.getElementById("mode-toggle");
  mt.textContent = `Mode: ${state.mode === "normal" ? "Normal" : "Multi"}`;
  mt.dataset.cmd = state.mode === "normal" ? "ACEF__Mode_Multi" : "ACEF__Mode_Normal";
  mt.dataset.confirm = "Switch mode? Reboot required to take effect.";
  // Disable the static action-bar buttons during a swap. Per-card Load/Unload
  // buttons (slots, toolheads) own their own disabled state — don't touch those.
  const disabled = state.swap_in_progress;
  for (const btn of document.querySelectorAll(".actionbar button")) {
    btn.disabled = disabled;
  }
}
```

- [ ] **Step 2: Add ACE switcher in topbar**

Modify `renderTopbar`:

```javascript
function renderTopbar() {
  const label = document.getElementById("active-ace-label");
  if (state.device_count <= 1) {
    label.textContent = state.active_device !== null ? `ACE ${state.active_device}` : "—";
  } else {
    // Multi-ACE: render a button cluster
    label.innerHTML = "";
    for (let i = 0; i < state.device_count; i++) {
      const b = document.createElement("button");
      b.textContent = `ACE ${i}`;
      b.dataset.cmd = `ACEA__Switch_${i}`;
      if (i === state.active_device) b.classList.add("primary");
      label.appendChild(b);
    }
  }
  document.getElementById("slots-active-ace").textContent =
    state.active_device !== null ? `(ACE ${state.active_device})` : "(none)";
}
```

- [ ] **Step 3: Smoke test**

In browser, with a synthetic state showing `auto_feed=true, mode=multi`:
- Action bar: "Auto-feed: ON" → click → command sent → next state event toggles to OFF
- Mode toggle shows "Mode: Multi" → click → confirm dialog appears
- During simulated `swap_in_progress=true`, buttons disabled

- [ ] **Step 4: Commit**

```bash
git add multiace_web/static/app.js
git commit -m "feat(web): render action bar (auto-feed, mode) and multi-ACE switcher"
```

---

## Task 17: Frontend — Dryer, Config, Diagnostics views

**Files:**
- Modify: `multiace_web/static/app.js`

- [ ] **Step 1: Add dryer panel**

Add this function and call it from `renderAll`:

```javascript
function renderDryer() {
  const panel = document.getElementById("dryer-panel");
  panel.innerHTML = "";
  const count = Math.max(state.device_count, 1);
  for (let i = 0; i < count; i++) {
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `
      <h3>ACE ${i}</h3>
      <div class="actions">
        <button data-cmd="ACED__Dry_Start_${i}">Start dry</button>
        <button data-cmd="ACED__Dry_Stop" data-confirm="Stop dryer?">Stop dry</button>
      </div>
      <p class="muted">Custom temp/duration via Config tab (per-ACE overrides).</p>
    `;
    panel.appendChild(card);
  }
}
```

Add `renderDryer()` to the `renderAll` function:

```javascript
function renderAll() {
  renderTopbar();
  renderSlots();
  renderToolheads();
  renderActivity();
  renderActionBar();
  renderDryer();
  renderConfig();
  renderDiag();
}
```

- [ ] **Step 2: Add config view**

```javascript
let configValues = {}; // last fetched config

async function renderConfig() {
  const fields = document.getElementById("config-fields");
  if (Object.keys(configValues).length === 0) {
    try {
      const resp = await fetch("/api/config", { headers: authHeader() });
      const body = await resp.json();
      configValues = body.values || {};
    } catch (e) {
      fields.innerHTML = `<p class="muted">Failed to load config.</p>`;
      return;
    }
  }
  fields.innerHTML = "";
  for (const [k, v] of Object.entries(configValues)) {
    const lbl = document.createElement("label");
    lbl.innerHTML = `<span>${k}</span><input type="text" name="${k}" value="${v}" />`;
    fields.appendChild(lbl);
  }
}

document.addEventListener("submit", async (ev) => {
  if (ev.target.id !== "config-form") return;
  ev.preventDefault();
  if (!(await confirmDialog("Save config and restart Klipper?"))) return;
  const updates = {};
  for (const input of ev.target.querySelectorAll("input[name]")) {
    if (input.value !== configValues[input.name]) {
      updates[input.name] = input.value;
    }
  }
  if (Object.keys(updates).length === 0) {
    toast("No changes to save");
    return;
  }
  try {
    const resp = await fetch("/api/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json", ...authHeader() },
      body: JSON.stringify({ values: updates }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      toast(`Save failed: ${err.detail || resp.statusText}`, "error");
      return;
    }
    toast("Saved. Klipper restarting…", "success");
    configValues = { ...configValues, ...updates };
  } catch (e) {
    toast(`Save failed: ${e.message}`, "error");
  }
});
```

- [ ] **Step 3: Add diagnostics view**

```javascript
function renderDiag() {
  document.getElementById("diag-state").textContent =
    JSON.stringify(state, null, 2);
}

// Lazy-load klippy log slice when diag view opens
document.addEventListener("click", async (ev) => {
  const tab = ev.target.closest('.tab[data-view="diag"]');
  if (!tab) return;
  const pre = document.getElementById("diag-klippy");
  pre.textContent = "Loading…";
  try {
    const resp = await fetch("/api/logs/klippy?lines=200", { headers: authHeader() });
    const body = await resp.json();
    pre.textContent = (body.lines || []).join("\n");
  } catch (e) {
    pre.textContent = `Failed: ${e.message}`;
  }
});
```

- [ ] **Step 4: Smoke test**

- Tap **Dryer** tab: see one (or N for multi-ACE) dryer card with Start/Stop buttons.
- Tap **Config**: see all key/value rows from ace.cfg as inputs. Edit `feed_speed`, save → confirm dialog → toast says "Saved. Klipper restarting…".
- Tap **Diag**: see current state JSON dumped, and klippy.log tail loaded.

- [ ] **Step 5: Commit**

```bash
git add multiace_web/static/app.js
git commit -m "feat(web): add dryer, config editor, and diagnostics views"
```

---

## Task 18: Install scripts — systemd, nginx, install_web.sh

**Files:**
- Create: `multiace_web/install/multiace-web.service`
- Create: `multiace_web/install/nginx-multiace.conf`
- Create: `multiace_web/install/install_web.sh`
- Create: `multiace_web/install/uninstall_web.sh`

- [ ] **Step 1: Create systemd unit**

Create `multiace_web/install/multiace-web.service`:

```ini
[Unit]
Description=multiACE Web Console
After=network.target moonraker.service
Wants=moonraker.service

[Service]
Type=simple
User=lava
WorkingDirectory=/userdata/multiace-web/app
Environment=MULTIACE_LOG_DIR=/home/lava/printer_data/logs
Environment=MULTIACE_CONFIG=/home/lava/printer_data/config/extended/ace.cfg
Environment=MOONRAKER_URL=http://127.0.0.1:7125
Environment=MULTIACE_WEB_PORT=7126
EnvironmentFile=-/userdata/multiace-web/app/.env
ExecStart=/userdata/multiace-web/venv/bin/uvicorn multiace_web.server:app --host 127.0.0.1 --port 7126
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Create nginx config**

Create `multiace_web/install/nginx-multiace.conf`:

```nginx
# Mount multiACE Web Console at /multiace/ on the existing Fluidd nginx server.
# Place this in /etc/nginx/conf.d/multiace.conf and reload nginx.
location /multiace/ {
    proxy_pass http://127.0.0.1:7126/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 600s;
    proxy_send_timeout 600s;
}
```

- [ ] **Step 3: Create install_web.sh**

Create `multiace_web/install/install_web.sh`:

```bash
#!/bin/bash
# multiACE Web Console installer
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

INSTALL_BASE="/userdata/multiace-web"
APP_DIR="$INSTALL_BASE/app"
VENV_DIR="$INSTALL_BASE/venv"
NGINX_CONF="/etc/nginx/conf.d/multiace.conf"
SYSTEMD_UNIT="/etc/systemd/system/multiace-web.service"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [multiACE-web] $1"; }

log "=== multiACE Web Console install ==="

# Sanity check: /oem/.debug must exist or overlay will be wiped on next boot
if [ ! -f /oem/.debug ]; then
  log "ERROR: /oem/.debug missing — overlay will not persist. Aborting."
  log "Create it with: touch /oem/.debug"
  exit 1
fi

log "Source: $SOURCE_DIR"
log "Target: $INSTALL_BASE"

# Stop existing service if running
systemctl stop multiace-web 2>/dev/null || true

# Copy app to persistent partition
mkdir -p "$INSTALL_BASE"
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR"
cp -r "$SOURCE_DIR/src" "$APP_DIR/"
cp -r "$SOURCE_DIR/static" "$APP_DIR/"
cp "$SOURCE_DIR/pyproject.toml" "$APP_DIR/"
log "App files copied to $APP_DIR"

# Create venv on persistent partition
if [ ! -d "$VENV_DIR" ]; then
  log "Creating venv at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install --upgrade pip --quiet
"$VENV_DIR/bin/pip" install -e "$APP_DIR" --quiet
log "Python dependencies installed"

# Install systemd unit
cp "$SCRIPT_DIR/multiace-web.service" "$SYSTEMD_UNIT"
systemctl daemon-reload
systemctl enable multiace-web
systemctl start multiace-web
log "systemd unit installed and started"

# Install nginx snippet
mkdir -p /etc/nginx/conf.d
cp "$SCRIPT_DIR/nginx-multiace.conf" "$NGINX_CONF"
nginx -t && systemctl reload nginx
log "nginx config installed; reloaded"

log "Service status:"
systemctl status multiace-web --no-pager -l | head -10 || true

log ""
log "=== Install complete ==="
log "Open http://$(hostname -I | awk '{print $1}')/multiace/"
log ""
```

- [ ] **Step 4: Create uninstall_web.sh**

Create `multiace_web/install/uninstall_web.sh`:

```bash
#!/bin/bash
# multiACE Web Console uninstaller
set -e

INSTALL_BASE="/userdata/multiace-web"
NGINX_CONF="/etc/nginx/conf.d/multiace.conf"
SYSTEMD_UNIT="/etc/systemd/system/multiace-web.service"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [multiACE-web] $1"; }

log "=== multiACE Web Console uninstall ==="

systemctl stop multiace-web 2>/dev/null || true
systemctl disable multiace-web 2>/dev/null || true
rm -f "$SYSTEMD_UNIT"
systemctl daemon-reload
log "systemd unit removed"

rm -f "$NGINX_CONF"
nginx -t && systemctl reload nginx
log "nginx config removed; reloaded"

rm -rf "$INSTALL_BASE"
log "App + venv removed from $INSTALL_BASE"

log "=== Uninstall complete ==="
```

- [ ] **Step 5: Make scripts executable + commit**

```bash
chmod +x multiace_web/install/install_web.sh multiace_web/install/uninstall_web.sh
git add multiace_web/install/
git commit -m "feat(web): add systemd unit, nginx config, install/uninstall scripts"
```

---

## Task 19: Hook into parent install_multiace.sh

**Files:**
- Modify: `multiace/install_multiace.sh`
- Modify: `multiace/uninstall_multiace.sh`

- [ ] **Step 1: Read current install_multiace.sh end**

```bash
cat multiace/install_multiace.sh | tail -20
```

- [ ] **Step 2: Add web console install hook**

Append to `multiace/install_multiace.sh` before the final `log "Please reboot..."`:

```bash
# --- Optional: Install web console ---
WEB_INSTALL_DIR="${INSTALL_DIR%/multiace}/multiace_web"
if [ -d "$WEB_INSTALL_DIR/install" ] && [ -f "$WEB_INSTALL_DIR/install/install_web.sh" ]; then
    log "Installing multiACE Web Console..."
    if bash "$WEB_INSTALL_DIR/install/install_web.sh"; then
        log "  Web console installed (http://<printer-ip>/multiace/)"
    else
        log "  WARNING: Web console install failed; continuing without it"
    fi
else
    log "Web console source not found, skipping"
fi
```

- [ ] **Step 3: Add web console uninstall hook to uninstall_multiace.sh**

Append to `multiace/uninstall_multiace.sh` before the existing tail:

```bash
# --- Remove web console if installed ---
if [ -f /userdata/multiace-web/app/install/uninstall_web.sh ]; then
    log "Removing multiACE Web Console..."
    bash /userdata/multiace-web/app/install/uninstall_web.sh || true
fi
```

- [ ] **Step 4: Commit**

```bash
git add multiace/install_multiace.sh multiace/uninstall_multiace.sh
git commit -m "feat(install): hook web console into install_multiace.sh"
```

---

## Task 20: Documentation

**Files:**
- Modify: `multiace_web/README.md`
- Modify: `README.md` (top-level)

- [ ] **Step 1: Expand `multiace_web/README.md`**

Replace `multiace_web/README.md` content with:

```markdown
# multiACE Web Console

Web console for managing Anycubic ACE Pro filament changers on a Snapmaker U1 running multiACE.

Mobile-responsive UI accessible on the LAN at `http://<printer-ip>/multiace/` (or via your existing FilamentHub Cloudflare tunnel). Provides:

- Live state for ACE slots and toolheads
- Per-toolhead Load / Unload, ACE switching, dryer controls
- Activity feed of all multiACE events with error highlighting
- Inline `ace.cfg` editor with Klipper RESTART trigger
- Diagnostics view with state log, klippy.log slice, per-ACE detail

## Architecture

FastAPI backend on the printer (port 7126), reverse-proxied through nginx at `/multiace/`. Frontend is vanilla HTML/JS/CSS — no framework, no build step, matches FilamentHub's stack.

The backend tails `multiace_state.log` for activity events and polls `ACE_HEAD_STATUS` every 5s for current state. Commands proxy through Moonraker's existing gcode endpoint. WebSocket pushes live updates to all connected browsers.

## Local development

```bash
cd multiace_web
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest                                    # all backend tests
MOONRAKER_URL=http://192.168.1.171:7125 \
  MULTIACE_LOG_DIR=/path/to/synthetic/logs \
  uvicorn multiace_web.server:app --port 7126 --reload
```

Open http://localhost:7126/.

For frontend testing without a real printer, append synthetic events to your `MULTIACE_LOG_DIR/multiace_state.log` and watch the UI update in real time.

## Install on the printer

`install_multiace.sh` runs `install/install_web.sh` automatically as part of its flow. To install just the web console (skipping the rest):

```bash
ssh root@<printer-ip>
bash /tmp/multiace/multiace_web/install/install_web.sh
```

Prerequisites:
- `/oem/.debug` must exist (overlay persistence; the main multiACE installer ensures this).
- nginx must already be running (it is on PAXX firmware).

The installer creates a Python venv at `/userdata/multiace-web/venv/` (persistent partition; survives reboots), drops a systemd unit at `/etc/systemd/system/multiace-web.service`, and an nginx snippet at `/etc/nginx/conf.d/multiace.conf`.

## Configuration

Set via systemd unit `EnvironmentFile=-/userdata/multiace-web/app/.env`:

| Variable | Default | Purpose |
|---|---|---|
| `MULTIACE_LOG_DIR` | `/home/lava/printer_data/logs` | Where multiACE writes its logs |
| `MULTIACE_CONFIG` | `.../config/extended/ace.cfg` | Path to ace.cfg for editor |
| `MOONRAKER_URL` | `http://127.0.0.1:7125` | Moonraker base URL |
| `MULTIACE_WEB_PORT` | `7126` | Port the FastAPI app binds |
| `MULTIACE_TOKEN` | (unset) | If set, requires `Authorization: Bearer <token>` |

## Uninstall

```bash
bash /userdata/multiace-web/app/install/uninstall_web.sh
```

## License

GPL-3.0 — same as the parent multiACE project.
```

- [ ] **Step 2: Add a section to top-level `README.md`**

Add a new section after the existing "Features" section in `README.md`:

```markdown
## Web Console (optional)

multiACE includes an optional web console at `http://<printer-ip>/multiace/`. Provides live ACE state, all macro commands, dryer controls, inline `ace.cfg` editor, and diagnostics. Mobile-responsive (works on phones), no Fluidd plugin needed.

The console installs automatically when you run `install_multiace.sh`. See `multiace_web/README.md` for details.
```

- [ ] **Step 3: Commit**

```bash
git add multiace_web/README.md README.md
git commit -m "docs(web): add README for web console + top-level mention"
```

---

## Task 21: On-printer smoke test

**Files:** None — just deployment verification.

- [ ] **Step 1: Push the multiace_web folder to the printer**

```bash
scp -r multiace_web/ root@192.168.1.171:/tmp/multiace_web/
```

- [ ] **Step 2: Run the install script**

```bash
ssh root@192.168.1.171 "bash /tmp/multiace_web/install/install_web.sh"
```

Expected output: install logs ending with `=== Install complete === Open http://<ip>/multiace/`.

- [ ] **Step 3: Verify the service is running**

```bash
ssh root@192.168.1.171 "systemctl status multiace-web --no-pager"
```

Expected: `active (running)`.

- [ ] **Step 4: Verify the page loads**

In a browser, open `http://192.168.1.171/multiace/`. Expected: page renders, status dot turns green, all 4 toolheads visible (currently loaded from prior session — T0/T1/T2/T3 all showing filament).

- [ ] **Step 5: Run the regression checklist from the spec**

For each item, verify behavior in the browser:

- [ ] Page load shows current state within 2s
- [ ] Each command button reaches Moonraker and returns response within 1s (test with a no-op like `ACEC__Unload_T0` → `ACEC__Load_T0`)
- [ ] WS reconnects within 5s of network drop (kill the service via `systemctl stop multiace-web`, wait, restart, observe browser banner cycle yellow → green)
- [ ] Config edit + save reloads Klipper without manual intervention (edit `feed_speed`, save, observe Klipper restart in `klippy.log`)
- [ ] Failed load surfaces error on the right toolhead card (trigger by manually retracting filament from a slot, then `Load_T*`)
- [ ] Mobile layout doesn't horizontal-scroll on a 360px viewport (test in Chrome DevTools mobile emulator)

- [ ] **Step 6: Commit any fixes from smoke testing**

If any issues surface during smoke testing, fix and commit per-issue:

```bash
git add ...
git commit -m "fix(web): <issue>"
```

---

## Self-review notes

After completing all tasks:

1. **Spec coverage:** Each spec section should map to at least one task:
   - Live state visibility → Tasks 8, 13, 14
   - Command surface → Tasks 8, 13, 14, 15
   - Activity feed → Tasks 2, 8, 15
   - Diagnostics → Tasks 8, 17
   - Config editor → Tasks 5, 8, 17
   - Mobile responsive → Task 12
   - WebSocket + reconnect → Tasks 10, 13
   - Persistent install → Task 18
   - nginx reverse-proxy → Task 18
   - Optional auth → Task 6
   - Failure modes → spread across 8, 13

2. **Type/name consistency:**
   - `CurrentState`, `EventBuffer`, `LogTailer`, `MoonrakerClient`, `StatusPoller`, `TokenAuth` — all match across tasks.
   - WebSocket message format `{type: state|event, payload}` consistent in server.py and app.js.
   - API endpoints match between backend and frontend: `/api/state`, `/api/command`, `/api/events`, `/api/logs/{kind}`, `/api/config`, `/ws`.

3. **No placeholders:** Every step has actual code, exact paths, and exact commands.
