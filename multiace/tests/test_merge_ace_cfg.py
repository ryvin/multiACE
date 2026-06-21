"""Tests for tools/merge_ace_cfg.py — preserves user [ace]/[ace N] scalar
values when refreshing to a new ace.cfg.default shipped by an update."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import merge_ace_cfg as m


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def _merge(tmp_path, old_text, new_text):
    old = _write(tmp_path, "old.cfg", old_text)
    new = _write(tmp_path, "new.default", new_text)
    out = str(tmp_path / "out.cfg")
    notes, orphans = m.merge(old, new, out)
    return (tmp_path / "out.cfg").read_text(encoding="utf-8"), notes, orphans


def test_preserves_changed_scalar_value(tmp_path):
    old = "[ace]\ndefault_park_retract_length_mm: 700\n"
    new = "[ace]\ndefault_park_retract_length_mm: 500\nfoo: bar\n"
    out, notes, orphans = _merge(tmp_path, old, new)
    assert "default_park_retract_length_mm: 700" in out
    assert "foo: bar" in out
    assert any("default_park_retract_length_mm" in n and "500 -> 700" in n for n in notes)


def test_unchanged_value_is_copied_without_note(tmp_path):
    old = "[ace]\nbaud: 115200\n"
    new = "[ace]\nbaud: 115200\n"
    out, notes, orphans = _merge(tmp_path, old, new)
    assert "baud: 115200" in out
    assert notes == []


def test_uncomments_user_overridden_key(tmp_path):
    old = "[ace]\nfa_debug: true\n"
    new = "[ace]\n#fa_debug: false\n"
    out, notes, orphans = _merge(tmp_path, old, new)
    assert "fa_debug: true" in out
    assert "#fa_debug: false" not in out
    assert any("was commented" in n for n in notes)


def test_sections_outside_ace_are_copied_verbatim(tmp_path):
    # A macro body in the new default must survive unchanged, and the user's
    # value for a key in a non-[ace] section must NOT be pulled in.
    old = "[gcode_macro FOO]\nvariable_x: 9\n"
    new = "[gcode_macro FOO]\nvariable_x: 1\ngcode:\n  G28\n"
    out, notes, orphans = _merge(tmp_path, old, new)
    assert "variable_x: 1" in out          # new default wins outside [ace]
    assert "  G28" in out                  # macro body preserved
    assert notes == []


def test_indented_lines_in_ace_untouched(tmp_path):
    old = "[ace]\nserial: /dev/ttyACM0\n"
    new = "[ace]\nserial: /dev/ttyACM9\n  continuation_should_stay\n"
    out, notes, orphans = _merge(tmp_path, old, new)
    assert "serial: /dev/ttyACM0" in out     # user value preserved
    assert "  continuation_should_stay" in out


def test_appends_missing_ace_section(tmp_path):
    old = "[ace 1]\nserial: /dev/ttyACM1\n"
    new = "[ace]\nserial: /dev/ttyACM0\n"
    out, notes, orphans = _merge(tmp_path, old, new)
    assert "[ace 1]" in out
    assert "serial: /dev/ttyACM1" in out
    assert any("appended" in n for n in notes)


def test_orphan_key_reported_not_written(tmp_path):
    old = "[ace]\nlegacy_only_key: 1\n"
    new = "[ace]\nserial: /dev/ttyACM0\n"
    out, notes, orphans = _merge(tmp_path, old, new)
    assert "legacy_only_key" not in out
    assert any("legacy_only_key" in o for o in orphans)


def test_main_requires_three_paths(tmp_path):
    assert m.main(["merge_ace_cfg.py", "only", "two"]) == 1
