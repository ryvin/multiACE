import pytest
from fastapi.testclient import TestClient
from filamenthub_plugin.config import Config
from filamenthub_plugin.app import create_app

@pytest.fixture
def cfg():
    return Config(
        filamenthub_url="http://fh.test",
        printer_id="davinci-u1",
        multiace_url="http://ma.test",
        port=8089,
    )

@pytest.fixture
def client(cfg):
    return TestClient(create_app(cfg))
