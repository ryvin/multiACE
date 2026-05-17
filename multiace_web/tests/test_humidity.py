"""Tests for the pluggable external humidity sensor adapter in server.py.

The adapter supports four shapes out of the box:
1. Generic JSON: {"humidity": ..., "temperature": ...}
2. Home Assistant /api/states/<entity>: {"state": "47.2", "attributes": {...}}
3. SwitchBot Cloud: {"body": {"humidity": ..., "temperature": ...}}
4. Explicit dot-paths via MULTIACE_HUMIDITY_HUM_PATH / _TEMP_PATH for nested or unusual shapes.
"""
import pytest
import respx

from multiace_web import server


# ---- _resolve_path: dot-path JSON access ----

def test_resolve_path_top_level_key():
    assert server._resolve_path({"humidity": 42.5}, "humidity") == 42.5


def test_resolve_path_nested_dict():
    obj = {"sensor": {"data": {"rh": 45.6}}}
    assert server._resolve_path(obj, "sensor.data.rh") == 45.6


def test_resolve_path_missing_key_returns_none():
    assert server._resolve_path({"humidity": 50}, "rh") is None


def test_resolve_path_partial_path_returns_none():
    assert server._resolve_path({"sensor": {}}, "sensor.data.rh") is None


def test_resolve_path_supports_list_index():
    obj = {"readings": [{"h": 30}, {"h": 40}, {"h": 50}]}
    assert server._resolve_path(obj, "readings.1.h") == 40


def test_resolve_path_handles_non_dict():
    """Bare strings/numbers along the path stop traversal gracefully."""
    assert server._resolve_path({"value": 10}, "value.subkey") is None


# ---- _guess_humidity: auto-detect common shapes ----

def test_guess_humidity_generic_shape():
    assert server._guess_humidity({"humidity": 47.5}) == 47.5


def test_guess_humidity_alt_keys():
    assert server._guess_humidity({"humidity_pct": 30}) == 30
    assert server._guess_humidity({"rh": 60}) == 60
    assert server._guess_humidity({"RH": 60}) == 60


def test_guess_humidity_home_assistant_device_class():
    """HA states API exposes humidity sensors with device_class=humidity."""
    ha_response = {
        "entity_id": "sensor.ace_humidity",
        "state": "55.3",
        "attributes": {
            "device_class": "humidity",
            "unit_of_measurement": "%",
            "friendly_name": "ACE Humidity",
        },
    }
    assert server._guess_humidity(ha_response) == "55.3"


def test_guess_humidity_home_assistant_unit_fallback():
    """Even if device_class isn't set, '%' unit hint catches HA-style sensors."""
    ha_response = {
        "state": "42",
        "attributes": {"unit_of_measurement": "% RH"},
    }
    assert server._guess_humidity(ha_response) == "42"


def test_guess_humidity_switchbot_cloud_shape():
    """SwitchBot Cloud /v1.0/devices/<id>/status nests under 'body'."""
    sb_response = {
        "statusCode": 100,
        "body": {"humidity": 38, "temperature": 23.4, "battery": 95},
        "message": "success",
    }
    assert server._guess_humidity(sb_response) == 38


def test_guess_humidity_returns_none_for_unknown_shape():
    assert server._guess_humidity({"foo": "bar"}) is None


def test_guess_humidity_returns_none_for_non_dict():
    assert server._guess_humidity([1, 2, 3]) is None
    assert server._guess_humidity("47") is None


# ---- _guess_temperature ----

def test_guess_temperature_generic():
    assert server._guess_temperature({"temperature": 22.4}) == 22.4
    assert server._guess_temperature({"temperature_c": 22}) == 22
    assert server._guess_temperature({"temp": 22}) == 22
    assert server._guess_temperature({"temp_c": 22}) == 22


def test_guess_temperature_home_assistant():
    ha = {
        "state": "23.1",
        "attributes": {"device_class": "temperature", "unit_of_measurement": "°C"},
    }
    assert server._guess_temperature(ha) == "23.1"


def test_guess_temperature_switchbot():
    sb = {"body": {"humidity": 40, "temperature": 21.8}}
    assert server._guess_temperature(sb) == 21.8


