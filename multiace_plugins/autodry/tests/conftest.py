# License: GPL-3.0
import pytest
from fastapi.testclient import TestClient
from autodry_plugin.config import Config
from autodry_plugin.app import create_app


@pytest.fixture
def cfg(tmp_path):
    return Config(
        moonraker_url="http://mr.test",
        multiace_url="http://ma.test",
        port=8090,
        round_robin=False,
        tick_sec=0,  # disable the background tick loop under test
        state_path=tmp_path / ".autodry_state.json",
        default_target_pct=15,
        default_temp_c=55,
        default_duration_min=240,
        hysteresis_pp=5,
        cooldown_min=30,
        debounce_required=3,
        max_run_min=720,
        daily_duty_max_min=1080,
        min_delta_pct=3,
    )


@pytest.fixture
def client(cfg):
    return TestClient(create_app(cfg))
