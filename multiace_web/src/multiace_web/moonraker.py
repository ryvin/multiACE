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