# ---- _read_humidity: full integration with caching, env config, error handling ----

@pytest.fixture(autouse=True)
def reset_humidity_cache():
    """Clear the module-level cache before each test so they don't bleed."""
    server._HUMIDITY_CACHE["data"] = None
    server._HUMIDITY_CACHE["ts"] = 0.0
    yield


@pytest.mark.asyncio
async def test_read_humidity_returns_unconfigured_when_no_url(monkeypatch):
    monkeypatch.delenv("MULTIACE_HUMIDITY_URL", raising=False)
    result = await server._read_humidity()
    assert result == {"configured": False}


@pytest.mark.asyncio
async def test_read_humidity_fetches_generic_shape(monkeypatch):
    monkeypatch.setenv("MULTIACE_HUMIDITY_URL", "http://sensor.local/json")
    monkeypatch.setenv("MULTIACE_HUMIDITY_LABEL", "ACE Pro")
    async with respx.mock(base_url="http://sensor.local") as mock:
        mock.get("/json").respond(200, json={"humidity": 47.5, "temperature": 23.1})
        result = await server._read_humidity()
    assert result["configured"] is True
    assert result["ok"] is True
    assert result["humidity_pct"] == 47.5
    assert result["temp_c"] == 23.1
    assert result["label"] == "ACE Pro"


@pytest.mark.asyncio
async def test_read_humidity_fetches_home_assistant_shape(monkeypatch):
    monkeypatch.setenv("MULTIACE_HUMIDITY_URL",
                        "http://homeassistant.local:8123/api/states/sensor.ace_h")
    monkeypatch.setenv("MULTIACE_HUMIDITY_AUTH", "Bearer abc123")
    ha_payload = {
        "state": "55.3",
        "attributes": {"device_class": "humidity", "unit_of_measurement": "%"},
    }
    async with respx.mock(base_url="http://homeassistant.local:8123") as mock:
        route = mock.get("/api/states/sensor.ace_h").respond(200, json=ha_payload)
        result = await server._read_humidity()
    assert result["humidity_pct"] == 55.3
    # Auth header must be forwarded
    assert route.calls[0].request.headers["authorization"] == "Bearer abc123"


@pytest.mark.asyncio
async def test_read_humidity_with_explicit_path(monkeypatch):
    """When the JSON shape isn't a known one, the user supplies a dot-path."""
    monkeypatch.setenv("MULTIACE_HUMIDITY_URL", "http://sensor.local/api")
    monkeypatch.setenv("MULTIACE_HUMIDITY_HUM_PATH", "sensor.0.h")
    monkeypatch.setenv("MULTIACE_HUMIDITY_TEMP_PATH", "sensor.0.t")
    payload = {"sensor": [{"h": 33.3, "t": 24.1}]}
    async with respx.mock(base_url="http://sensor.local") as mock:
        mock.get("/api").respond(200, json=payload)
        result = await server._read_humidity()
    assert result["humidity_pct"] == 33.3
    assert result["temp_c"] == 24.1


@pytest.mark.asyncio
async def test_read_humidity_handles_non_numeric_value(monkeypatch):
    """If the resolved humidity is a string, coerce to float."""
    monkeypatch.setenv("MULTIACE_HUMIDITY_URL", "http://sensor.local/json")
    async with respx.mock(base_url="http://sensor.local") as mock:
        mock.get("/json").respond(200, json={"humidity": "47.2", "temperature": "23"})
        result = await server._read_humidity()
    assert result["humidity_pct"] == 47.2
    assert result["temp_c"] == 23.0


@pytest.mark.asyncio
async def test_read_humidity_handles_uncoercible_value(monkeypatch):
    """If the value is missing or non-numeric, return None (don't crash)."""
    monkeypatch.setenv("MULTIACE_HUMIDITY_URL", "http://sensor.local/json")
    async with respx.mock(base_url="http://sensor.local") as mock:
        mock.get("/json").respond(200, json={"humidity": "not a number"})
        result = await server._read_humidity()
    assert result["humidity_pct"] is None
    assert result["ok"] is True  # the fetch succeeded, the parse just couldn't coerce


