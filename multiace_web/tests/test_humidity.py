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
