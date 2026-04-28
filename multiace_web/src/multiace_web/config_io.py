"""Read/write ace.cfg preserving comments and formatting.

ace.cfg is a Klipper-style INI file with `key: value` lines (note the colon, not
equals). We preserve all formatting except for the specific keys we update.
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

# Matches a non-commented `key: value` line under [ace], capturing key and value.
# Allows leading whitespace, optional inline comment after the value.
_KV_RE = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*([^#\n]*?)\s*(?:#.*)?$")


def read_ace_config(path: Path) -> dict[str, str]:
    """Return all uncommented key/value pairs from ace.cfg as a flat dict."""
    text = path.read_text()
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        if raw_line.lstrip().startswith("#"):
            continue
        m = _KV_RE.match(raw_line.strip())
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if val == "":
            continue
        values[key] = val
    return values


def write_ace_config(path: Path, updates: dict[str, str]) -> None:
    """Update specified keys in ace.cfg, preserving formatting and comments.

    Writes atomically: writes to tmp file, fsyncs, renames over original.
    Saves a .bak copy of the previous content first.
    """
    text = path.read_text()
    backup_path = path.with_suffix(path.suffix + ".bak")
    backup_path.write_text(text)

    lines = text.splitlines(keepends=True)
    keys_seen: set[str] = set()
    new_lines: list[str] = []
    for raw_line in lines:
        if raw_line.lstrip().startswith("#"):
            new_lines.append(raw_line)
            continue
        stripped = raw_line.strip()
        m = _KV_RE.match(stripped)
        if not m or m.group(1) not in updates:
            new_lines.append(raw_line)
            continue
        key = m.group(1)
        new_val = updates[key]
        keys_seen.add(key)
        leading_ws = raw_line[: len(raw_line) - len(raw_line.lstrip())]
        comment_match = re.search(r"#.*$", raw_line)
        trailing_comment = (" " + comment_match.group(0)) if comment_match else ""
        new_lines.append(f"{leading_ws}{key}: {new_val}{trailing_comment}\n")

    for key, val in updates.items():
        if key not in keys_seen:
            new_lines.append(f"{key}: {val}\n")

    fd, tmp_path_str = tempfile.mkstemp(
        dir=path.parent, prefix=path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path_str, path)
    except Exception:
        try:
            os.unlink(tmp_path_str)
        except OSError:
            pass
        raise
