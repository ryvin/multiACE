"""FastAPI server entrypoint."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

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


# Macro names: ACE_*, ACEA__Switch_*, ACEB__Load_*, ACEC__*, ACED__*, ACEE__*, ACEF__*, ACEG__*
# Must start with uppercase letter; remaining chars may be upper/lower, digits, or underscores.
# Length cap to prevent abuse.
_MACRO_RE = r"^[A-Z][A-Za-z0-9_]{0,63}$"
_CONFIG_KEY_RE = r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$"


class CommandRequest(BaseModel):
    macro: str = Field(min_length=1, max_length=64, pattern=_MACRO_RE)


class ConfigRequest(BaseModel):
    values: dict[str, str] = Field(min_length=1, max_length=64)

    @field_validator("values")
    @classmethod
    def validate_keys_and_values(cls, v: dict[str, str]) -> dict[str, str]:
        import re
        key_re = re.compile(_CONFIG_KEY_RE)
        for key, val in v.items():
            if not key_re.match(key):
                raise ValueError(f"invalid config key: {key!r}")
            if not isinstance(val, str):
                raise ValueError(f"value for {key} must be a string")
            if len(val) > 256:
                raise ValueError(f"value for {key} too long ({len(val)} chars)")
            if any(c in val for c in "\n\r#"):
                raise ValueError(f"value for {key} contains forbidden character (\\n, \\r, or #)")
        return v


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Wire up background tasks + Moonraker client."""
    moonraker_url = _env("MOONRAKER_URL", "http://127.0.0.1:7125")
    log_dir = Path(_env("MULTIACE_LOG_DIR", "/home/lava/printer_data/logs"))

    state = CurrentState()
    events = EventBuffer(maxlen=200)
    ws_clients: set = set()

    app.state.state = state
    app.state.events = events
    app.state.ws_clients = ws_clients

    # Allow tests to inject a mock before lifespan runs.
    _lifespan_owns_moonraker = not hasattr(app.state, "moonraker")
    if _lifespan_owns_moonraker:
        moonraker = MoonrakerClient(moonraker_url)
        app.state.moonraker = moonraker
    else:
        moonraker = app.state.moonraker

    async def on_state_line(line: str) -> None:
        parsed = parse_state_log_line(line + "\n")
        if not parsed:
            return
        ts, data = parsed
        state.apply_event(data, ts=ts)  # apply_event sets last_action_at when ts given
        eid = events.append({**data, "ts": ts})  # I6: ts wins over data["ts"] if collision
        msg = json.dumps({"type": "event", "id": eid, "ts": ts, "payload": data})
        await _broadcast(ws_clients, msg)
        snap = json.dumps({"type": "state", "payload": state.to_dict()})
        await _broadcast(ws_clients, snap)

    state_log = log_dir / "multiace_state.log"
    # usb_log = log_dir / "multiace_usb.log"  # reserved for v1.x diagnostics endpoint

    state_tailer = LogTailer(state_log, on_line=on_state_line)
    poller = StatusPoller(moonraker, interval=5.0)

    tasks: list[asyncio.Task] = []
    if app.state.start_background_tasks:
        tasks = [
            asyncio.create_task(state_tailer.run()),
            asyncio.create_task(poller.run()),
        ]
    app.state.background_tasks = tasks

    try:
        yield
    finally:
        state_tailer.stop()
        poller.stop()
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=2.0)
            for t in pending:
                t.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        if _lifespan_owns_moonraker:
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

    @app.post("/api/command")
    async def post_command(request: Request, body: CommandRequest) -> dict:
        try:
            result = await request.app.state.moonraker.run_gcode(body.macro)
        except MoonrakerError as e:
            raise HTTPException(502, str(e))
        return {"ok": True, "result": result}

    @app.put("/api/config")
    async def put_config(request: Request, body: ConfigRequest) -> dict:
        # Whitelist: only keys that already exist in ace.cfg [ace] section can be updated.
        # Adding new keys requires editing the file directly (prevents injection).
        try:
            current = read_ace_config(request.app.state.config_path)
        except OSError as e:
            raise HTTPException(500, "read config failed")
        unknown = set(body.values.keys()) - set(current.keys())
        if unknown:
            raise HTTPException(400, f"unknown config keys: {sorted(unknown)}")
        try:
            write_ace_config(request.app.state.config_path, body.values)
        except OSError as e:
            raise HTTPException(500, "write config failed")
        try:
            await request.app.state.moonraker.run_gcode("RESTART")
        except MoonrakerError as e:
            raise HTTPException(502, f"saved but RESTART failed: {e}")
        return {"ok": True, "restarted": True}

    @app.get("/api/logs/{kind}")
    async def get_logs(request: Request, kind: str, lines: int = 200) -> dict:
        if kind not in ("klippy",):
            raise HTTPException(400, f"unknown log kind: {kind}")
        try:
            content = await request.app.state.moonraker.get_logs(kind=kind, lines=lines)
        except MoonrakerError as e:
            raise HTTPException(502, str(e))
        return {"lines": content}

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        # Token check. Browsers can't set Authorization headers on WS, so also
        # accept ?token=... query param.
        # NOTE: BaseHTTPMiddleware doesn't run on WS frames; we enforce auth
        # explicitly here at the handshake.
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
        app,
        host="0.0.0.0",
        port=int(_env("MULTIACE_WEB_PORT", "7126")),
        log_level="info",
    )


# Module-level app for `uvicorn multiace_web.server:app`.
# Construction is cheap (no I/O, no background tasks until lifespan runs).
# Tests call create_app() directly and never import this symbol.
# Static dir resolves to src/multiace_web/static/ which is packaged correctly
# when installed via pip (alongside this file).
app = create_app(
    static_dir=Path(__file__).resolve().parent / "static",
)
