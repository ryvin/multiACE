# License: GPL-3.0
"""External Govee-bridge humidity source for the autodry FSM.

Ported (not imported) from ``multiace_web/src/multiace_web/server.py``'s
``_read_humidity_per_ace`` / ``_derive_sensors_url`` / ``_HUMIDITY_PER_ACE_CACHE``
— see that module for the canonical fork implementation this vendors. Like
``fsm.py``, this is a standalone decay71 sidecar module and must not import
from ``multiace_web``.

Why this exists: the ACE Pro's own ``humidity`` field (the real decay71
``ace.aces[i].humidity`` shape — see ``moonraker_client.parse_ace_object``)
was verified **null** on live hardware, both at idle and mid-dry-cycle
(2026-07-08). Without an external sensor, auto-trigger has no usable
humidity signal at all. This bridge talks to a small local HTTP shim (the
fork's "Govee bridge", e.g. a BLE-to-HTTP relay for Govee H5075-class
sensors) that exposes:

- ``GET <MULTIACE_HUMIDITY_URL>``  — single-sensor shape, unused by this
  module directly but its URL is the basis for deriving the per-ACE URL.
- ``GET <sensors_url>`` (``MULTIACE_HUMIDITY_SENSORS_URL``, or the single
  URL's trailing ``/sensor`` replaced with ``/sensors``) — returns
  ``{mac: {temperature, humidity, battery, rssi, name, age_s} | None, ...}``
  in ``GOVEE_BRIDGE_MACS`` config order (relies on Python dict insertion
  order). The Nth MAC's reading is mapped **positionally** to ACE index N —
  there is no MAC-to-ACE identity in the payload itself.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

_HUMIDITY_TTL_SEC = 30.0
_DEFAULT_TIMEOUT_SEC = 8.0
_DEFAULT_LABEL_PREFIX = "ACE"


def _derive_sensors_url(single_url: str) -> str:
    """Map a configured .../sensor URL to its sibling .../sensors. Returns the
    input unchanged if it doesn't end in /sensor (caller may have explicitly
    set the sensors URL, or pointed at a non-bridge endpoint)."""
    if not single_url:
        return ""
    if single_url.endswith("/sensor"):
        return single_url[: -len("/sensor")] + "/sensors"
    return single_url


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@dataclass
class AceHumidity:
    """One ACE's humidity-bridge reading for one tick/status read.

    ``configured=False`` means this ACE index has no bridge MAC assigned
    (either the bridge itself is unconfigured, or there are fewer MACs than
    ACEs) — the caller should treat this as "no data", not as an error.
    ``configured=True, ok=False`` means a MAC is assigned but the reading is
    unusable this tick (fetch error, or the bridge hasn't warmed up yet) —
    the caller should treat this as a stale/missed sample, NOT as opt-out,
    so a transient bridge outage doesn't quietly disable auto-dry forever.
    """
    configured: bool
    ok: bool = False
    humidity_pct: float | None = None
    temp_c: float | None = None
    label: str | None = None
    fetched_at: float | None = None
    error: str | None = None
    warming_up: bool = False
    mac: str | None = None
    name: str | None = None
    battery: Any = None
    rssi: Any = None
    age_s: Any = None


class HumidityBridge:
    """Fetches per-ACE humidity from a Govee-bridge ``/sensors`` endpoint.

    Stateful (holds a TTL cache), so construct one instance and reuse it
    across ticks/requests rather than creating a fresh one each time —
    mirrors the fork's module-level ``_HUMIDITY_PER_ACE_CACHE`` dict.
    """

    def __init__(
        self,
        humidity_url: str = "",
        sensors_url: str = "",
        auth: str = "",
        label: str = "",
        ttl_sec: float = _HUMIDITY_TTL_SEC,
        timeout_sec: float = _DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self._single_url = (humidity_url or "").strip()
        self._sensors_url = (sensors_url or "").strip() or _derive_sensors_url(self._single_url)
        self._auth = (auth or "").strip()
        self._label_prefix = (label or "").strip() or _DEFAULT_LABEL_PREFIX
        self._ttl_sec = ttl_sec
        self._timeout_sec = timeout_sec
        self._cache_data: list[AceHumidity] | None = None
        self._cache_n: int = 0
        self._cache_ts: float = 0.0

    @property
    def configured(self) -> bool:
        return bool(self._single_url)

    async def fetch_per_ace(self, device_count: int) -> dict[int, AceHumidity]:
        """Return {ace_index: AceHumidity} for ace_index in range(device_count).

        If MULTIACE_HUMIDITY_URL is unset, every entry is
        AceHumidity(configured=False) — matches the single-sensor "no
        humidity configured" behavior. On fetch failure, EVERY entry is
        AceHumidity(configured=True, ok=False, error=...) so the FSM treats
        it as a stale/missed sample rather than a silent opt-out.
        """
        if device_count < 1:
            return {}

        if not self.configured:
            return {i: AceHumidity(configured=False) for i in range(device_count)}

        now = time.time()
        if (
            self._cache_data is not None
            and self._cache_n == device_count
            and (now - self._cache_ts) < self._ttl_sec
        ):
            return {i: self._cache_data[i] for i in range(device_count)}

        headers: dict[str, str] = {}
        if self._auth:
            headers["Authorization"] = self._auth

        try:
            async with httpx.AsyncClient(timeout=self._timeout_sec) as client:
                resp = await client.get(self._sensors_url, headers=headers)
                resp.raise_for_status()
                body = resp.json()
        except Exception as e:  # noqa: BLE001 - vendored fork behavior: any fetch error degrades uniformly
            err = f"{type(e).__name__}: {e}"
            out = [AceHumidity(configured=True, ok=False, error=err) for _ in range(device_count)]
            self._cache_data, self._cache_n, self._cache_ts = out, device_count, now
            return {i: out[i] for i in range(device_count)}

        # Bridge /sensors returns {mac: {...} | None, ...} in
        # GOVEE_BRIDGE_MACS config order. dict insertion order (Python 3.7+)
        # is what lets us align entries to ACE indices positionally.
        ordered_entries: list[tuple[str, Any]] = list(body.items()) if isinstance(body, dict) else []

        out = []
        for ace_idx in range(device_count):
            if ace_idx < len(ordered_entries):
                mac, payload = ordered_entries[ace_idx]
                if not isinstance(payload, dict):
                    # Bridge still warming up — MAC configured, no reading yet.
                    out.append(AceHumidity(
                        configured=True, ok=False, warming_up=True,
                        label=f"{self._label_prefix} {ace_idx}", mac=mac,
                    ))
                else:
                    out.append(AceHumidity(
                        configured=True,
                        ok=True,
                        humidity_pct=_to_float(payload.get("humidity")),
                        temp_c=_to_float(payload.get("temperature")),
                        label=f"{self._label_prefix} {ace_idx}",
                        fetched_at=now,
                        mac=mac,
                        name=payload.get("name"),
                        battery=payload.get("battery"),
                        rssi=payload.get("rssi"),
                        age_s=payload.get("age_s"),
                    ))
            else:
                # Fewer MACs than ACEs — pad with "not configured" for this ACE.
                out.append(AceHumidity(configured=False))

        self._cache_data, self._cache_n, self._cache_ts = out, device_count, now
        return {i: out[i] for i in range(device_count)}
