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

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
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


def _info(msg: str) -> None:
    """Print an info message to stderr unconditionally."""
    print(f"[postprocess] INFO: {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ToolMeta:
    index: int
    type: str   # uppercase, e.g. "PLA"
    color: str  # lowercase no-#, e.g. "ff0000"


@dataclass
class Candidate:
    ace: int
    slot: int
    spool_id: int
    spool_name: str = ""


@dataclass
class ToolResolution:
    tool: ToolMeta
    match_quality: str  # "exact" | "ambiguous" | "none"
    candidates: list[Candidate] = field(default_factory=list)
    resolved: Optional[Candidate] = None
    physical_head: Optional[int] = None   # set by plan_swaps


@dataclass
class SwapEvent:
    line: int       # 0-based line number in gcode
    layer: int      # current layer number (or 0 if unknown)
    head: int       # physical head index 0-3 to evict
    from_ace: int   # ACE index of evicted filament
    from_slot: int
    to_ace: int     # source slot of new filament
    to_slot: int


@dataclass
class AliasDecision:
    """One Tn → Tm rewrite recorded by optimize_aliases() for the sidecar."""
    line: int                # 0-based line index in the original gcode
    layer: Optional[int]     # layer N from preceding `; --- layer N ---`, or None
    original_tool: int       # Tn (the slicer-emitted index that got rewritten)
    alias_tool: int          # Tm (the existing loaded tool that absorbed it)
    reason: str              # human-readable, e.g. "color+type match, T0 already loaded"


@dataclass
class LayerDecision:
    """One layer's pre-load plan recorded by prelayer_reload() for the sidecar."""
    layer: int               # layer N (0-based)
    distinct_tools: list[int]  # tool indices that appear within the layer
    preloads: list[dict]     # [{"head": h, "ace": a, "slot": s, "tool": n}, ...]
    skipped: bool            # True if layer was >4 distinct tools and skipped
    skip_reason: Optional[str]


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


# ---------------------------------------------------------------------------
# Slot query
# ---------------------------------------------------------------------------

def query_slots(printer_url: str) -> dict:
    """GET /multiace/api/slots and return the parsed JSON dict.

    Uses urllib.request (stdlib only). Raises on network error or non-200.
    """
    url = f"{printer_url.rstrip('/')}/multiace/api/slots"
    _dbg(f"GET {url}")
    try:
        with urllib_request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib_error.HTTPError as e:
        raise RuntimeError(f"GET {url} failed: HTTP {e.code}") from e
    except Exception as e:
        raise RuntimeError(f"GET {url} failed: {e}") from e


# ---------------------------------------------------------------------------
# Tool matching
# ---------------------------------------------------------------------------

def match_tools(tools: list[dict], slots_response: dict) -> list[ToolResolution]:
    """Match each slicer tool against bound spools using exact (type, color_hex).

    slots_response is the JSON from GET /api/slots:
      {"aces": [{"index": N, "slots": [{"slot": S, "spool": {...} | null}]}]}

    Returns one ToolResolution per tool (same order as input).
    """
    all_bindings: list[tuple[int, int, str, str, int, str]] = []
    for ace_block in (slots_response.get("aces") or []):
        ace_idx = int(ace_block["index"])
        for slot_block in (ace_block.get("slots") or []):
            spool = slot_block.get("spool")
            if not spool:
                continue
            mat = (spool.get("material") or "").strip().upper()
            col = (spool.get("color") or "").strip().lower().lstrip("#")
            all_bindings.append((
                ace_idx,
                int(slot_block["slot"]),
                mat,
                col,
                int(spool["spool_id"]),
                spool.get("name") or "",
            ))

    resolutions: list[ToolResolution] = []
    for tool in tools:
        tmeta = ToolMeta(
            index=len(resolutions),
            type=tool["type"].upper(),
            color=tool["color"].lower().lstrip("#"),
        )
        candidates = [
            Candidate(ace=ace, slot=slot, spool_id=sid, spool_name=name)
            for ace, slot, mat, col, sid, name in all_bindings
            if mat == tmeta.type and col == tmeta.color
        ]
        if len(candidates) == 1:
            mq = "exact"
            resolved = candidates[0]
        elif len(candidates) > 1:
            mq = "ambiguous"
            resolved = None
        else:
            mq = "none"
            resolved = None

        resolutions.append(ToolResolution(
            tool=tmeta,
            match_quality=mq,
            candidates=candidates,
            resolved=resolved,
        ))
    return resolutions


# ---------------------------------------------------------------------------
# Swap planner
# ---------------------------------------------------------------------------

_LAYER_RE = re.compile(r"^\s*;\s*-+\s*layer\s+(\d+)\s*-+", re.IGNORECASE)
_TOOL_RE = re.compile(r"^\s*T(\d+)\s*(?:;.*)?$")


def plan_swaps(resolutions: list[ToolResolution], gcode_lines: list[str]) -> list[SwapEvent]:
    """Greedy swap planner: minimize number of physical-head reassignments.

    Algorithm:
      1. Assign the first 4 distinct resolved tools (in order of first use in
         gcode) to physical heads 0-3.
      2. Walk gcode line-by-line; when a Tn is encountered:
         - If the tool's filament is already on a head → use that head (no swap).
         - Else find the "best" head to evict (greedy: first head whose tool
           won't appear again in the remaining gcode). Assign new tool to that
           head → one swap event.
      3. Unresolved tools (match_quality != "exact" and no resolved) are skipped.
    """
    resolved: dict[int, Candidate] = {}
    for r in resolutions:
        if r.resolved is not None:
            resolved[r.tool.index] = r.resolved

    appearance_order: list[int] = []
    seen: set[int] = set()
    future_uses: dict[int, list[int]] = {}
    for lineno, line in enumerate(gcode_lines):
        m = _TOOL_RE.match(line)
        if m:
            t = int(m.group(1))
            if t not in seen:
                seen.add(t)
                if t in resolved:
                    appearance_order.append(t)
            future_uses.setdefault(t, []).append(lineno)

    head_to_tool: dict[int, Optional[int]] = {0: None, 1: None, 2: None, 3: None}
    tool_to_head: dict[int, int] = {}
    next_head = 0
    for t in appearance_order[:4]:
        if t not in tool_to_head:
            head_to_tool[next_head] = t
            tool_to_head[t] = next_head
            next_head += 1

    swaps: list[SwapEvent] = []
    current_layer = 0

    for lineno, line in enumerate(gcode_lines):
        lm = _LAYER_RE.match(line)
        if lm:
            current_layer = int(lm.group(1))
            continue

        tm = _TOOL_RE.match(line)
        if not tm:
            continue
        t = int(tm.group(1))
        if t not in resolved:
            continue

        if t in tool_to_head:
            if t < len(resolutions):
                resolutions[t].physical_head = tool_to_head[t]
            continue

        remaining_uses: dict[int, int] = {}
        for h, ht in head_to_tool.items():
            if ht is None:
                remaining_uses[h] = -1
                continue
            uses = [u for u in future_uses.get(ht, []) if u > lineno]
            remaining_uses[h] = min(uses) if uses else -1

        evict_head = min(
            remaining_uses,
            key=lambda h: (
                remaining_uses[h] != -1,                               # free heads first
                -remaining_uses[h] if remaining_uses[h] != -1 else 0,  # then largest next-use line wins (furthest future)
                h,                                                      # tiebreak by head index
            )
        )

        old_tool = head_to_tool[evict_head]
        old_cand = resolved[old_tool] if old_tool is not None and old_tool in resolved else None
        new_cand = resolved[t]

        swaps.append(SwapEvent(
            line=lineno,
            layer=current_layer,
            head=evict_head,
            from_ace=old_cand.ace if old_cand else 0,
            from_slot=old_cand.slot if old_cand else 0,
            to_ace=new_cand.ace,
            to_slot=new_cand.slot,
        ))

        if old_tool is not None and old_tool in tool_to_head:
            del tool_to_head[old_tool]
        head_to_tool[evict_head] = t
        tool_to_head[t] = evict_head
        if t < len(resolutions):
            resolutions[t].physical_head = evict_head

    for h, t in head_to_tool.items():
        if t is not None and t < len(resolutions) and resolutions[t].physical_head is None:
            resolutions[t].physical_head = h

    return swaps


# optimize_aliases uses permissive Tn matching (accepts T5 F300, T5 ; comment, etc.) — plan_swaps's stricter _TOOL_RE rejects those
_TOOL_RE_OPTIM = re.compile(r"^T(\d+)\b")
_LAYER_RE_OPTIM = re.compile(r"^;\s*---\s*layer\s+(\d+)\s*---")


def optimize_aliases(
    lines: list[str],
    resolutions: list,
) -> tuple[list[str], list]:
    """Single linear pass over gcode. When a Tn line appears and a different
    Tm (already encountered) shares the same (color, type), rewrite Tn → Tm.

    See docs/superpowers/specs/2026-05-17-swaptimizer-design.md for the
    full algorithm and correctness assumptions.

    Returns (rewritten_lines, list[AliasDecision]).
    Unresolved tools (match_quality='none') are skipped — no color to alias against.
    """
    # Build tool index → (color, type) map, skipping unresolved
    tool_meta: dict = {}
    for r in resolutions:
        if r.match_quality == "none":
            continue
        tool_meta[r.tool.index] = (r.tool.color, r.tool.type)

    seen_tools: set = set()       # tools we've already issued (proxy for "loaded somewhere")
    out_lines = list(lines)
    decisions: list = []
    current_layer = None

    for line_idx, line in enumerate(lines):
        m_layer = _LAYER_RE_OPTIM.match(line)
        if m_layer:
            current_layer = int(m_layer.group(1))
            continue
        m_tool = _TOOL_RE_OPTIM.match(line)
        if not m_tool:
            continue
        n = int(m_tool.group(1))
        if n not in tool_meta:
            continue   # unresolved — skip
        n_color, n_type = tool_meta[n]
        alias = None
        for m in seen_tools:
            if m == n:
                continue
            m_color, m_type = tool_meta[m]
            if m_color == n_color and m_type == n_type:
                alias = m
                break
        if alias is not None:
            # Rewrite the Tn keyword (preserve any trailing args after T<digits>)
            out_lines[line_idx] = f"T{alias}" + line[m_tool.end():]
            decisions.append(AliasDecision(
                line=line_idx,
                layer=current_layer,
                original_tool=n,
                alias_tool=alias,
                reason=f"color+type match, T{alias} already loaded",
            ))
        else:
            seen_tools.add(n)
    return out_lines, decisions


# ---------------------------------------------------------------------------
# Atomic JSON writer
# ---------------------------------------------------------------------------

def _atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON atomically: tempfile in same dir, then os.replace."""
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmppath = tempfile.mkstemp(dir=str(parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=False)
            f.write("\n")
        os.replace(tmppath, path)
    except Exception:
        try:
            os.unlink(tmppath)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Gcode rewriter
# ---------------------------------------------------------------------------

def rewrite_gcode(lines: list[str], resolutions: list[ToolResolution],
                  swaps: list[SwapEvent]) -> list[str]:
    """Rewrite gcode: remap logical T4-T7 to physical T0-T3; insert PARK/LOAD at swaps."""
    logical_to_physical: dict[int, int] = {}
    for r in resolutions:
        if r.physical_head is not None:
            logical_to_physical[r.tool.index] = r.physical_head

    swap_by_line: dict[int, SwapEvent] = {s.line: s for s in swaps}

    header_comments: list[str] = []
    for r in resolutions:
        cand = r.resolved
        if cand:
            header_comments.append(
                f"; multiace.tool{r.tool.index}: type={r.tool.type} "
                f"color=#{r.tool.color} head={r.physical_head} "
                f"source=ace{cand.ace}/slot{cand.slot} spool_id={cand.spool_id}"
            )
        else:
            header_comments.append(
                f"; multiace.tool{r.tool.index}: type={r.tool.type} "
                f"color=#{r.tool.color} head=UNRESOLVED"
            )
    total_swaps = len(swaps)
    swap_layers = ",".join(str(s.layer) for s in swaps) if swaps else "none"
    unresolved_count = sum(1 for r in resolutions if r.resolved is None)
    status = "ready" if unresolved_count == 0 else "pending"
    header_comments += [
        f"; multiace.swaps: {total_swaps} (at layers {swap_layers})",
        f"; multiace.status: {status}",
    ]

    header_insert_at = 0
    for i, line in enumerate(lines):
        if line.strip().startswith("; generated by") or line.strip().startswith("; filament_type"):
            header_insert_at = i
            break

    out: list[str] = []
    header_emitted = False
    for lineno, line in enumerate(lines):
        if not header_emitted and lineno == header_insert_at + 1:
            out.extend(header_comments)
            header_emitted = True

        if lineno in swap_by_line:
            sw = swap_by_line[lineno]
            out.append(f"ACE_PARK_HEAD HEAD={sw.head}")
            out.append(f"ACE_LOAD_HEAD HEAD={sw.head} ACE={sw.to_ace} SLOT={sw.to_slot}")

        tm = _TOOL_RE.match(line)
        if tm:
            logical = int(tm.group(1))
            phys = logical_to_physical.get(logical, logical)
            out.append(f"T{phys}" + (line[tm.end():] if tm.end() < len(line) else ""))
        else:
            out.append(line)

    return out


# ---------------------------------------------------------------------------
# Sidecar writer
# ---------------------------------------------------------------------------

def write_sidecar(gcode_path: Path, resolutions: list[ToolResolution],
                  swaps: list[SwapEvent], status: str,
                  reason: Optional[str] = None, errors: Optional[list] = None) -> None:
    """Write <gcode_path>.multiace.json atomically."""
    tools_dict: dict[str, dict] = {}
    for r in resolutions:
        cand = r.resolved
        tools_dict[str(r.tool.index)] = {
            "type": r.tool.type,
            "color": r.tool.color,   # stored without leading # to match parse_header normalization
            "match_quality": r.match_quality,
            "candidates": [
                {"ace": c.ace, "slot": c.slot, "spool_id": c.spool_id, "spool_name": c.spool_name}
                for c in r.candidates
            ],
            "resolved": (
                {"ace": cand.ace, "slot": cand.slot, "spool_id": cand.spool_id}
                if cand else None
            ),
            "physical_head": r.physical_head,
        }
    swaps_list = [
        {
            "line": s.line,
            "layer": s.layer,
            "head": s.head,
            "from": {"ace": s.from_ace, "slot": s.from_slot},
            "to": {"ace": s.to_ace, "slot": s.to_slot},
        }
        for s in swaps
    ]
    data = {
        "schema": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gcode_path": str(gcode_path),
        "status": status,
        "reason": reason,
        "tools": tools_dict,
        "swaps": swaps_list,
        "errors": errors or [],
    }
    sidecar_path = Path(str(gcode_path) + ".multiace.json")
    _atomic_write_json(sidecar_path, data)
    _info(f"Sidecar written: {sidecar_path}")
