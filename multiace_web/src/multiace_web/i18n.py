# Copyright (C) 2026  multiACE contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""i18n catalog loading for the web console.

Catalogs are static JSON files (``static/i18n/<code>.json``) — nested string
maps with a ``_meta`` block (``language``, ``name``, ``fallback``). A requested
language is deep-merged over the English fallback so a partial translation
never renders blank strings. Framework-free so it can be unit-tested without
FastAPI; ``server.py`` exposes it via ``/api/i18n`` and ``/api/i18n/{lang}``.
"""
import json
import re
from pathlib import Path

DEFAULT_LANG = "en"

# A language code is a short alpha token (e.g. en, de, zh, pt). Anything else
# (path separators, "..", digits) is rejected before it touches the filesystem.
_LANG_RE = re.compile(r"^[a-z]{2,8}$")


def _read(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _deep_merge(base, overlay):
    """Return base with overlay applied. Nested dicts merge recursively;
    overlay scalar/leaf values win. Inputs are not mutated."""
    out = dict(base)
    for key, val in overlay.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def list_languages(i18n_dir):
    """Return ``[{'code', 'name'}, ...]`` for each ``<code>.json`` in the dir,
    English first then alphabetical. Missing dir or unreadable files yield []
    (or are skipped)."""
    out = []
    p = Path(i18n_dir)
    if not p.is_dir():
        return out
    for f in sorted(p.glob("*.json")):
        code = f.stem
        if not _LANG_RE.match(code):
            continue
        data = _read(f)
        if data is None:
            continue
        name = (data.get("_meta") or {}).get("name", code)
        out.append({"code": code, "name": name})
    out.sort(key=lambda d: (d["code"] != DEFAULT_LANG, d["code"]))
    return out


def load_catalog(i18n_dir, lang, fallback=DEFAULT_LANG):
    """Return the merged catalog dict for ``lang`` (overlay) on top of
    ``fallback`` (English). Returns None when no usable catalog exists or when
    ``lang`` is not a valid language code (guards against path traversal)."""
    if not (isinstance(lang, str) and _LANG_RE.match(lang)):
        return None
    p = Path(i18n_dir)
    base = _read(p / ("%s.json" % fallback))
    target = _read(p / ("%s.json" % lang)) if lang != fallback else None
    if base is None and target is None:
        return None
    return _deep_merge(base or {}, target or {})
