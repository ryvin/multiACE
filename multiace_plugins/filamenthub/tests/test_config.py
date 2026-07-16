# License: GPL-3.0
import os
from filamenthub_plugin.config import load_config

def test_load_config_reads_env(monkeypatch):
    monkeypatch.setenv("FILAMENTHUB_URL", "http://fh.local")
    monkeypatch.setenv("MULTIACE_PRINTER_ID", "davinci-u1")
    monkeypatch.delenv("MULTIACE_URL", raising=False)
    monkeypatch.delenv("FILAMENTHUB_PLUGIN_PORT", raising=False)
    cfg = load_config()
    assert cfg.filamenthub_url == "http://fh.local"
    assert cfg.printer_id == "davinci-u1"
    assert cfg.multiace_url == "http://127.0.0.1:7126"   # default
    assert cfg.port == 8089                               # default


def test_ace_state_url_defaults_from_filamenthub_url(monkeypatch):
    monkeypatch.setenv("FILAMENTHUB_URL", "https://fh.example.com/")
    monkeypatch.setenv("MULTIACE_PRINTER_ID", "u1-1")
    monkeypatch.delenv("FILAMENTHUB_ACE_STATE_URL", raising=False)
    from filamenthub_plugin.config import load_config
    cfg = load_config()
    assert cfg.ace_state_url == "https://fh.example.com/fleet/api/ace-state"


def test_ace_state_url_explicit_override(monkeypatch):
    monkeypatch.setenv("FILAMENTHUB_URL", "https://fh.example.com")
    monkeypatch.setenv("MULTIACE_PRINTER_ID", "u1-1")
    monkeypatch.setenv("FILAMENTHUB_ACE_STATE_URL", "http://127.0.0.1:7127/api/ace-state")
    from filamenthub_plugin.config import load_config
    cfg = load_config()
    assert cfg.ace_state_url == "http://127.0.0.1:7127/api/ace-state"


def test_desired_state_path_default(monkeypatch):
    monkeypatch.setenv("FILAMENTHUB_URL", "http://fh.test")
    monkeypatch.setenv("MULTIACE_PRINTER_ID", "u1-1")
    monkeypatch.delenv("FILAMENTHUB_DESIRED_PATH", raising=False)
    from filamenthub_plugin.config import load_config
    assert load_config().desired_state_path == ".filamenthub_desired.json"


def test_desired_state_path_override(monkeypatch):
    monkeypatch.setenv("FILAMENTHUB_URL", "http://fh.test")
    monkeypatch.setenv("MULTIACE_PRINTER_ID", "u1-1")
    monkeypatch.setenv("FILAMENTHUB_DESIRED_PATH", "/tmp/d.json")
    from filamenthub_plugin.config import load_config
    assert load_config().desired_state_path == "/tmp/d.json"