"""Read/write the [ace] section of ace.cfg, preserving comments and formatting.

ace.cfg is a Klipper-style INI file with `key: value` lines. We:
- Track section headers `[name]`.
- Only read/write keys when current section is `[ace]`.
- Append unknown keys at the end of the [ace] section (just before the next section header or EOF).

Lines starting with `#` are comments and are not parsed. Inline `# ...` after a value is preserved. Values cannot contain literal `#` (Klipper convention).
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path

_KV_RE = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*([^#\n]*?)\s*(?:#.*)?$")
_SECTION_RE = re.compile(r"^\[([^\]]+)\]\s*$")
ACE_SECTION = "ace"


def _is_ace_section(name: str) -> bool:
    return name.strip() == ACE_SECTION


def read_ace_config(path: Path) -> dict[str, str]:
    """Return all uncommented key/value pairs from the [ace] section as a flat dict."""
    text = path.read_text()
    values: dict[str, str] = {}
    in_ace = False
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        sec_m = _SECTION_RE.match(stripped)
        if sec_m:
            in_ace = _is_ace_section(sec_m.group(1))
            continue
        if not in_ace:
            continue
        if stripped.startswith("#") or stripped == "":
            continue
        m = _KV_RE.match(stripped)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if val == "":
            continue
        values[key] = val
    return values


def write_ace_config(path: Path, updates: dict[str, str]) -> None:
    """Update specified keys inside the [ace] section, preserving formatting and comments.

    Writes atomically: writes to tmp file, fsyncs, renames over original.
    Saves a .bak copy of the previous content first (atomically via shutil.copy2 + os.replace).
    Updates that don't match an existing [ace] key are appended just before the next section
    header (or EOF if [ace] is the last section).
    """
    text = path.read_text()
    backup_path = path.with_suffix(path.suffix + ".bak")
    # Atomic backup: copy to tmp then replace
    fd_b, tmp_backup_str = tempfile.mkstemp(
        dir=path.parent, prefix=path.name + ".bak.", suffix=".tmp"
    )
    os.close(fd_b)
    try:
        shutil.copy2(path, tmp_backup_str)
        os.replace(tmp_backup_str, backup_path)
    except Exception:
        try:
            os.unlink(tmp_backup_str)
        except OSError:
            pass
        raise

    lines = text.splitlines(keepends=True)
    keys_seen: set[str] = set()
    new_lines: list[str] = []
    in_ace = False
    ace_end_idx: int | None = None  # index in new_lines where new keys should be inserted

    for i, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        sec_m = _SECTION_RE.match(stripped)
        if sec_m:
            section_name = sec_m.group(1)
            # Leaving [ace]? Mark insertion point before this header.
            if in_ace and not _is_ace_section(section_name):
                # We've found the section after [ace]; remember where to splice
                if ace_end_idx is None:
                    ace_end_idx = len(new_lines)
            in_ace = _is_ace_section(section_name)
            new_lines.append(raw_line)
            continue

        if not in_ace:
            new_lines.append(raw_line)
            continue

        # Inside [ace]
        if stripped.startswith("#") or stripped == "":
            new_lines.append(raw_line)
            continue

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

    # If [ace] is the last (or only) section, splice point is end of file
    if ace_end_idx is None and in_ace:
        ace_end_idx = len(new_lines)

    # Append unknown keys at the splice point
    new_keys = [(k, v) for k, v in updates.items() if k not in keys_seen]
    if new_keys:
        if ace_end_idx is None:
            # No [ace] section in file at all — append [ace] header + keys at end
            if new_lines and not new_lines[-1].endswith("\n"):
                new_lines.append("\n")
            new_lines.append("[ace]\n")
            for k, v in new_keys:
                new_lines.append(f"{k}: {v}\n")
        else:
            insert_lines = [f"{k}: {v}\n" for k, v in new_keys]
            new_lines = new_lines[:ace_end_idx] + insert_lines + new_lines[ace_end_idx:]

    fd, tmp_path_str = tempfile.mkstemp(
        dir=path.parent, prefix=path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
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
