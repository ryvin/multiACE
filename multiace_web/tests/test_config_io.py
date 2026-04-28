from pathlib import Path

from multiace_web.config_io import read_ace_config, write_ace_config


SAMPLE_CFG = """
[save_variables]
filename: /home/lava/printer_data/config/extended/multiace/ace_vars.cfg

[ace]

# ace_device_count: 3

# Logging
state_debug: true
usb_debug: true

# Serial
baud: 115200

# Feed/retract
feed_speed: 80
retract_speed: 30
retract_length: 700        # 80cm tubes
load_length: 1500          # bumped from 880

# Per-toolhead overrides
# load_length_0: 2100
# load_length_1: 2050

dryer_temp: 55
dryer_duration: 240
"""


def test_read_ace_config_returns_known_keys(tmp_path: Path):
    cfg = tmp_path / "ace.cfg"
    cfg.write_text(SAMPLE_CFG)
    values = read_ace_config(cfg)
    assert values["feed_speed"] == "80"
    assert values["retract_speed"] == "30"
    assert values["retract_length"] == "700"
    assert values["load_length"] == "1500"
    assert values["dryer_temp"] == "55"
    assert values["state_debug"] == "true"
    assert "ace_device_count" not in values
    assert "load_length_0" not in values


def test_write_ace_config_updates_only_specified_keys(tmp_path: Path):
    cfg = tmp_path / "ace.cfg"
    cfg.write_text(SAMPLE_CFG)
    write_ace_config(cfg, {"load_length": "2000", "feed_speed": "100"})
    text = cfg.read_text()
    assert "load_length: 2000" in text
    assert "feed_speed: 100" in text
    assert "retract_speed: 30" in text
    assert "dryer_temp: 55" in text
    assert "# 80cm tubes" in text or "# bumped" in text


def test_write_ace_config_creates_atomic_backup(tmp_path: Path):
    cfg = tmp_path / "ace.cfg"
    cfg.write_text(SAMPLE_CFG)
    write_ace_config(cfg, {"feed_speed": "100"})
    backup = cfg.with_suffix(".cfg.bak")
    assert backup.exists()
    assert "feed_speed: 80" in backup.read_text()


def test_write_ace_config_appends_unknown_key(tmp_path: Path):
    cfg = tmp_path / "ace.cfg"
    cfg.write_text(SAMPLE_CFG)
    write_ace_config(cfg, {"new_per_tool_setting": "42"})
    text = cfg.read_text()
    assert "new_per_tool_setting: 42" in text
