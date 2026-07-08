# License: GPL-3.0
"""Auto-Dry plugin FastAPI app: manifest + per-ACE status/config/dry endpoints
+ a background tick loop that drives the vendored FSM (fsm.py) off Moonraker
polling, independent of any UI client being connected.
"""
from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import Config
from .fsm import (
    AutodryManager,
    FSMState,
    Inputs,
    manual_trigger,
    reset_fault as fsm_reset_fault,
    tick_fsm,
)
from .humidity_bridge import AceHumidity, HumidityBridge
from .moonraker_client import MoonrakerClient, MoonrakerError, active_device_0based, parse_ace_object
from .multiace_client import MultiAceClient
from .persistence import load_manager, save_manager

log = logging.getLogger("autodry.plugin")

MANIFEST = {"name": "autodry", "label": "Auto-Dry", "version": "0.1.0", "ui_url": "/"}


class ConfigReq(BaseModel):
    ace: int
    target_pct: int | None = None
    temp: int | None = None
    duration_min: int | None = None
    enabled: bool | None = None


class DryReq(BaseModel):
    ace: int


def _fault_to_dict(fault) -> dict[str, Any] | None:
    if fault is None:
        return None
    return {"code": fault.code, "since_ts": fault.since_ts, "msg": fault.msg}


def _last_run_to_dict(last_run) -> dict[str, Any] | None:
    if last_run is None:
        return None
    return dataclasses.asdict(last_run)


def _remaining_min(fsm, now_ts: float) -> int | None:
    if fsm.snapshot.state != FSMState.DRYING or not fsm.eph.drying_started_ts:
        return None
    elapsed_min = (now_ts - fsm.eph.drying_started_ts) / 60.0
    return max(0, int(round(fsm.config.duration_min - elapsed_min)))


def _effective_humidity(live, humidity: "AceHumidity | None") -> tuple[float | None, str]:
    """Resolve the humidity_pct to display + its source.

    The Govee bridge is the PRIMARY source (it's the only one proven to
    return non-null readings on live hardware — the ACE's own `humidity`
    field is verified null both idle and mid-dry). When the bridge isn't
    configured or its reading isn't usable this tick, we fall back to
    whatever the `ace` Klipper object reports (kept for the `units[]` /
    per-unit shape some setups may still expose) purely for /status display
    continuity; `humidity_source` only ever flags "bridge" vs "none" — it
    does not distinguish the legacy ACE-object fallback as a third source,
    since that fallback is not used for auto-trigger arming (see
    _tick_once), only for display.
    """
    if humidity is not None and humidity.ok and humidity.humidity_pct is not None:
        return humidity.humidity_pct, "bridge"
    if live is not None and live.humidity_ok:
        return live.humidity_pct, "none"
    return None, "none"


def _status_entry(fsm, live, humidity: "AceHumidity | None" = None) -> dict[str, Any]:
    now_ts = time.time()
    humidity_pct, humidity_source = _effective_humidity(live, humidity)
    return {
        "ace": fsm.ace,
        "enabled": fsm.config.enabled,
        "state": fsm.snapshot.state.value,
        "target_pct": fsm.config.target_pct,
        "temp_c": fsm.config.temp_c,
        "duration_min": fsm.config.duration_min,
        "hysteresis_pp": fsm.config.hysteresis_pp,
        "humidity_pct": humidity_pct,
        "humidity_source": humidity_source,
        "remaining_min": _remaining_min(fsm, now_ts),
        "fault": _fault_to_dict(fsm.snapshot.fault),
        "last_run": _last_run_to_dict(fsm.snapshot.last_run),
    }