@pytest.mark.asyncio
async def test_read_humidity_handles_http_error_gracefully(monkeypatch):
    monkeypatch.setenv("MULTIACE_HUMIDITY_URL", "http://sensor.local/json")
    async with respx.mock(base_url="http://sensor.local") as mock:
        mock.get("/json").respond(500)
        result = await server._read_humidity()
    assert result["configured"] is True
    assert result["ok"] is False
    assert "error" in result


@pytest.mark.asyncio
async def test_read_humidity_handles_connection_error(monkeypatch):
    import httpx
    monkeypatch.setenv("MULTIACE_HUMIDITY_URL", "http://sensor.local/json")
    async with respx.mock(base_url="http://sensor.local") as mock:
        mock.get("/json").mock(side_effect=httpx.ConnectError("nope"))
        result = await server._read_humidity()
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_read_humidity_caches_within_ttl(monkeypatch):
    """Two calls within the TTL window result in one upstream HTTP request."""
    monkeypatch.setenv("MULTIACE_HUMIDITY_URL", "http://sensor.local/json")
    async with respx.mock(base_url="http://sensor.local") as mock:
        route = mock.get("/json").respond(200, json={"humidity": 50})
        await server._read_humidity()
        await server._read_humidity()
        await server._read_humidity()
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_read_humidity_refetches_after_ttl(monkeypatch):
    monkeypatch.setenv("MULTIACE_HUMIDITY_URL", "http://sensor.local/json")
    monkeypatch.setattr(server, "_HUMIDITY_TTL_SEC", 0.0)
    async with respx.mock(base_url="http://sensor.local") as mock:
        route = mock.get("/json").respond(200, json={"humidity": 50})
        await server._read_humidity()
        await server._read_humidity()
    assert route.call_count == 2


# ---- _read_humidity_per_ace (Govee multi-MAC bridge fan-out) ----

@pytest.fixture(autouse=True)
def _clear_per_ace_cache():
    """Avoid cache bleed between per-ACE humidity tests."""
    server._HUMIDITY_PER_ACE_CACHE["data"] = None
    server._HUMIDITY_PER_ACE_CACHE["ts"] = 0.0
    yield


def test_derive_sensors_url_appends_s():
    assert server._derive_sensors_url("http://127.0.0.1:7127/sensor") \
        == "http://127.0.0.1:7127/sensors"


def test_derive_sensors_url_passthrough_when_not_sensor_suffix():
    assert server._derive_sensors_url("http://homeassistant/api/states/sensor.x") \
        == "http://homeassistant/api/states/sensor.x"


def test_derive_sensors_url_empty():
    assert server._derive_sensors_url("") == ""


@pytest.mark.asyncio
async def test_read_humidity_per_ace_returns_unconfigured_when_no_url(monkeypatch):
    monkeypatch.delenv("MULTIACE_HUMIDITY_URL", raising=False)
    result = await server._read_humidity_per_ace(device_count=2)
    assert result == [{"configured": False}, {"configured": False}]


@pytest.mark.asyncio
async def test_read_humidity_per_ace_orders_by_bridge_response(monkeypatch):
    monkeypatch.setenv("MULTIACE_HUMIDITY_URL", "http://127.0.0.1:7127/sensor")
    monkeypatch.setenv("MULTIACE_HUMIDITY_LABEL", "ACE Pro")
    sensors_payload = {
        "E8:76:C6:46:55:68": {
            "temperature": 27.14, "humidity": 42.7, "battery": 66,
            "rssi": -54, "name": "GVH5104_5568", "age_s": 1.2,
        },
        "E8:76:C4:06:69:29": {
            "temperature": 29.44, "humidity": 36.6, "battery": 29,
            "rssi": -64, "name": "GVH5104_6929", "age_s": 0.8,
        },
    }
    async with respx.mock(base_url="http://127.0.0.1:7127") as mock:
        mock.get("/sensors").respond(200, json=sensors_payload)
        result = await server._read_humidity_per_ace(device_count=2)
    assert len(result) == 2
    assert result[0]["humidity_pct"] == 42.7
    assert result[0]["temp_c"] == 27.14
    assert result[0]["mac"] == "E8:76:C6:46:55:68"
    assert result[0]["label"] == "ACE Pro 0"
    assert result[1]["humidity_pct"] == 36.6
    assert result[1]["mac"] == "E8:76:C4:06:69:29"
    assert result[1]["label"] == "ACE Pro 1"


