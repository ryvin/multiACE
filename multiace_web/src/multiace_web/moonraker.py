"""Async client wrapping Moonraker's HTTP API."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

import httpx

_KIND_RE = re.compile(r"^[a-z_]+$")


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

    async def query_objects(self, objects: list[str]) -> dict[str, Any]:
        """GET /printer/objects/query?o1&o2&… → returns the status dict.

        The status dict maps object name → its current values. Used by the
        Dashboard to surface print state alongside ACE state.
        """
        if not objects:
            return {}
        # Each object is encoded as a key with no value
        query = "&".join(quote(obj, safe="") for obj in objects)
        url = f"/printer/objects/query?{query}"
        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise MoonrakerError(f"query_objects {objects!r} failed: {e}") from e
        return resp.json().get("result", {}).get("status", {})

    async def get_logs(self, kind: str = "klippy", lines: int = 200) -> list[str]:
        """Fetch a slice of klippy.log via Moonraker's file API."""
        if not _KIND_RE.match(kind):
            raise MoonrakerError(f"invalid log kind: {kind!r}")
        path = f"/server/files/logs/{kind}.log"
        try:
            resp = await self._client.get(path)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise MoonrakerError(f"get_logs {kind} failed: {e}") from e
        text = resp.text
        return text.splitlines()[-lines:]

    async def list_gcode_files(self) -> list[dict]:
        """GET /server/files/list?root=gcodes → list of file metadata dicts.

        Each dict has at minimum: filename (str), modified (float), size (int).
        Returns empty list on error.
        """
        try:
            resp = await self._client.get("/server/files/list?root=gcodes")
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise MoonrakerError(f"list_gcode_files failed: {e}") from e
        return resp.json().get("result", [])

    async def start_print(self, filename: str) -> str:
        """POST /printer/print/start?filename=<encoded> → returns result string.

        Asks Moonraker to start a print of the named gcode file from the
        gcodes root. Filename should be relative to the gcodes dir (e.g.
        "demo.gcode" or "subfolder/demo.gcode").
        """
        url = f"/printer/print/start?filename={quote(filename)}"
        try:
            resp = await self._client.post(url)
        except httpx.HTTPError as e:
            raise MoonrakerError(f"start_print {filename!r} connection error: {e}") from e
        if resp.status_code >= 400:
            try:
                err = resp.json().get("error", {}).get("message", resp.text)
            except Exception:
                err = resp.text
            raise MoonrakerError(f"start_print {filename!r} failed: {err}")
        return resp.json().get("result", "ok")