def create_app(cfg: Config) -> FastAPI:
    manager: AutodryManager = load_manager(cfg.state_path)
    # One bridge instance shared across ticks/requests so its TTL cache is
    # actually effective (see humidity_bridge.HumidityBridge).
    humidity_bridge = HumidityBridge(
        humidity_url=cfg.humidity_url,
        sensors_url=cfg.humidity_sensors_url,
        auth=cfg.humidity_auth,
        label=cfg.humidity_label,
    )

    def _fsm_cfg_kwargs() -> dict[str, Any]:
        return {
            "debounce_required": cfg.debounce_required,
            "cooldown_min": cfg.cooldown_min,
            "max_run_min": cfg.max_run_min,
            "daily_duty_max_min": cfg.daily_duty_max_min,
            "min_delta_pct": cfg.min_delta_pct,
        }

    async def _tick_once() -> None:
        mr = MoonrakerClient(cfg.moonraker_url)
        ma = MultiAceClient(cfg.multiace_url)
        try:
            try:
                ace_obj = (await mr.query_objects(["ace"])).get("ace") or {}
            except MoonrakerError:
                ace_obj = {}
            try:
                ps_obj = (await mr.query_objects(["print_stats"])).get("print_stats") or {}
            except MoonrakerError:
                ps_obj = {}
            print_state = str(ps_obj.get("state") or "standby")

            ma_state = await ma.get_state()
            swap_in_progress = bool(ma_state.get("swap_in_progress"))

            device_count = int(ace_obj.get("device_count") or ma_state.get("device_count") or 1)
            # active/print_state/swap still come from the `ace` / print_stats
            # / multiACE objects above. We deliberately do NOT call
            # parse_ace_object() here for humidity anymore — the ACE's own
            # humidity field is verified null on live hardware (idle and
            # mid-dry); the bridge below is the sole humidity source for the
            # tick loop's auto-trigger arming.
            active = active_device_0based(ace_obj)
            humidity_by_ace = await humidity_bridge.fetch_per_ace(device_count)

            for i in range(device_count):
                fsm = manager.get(i)
                if not fsm.config.enabled:
                    continue

                is_target = cfg.round_robin or active is None or active == i
                fsm.locked = not is_target
                if fsm.locked:
                    continue

                if (cfg.round_robin and active is not None and active != i
                        and print_state not in ("printing", "paused")):
                    # Round-robin: switch the shared serial connection to this
                    # ACE (single-connection firmware limitation — see
                    # CLAUDE.md instinct #8). The bridge's humidity reading is
                    # independent of which ACE currently holds the serial
                    # connection, so we don't need to re-query the `ace`
                    # object here — just perform the switch itself.
                    try:
                        await mr.run_gcode(f"ACE_SWITCH TARGET={i}")
                    except MoonrakerError:
                        log.warning("autodry: round-robin switch to ace=%d failed", i)
                        continue

                humidity = humidity_by_ace.get(i)
                humidity_ok = bool(humidity and humidity.ok and humidity.humidity_pct is not None)
                inputs = Inputs(
                    humidity_ok=humidity_ok,
                    humidity_pct=humidity.humidity_pct if (humidity and humidity.humidity_pct is not None) else 0.0,
                    print_state=print_state,
                    swap_in_progress=swap_in_progress,
                )
                new_snap, transitions = tick_fsm(
                    fsm.config, fsm.snapshot, fsm.eph, inputs, time.time(),
                    **_fsm_cfg_kwargs(),
                )
                fsm.snapshot = new_snap
                for t in transitions:
                    if t.event == "AUTODRY_TRIGGERED":
                        gcode = f"ACE_DRY ACE={i} TEMP={fsm.config.temp_c} DURATION={fsm.config.duration_min}"
                        try:
                            await mr.run_gcode(gcode)
                        except MoonrakerError:
                            log.exception("autodry: ACE_DRY failed for ace=%d", i)

            save_manager(cfg.state_path, manager)
        finally:
            await mr.close()

    async def _tick_loop() -> None:
        log.info("autodry: tick loop starting (tick_sec=%s round_robin=%s)",
                  cfg.tick_sec, cfg.round_robin)
        while True:
            try:
                await _tick_once()
            except Exception:
                log.exception("autodry: tick failed; continuing")
            await asyncio.sleep(cfg.tick_sec)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = None
        if cfg.tick_sec > 0:
            task = asyncio.create_task(_tick_loop())
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title="Auto-Dry Plugin", lifespan=lifespan)
    app.state.cfg = cfg
    app.state.manager = manager
    app.state.humidity_bridge = humidity_bridge
    # Exposed for tests to drive the tick loop synchronously without waiting
    # on the background asyncio task (which is disabled under test anyway
    # via tick_sec=0 — see conftest.cfg).
    app.state.tick_once = _tick_once

    @app.get("/integration-manifest")
    def integration_manifest():
        return MANIFEST

    @app.get("/status")
    async def status():
        mr = MoonrakerClient(cfg.moonraker_url)
        try:
            ace_obj = (await mr.query_objects(["ace"])).get("ace") or {}
        except MoonrakerError as e:
            log.warning("autodry: /status Moonraker query failed: %s", e)
            ace_obj = {}
        finally:
            await mr.close()

        live_by_ace = parse_ace_object(ace_obj)
        device_count = int(ace_obj.get("device_count") or 0)
        highest_known = max(manager.fsms.keys(), default=-1) + 1
        n = max(device_count, highest_known, 1)
        humidity_by_ace = await humidity_bridge.fetch_per_ace(n)

        return {"aces": [
            _status_entry(manager.get(i), live_by_ace.get(i), humidity_by_ace.get(i))
            for i in range(n)
        ]}

    @app.post("/config")
    async def set_config(req: ConfigReq):
        if req.ace < 0:
            raise HTTPException(status_code=400, detail="ace must be >= 0")
        fsm = manager.get(req.ace)
        if req.target_pct is not None:
            if not (0 <= req.target_pct <= 100):
                raise HTTPException(status_code=400, detail="target_pct must be 0-100")
            fsm.config.target_pct = req.target_pct
        if req.temp is not None:
            fsm.config.temp_c = req.temp
        if req.duration_min is not None:
            if req.duration_min <= 0:
                raise HTTPException(status_code=400, detail="duration_min must be > 0")
            fsm.config.duration_min = req.duration_min
        if req.enabled is not None:
            fsm.config.enabled = req.enabled
        save_manager(cfg.state_path, manager)
        return {"ok": True, "ace": fsm.ace, "config": dataclasses.asdict(fsm.config)}

    @app.post("/dry")
    async def dry_now(req: DryReq):
        if req.ace < 0:
            raise HTTPException(status_code=400, detail="ace must be >= 0")
        fsm = manager.get(req.ace)
        if fsm.snapshot.state == FSMState.DRYING:
            raise HTTPException(status_code=409, detail="ACE is already drying")

        mr = MoonrakerClient(cfg.moonraker_url)
        try:
            try:
                ace_obj = (await mr.query_objects(["ace"])).get("ace") or {}
                live = parse_ace_object(ace_obj).get(req.ace)
            except MoonrakerError:
                live = None

            gcode = f"ACE_DRY ACE={fsm.ace} TEMP={fsm.config.temp_c} DURATION={fsm.config.duration_min}"
            try:
                await mr.run_gcode(gcode)
            except MoonrakerError as e:
                log.warning("autodry: manual dry failed for ace=%d: %s", req.ace, e)
                raise HTTPException(status_code=502, detail="Moonraker dry command failed") from e
        finally:
            await mr.close()

        inputs = Inputs(
            humidity_ok=bool(live and live.humidity_ok),
            humidity_pct=live.humidity_pct if live else 0.0,
        )
        result = manual_trigger(fsm.snapshot, fsm.eph, inputs, time.time())
        if result is not None:
            fsm.snapshot, _transition = result
        save_manager(cfg.state_path, manager)
        return {"ok": True, "ace": fsm.ace, "temp_c": fsm.config.temp_c,
                "duration_min": fsm.config.duration_min}

    @app.post("/reset-fault")
    async def reset_fault(req: DryReq):
        if req.ace < 0:
            raise HTTPException(status_code=400, detail="ace must be >= 0")
        fsm = manager.get(req.ace)
        fsm.snapshot = fsm_reset_fault(fsm.snapshot)
        save_manager(cfg.state_path, manager)
        return {"ok": True, "ace": fsm.ace, "state": fsm.snapshot.state.value}

    static_dir = Path(__file__).parent / "static"
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app