@pytest.mark.asyncio
async def test_read_humidity_per_ace_pads_when_fewer_macs_than_aces(monkeypatch):
    monkeypatch.setenv("MULTIACE_HUMIDITY_URL", "http://127.0.0.1:7127/sensor")
    async with respx.mock(base_url="http://127.0.0.1:7127") as mock:
        mock.get("/sensors").respond(200, json={
            "AA:BB:CC:DD:EE:FF": {
                "temperature": 24.0, "humidity": 40.0, "battery": 90,
                "rssi": -50, "name": "GVH5104_ONE", "age_s": 0.5,
            },
        })
        result = await server._read_humidity_per_ace(device_count=3)
    assert len(result) == 3
    assert result[0]["configured"] is True and result[0]["ok"] is True
    assert result[1] == {"configured": False}
    assert result[2] == {"configured": False}


@pytest.mark.asyncio
async def test_read_humidity_per_ace_handles_warming_up_device(monkeypatch):
    """Bridge returns null payload for a MAC it hasn't seen yet — must surface
    as configured+warming_up so the UI can render a placeholder."""
    monkeypatch.setenv("MULTIACE_HUMIDITY_URL", "http://127.0.0.1:7127/sensor")
    async with respx.mock(base_url="http://127.0.0.1:7127") as mock:
        mock.get("/sensors").respond(200, json={"AA:BB:CC:DD:EE:FF": None})
        result = await server._read_humidity_per_ace(device_count=1)
    assert result[0]["configured"] is True
    assert result[0]["ok"] is False
    assert result[0].get("warming_up") is True


@pytest.mark.asyncio
async def test_read_humidity_per_ace_returns_error_payload_on_fetch_failure(monkeypatch):
    monkeypatch.setenv("MULTIACE_HUMIDITY_URL", "http://127.0.0.1:7127/sensor")
    async with respx.mock(base_url="http://127.0.0.1:7127") as mock:
        mock.get("/sensors").respond(500, json={})
        result = await server._read_humidity_per_ace(device_count=2)
    assert all(e["configured"] is True and e["ok"] is False for e in result)


@pytest.mark.asyncio
async def test_read_humidity_per_ace_uses_explicit_sensors_url(monkeypatch):
    """MULTIACE_HUMIDITY_SENSORS_URL overrides the /sensor → /sensors derivation."""
    monkeypatch.setenv("MULTIACE_HUMIDITY_URL", "http://homeassistant/api/states/sensor.x")
    monkeypatch.setenv("MULTIACE_HUMIDITY_SENSORS_URL",
                       "http://127.0.0.1:7127/sensors")
    async with respx.mock(base_url="http://127.0.0.1:7127") as mock:
        mock.get("/sensors").respond(200, json={
            "AA:BB:CC:DD:EE:FF": {
                "temperature": 22.0, "humidity": 30.0, "battery": 80,
                "rssi": -55, "name": "GVH5104_X", "age_s": 0.1,
            },
        })
        result = await server._read_humidity_per_ace(device_count=1)
    assert result[0]["humidity_pct"] == 30.0


