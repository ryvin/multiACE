# License: GPL-3.0
"""Environment configuration for the autodry plugin."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# decay71's plugin discovery scans this port range for GET /integration-manifest.
# filamenthub already occupies 8089; autodry defaults to the next free port.
_PLUGIN_PORT_MIN = 8089
_PLUGIN_PORT_MAX = 8098


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    moonraker_url: str
    multiace_url: str
    port: int
    round_robin: bool
    tick_sec: float
    state_path: Path
    default_target_pct: int
    default_temp_c: int
    default_duration_min: int
    hysteresis_pp: int
    cooldown_min: int
    debounce_required: int
    max_run_min: int
    daily_duty_max_min: int
    min_delta_pct: int
    # External Govee-bridge humidity source (see humidity_bridge.py). Empty
    # humidity_url means the bridge is disabled — auto-trigger stays inert
    # (humidity_ok=False), manual /dry is unaffected. The ACE Pro's own
    # humidity reading is unusable (verified null on live hardware, both
    # idle and mid-dry), so this bridge is the only viable humidity source
    # for auto-trigger today.
    humidity_url: str = ""
    humidity_sensors_url: str = ""
    humidity_auth: str = ""
    humidity_label: str = ""


def load_config() -> Config:
    """Build Config from the environment. Fails fast (raises ValueError) on
    a malformed or out-of-range value rather than starting with silently
    wrong settings — this is a printer-safety-adjacent service."""
    port = int(os.environ.get("AUTODRY_PLUGIN_PORT", "8090"))
    if not (_PLUGIN_PORT_MIN <= port <= _PLUGIN_PORT_MAX):
        raise ValueError(
            f"AUTODRY_PLUGIN_PORT={port} is outside decay71's plugin port range "
            f"({_PLUGIN_PORT_MIN}-{_PLUGIN_PORT_MAX})"
        )

    tick_sec = float(os.environ.get("AUTODRY_TICK_SEC", "30"))
    if tick_sec < 0:
        raise ValueError(f"AUTODRY_TICK_SEC must be >= 0, got {tick_sec}")

    default_target_pct = int(os.environ.get("AUTODRY_DEFAULT_TARGET_PCT", "15"))
    if not (0 <= default_target_pct <= 100):
        raise ValueError(f"AUTODRY_DEFAULT_TARGET_PCT must be 0-100, got {default_target_pct}")

    return Config(
        moonraker_url=os.environ.get("MOONRAKER_URL", "http://127.0.0.1:7125"),
        multiace_url=os.environ.get("MULTIACE_URL", "http://127.0.0.1:7126"),
        port=port,
        round_robin=_env_bool("AUTODRY_ROUND_ROBIN", False),
        tick_sec=tick_sec,
        state_path=Path(os.environ.get("AUTODRY_STATE_PATH", ".autodry_state.json")),
        default_target_pct=default_target_pct,
        default_temp_c=int(os.environ.get("AUTODRY_DEFAULT_TEMP_C", "55")),
        default_duration_min=int(os.environ.get("AUTODRY_DEFAULT_DURATION_MIN", "240")),
        hysteresis_pp=int(os.environ.get("AUTODRY_HYSTERESIS_PP", "5")),
        cooldown_min=int(os.environ.get("AUTODRY_COOLDOWN_MIN", "30")),
        debounce_required=int(os.environ.get("AUTODRY_DEBOUNCE_REQUIRED", "3")),
        max_run_min=int(os.environ.get("AUTODRY_MAX_RUN_MIN", "720")),
        daily_duty_max_min=int(os.environ.get("AUTODRY_DAILY_DUTY_MAX_MIN", "1080")),
        min_delta_pct=int(os.environ.get("AUTODRY_MIN_DELTA_PCT", "3")),
        humidity_url=os.environ.get("MULTIACE_HUMIDITY_URL", "").strip(),
        humidity_sensors_url=os.environ.get("MULTIACE_HUMIDITY_SENSORS_URL", "").strip(),
        humidity_auth=os.environ.get("MULTIACE_HUMIDITY_AUTH", "").strip(),
        humidity_label=os.environ.get("MULTIACE_HUMIDITY_LABEL", "").strip(),
    )
