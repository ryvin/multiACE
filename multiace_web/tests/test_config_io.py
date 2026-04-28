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

[gcode_macro SET_ACE_MODE]
description: Switch ACE mode. Usage: SET_ACE_MODE MODE=multi
gcode:
    {% set mode = params.MODE|default('multi') %}
    SET_GCODE_VARIABLE MACRO=_ACE_VARS VARIABLE=mode VALUE='"{mode}"'
"""


def test_read_ace_config_returns_only_ace_section_keys(tmp_path: Path):
    cfg = tmp_path / "ace.cfg"
    cfg.write_text(SAMPLE_CFG)
    values = read_ace_config(cfg)
    assert values["feed_speed"] == "80"
    assert values["retract_speed"] == "30"
    assert values["retract_length"] == "700"
    assert values["load_length"] == "1500"
    assert values["dryer_temp"] == "55"
    assert values["state_debug"] == "true"
    # Comments not returned
    assert "ace_device_count" not in values
    assert "load_length_0" not in values
    # Other sections must NOT leak in
    assert "filename" not in values  # from [save_variables]
    assert "description" not in values  # from [gcode_macro]
    assert "gcode" not in values  # from [gcode_macro]


def test_write_ace_config_updates_only_ace_section(tmp_path: Path):
    cfg = tmp_path / "ace.cfg"
    cfg.write_text(SAMPLE_CFG)
    write_ace_config(cfg, {"load_length": "2000", "feed_speed": "100"})
    text = cfg.read_text()
    assert "load_length: 2000" in text
    assert "feed_speed: 100" in text
    # Inline comment preserved on a modified line (load_length had `# bumped from 880`)
    assert "# bumped from 880" in text
    # Other unmodified lines preserved
    assert "retract_speed: 30" in text
    assert "dryer_temp: 55" in text
    # Untouched [save_variables] and [gcode_macro] content survives
    assert "[save_variables]" in text
    assert "filename: /home/lava/printer_data" in text
    assert "[gcode_macro SET_ACE_MODE]" in text
    assert "Switch ACE mode" in text


def test_write_ace_config_does_not_clobber_other_sections(tmp_path: Path):
    """Even if updates contain a key that exists in another section, that section is untouched."""
    cfg = tmp_path / "ace.cfg"
    cfg.write_text(SAMPLE_CFG)
    # 'filename' exists under [save_variables]; 'description' under [gcode_macro]
    write_ace_config(cfg, {"filename": "evil-value", "description": "evil"})
    text = cfg.read_text()
    # The original values in their respective sections are untouched
    assert "filename: /home/lava/printer_data" in text
    assert "description: Switch ACE mode" in text
    # The keys ARE inserted (since no matching key in [ace], they go to [ace] section as new entries)
    assert "filename: evil-value" in text
    assert "description: evil" in text


def test_write_ace_config_creates_atomic_backup(tmp_path: Path):
    cfg = tmp_path / "ace.cfg"
    cfg.write_text(SAMPLE_CFG)
    write_ace_config(cfg, {"feed_speed": "100"})
    backup = cfg.with_suffix(".cfg.bak")
    assert backup.exists()
    assert "feed_speed: 80" in backup.read_text()


def test_write_ace_config_appends_unknown_key_inside_ace_section(tmp_path: Path):
    cfg = tmp_path / "ace.cfg"
    cfg.write_text(SAMPLE_CFG)
    write_ace_config(cfg, {"new_per_tool_setting": "42"})
    text = cfg.read_text()
    assert "new_per_tool_setting: 42" in text
    # The new key must be inserted BEFORE [gcode_macro SET_ACE_MODE], not after it
    new_key_pos = text.find("new_per_tool_setting:")
    macro_pos = text.find("[gcode_macro SET_ACE_MODE]")
    assert new_key_pos > 0 and macro_pos > 0
    assert new_key_pos < macro_pos