@pytest.mark.asyncio
async def test_autodry_tick_specializes_humidity_per_ace(monkeypatch, tmp_path):
    """tick_one_ace should override Inputs.humidity_* with per-ACE entry before
    invoking tick_fsm — otherwise ACE 1's FSM would evaluate against ACE 0's
    sensor reading."""
    from pathlib import Path
    from multiace_web.autodryer import (
        AutoDryer, AutodryManager, PerAceConfig, FSMSnapshot, FSMState, Inputs,
    )
    import multiace_web.autodryer as ad_mod

    captured: dict = {}
    async def emit(_p):
        pass
    class AnnouncementsStub:
        async def post(self, **kw): return None
        async def dismiss(self, _eid): return None

    def fetch_inputs():
        return Inputs(
            active_device=1,
            head_source={"0": {"ace": 1, "slot": 0, "type": "PLA", "color": "000000"}},
            swap_in_progress=False,
            humidity_ok=True, humidity_pct=10.0,  # primary "dry" reading
            cavity_temp_c=25.0, klipper_print_state="standby", dryer_status="stop",
            user_profiles=None,
            humidity_per_ace=[
                {"configured": False},
                {"configured": True, "ok": True, "humidity_pct": 55.0},  # ACE 1 is wet
            ],
        )

    mgr = AutodryManager.with_defaults(device_count=2)
    mgr.get(1).config = PerAceConfig(enabled=True, target_pct=15, hysteresis_pp=5,
                                      default_filament_type="PLA")
    mgr.get(1).snapshot = FSMSnapshot(state=FSMState.WATCHING)

    real_tick = ad_mod.tick_fsm
    def spy(*args, **kwargs):
        captured["inputs"] = args[2]  # tick_fsm(persisted, eph, inputs, now_ts, ...)
        return real_tick(*args, **kwargs)
    monkeypatch.setattr(ad_mod, "tick_fsm", spy)

    state_path = tmp_path / "autodry_state.json"
    ad = AutoDryer(state_path=state_path,
                   inputs_fetcher=fetch_inputs, emit_event=emit,
                   announcements=AnnouncementsStub(), tick_sec=0,
                   manager=mgr)
    await ad.tick_one_ace(1, now_ts=1779_010_000.0)
    used = captured["inputs"]
    assert used.humidity_ok is True
    assert used.humidity_pct == 55.0, (
        f"tick_one_ace did not specialize humidity for ACE 1; got {used.humidity_pct}"
    )


@pytest.mark.asyncio
async def test_autodry_tick_overrides_active_device_and_sip(monkeypatch, tmp_path):
    """tick_one_ace must override Inputs.active_device + swap_in_progress to
    reflect the post-switch state — the poller calls us right after the
    Moonraker gcode completes but before the audit-log tailer has applied
    the SWITCH audit, so reading those fields from CurrentState would race
    with the tailer and leave them stale (target_active=False forever)."""
    from multiace_web.autodryer import (
        AutoDryer, AutodryManager, PerAceConfig, FSMSnapshot, FSMState, Inputs,
    )
    import multiace_web.autodryer as ad_mod

    captured: dict = {}
    async def emit(_p):
        pass
    class AnnouncementsStub:
        async def post(self, **kw): return None
        async def dismiss(self, _eid): return None

    def fetch_inputs():
        # Simulate the stale-tailer race: state still thinks we're on ACE 0
        # with swap_in_progress True from the previous SWITCH audit.
        return Inputs(
            active_device=0,
            head_source={"0": {"ace": 1, "slot": 0, "type": "PLA", "color": "000000"}},
            swap_in_progress=True,        # stale — actual swap finished
            humidity_ok=True, humidity_pct=42.0,
            cavity_temp_c=25.0, klipper_print_state="standby", dryer_status="stop",
            user_profiles=None,
            humidity_per_ace=None,
        )

    mgr = AutodryManager.with_defaults(device_count=2)
    mgr.get(1).config = PerAceConfig(enabled=True, target_pct=15, hysteresis_pp=5,
                                      default_filament_type="PLA")
    mgr.get(1).snapshot = FSMSnapshot(state=FSMState.IDLE)

    real_tick = ad_mod.tick_fsm
    def spy(*args, **kwargs):
        captured["inputs"] = args[2]
        return real_tick(*args, **kwargs)
    monkeypatch.setattr(ad_mod, "tick_fsm", spy)

    ad = AutoDryer(state_path=tmp_path / "autodry_state.json",
                   inputs_fetcher=fetch_inputs, emit_event=emit,
                   announcements=AnnouncementsStub(), tick_sec=0,
                   manager=mgr)
    await ad.tick_one_ace(1, now_ts=1779_010_000.0)
    used = captured["inputs"]
    assert used.active_device == 1, (
        f"tick_one_ace did not override active_device; FSM sees stale value "
        f"{used.active_device}, can_be_armed will be False and FSM stays IDLE"
    )
    assert used.swap_in_progress is False, (
        "tick_one_ace did not override swap_in_progress; sip=True blocks "
        "can_be_armed"
    )
