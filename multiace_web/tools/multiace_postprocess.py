"""multiace_postprocess — 8-color G-code post-processor for multiACE / Snapmaker U1.

Usage (Orca Slicer "Post-processing scripts" field):
    python3 /path/to/multiace_postprocess.py;{output_filepath}

Environment variables:
    DAVINCI_U1_HOST              IP or hostname of the Snapmaker U1 printer
                                 (used for optional slot-query against the
                                 multiACE web console).  Example: 192.168.1.50
    MULTIACE_POSTPROCESS_DEBUG   Set to any non-empty value to enable verbose
                                 debug output on stderr.

The script rewrites the G-code in-place:
  1. Parses the Orca header comments to discover filament types + colours.
  2. Queries the printer's /api/state to learn which physical ACE slots hold
     which spools (by RFID or manual mapping).
  3. Matches slicer tools (T0…T7) to ACE slot addresses.
  4. Plans the minimum set of ACE_SWAP / ACE_LOAD_HEAD calls.
  5. Rewrites each Tn line to the required multiACE macros.
  6. Writes a human-readable sidecar .multiace_map file next to the G-code.

Only stdlib is used — no pip dependencies — so the script runs in any Python
3.8+ environment without a venv.
"""

# Copyright (C) 2026 Raul (raul@leadingbit.com)
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib import error as urllib_error
from urllib import request as urllib_request

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------

DEBUG: bool = bool(os.environ.get("MULTIACE_POSTPROCESS_DEBUG", ""))


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

def _dbg(msg: str) -> None:
    """Print a debug message to stderr (only when DEBUG is set)."""
    if DEBUG:
        print(f"[multiace_postprocess DEBUG] {msg}", file=sys.stderr)


def _warn(msg: str) -> None:
    """Print a warning message to stderr unconditionally."""
    print(f"[multiace_postprocess WARN] {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Header parser
# ---------------------------------------------------------------------------

def parse_header(lines: list) -> Optional[list]:
    """Parse Orca-slicer G-code header comments and return per-tool metadata.

    Looks for:
        ; filament_type = PLA;PLA;PETG;...
        ; filament_colour = #FF0000;#FFFFFF;...

    Args:
        lines: List of G-code lines (strings, no trailing newline required).

    Returns:
        A list of dicts ``[{"type": "PLA", "color": "ff0000"}, ...]``, one
        entry per slicer tool, or ``None`` if:
          - the ``filament_type`` comment is absent, or
          - fewer than 2 tools are present (single-extruder job — skip).
    """
    filament_types: Optional[list] = None
    filament_colours: Optional[list] = None

    type_re = re.compile(r"^;\s*filament_type\s*=\s*(.+)$")
    colour_re = re.compile(r"^;\s*filament_colour\s*=\s*(.+)$")

    for line in lines:
        m = type_re.match(line)
        if m and filament_types is None:
            filament_types = [t.strip() for t in m.group(1).split(";")]
            _dbg(f"filament_type raw: {filament_types}")
            continue

        m = colour_re.match(line)
        if m and filament_colours is None:
            raw_colours = [c.strip() for c in m.group(1).split(";")]
            filament_colours = [c.lstrip("#").lower() for c in raw_colours]
            _dbg(f"filament_colour raw: {filament_colours}")
            continue

        # Both found — no need to scan further
        if filament_types is not None and filament_colours is not None:
            break

    if filament_types is None or filament_colours is None:
        _dbg("parse_header: missing filament_type or filament_colour comment")
        return None

    if len(filament_types) < 2:
        _dbg(f"parse_header: only {len(filament_types)} tool(s) — skipping")
        return None

    # Zip types and colours; pad colours with empty string if shorter
    tools = []
    for i, ftype in enumerate(filament_types):
        colour = filament_colours[i] if i < len(filament_colours) else ""
        tools.append({"type": ftype, "color": colour})

    _dbg(f"parse_header: {len(tools)} tools parsed")
    return tools
