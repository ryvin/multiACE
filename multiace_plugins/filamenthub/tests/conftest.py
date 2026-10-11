# License: GPL-3.0
import pytest
from fastapi.testclient import TestClient
from filamenthub_plugin.config import Config
from filamenthub_plugin.app import create_app

@pytest.fixture
def cfg(tmp_path_factory):
    return Config(
        filamenthub_url="http://fh.test",
        printer_id="davinci-u1",
        multiace_url="http://ma.test",
        port=8089,
        ace_state_url="http://fh.test/fleet/api/ace-state",
        desired_state_path=str(tmp_path_factory.mktemp("desired") / "d.json"),
    )

@pytest.fixture
def client(cfg):
    return TestClient(create_app(cfg))