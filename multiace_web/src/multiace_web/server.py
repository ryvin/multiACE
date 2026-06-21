"""FastAPI server entrypoint."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re as _re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import __version__
from .auth import TokenAuth
from .config_io import read_ace_config, write_ace_config
from .i18n import list_languages, load_catalog, DEFAULT_LANG
from .snapshots import SnapshotStore, capture_loadout, plan_apply
from .moonraker import MoonrakerClient, MoonrakerError
from .poller import StatusPoller, PrintStatePoller, MultiAcePoller
from .announcements import AnnouncementsClient
from .autodryer import (
    AutoDryer,
    Inputs,
    PersistedState,
    load_persisted_state,
    save_persisted_state,
)
from .state import CurrentState, EventBuffer, parse_state_log_line
from .tailer import LogTailer

log = logging.getLogger(__name__)


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


# ---- External humidity sensor ----
# We support any sensor that exposes a JSON HTTP endpoint by configuring:
#   MULTIACE_HUMIDITY_URL          – required; full URL to GET
#   MULTIACE_HUMIDITY_AUTH         – optional; sent as `Authorization` header
#   MULTIACE_HUMIDITY_HUM_PATH     – optional; dot-path into the JSON for humidity %
#   MULTIACE_HUMIDITY_TEMP_PATH    – optional; dot-path for ambient temperature
#   MULTIACE_HUMIDITY_LABEL        – optional; display label (default "Sensor")
# If only HUMIDITY_URL is set, we attempt common shapes (HA states API, generic
# {humidity, temperature}, SwitchBot Cloud /v1.0/devices/<id>/status, etc.).
_HUMIDITY_CACHE: dict[str, Any] = {"data": None, "ts": 0.0}
_HUMIDITY_PER_ACE_CACHE: dict[str, Any] = {"data": None, "ts": 0.0}
_HUMIDITY_TTL_SEC = 30.0


def _derive_sensors_url(single_url: str) -> str:
    """Map a configured /sensor URL to its sibling /sensors. Returns the input
    unchanged if it doesn't end in /sensor (caller may have explicitly set the
    sensors URL or pointed at a non-bridge endpoint)."""
    if not single_url:
        return ""
    if single_url.endswith("/sensor"):
        return single_url[:-len("/sensor")] + "/sensors"
    return single_url


def _resolve_path(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def _guess_humidity(body: Any) -> Any:
    if not isinstance(body, dict):
        return None
    for key in ("humidity", "humidity_pct", "rh", "RH"):
        if key in body and body[key] is not None:
            return body[key]
    # Home Assistant /api/states/<entity> shape
    attrs = body.get("attributes") or {}
    if attrs.get("device_class") == "humidity" and "state" in body:
        return body["state"]
    if "%" in str(attrs.get("unit_of_measurement", "")) and "state" in body:
        return body["state"]
    # SwitchBot Cloud /v1.0/devices/<id>/status shape
    if isinstance(body.get("body"), dict) and "humidity" in body["body"]:
        return body["body"]["humidity"]
    return None


def _guess_temperature(body: Any) -> Any:
    if not isinstance(body, dict):
        return None
    for key in ("temperature", "temperature_c", "temp", "temp_c"):
        if key in body and body[key] is not None:
            return body[key]
    attrs = body.get("attributes") or {}
    if attrs.get("device_class") == "temperature" and "state" in body:
        return body["state"]
    if isinstance(body.get("body"), dict) and "temperature" in body["body"]:
        return body["body"]["temperature"]
    return None


def _serialize_spool_cache(cache: dict) -> dict:
    """Serialize SpoolBinding dicts to plain JSON-friendly nested dicts.
    Keys are stringified (ace, slot are int → str) for stable WS shape."""
    out: dict[str, dict[str, dict]] = {}
    for ace, slots in (cache or {}).items():
        sub: dict[str, dict] = {}
        for slot, b in (slots or {}).items():
            sub[str(slot)] = {
                "spool_id": b.spool_id,
                "name": b.name,
                "material": b.material,
                "color": b.color,
                "weight_remaining_g": b.weight_remaining_g,
            }
        out[str(ace)] = sub
    return out


def build_slots_payload(state, spool_cache) -> dict:
    """Build the /api/slots response from current state + spool_cache. Shared by
    the /api/slots route and the snapshot capture/apply endpoints."""
    s = state
    device_count = int(getattr(s, "device_count", 1) or 1) if s else 1
    if device_count < 1:
        device_count = 1
    active = int(getattr(s, "active_device", 0) or 0) if s else 0
    gate_status = list(getattr(s, "gate_status", [0, 0, 0, 0]) or [0, 0, 0, 0]) if s else [0, 0, 0, 0]
    cache = spool_cache or {}
    aces = []
    for ace_idx in range(device_count):
        slots = []
        for slot in range(4):
            binding = (cache.get(ace_idx) or {}).get(slot)
            slots.append({
                "slot": slot,
                "gate_status": gate_status[slot] if ace_idx == active and slot < len(gate_status) else None,
                "spool": (
                    {
                        "spool_id": binding.spool_id,
                        "name": binding.name,
                        "material": binding.material,
                        "color": binding.color,
                        "weight_remaining_g": binding.weight_remaining_g,
                    }
                    if binding else None
                ),
            })
        aces.append({"index": ace_idx, "is_active": ace_idx == active, "slots": slots})
    return {"aces": aces}


def _state_payload(app: Any) -> dict:
    """Build the WS 'state' payload — CurrentState.to_dict + spool_cache.

    Centralizes the merge so all three call sites stay consistent.
    """
    payload = app.state.state.to_dict()
    payload["spool_cache"] = _serialize_spool_cache(getattr(app.state, "spool_cache", {}))
    return payload


async def _read_humidity() -> dict:
    url = os.environ.get("MULTIACE_HUMIDITY_URL", "").strip()
    if not url:
        return {"configured": False}

    now = time.time()
    cached = _HUMIDITY_CACHE.get("data")
    if cached is not None and (now - _HUMIDITY_CACHE["ts"]) < _HUMIDITY_TTL_SEC:
        return cached

    headers: dict[str, str] = {}
    auth = os.environ.get("MULTIACE_HUMIDITY_AUTH", "").strip()
    if auth:
        headers["Authorization"] = auth

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            body = resp.json()
    except Exception as e:
        out = {
            "configured": True, "ok": False,
            "error": f"{type(e).__name__}: {e}",
        }
        _HUMIDITY_CACHE.update(data=out, ts=now)
        return out

    hum_path = os.environ.get("MULTIACE_HUMIDITY_HUM_PATH", "").strip()
    temp_path = os.environ.get("MULTIACE_HUMIDITY_TEMP_PATH", "").strip()
    label = os.environ.get("MULTIACE_HUMIDITY_LABEL", "").strip() or "Sensor"

    raw_hum = _resolve_path(body, hum_path) if hum_path else _guess_humidity(body)
    raw_temp = _resolve_path(body, temp_path) if temp_path else _guess_temperature(body)

    def _to_float(v):
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    out = {
        "configured": True,
        "ok": True,
        "humidity_pct": _to_float(raw_hum),
        "temp_c": _to_float(raw_temp),
        "label": label,
        "fetched_at": now,
    }
    _HUMIDITY_CACHE.update(data=out, ts=now)
    return out


async def _read_humidity_per_ace(device_count: int) -> list[dict]:
    """Fetch the per-ACE humidity map from the Govee bridge's /sensors endpoint.

    Returns a list of length `device_count` where index N is the humidity
    reading for ACE N. Mapping is positional: the bridge serializes /sensors
    in GOVEE_BRIDGE_MACS config order, so the Nth MAC's reading goes to ACE N.

    Each entry is shaped like _read_humidity's output:
      {configured: True, ok: True|False, humidity_pct, temp_c, label, fetched_at}
    Entries past the configured-MAC count are {configured: False} so the
    dashboard can render an empty/placeholder tile rather than crash.

    If MULTIACE_HUMIDITY_URL is unset, returns a list of {configured: False}
    of length device_count (matches single-sensor "no humidity" behavior).
    """
    if device_count < 1:
        return []
    single_url = os.environ.get("MULTIACE_HUMIDITY_URL", "").strip()
    if not single_url:
        return [{"configured": False} for _ in range(device_count)]

    sensors_url = os.environ.get("MULTIACE_HUMIDITY_SENSORS_URL", "").strip() \
        or _derive_sensors_url(single_url)

    now = time.time()
    cached = _HUMIDITY_PER_ACE_CACHE.get("data")
    cached_n = len(cached) if isinstance(cached, list) else 0
    if cached is not None and cached_n == device_count and \
            (now - _HUMIDITY_PER_ACE_CACHE["ts"]) < _HUMIDITY_TTL_SEC:
        return cached

    headers: dict[str, str] = {}
    auth = os.environ.get("MULTIACE_HUMIDITY_AUTH", "").strip()
    if auth:
        headers["Authorization"] = auth

    label_prefix = os.environ.get("MULTIACE_HUMIDITY_LABEL", "").strip() or "ACE"

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(sensors_url, headers=headers)
            resp.raise_for_status()
            body = resp.json()
    except Exception as e:
        # Per-ACE list also serves as the source for autodry FSMs; mark each
        # slot as failed-but-configured so the FSM's stale guard kicks in
        # rather than treating it as opt-out.
        err = f"{type(e).__name__}: {e}"
        out = [{"configured": True, "ok": False, "error": err} for _ in range(device_count)]
        _HUMIDITY_PER_ACE_CACHE.update(data=out, ts=now)
        return out

    # Bridge /sensors returns {mac: {temperature, humidity, battery, rssi, name, age_s} | None, ...}
    # in GOVEE_BRIDGE_MACS config order. Iterate the dict (Python 3.7+ dict
    # preserves insertion order) and align to ACE indices positionally.
    ordered_entries: list[tuple[str, Any]] = list(body.items()) if isinstance(body, dict) else []

    def _to_float(v):
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    out: list[dict] = []
    for ace_idx in range(device_count):
        if ace_idx < len(ordered_entries):
            mac, payload = ordered_entries[ace_idx]
            if not isinstance(payload, dict):
                # Bridge still warming up — MAC configured, no reading yet
                out.append({
                    "configured": True, "ok": False, "warming_up": True,
                    "label": f"{label_prefix} {ace_idx}",
                    "mac": mac,
                })
            else:
                out.append({
                    "configured": True,
                    "ok": True,
                    "humidity_pct": _to_float(payload.get("humidity")),
                    "temp_c": _to_float(payload.get("temperature")),
                    "label": f"{label_prefix} {ace_idx}",
                    "mac": mac,
                    "name": payload.get("name"),
                    "battery": payload.get("battery"),
                    "rssi": payload.get("rssi"),
                    "age_s": payload.get("age_s"),
                    "fetched_at": now,
                })
        else:
            # Fewer MACs than ACEs — pad with "not configured" for this ACE
            out.append({"configured": False})

    _HUMIDITY_PER_ACE_CACHE.update(data=out, ts=now)
    return out


# Macro names: ACE_*, ACEA__Switch_*, ACEB__Load_*, ACEC__*, ACED__*, ACEE__*, ACEF__*, ACEG__*
# Must start with uppercase letter; remaining chars may be upper/lower, digits, or underscores.
# Length cap to prevent abuse.
_MACRO_RE = r"^[A-Z][A-Za-z0-9_]{0,63}$"
_CONFIG_KEY_RE = r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$"

# ACE_LOAD_HEAD preflight: parse HEAD=N [ACE=M] [SLOT=S] from a free-form script string.
_LOAD_HEAD_RE = _re.compile(
    r"^\s*ACE_LOAD_HEAD\s+HEAD=(\d+)(?:\s+ACE=(\d+))?(?:\s+SLOT=(\d+))?\s*$",
    _re.IGNORECASE,
)


def _preflight_load_head(state: Any, script: str) -> "str | None":
    """Return error string if the load is provably going to fail, else None.

    state is the current CurrentState; checks head_source[head] for busy, and
    gate_status[slot] for empty (only when ACE matches active_device, since
    gate_status reflects only the active ACE per firmware design).
    """
    if state is None:
        return None
    m = _LOAD_HEAD_RE.match(script)
    if not m:
        return None
    head = int(m.group(1))
    active_device = int(getattr(state, "active_device", 0) or 0)
    ace = int(m.group(2)) if m.group(2) is not None else active_device
    slot = int(m.group(3)) if m.group(3) is not None else head

    head_source = getattr(state, "head_source", {}) or {}
    src = head_source.get(head) or head_source.get(str(head))
    # Allow loads when the head's current source is parked (LENGTH= retract).
    # That's the whole point of swap-park leg 2 — the head is empty, the
    # bookkeeping just remembers which slot to swap-back to. Treat parked
    # sources as not-busy.
    if src and not src.get("parked"):
        return f"head T{head} is busy — unload first"
    if ace == active_device:
        gate = getattr(state, "gate_status", []) or []
        if slot < len(gate) and gate[slot] == 0:
            return f"ACE {ace} slot {slot} is empty"
    return None


class CommandRequest(BaseModel):
    macro: Optional[str] = Field(default=None, min_length=1, max_length=64, pattern=_MACRO_RE)
    script: Optional[str] = Field(default=None, min_length=1, max_length=256)

    def effective_script(self) -> str:
        """Return the gcode string to forward to Moonraker."""
        if self.script is not None:
            return self.script
        if self.macro is not None:
            return self.macro
        raise ValueError("either 'macro' or 'script' must be provided")


class DryRequest(BaseModel):
    """Parameterized dryer start request, mapped to ACE_DRY ACE=N TEMP=T DURATION=D."""
    ace: int = Field(ge=0, le=7)
    temp_c: int = Field(ge=30, le=120)  # ACE Pro hardware caps; multiACE further enforces max_dryer_temperature
    duration_min: int = Field(ge=1, le=2880)  # up to 48h


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


class DryStopRequest(BaseModel):
    """Request body for POST /api/dry/stop — select which ACE to stop drying."""
    ace: int = Field(ge=0, le=3)


class AutodryConfigUpdate(BaseModel):
    """Per-ACE autodry config update — used by POST /api/autodry?ace=N."""
    enabled: Optional[bool] = None
    target_pct: Optional[int] = Field(default=None, ge=5, le=60)
    hysteresis_pp: Optional[int] = Field(default=None, ge=1, le=15)
    default_filament_type: Optional[str] = None
    keep_ready: Optional[bool] = None


class AssignSpoolRequest(BaseModel):
    """Body for POST /api/slots/{ace}/{slot}/assign — bind a FilamentHub spool
    to the slot (same effect as a scan), no RFID needed."""
    spool_id: int = Field(ge=1)


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
    app.state.last_print = {}
    # Tracks the wall-clock time of the last successful print payload write
    # (by either the PrintStatePoller or GET /api/print). The autodry FSM
    # uses this to detect Moonraker outages — see autodry_inputs_fetcher.
    app.state.last_print_at = 0.0
    # Spool cache: {ace_idx: {slot: SpoolBinding}}. Populated by Task 8
    # SpoolmanPoller. Defaults to empty so /api/slots works immediately.
    if not hasattr(app.state, "spool_cache"):
        app.state.spool_cache = {}
    # Per-ACE data cache: {ace_idx: {dryer_status, humidity, last_seen_ts}}.
    # Written by MultiAcePoller on each tick (Task 8). Defaults to empty so
    # /api/print works immediately with null fields for inactive ACEs.
    if not hasattr(app.state, "last_ace_data"):
        app.state.last_ace_data = {}

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
        snap = json.dumps({"type": "state", "payload": _state_payload(app)})
        await _broadcast(ws_clients, snap)

    state_log = log_dir / "multiace_state.log"
    # usb_log = log_dir / "multiace_usb.log"  # reserved for v1.x diagnostics endpoint

    # Bootstrap state from the most recent STATE entry in the existing log.
    # multiACE only writes on actual events (load/unload/swap/error), so after a
    # service restart we'd otherwise show device_count=0 / empty head_source until
    # the next event lands — which can be hours away if the printer is idle.
    _bootstrap_state_from_log(state, state_log)

    state_tailer = LogTailer(state_log, on_line=on_state_line)
    poller = StatusPoller(moonraker, interval=5.0, state=state)

    # Synchronously probe ACEC__Park_T0 presence so /api/state returns the
    # right swap_park_available before the StatusPoller's first 5s tick. Without
    # this, a chevron click within ~5s of a multiace-web restart sees
    # swap_park_available=False (default) and falls through to the full-unload
    # branch instead of the park branch — see Bug D in the T6 test on 2026-05-10.
    # Failures (Klipper down, network blip) keep the default False; the periodic
    # poller will pick up the macro presence as soon as Klipper is reachable.
    try:
        async with httpx.AsyncClient() as _probe_client:
            state.swap_park_available = await poller._probe_swap_park(_probe_client)
    except Exception:
        log.exception("startup swap_park probe failed; will retry via poller")
    print_poller = PrintStatePoller(
        fetcher=lambda: _compute_print_payload(moonraker),
        app_state=app.state,
        interval=float(os.environ.get("MULTIACE_PRINT_POLL_SEC", "4")),
    )

    # ---- AutoDryer ----
    # NOTE: default lives at INSTALL_BASE (parent of $APP_DIR), NOT inside
    # $APP_DIR — install_web.sh `rm -rf $APP_DIR` would otherwise wipe persisted
    # autodry state on every redeploy.
    autodry_state_path = Path(_env(
        "MULTIACE_AUTODRY_STATE_PATH",
        "/userdata/multiace-web/.autodry_state.json",
    ))
    # Apply env defaults to the persisted state file the *first* time it's
    # created. Subsequent runs use whatever the user has set via POST.
    if not autodry_state_path.exists():
        try:
            save_persisted_state(autodry_state_path, PersistedState(
                mode=os.environ.get("MULTIACE_AUTODRY_MODE", "off"),
                target_ace=int(os.environ.get("MULTIACE_AUTODRY_TARGET_ACE", "0")),
                target_pct=int(os.environ.get("MULTIACE_AUTODRY_DEFAULT_TARGET_PCT", "15")),
                hysteresis_pp=int(os.environ.get(
                    "MULTIACE_AUTODRY_DEFAULT_HYSTERESIS_PP", "5",
                )),
            ))
        except OSError as e:
            log.info(
                "autodry state seed skipped (path %s not writable: %s); using runtime defaults",
                autodry_state_path, e,
            )

    # We need an httpx.AsyncClient for the announcements client. Reuse the
    # one Moonraker uses if available, else create a sibling.
    _lifespan_owns_ann_http = not (hasattr(moonraker, "_client") and isinstance(getattr(moonraker, "_client"), httpx.AsyncClient))
    if _lifespan_owns_ann_http:
        ann_http = httpx.AsyncClient()
    else:
        ann_http = moonraker._client
    announcements = AnnouncementsClient(ann_http, moonraker_url)

    def autodry_inputs_fetcher() -> Inputs:
        """Snapshot the data the FSM needs from the in-process state model.

        Humidity comes from the cached _read_humidity output (the same the
        dashboard uses); klipper_print_state and dryer_status come from the
        most recent /api/print fetch (cached on app.state).

        Staleness guard: if the cached print payload is older than 3× the
        configured poll interval, treat it as stale and feed the FSM a
        neutral "standby + humidity-unknown" snapshot. Without this the FSM
        could keep evaluating against a payload from many minutes ago — for
        example, deciding to start drying because the cached state still
        says ``standby`` while Klipper has actually been printing for an
        hour but Moonraker is unreachable so PrintStatePoller has been
        silently swallowing errors. The FSM's transitions already handle
        missing humidity / standby state safely (they keep it in
        IDLE/WATCHING) so this is a safe degradation.
        """
        st = state.to_dict()
        last_print: dict = getattr(app.state, "last_print", {}) or {}
        last_print_at: float = getattr(app.state, "last_print_at", 0.0)

        poll_interval = float(os.environ.get("MULTIACE_PRINT_POLL_SEC", "4"))
        is_stale = (
            (time.time() - last_print_at) > (3 * poll_interval)
            if last_print_at > 0
            else True
        )

        humidity = last_print.get("humidity") or {}
        dryer = last_print.get("dryer") or {}
        per_ace = last_print.get("humidity_per_ace") if not is_stale else None
        if not isinstance(per_ace, list):
            per_ace = None
        return Inputs(
            active_device=st.get("active_device"),
            head_source=st.get("head_source") or {},
            swap_in_progress=bool(st.get("swap_in_progress")),
            humidity_ok=False if is_stale else bool(humidity.get("ok")),
            humidity_pct=0.0 if is_stale else float(humidity.get("humidity_pct") or 0.0),
            cavity_temp_c=None if is_stale else last_print.get("cavity_temp_c"),
            klipper_print_state="standby" if is_stale else str(last_print.get("state") or "standby"),
            dryer_status="stop" if is_stale else str(dryer.get("status") or "stop"),
            user_profiles=None,
            humidity_per_ace=per_ace,
        )

    # Expose on app.state so tests can call it directly without scraping
    # the AutoDryer's private attributes.
    app.state.autodry_inputs_fetcher = autodry_inputs_fetcher

    async def autodry_emit_event(payload: dict) -> None:
        """Emit an AUTODRY_* event into the existing event broadcaster."""
        ts = time.time()
        eid = events.append({**payload, "ts": ts})
        msg = json.dumps({"type": "event", "id": eid, "ts": ts, "payload": payload})
        await _broadcast(ws_clients, msg)

    # Resolve device_count for the per-ACE manager. At boot time last_state
    # (from the log bootstrap) may already be available; default to 1 so the
    # single-ACE legacy path still works when state hasn't been populated yet.
    _boot_device_count = int(getattr(state, "device_count", 0) or 1)
    _manager = AutoDryer.load_manager(autodry_state_path, device_count=_boot_device_count)

    async def autodry_run_ace_dry(ace: int, temp: int, dur: int) -> None:
        """Fire ``ACE_DRY ACE=N TEMP=T DURATION=D`` via Moonraker when the
        autodry FSM transitions to DRYING in mode=active. Without this hook
        the FSM advanced state + announced but no physical dryer ever
        started."""
        gcode = f"ACE_DRY ACE={ace} TEMP={temp} DURATION={dur}"
        log.info("autodry: firing %s", gcode)
        try:
            await moonraker.run_gcode(gcode)
        except Exception:
            log.exception("autodry: ACE_DRY invocation failed (%s)", gcode)

    autodry = AutoDryer(
        state_path=autodry_state_path,
        inputs_fetcher=autodry_inputs_fetcher,
        emit_event=autodry_emit_event,
        announcements=announcements,
        tick_sec=float(os.environ.get("MULTIACE_AUTODRY_TICK_SEC", "60")),
        manager=_manager,
        run_ace_dry=autodry_run_ace_dry,
    )
    app.state.autodry = autodry

    # ---- SpoolmanClient boot wiring ----
    fh_url = os.environ.get("FILAMENTHUB_URL", "").strip()
    fh_printer = os.environ.get("FILAMENTHUB_PRINTER_ID", "").strip() or "u1-1"
    app.state.spool_cache_last_seen_ts = 0.0
    if fh_url:
        from .spoolman import SpoolmanClient
        app.state.spoolman = SpoolmanClient(base_url=fh_url, printer_id=fh_printer)

        async def _spool_poll_loop():
            while True:
                try:
                    bindings = await app.state.spoolman.list_all_bindings()
                    if bindings:
                        app.state.spool_cache = bindings
                        app.state.spool_cache_last_seen_ts = time.time()
                    elif (time.time() - (app.state.spool_cache_last_seen_ts or 0)) > 300:
                        # 5 minutes of failures → clear cache so the UI shows blank
                        app.state.spool_cache = {}
                except Exception:
                    log.exception("spool_cache poll loop iteration failed")
                try:
                    await asyncio.sleep(5.0)
                except asyncio.CancelledError:
                    return

        app.state.spool_poll_task = asyncio.create_task(_spool_poll_loop()) if app.state.start_background_tasks else None
        log.info("Spoolman polling enabled at %s", fh_url)
    else:
        app.state.spoolman = None
        app.state.spool_poll_task = None
        log.info("FILAMENTHUB_URL not set — spool cache disabled")

    # ---- MultiAcePoller — drives per-ACE autodry FSMs in round-robin while
    # idle; pins to the active ACE during a print. Replaces the legacy
    # autodry.run() loop which only managed a single FSM. The poller calls
    # autodry.tick_one_ace(N) per cycle so each FSM advances independently.
    multi_ace_poller = MultiAcePoller(
        moonraker=moonraker,
        autodry=autodry,
        device_count=max(_boot_device_count, 1),
        period_s=float(os.environ.get("MULTIACE_AUTODRY_POLLER_SEC", "30")),
        last_ace_data=app.state.last_ace_data,
    )
    app.state.multi_ace_poller = multi_ace_poller

    tasks: list[asyncio.Task] = []
    if app.state.start_background_tasks:
        tasks = [
            asyncio.create_task(state_tailer.run()),
            asyncio.create_task(poller.run()),
            asyncio.create_task(print_poller.run()),
            asyncio.create_task(multi_ace_poller.run()),
        ]
    app.state.background_tasks = tasks

    try:
        yield
    finally:
        state_tailer.stop()
        poller.stop()
        print_poller.stop()
        multi_ace_poller.stop()
        if getattr(app.state, "spool_poll_task", None) is not None:
            app.state.spool_poll_task.cancel()
            try:
                await app.state.spool_poll_task
            except (asyncio.CancelledError, Exception):
                pass
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=2.0)
            for t in pending:
                t.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        if _lifespan_owns_ann_http:
            await ann_http.aclose()
        if _lifespan_owns_moonraker:
            await moonraker.close()


async def _compute_print_payload(moonraker: MoonrakerClient) -> dict:
    """Build the print summary used by GET /api/print and the AutoDryer poller.

    Pulled out of the HTTP handler so the autodry FSM can run on a server-side
    cadence even when no UI client is connected. Includes Klipper print_stats,
    virtual_sdcard, toolhead, the multiACE [ace] dryer_status, U1 cavity
    temperature, and (when configured) an external humidity reading.
    """
    result = await moonraker.query_objects(
        ["print_stats", "virtual_sdcard", "toolhead", "ace",
         "temperature_sensor cavity"]
    )

    ps = result.get("print_stats") or {}
    sd = result.get("virtual_sdcard") or {}
    th = result.get("toolhead") or {}
    ace_obj = result.get("ace") or {}
    cavity = result.get("temperature_sensor cavity") or {}

    progress = float(sd.get("progress") or 0.0)
    print_duration = float(ps.get("print_duration") or 0.0)
    total_duration = float(ps.get("total_duration") or 0.0)
    # ETA: simple linear extrapolation from print_duration only (excludes pauses).
    eta_sec: float | None = None
    if progress > 0.001:
        eta_sec = max(0.0, (print_duration / progress) - print_duration)

    # Map Klipper extruder name to a head index (extruder=0, extruderN=N).
    ext = th.get("extruder")
    head_idx: int | None = None
    if isinstance(ext, str):
        if ext == "extruder":
            head_idx = 0
        elif ext.startswith("extruder"):
            tail = ext[len("extruder"):]
            if tail.isdigit():
                head_idx = int(tail)

    info = ps.get("info") or {}
    exc = ps.get("exception") or None
    # Klipper's exception is sometimes {} (empty); treat as None.
    if isinstance(exc, dict) and not exc:
        exc = None

    # Dryer status from multiACE's [ace] printer object.
    # Field units (verified live): status ("stop" | "drying" | …),
    # target_temp °C, duration MINUTES, remain_time SECONDS (yes, mixed).
    # We normalize remain to minutes for the dashboard.
    ds = ace_obj.get("dryer_status") or {}
    dryer = {
        "status": ds.get("status") or "stop",
        "target_temp": int(ds.get("target_temp") or 0),
        "duration_min": int(ds.get("duration") or 0),
        "remain_min": int(round((ds.get("remain_time") or 0) / 60.0)),
        "remain_sec": int(ds.get("remain_time") or 0),
    }

    cavity_temp_raw = cavity.get("temperature")
    cavity_temp = float(cavity_temp_raw) if cavity_temp_raw is not None else None

    # External humidity is fetched lazily and cached; failures don't poison the payload.
    humidity = await _read_humidity()

    # Per-ACE humidity (Govee multi-MAC bridge). Length-0 if no sensors
    # configured. Top-level `humidity` stays as the primary for backward-compat
    # with legacy single-sensor clients.
    device_count_for_humidity = int((ace_obj or {}).get("device_count") or 1)
    if device_count_for_humidity < 1:
        device_count_for_humidity = 1
    humidity_per_ace = await _read_humidity_per_ace(device_count_for_humidity)

    return {
        "state": ps.get("state") or "standby",
        "filename": ps.get("filename") or None,
        "progress": progress,
        "print_duration": print_duration,
        "total_duration": total_duration,
        "eta_sec": eta_sec,
        "layer": info.get("current_layer"),
        "total_layer": info.get("total_layer"),
        "current_extruder": head_idx,
        "exception": exc,
        "message": ps.get("message") or None,
        "dryer": dryer,
        "cavity_temp_c": cavity_temp,
        "humidity": humidity,
        "humidity_per_ace": humidity_per_ace,
    }


def _bootstrap_state_from_log(state: "CurrentState", log_path: Path) -> None:
    """Apply the most recent STATE entry from the log so /api/state isn't blank
    after a service restart while multiACE is idle.

    Reads from the END of the file backward, capping at ~64 KB scanned (large
    enough to cover thousands of recent events on a busy printer, small enough
    to avoid loading hours-old logs into memory). If no STATE entry is found,
    state stays at its default (caller is on their own).
    """
    try:
        if not log_path.exists():
            return
        size = log_path.stat().st_size
        if size == 0:
            return
        offset = max(0, size - 65536)
        with log_path.open("rb") as f:
            f.seek(offset)
            tail = f.read().decode("utf-8", errors="replace")
        # Walk lines from the end looking for a parseable STATE entry
        for line in reversed(tail.splitlines()):
            parsed = parse_state_log_line(line + "\n")
            if parsed:
                ts, data = parsed
                state.apply_event(data, ts=ts)
                log.info("bootstrapped state from log entry at %s", ts)
                return
    except OSError as e:
        log.warning("could not bootstrap state from %s: %s", log_path, e)


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
    # Loadout snapshots live on the persistent partition (overlayfs is wiped on
    # reboot). Overridable for tests / local dev via MULTIACE_SNAPSHOTS_DIR.
    app.state.snapshots = SnapshotStore(Path(_env(
        "MULTIACE_SNAPSHOTS_DIR", "/userdata/multiace-web/snapshots")))

    token = os.environ.get("MULTIACE_TOKEN")
    app.state.token = token
    app.add_middleware(TokenAuth, token=token)

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    def _i18n_dir() -> Optional[Path]:
        return (static_dir / "i18n") if static_dir else None

    @app.get("/api/i18n")
    async def get_i18n_languages() -> dict:
        i18n_dir = _i18n_dir()
        langs = list_languages(i18n_dir) if i18n_dir else []
        return {"languages": langs, "default": DEFAULT_LANG}

    @app.get("/api/i18n/{lang}")
    async def get_i18n_catalog(lang: str) -> dict:
        i18n_dir = _i18n_dir()
        catalog = load_catalog(i18n_dir, lang) if i18n_dir else None
        if catalog is None:
            raise HTTPException(404, f"no catalog for language: {lang}")
        return catalog

    # --- Loadout snapshots ---------------------------------------------------
    def _current_slots(request: Request) -> dict:
        return build_slots_payload(
            request.app.state.state,
            getattr(request.app.state, "spool_cache", {}) or {})

    @app.get("/api/snapshots")
    async def list_snapshots(request: Request) -> dict:
        return {"snapshots": request.app.state.snapshots.list()}

    @app.post("/api/snapshots/{name}")
    async def save_snapshot(request: Request, name: str) -> dict:
        store = request.app.state.snapshots
        head_source = getattr(request.app.state.state, "head_source", {}) or {}
        snap = capture_loadout(head_source, _current_slots(request))
        snap["name"] = name
        snap["created_at"] = datetime.now(timezone.utc).isoformat()
        try:
            store.save(name, snap)
        except ValueError as e:
            raise HTTPException(422, str(e))
        return {"saved": name, "created_at": snap["created_at"], "heads": snap["heads"]}

    @app.delete("/api/snapshots/{name}")
    async def delete_snapshot(request: Request, name: str) -> dict:
        if not request.app.state.snapshots.delete(name):
            raise HTTPException(404, f"no snapshot: {name}")
        return {"deleted": name}

    @app.post("/api/snapshots/{name}/apply")
    async def apply_snapshot(request: Request, name: str) -> dict:
        snap = request.app.state.snapshots.load(name)
        if snap is None:
            raise HTTPException(404, f"no snapshot: {name}")
        return plan_apply(snap, _current_slots(request))

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
        # Validate that at least one of macro/script was provided.
        if body.macro is None and body.script is None:
            raise HTTPException(422, "either 'macro' or 'script' must be provided")
        gcode = body.effective_script()
        # ACE_LOAD_HEAD preflight: reject provably invalid loads before round-tripping.
        if body.script is not None:
            err = _preflight_load_head(request.app.state.state, gcode)
            if err:
                return JSONResponse(status_code=409, content={"error": err})
        try:
            result = await request.app.state.moonraker.run_gcode(gcode)
        except MoonrakerError as e:
            raise HTTPException(502, str(e))
        return {"ok": True, "result": result}

    @app.get("/api/print")
    async def get_print(request: Request) -> dict:
        """Summarized print state from Moonraker for the Dashboard.

        Also includes the multiACE dryer_status, the U1 cavity temperature,
        and (when configured) an external humidity reading so the Dashboard
        can render an environment strip without extra polls. Identical to the
        payload the server-side PrintStatePoller writes into app.state.last_print.

        The response also includes an `aces` list with per-ACE dryer/humidity
        data: live values for the active ACE, last-known cached values from
        MultiAcePoller ticks for inactive ACEs (nulls when cache is empty).
        """
        try:
            payload = await _compute_print_payload(request.app.state.moonraker)
        except MoonrakerError as e:
            raise HTTPException(502, str(e))
        # Dual writer with PrintStatePoller — see comment in poller.py.
        # We also bump last_print_at so the autodry staleness guard treats
        # a UI-driven fetch as a fresh tick.
        request.app.state.last_print = payload
        request.app.state.last_print_at = time.time()

        # Per-ACE block: live data for active ACE, last-known cached for others.
        s = request.app.state.state
        device_count = int(getattr(s, "device_count", 1) or 1) if s else 1
        if device_count < 1:
            device_count = 1
        active = int(getattr(s, "active_device", 0) or 0) if s else 0
        cache = getattr(request.app.state, "last_ace_data", {}) or {}
        # payload["dryer"] is live data for the active ACE; humidity_per_ace
        # is the Govee bridge fan-out indexed by ACE (each ACE has its own
        # chamber sensor in multi-ACE setups). Falls back to the legacy single
        # sensor (payload["humidity"]) for ACEs without a dedicated MAC.
        live_dryer = payload.get("dryer")
        per_ace_humidity = payload.get("humidity_per_ace") or []
        legacy_humidity = payload.get("humidity")

        def _humidity_for(idx: int) -> Any:
            if idx < len(per_ace_humidity):
                entry = per_ace_humidity[idx]
                if isinstance(entry, dict) and entry.get("configured"):
                    return entry
            return legacy_humidity

        aces_block = []
        for ace_idx in range(device_count):
            if ace_idx == active:
                aces_block.append({
                    "index": ace_idx,
                    "dryer": live_dryer,
                    "humidity": _humidity_for(ace_idx),
                    "last_seen_ts": time.time(),
                    "is_active": True,
                })
            else:
                cached = cache.get(ace_idx) or {}
                aces_block.append({
                    "index": ace_idx,
                    "dryer": cached.get("dryer_status"),
                    "humidity": _humidity_for(ace_idx),
                    "last_seen_ts": cached.get("last_seen_ts"),
                    "is_active": False,
                })
        payload["aces"] = aces_block

        return payload

    @app.post("/api/dry")
    async def post_dry(request: Request, body: DryRequest) -> dict:
        # ACE_DRY accepts ACE=N [TEMP=T] [DURATION=D]; multiACE enforces its own
        # max_dryer_temperature, so we don't need to read the config to clamp.
        gcode = f"ACE_DRY ACE={body.ace} TEMP={body.temp_c} DURATION={body.duration_min}"
        try:
            result = await request.app.state.moonraker.run_gcode(gcode)
        except MoonrakerError as e:
            raise HTTPException(502, str(e))
        return {"ok": True, "result": result, "gcode": gcode}

    @app.get("/api/slots")
    async def get_slots(request: Request) -> dict:
        return build_slots_payload(
            request.app.state.state,
            getattr(request.app.state, "spool_cache", {}) or {})

    @app.get("/api/filamenthub/spools")
    async def list_filamenthub_spools(request: Request) -> dict:
        sm = getattr(request.app.state, "spoolman", None)
        if sm is None:
            return {"configured": False, "spools": []}
        return {"configured": True, "spools": await sm.list_spools()}

    @app.post("/api/slots/{ace}/{slot}/assign")
    async def assign_slot_spool(request: Request, ace: int, slot: int,
                                body: AssignSpoolRequest) -> dict:
        sm = getattr(request.app.state, "spoolman", None)
        if sm is None:
            raise HTTPException(503, "FilamentHub not configured")
        if not (0 <= ace <= 3 and 0 <= slot <= 3):
            raise HTTPException(422, "ace and slot must be 0-3")
        try:
            location = await sm.assign_spool(body.spool_id, ace, slot)
        except Exception as e:  # noqa: BLE001 — surface upstream failure to caller
            raise HTTPException(502, f"FilamentHub assign failed: {e}")
        # Refresh the binding cache so the slot reflects the new spool at once.
        try:
            request.app.state.spool_cache = await sm.list_all_bindings()
        except Exception:
            pass
        return {"ok": True, "spool_id": body.spool_id, "location": location}

    @app.delete("/api/slots/{ace}/{slot}/assign")
    async def unassign_slot_spool(request: Request, ace: int, slot: int) -> dict:
        sm = getattr(request.app.state, "spoolman", None)
        if sm is None:
            raise HTTPException(503, "FilamentHub not configured")
        if not (0 <= ace <= 3 and 0 <= slot <= 3):
            raise HTTPException(422, "ace and slot must be 0-3")
        try:
            cleared = await sm.unassign_slot(ace, slot)
        except Exception as e:  # noqa: BLE001 — surface upstream failure to caller
            raise HTTPException(502, f"FilamentHub unassign failed: {e}")
        try:
            request.app.state.spool_cache = await sm.list_all_bindings()
        except Exception:
            pass
        return {"ok": True, "cleared_spool_id": cleared}

    @app.post("/api/dry/stop")
    async def post_dry_stop(request: Request, body: DryStopRequest) -> dict:
        mr = request.app.state.moonraker
        try:
            await mr.run_gcode(f"ACE_SWITCH TARGET={body.ace}")
        except MoonrakerError as e:
            return JSONResponse(
                status_code=502,
                content={"error": f"could not switch to ACE {body.ace}: {e}"},
            )
        try:
            await mr.run_gcode("ACE_STOP_DRYING")
        except MoonrakerError as e:
            return JSONResponse(status_code=502, content={"error": str(e)})
        return {"ok": True}

    @app.get("/api/autodry")
    async def get_autodry(request: Request, ace: Optional[int] = None) -> Any:
        ad: AutoDryer = request.app.state.autodry
        if ace is None:
            return _autodry_to_dict(ad.persisted)
        try:
            fsm = ad.manager.get(ace)
        except (KeyError, AttributeError):
            return JSONResponse(status_code=404, content={"error": f"no FSM for ace={ace}"})
        return {
            "ace": ace,
            "enabled": fsm.config.enabled,
            "target_pct": fsm.config.target_pct,
            "hysteresis_pp": fsm.config.hysteresis_pp,
            "default_filament_type": fsm.config.default_filament_type,
            "keep_ready": fsm.config.keep_ready,
            "state": fsm.snapshot.state.value,
            "locked": fsm.locked,
            "unreachable": fsm.unreachable,
            "fault": (
                {"code": fsm.snapshot.fault.code,
                 "since_ts": fsm.snapshot.fault.since_ts,
                 "msg": fsm.snapshot.fault.msg}
                if fsm.snapshot.fault else None
            ),
        }

    @app.post("/api/autodry")
    async def post_autodry(request: Request, body: dict, ace: Optional[int] = None) -> Any:
        ad: AutoDryer = request.app.state.autodry
        if ace is None:
            # ===== Existing single-FSM action handler, UNCHANGED =====
            action = body.get("action")
            value = body.get("value")
            if action == "set_mode":
                if value not in ("off", "log", "active"):
                    raise HTTPException(400, "value must be off|log|active")
                ad.update_config(mode=value)
            elif action == "set_target":
                if not isinstance(value, int) or not (5 <= value <= 60):
                    raise HTTPException(400, "value must be int 5-60")
                ad.update_config(target_pct=value)
            elif action == "set_hysteresis":
                if not isinstance(value, int) or not (1 <= value <= 15):
                    raise HTTPException(400, "value must be int 1-15")
                ad.update_config(hysteresis_pp=value)
            elif action == "set_target_ace":
                if not isinstance(value, int) or not (0 <= value <= 3):
                    raise HTTPException(400, "value must be int 0-3")
                ad.update_config(target_ace=value)
            elif action == "set_default_filament_type":
                allowed = {None, "PLA", "PETG", "TPU", "ABS", "ASA", "PA", "PC", "PVA"}
                if value is not None and not isinstance(value, str):
                    raise HTTPException(400, "value must be a string or null")
                normalized = value.strip().upper() if isinstance(value, str) and value.strip() else None
                if normalized not in allowed:
                    raise HTTPException(400, f"value must be one of {sorted(x for x in allowed if x)} or null")
                ad.update_config(default_filament_type=normalized)
            elif action == "force_evaluate":
                ad.force_evaluate()
            elif action == "reset_fault":
                ad.reset_fault()
            else:
                raise HTTPException(400, f"unknown action: {action}")
            return _autodry_to_dict(ad.persisted)

        # ===== New per-ACE config update =====
        try:
            fsm = ad.manager.get(ace)
        except (KeyError, AttributeError):
            return JSONResponse(status_code=404, content={"error": f"no FSM for ace={ace}"})
        # Per-ACE actions (handled before pydantic config-update validation
        # because "action" is not a valid AutodryConfigUpdate field).
        if isinstance(body, dict) and body.get("action") == "reset_fault":
            ad.manager.reset_fault(ace)
            ad._save_manager()
            return {
                "ok": True,
                "ace": ace,
                "state": fsm.snapshot.state.value,
                "fault": None,
            }
        # Validate via pydantic
        try:
            update = AutodryConfigUpdate(**body)
        except Exception as e:
            return JSONResponse(status_code=422, content={"error": str(e)})
        # Validate filament type allowlist
        _ALLOWED_FILAMENT_TYPES = {None, "PLA", "PETG", "TPU", "ABS", "ASA", "PA", "PC", "PVA"}
        if update.default_filament_type is not None:
            normalized = update.default_filament_type.strip().upper() if update.default_filament_type.strip() else None
            if normalized not in _ALLOWED_FILAMENT_TYPES:
                return JSONResponse(status_code=400,
                                    content={"error": "invalid default_filament_type"})
            fsm.config.default_filament_type = normalized
        if update.enabled is not None:
            fsm.config.enabled = update.enabled
        if update.target_pct is not None:
            fsm.config.target_pct = update.target_pct
        if update.hysteresis_pp is not None:
            fsm.config.hysteresis_pp = update.hysteresis_pp
        if update.keep_ready is not None:
            fsm.config.keep_ready = update.keep_ready
        ad._save_manager()
        return {"ok": True, "ace": ace}

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

    @app.get("/api/web-config")
    async def get_web_config() -> dict:
        """Frontend boots and reads this once to know if FilamentHub picker
        is available and what query params to use."""
        return {
            "filamenthub_url": os.environ.get("FILAMENTHUB_URL", "").strip(),
            "filamenthub_printer_id": os.environ.get("FILAMENTHUB_PRINTER_ID", "").strip() or "u1-1",
        }

    # Store the module-level revalidate function on app.state so tests can
    # monkeypatch it via app.state._revalidate_impl without breaking the
    # module-level monkeypatch path used by test_revalidate_returns_updated_sidecar.
    if not hasattr(app.state, "_revalidate_impl"):
        app.state._revalidate_impl = _revalidate_gcode

    @app.get("/api/print_queue")
    async def get_print_queue(request: Request) -> dict:
        """List gcode files that have multiace sidecars, sorted by generated_at desc."""
        gcode_dir: Path = getattr(
            request.app.state, "gcode_dir",
            Path(_env("MULTIACE_GCODE_DIR", "/home/lava/printer_data/gcodes")),
        )
        try:
            moonraker_client: MoonrakerClient = request.app.state.moonraker
            file_list = await moonraker_client.list_gcode_files()
        except MoonrakerError as e:
            raise HTTPException(502, str(e))

        sidecar_names = {f["filename"] for f in file_list
                         if f["filename"].endswith(".multiace.json")}
        items = []
        for f in file_list:
            sidecar_name = f["filename"] + ".multiace.json"
            if sidecar_name not in sidecar_names:
                continue
            sidecar_path = gcode_dir / sidecar_name
            if not sidecar_path.exists():
                continue
            try:
                sidecar = json.loads(sidecar_path.read_text())
            except Exception:
                continue
            items.append({
                "filename": f["filename"],
                "status": sidecar.get("status"),
                "reason": sidecar.get("reason"),
                "generated_at": sidecar.get("generated_at"),
                "tools": sidecar.get("tools", {}),
                "match_summary": sidecar.get("match_summary", {}),
                "swaps": sidecar.get("swaps", []),
                "errors": sidecar.get("errors", []),
            })

        items.sort(key=lambda x: x.get("generated_at") or "", reverse=True)
        return {"items": items}

    @app.post("/api/print_queue/{gcode_filename:path}/revalidate")
    async def revalidate_gcode(request: Request, gcode_filename: str) -> dict:
        """Re-run match+plan against current slot bindings for an existing gcode+sidecar."""
        # Safety check: refuse if a swap is in progress
        s = request.app.state.state
        if getattr(s, "swap_in_progress", False):
            raise HTTPException(409, "swap in progress — wait for it to finish before re-validating")

        gcode_dir: Path = getattr(
            request.app.state, "gcode_dir",
            Path(_env("MULTIACE_GCODE_DIR", "/home/lava/printer_data/gcodes")),
        )
        gcode_path = gcode_dir / gcode_filename
        sidecar_path = Path(str(gcode_path) + ".multiace.json")
        if not sidecar_path.exists():
            raise HTTPException(404, f"no sidecar for {gcode_filename}")

        # Build slots_resp from current in-process state (mirrors /api/slots logic)
        state_obj = request.app.state.state
        spool_cache = getattr(request.app.state, "spool_cache", {}) or {}
        gate_status = list(getattr(state_obj, "gate_status", []) or [])
        active = int(getattr(state_obj, "active_device", 0) or 0)
        device_count = int(getattr(state_obj, "device_count", 1) or 1)
        aces = []
        for ace_idx in range(max(device_count, 1)):
            slots = []
            for slot in range(4):
                binding = (spool_cache.get(ace_idx) or {}).get(slot)
                slots.append({
                    "slot": slot,
                    "gate_status": (
                        gate_status[slot]
                        if ace_idx == active and slot < len(gate_status)
                        else None
                    ),
                    "spool": (
                        {
                            "spool_id": binding.spool_id,
                            "name": binding.name,
                            "material": binding.material,
                            "color": binding.color,
                            "weight_remaining_g": binding.weight_remaining_g,
                        }
                        if binding else None
                    ),
                })
            aces.append({"index": ace_idx, "is_active": ace_idx == active, "slots": slots})
        slots_resp = {"aces": aces}

        impl = getattr(request.app.state, "_revalidate_impl", _revalidate_gcode)
        result = await impl(gcode_path, slots_resp)
        return result

    @app.post("/api/print_queue/{gcode_filename:path}/print")
    async def start_print_for_gcode(request: Request, gcode_filename: str) -> dict:
        """Start a print of a gcode in the queue. Refuses if the sidecar is
        not 'ready' or if a print/swap is already in progress."""
        s = request.app.state.state
        if getattr(s, "swap_in_progress", False):
            raise HTTPException(409, "swap in progress — wait before starting a print")

        gcode_dir: Path = getattr(
            request.app.state, "gcode_dir",
            Path(_env("MULTIACE_GCODE_DIR", "/home/lava/printer_data/gcodes")),
        )
        gcode_path = gcode_dir / gcode_filename
        sidecar_path = Path(str(gcode_path) + ".multiace.json")

        # Refuse if no sidecar — caller should re-validate first
        if not sidecar_path.exists():
            raise HTTPException(404, f"no sidecar for {gcode_filename} — re-validate first")

        # Refuse if sidecar status != ready
        try:
            sidecar = json.loads(sidecar_path.read_text())
        except Exception as e:
            raise HTTPException(500, f"could not read sidecar: {e}")
        if sidecar.get("status") != "ready":
            raise HTTPException(
                409,
                f"sidecar status is {sidecar.get('status')!r}, not 'ready'. "
                f"Reason: {sidecar.get('reason')!r}. Fix loadout or re-validate first.",
            )

        moonraker: MoonrakerClient = request.app.state.moonraker
        try:
            result = await moonraker.start_print(gcode_filename)
        except MoonrakerError as e:
            raise HTTPException(502, f"Moonraker rejected start_print: {e}")
        return {"started": gcode_filename, "result": result}

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
            snap = json.dumps({"type": "state", "payload": _state_payload(ws.app)})
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


async def _revalidate_gcode(gcode_path: Path, slots_resp: dict) -> dict:
    """Re-run match + plan against current slot bindings and rewrite the sidecar.

    Imports the postprocessor at call time to avoid circular imports and to
    allow tools/ to remain a standalone script.
    """
    import sys as _sys
    import importlib
    tools_dir = Path(__file__).resolve().parents[2] / "tools"
    if str(tools_dir) not in _sys.path:
        _sys.path.insert(0, str(tools_dir))
    pp = importlib.import_module("multiace_postprocess")

    lines = gcode_path.read_text(encoding="utf-8", errors="replace").splitlines()
    tools = pp.parse_header(lines)
    if tools is None:
        return {"status": "error", "reason": "no_8_tool_header", "tools": {}, "swaps": []}
    resolutions = pp.match_tools(tools, slots_resp)
    swaps = pp.plan_swaps(resolutions, lines)
    new_lines = pp.rewrite_gcode(lines, resolutions, swaps)

    unresolved = [r for r in resolutions if r.match_quality != "exact" and r.resolved is None]
    ambiguous = [r for r in resolutions if r.match_quality == "ambiguous"]
    if ambiguous:
        status, reason = "pending", "ambiguous_match"
    elif unresolved:
        status, reason = "pending", "missing_bindings"
    else:
        status, reason = "ready", None

    gcode_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    pp.write_sidecar(gcode_path, resolutions, swaps, status, reason)
    return {
        "status": status,
        "reason": reason,
        "tools": {str(r.tool.index): {
            "type": r.tool.type,
            "color": f"#{r.tool.color}",
            "match_quality": r.match_quality,
            "resolved": (
                {"ace": r.resolved.ace, "slot": r.resolved.slot, "spool_id": r.resolved.spool_id}
                if r.resolved else None
            ),
            "physical_head": r.physical_head,
        } for r in resolutions},
        "swaps": [{"line": s.line, "layer": s.layer, "head": s.head} for s in swaps],
    }


def _autodry_to_dict(p: PersistedState) -> dict:
    return {
        "mode": p.mode,
        "target_ace": p.target_ace,
        "target_pct": p.target_pct,
        "hysteresis_pp": p.hysteresis_pp,
        "default_filament_type": p.default_filament_type,
        "fsm": {
            "state": p.fsm.state.value,
            "since_ts": p.fsm.since_ts,
            "fault": (
                {"code": p.fsm.fault.code,
                 "since_ts": p.fsm.fault.since_ts,
                 "msg": p.fsm.fault.msg}
                if p.fsm.fault else None
            ),
            "last_run": (
                {"kind": p.fsm.last_run.kind,
                 "outcome": p.fsm.last_run.outcome,
                 "started_ts": p.fsm.last_run.started_ts,
                 "ended_ts": p.fsm.last_run.ended_ts,
                 "trigger_rh": p.fsm.last_run.trigger_rh,
                 "end_rh": p.fsm.last_run.end_rh,
                 "temp_c_used": p.fsm.last_run.temp_c_used,
                 "duration_min": p.fsm.last_run.duration_min,
                 "ran_min": p.fsm.last_run.ran_min}
                if p.fsm.last_run else None
            ),
            "trigger_announcement_id": p.fsm.trigger_announcement_id,
            "cooldown_until_ts": p.fsm.cooldown_until_ts,
        },
    }


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
