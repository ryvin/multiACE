# License: GPL-3.0
import httpx
import respx

from autodry_plugin.humidity_bridge import HumidityBridge, _derive_sensors_url


def test_derive_sensors_url_replaces_trailing_sensor():
    assert _derive_sensors_url("http://govee.test/sensor") == "http://govee.test/sensors"


def test_derive_sensors_url_leaves_non_sensor_url_unchanged():
    assert _derive_sensors_url("http://govee.test/api/v1/humidity") == "http://govee.test/api/v1/humidity"


def test_derive_sensors_url_empty_input():
    assert _derive_sensors_url("") == ""


async def test_unset_url_returns_all_unconfigured():
    bridge = HumidityBridge(humidity_url="")
    out = await bridge.fetch_per_ace(3)
    assert len(out) == 3
    for i in range(3):
        assert out[i].configured is False
        assert out[i].ok is False


async def test_zero_device_count_returns_empty():
    bridge = HumidityBridge(humidity_url="http://govee.test/sensor")
    out = await bridge.fetch_per_ace(0)
    assert out == {}


@respx.mock
async def test_positional_mac_to_ace_mapping():
    route = respx.get("http://govee.test/sensors").mock(
        return_value=httpx.Response(200, json={
            "AA:AA:AA:AA:AA:01": {"humidity": 41.5, "temperature": 23.1, "battery": 88, "rssi": -55, "name": "Bay A", "age_s": 2},
            "AA:AA:AA:AA:AA:02": {"humidity": 33.0, "temperature": 22.0, "battery": 91, "rssi": -60, "name": "Bay B", "age_s": 4},
        }))
    bridge = HumidityBridge(humidity_url="http://govee.test/sensor")
    out = await bridge.fetch_per_ace(3)

    assert route.called
    assert out[0].configured is True
    assert out[0].ok is True
    assert out[0].humidity_pct == 41.5
    assert out[0].temp_c == 23.1
    assert out[0].mac == "AA:AA:AA:AA:AA:01"

    assert out[1].configured is True
    assert out[1].ok is True
    assert out[1].humidity_pct == 33.0
    assert out[1].mac == "AA:AA:AA:AA:AA:02"

    # Fewer MACs than ACEs — ACE 2 pads with "not configured".
    assert out[2].configured is False


@respx.mock
async def test_derives_sensors_url_from_single_sensor_url_when_not_explicit():
    route = respx.get("http://govee.test/sensors").mock(
        return_value=httpx.Response(200, json={"AA:BB": {"humidity": 10.0, "temperature": 20.0}}))
    bridge = HumidityBridge(humidity_url="http://govee.test/sensor")
    await bridge.fetch_per_ace(1)
    assert route.called


@respx.mock
async def test_explicit_sensors_url_overrides_derivation():
    route = respx.get("http://govee.test/custom-sensors-path").mock(
        return_value=httpx.Response(200, json={"AA:BB": {"humidity": 10.0, "temperature": 20.0}}))
    bridge = HumidityBridge(
        humidity_url="http://govee.test/sensor",
        sensors_url="http://govee.test/custom-sensors-path",
    )
    await bridge.fetch_per_ace(1)
    assert route.called


@respx.mock
async def test_auth_header_sent_when_configured():
    route = respx.get("http://govee.test/sensors").mock(
        return_value=httpx.Response(200, json={"AA:BB": {"humidity": 10.0, "temperature": 20.0}}))
    bridge = HumidityBridge(humidity_url="http://govee.test/sensor", auth="Bearer secrettoken")
    await bridge.fetch_per_ace(1)
    assert route.calls.last.request.headers["Authorization"] == "Bearer secrettoken"


@respx.mock
async def test_no_auth_header_when_unconfigured():
    route = respx.get("http://govee.test/sensors").mock(
        return_value=httpx.Response(200, json={"AA:BB": {"humidity": 10.0, "temperature": 20.0}}))
    bridge = HumidityBridge(humidity_url="http://govee.test/sensor")
    await bridge.fetch_per_ace(1)
    assert "Authorization" not in route.calls.last.request.headers


@respx.mock
async def test_fetch_error_marks_every_slot_configured_but_not_ok():
    respx.get("http://govee.test/sensors").mock(side_effect=httpx.ConnectError("refused"))
    bridge = HumidityBridge(humidity_url="http://govee.test/sensor")
    out = await bridge.fetch_per_ace(3)
    for i in range(3):
        assert out[i].configured is True
        assert out[i].ok is False
        assert out[i].error is not None
        assert "ConnectError" in out[i].error


@respx.mock
async def test_ttl_cache_hit_avoids_second_http_call():
    route = respx.get("http://govee.test/sensors").mock(
        return_value=httpx.Response(200, json={"AA:BB": {"humidity": 15.0, "temperature": 21.0}}))
    bridge = HumidityBridge(humidity_url="http://govee.test/sensor", ttl_sec=30.0)

    out1 = await bridge.fetch_per_ace(1)
    out2 = await bridge.fetch_per_ace(1)

    assert route.call_count == 1
    assert out1[0].humidity_pct == 15.0
    assert out2[0].humidity_pct == 15.0


@respx.mock
async def test_ttl_cache_expires_and_refetches():
    route = respx.get("http://govee.test/sensors").mock(
        return_value=httpx.Response(200, json={"AA:BB": {"humidity": 15.0, "temperature": 21.0}}))
    bridge = HumidityBridge(humidity_url="http://govee.test/sensor", ttl_sec=0.0)

    await bridge.fetch_per_ace(1)
    await bridge.fetch_per_ace(1)

    assert route.call_count == 2


@respx.mock
async def test_warming_up_when_payload_not_a_dict():
    respx.get("http://govee.test/sensors").mock(
        return_value=httpx.Response(200, json={"AA:BB": None}))
    bridge = HumidityBridge(humidity_url="http://govee.test/sensor")
    out = await bridge.fetch_per_ace(1)
    assert out[0].configured is True
    assert out[0].ok is False
    assert out[0].warming_up is True
    assert out[0].mac == "AA:BB"


def test_label_prefix_defaults_to_ace():
    bridge = HumidityBridge(humidity_url="http://govee.test/sensor")
    assert bridge._label_prefix == "ACE"  # noqa: SLF001 - white-box default check


def test_label_prefix_uses_configured_value():
    bridge = HumidityBridge(humidity_url="http://govee.test/sensor", label="Dryer")
    assert bridge._label_prefix == "Dryer"  # noqa: SLF001
