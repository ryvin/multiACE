# License: GPL-3.0
import pytest
from autodry_plugin.config import load_config

_ALL_VARS = (
    "MOONRAKER_URL", "MULTIACE_URL", "AUTODRY_PLUGIN_PORT", "AUTODRY_ROUND_ROBIN",
    "AUTODRY_TICK_SEC", "AUTODRY_STATE_PATH", "AUTODRY_DEFAULT_TARGET_PCT",
    "AUTODRY_DEFAULT_TEMP_C", "AUTODRY_DEFAULT_DURATION_MIN", "AUTODRY_HYSTERESIS_PP",
    "AUTODRY_COOLDOWN_MIN", "AUTODRY_DEBOUNCE_REQUIRED", "AUTODRY_MAX_RUN_MIN",
    "AUTODRY_DAILY_DUTY_MAX_MIN", "AUTODRY_MIN_DELTA_PCT",
    "MULTIACE_HUMIDITY_URL", "MULTIACE_HUMIDITY_SENSORS_URL",
    "MULTIACE_HUMIDITY_AUTH", "MULTIACE_HUMIDITY_LABEL",
)


def _clear_env(monkeypatch):
    for var in _ALL_VARS:
        monkeypatch.delenv(var, raising=False)


def test_load_config_defaults(monkeypatch):
    _clear_env(monkeypatch)
    cfg = load_config()
    assert cfg.moonraker_url == "http://127.0.0.1:7125"
    assert cfg.multiace_url == "http://127.0.0.1:7126"
    assert cfg.port == 8090
    assert cfg.round_robin is False
    assert cfg.tick_sec == 30
    assert cfg.default_target_pct == 15
    assert cfg.default_temp_c == 55
    assert cfg.default_duration_min == 240
    assert cfg.cooldown_min == 30


def test_load_config_reads_env(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("MOONRAKER_URL", "http://mr.local:7125")
    monkeypatch.setenv("MULTIACE_URL", "http://ma.local:7126")
    monkeypatch.setenv("AUTODRY_PLUGIN_PORT", "8095")
    monkeypatch.setenv("AUTODRY_ROUND_ROBIN", "true")
    monkeypatch.setenv("AUTODRY_TICK_SEC", "10")
    cfg = load_config()
    assert cfg.moonraker_url == "http://mr.local:7125"
    assert cfg.multiace_url == "http://ma.local:7126"
    assert cfg.port == 8095
    assert cfg.round_robin is True
    assert cfg.tick_sec == 10


@pytest.mark.parametrize("raw,expected", [
    ("1", True), ("true", True), ("YES", True), ("on", True),
    ("0", False), ("false", False), ("", False), ("nope", False),
])
def test_load_config_round_robin_bool_parsing(monkeypatch, raw, expected):
    _clear_env(monkeypatch)
    monkeypatch.setenv("AUTODRY_ROUND_ROBIN", raw)
    assert load_config().round_robin is expected


@pytest.mark.parametrize("bad_port", ["8088", "8099", "0", "-1"])
def test_load_config_port_out_of_range_fails_fast(monkeypatch, bad_port):
    _clear_env(monkeypatch)
    monkeypatch.setenv("AUTODRY_PLUGIN_PORT", bad_port)
    with pytest.raises(ValueError):
        load_config()


def test_load_config_negative_tick_sec_fails_fast(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("AUTODRY_TICK_SEC", "-5")
    with pytest.raises(ValueError):
        load_config()


@pytest.mark.parametrize("bad_pct", ["-1", "101"])
def test_load_config_bad_default_target_pct_fails_fast(monkeypatch, bad_pct):
    _clear_env(monkeypatch)
    monkeypatch.setenv("AUTODRY_DEFAULT_TARGET_PCT", bad_pct)
    with pytest.raises(ValueError):
        load_config()


def test_load_config_malformed_port_raises_value_error(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("AUTODRY_PLUGIN_PORT", "not-a-number")
    with pytest.raises(ValueError):
        load_config()


def test_load_config_humidity_bridge_defaults_to_unconfigured(monkeypatch):
    _clear_env(monkeypatch)
    cfg = load_config()
    assert cfg.humidity_url == ""
    assert cfg.humidity_sensors_url == ""
    assert cfg.humidity_auth == ""
    assert cfg.humidity_label == ""


def test_load_config_reads_humidity_bridge_env(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("MULTIACE_HUMIDITY_URL", "http://govee.local:8100/sensor")
    monkeypatch.setenv("MULTIACE_HUMIDITY_SENSORS_URL", "http://govee.local:8100/sensors")
    monkeypatch.setenv("MULTIACE_HUMIDITY_AUTH", "Bearer abc123")
    monkeypatch.setenv("MULTIACE_HUMIDITY_LABEL", "Dryer")
    cfg = load_config()
    assert cfg.humidity_url == "http://govee.local:8100/sensor"
    assert cfg.humidity_sensors_url == "http://govee.local:8100/sensors"
    assert cfg.humidity_auth == "Bearer abc123"
    assert cfg.humidity_label == "Dryer"
