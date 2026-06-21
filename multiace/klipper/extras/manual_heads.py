# Copyright (C) 2026  multiACE contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Manual-head (TPU) decision layer, adapted from decay71/multiACE 0.98b.
"""Klipper-free decision + serialization layer for the Manual Heads / TPU
feature.

A toolhead can be marked "manual": the operator hand-feeds flexible filament
(e.g. TPU) into it, so the head must BYPASS ACE feed, feed-assist, and
retract/park. Keeping the parsing, the bypass predicate, and the
save_variables serialization here (no Klipper imports) lets ace.py stay a thin
wrapper and lets this logic be unit-tested without hardware.

Head indices are 0-based, matching the firmware SLOT/HEAD params.
"""

import json

DEFAULT_HEAD_COUNT = 4


def parse_manual_heads(raw, head_count=DEFAULT_HEAD_COUNT):
    """Parse the ``manual_heads`` config value — a comma-separated list of
    0-based head indices (e.g. ``"0,3"``) — into a frozenset[int]. Mirrors the
    list-style parse used for fa_print_disable/fa_load_disable. Whitespace and
    duplicates are tolerated; out-of-range and non-numeric tokens are dropped.
    """
    out = set()
    for tok in (raw or "").split(","):
        tok = tok.strip()
        if tok.isdigit() and 0 <= int(tok) < head_count:
            out.add(int(tok))
    return frozenset(out)


def head_manual_bypasses(head, manual_set):
    """Pure predicate: should ACE feed/FA/retract/park be skipped for ``head``?
    True iff ``head`` is in ``manual_set``. Tolerates string indices and
    rejects garbage/None."""
    try:
        return int(head) in manual_set
    except (TypeError, ValueError):
        return False


def serialize_manual_heads(manual_set):
    """Serialize a set of manual head indices to a sorted JSON list string for
    save_variables (e.g. ``"[0, 3]"``). List form avoids decay71's dict +
    ``: True``/``: False`` literal-fixup hack."""
    return json.dumps(sorted(int(h) for h in manual_set))


def deserialize_manual_heads(value, head_count=DEFAULT_HEAD_COUNT):
    """Restore a set of manual head indices from a save_variables value, which
    may be a JSON string or an already-parsed list. Out-of-range and
    non-integer entries are dropped; anything malformed yields an empty set."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return set()
    if not isinstance(value, (list, tuple)):
        return set()
    out = set()
    for h in value:
        if isinstance(h, bool):
            continue
        if isinstance(h, int) and 0 <= h < head_count:
            out.add(h)
    return out
