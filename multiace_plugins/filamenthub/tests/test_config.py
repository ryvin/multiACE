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