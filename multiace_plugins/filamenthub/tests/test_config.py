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
    assert load_config().desired_state_path == \
        "/userdata/filamenthub-plugin/state/desired.json"


def test_desired_state_path_override(monkeypatch):
    monkeypatch.setenv("FILAMENTHUB_URL", "http://fh.test")
    monkeypatch.setenv("MULTIACE_PRINTER_ID", "u1-1")
    monkeypatch.setenv("FILAMENTHUB_DESIRED_PATH", "/tmp/d.json")
    from filamenthub_plugin.config import load_config
    assert load_config().desired_state_path == "/tmp/d.json"


_ALL_VARS = ("FILAMENTHUB_URL", "MULTIACE_PRINTER_ID", "MULTIACE_URL",
             "FILAMENTHUB_PLUGIN_PORT", "FILAMENTHUB_ACE_STATE_URL",
             "FILAMENTHUB_DESIRED_PATH")


def test_deploy_defaults_when_env_unset(monkeypatch):
    # A bare `python -m filamenthub_plugin` on the printer must target u1-b
    # on the LAN FilamentHub and persist desired state on /userdata.
    for var in _ALL_VARS:
        monkeypatch.delenv(var, raising=False)
    cfg = load_config()
    assert cfg.filamenthub_url == "http://192.168.1.174:3000"
    assert cfg.printer_id == "u1-b"
    assert cfg.multiace_url == "http://127.0.0.1:7126"
    assert cfg.port == 8089
    assert cfg.ace_state_url == "http://192.168.1.174:3000/fleet/api/ace-state"
    assert cfg.desired_state_path == "/userdata/filamenthub-plugin/state/desired.json"


def _init_script_default(text: str, var: str) -> str:
    marker = "${" + var + ":-"
    start = text.find(marker)
    assert start != -1, f"{var} default missing from S66filamenthub-plugin"
    start += len(marker)
    return text[start:text.index("}", start)]


def test_init_script_defaults_match_config():
    # Guard against drift between the BusyBox init script and config.py.
    import pathlib
    from filamenthub_plugin import config as cfgmod
    s66 = (pathlib.Path(__file__).resolve().parents[1]
           / "install" / "S66filamenthub-plugin").read_text()
    assert _init_script_default(s66, "FILAMENTHUB_URL") == cfgmod.DEFAULT_FILAMENTHUB_URL
    assert _init_script_default(s66, "MULTIACE_PRINTER_ID") == cfgmod.DEFAULT_PRINTER_ID
    assert _init_script_default(s66, "MULTIACE_URL") == cfgmod.DEFAULT_MULTIACE_URL
    assert _init_script_default(s66, "FILAMENTHUB_PLUGIN_PORT") == str(cfgmod.DEFAULT_PORT)
    assert _init_script_default(s66, "FILAMENTHUB_DESIRED_PATH") == cfgmod.DEFAULT_DESIRED_PATH