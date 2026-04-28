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
        state.apply_event(data, ts=ts)
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
    app.state.token = token
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

    if static_dir and static_dir.exists():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

        @app.get("/")
        async def root():
            return FileResponse(static_dir / "index.html")

    return app


def main() -> None:
    """Entry point for `multiace-web` script."""
    import uvicorn
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
