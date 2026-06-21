"""Tests for the i18n catalog loader and the /api/i18n endpoints.

Catalogs live in static/i18n/<code>.json as nested string maps with a `_meta`
block. A requested language is merged over the English fallback so a partial
translation never shows blank strings.
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from multiace_web import i18n
from multiace_web.server import create_app

_STATIC = Path(__file__).resolve().parent.parent / "src" / "multiace_web" / "static"


def _write_catalog(d, code, name, mapping, fallback="en"):
    data = {"_meta": {"language": code, "name": name, "fallback": fallback}}
    data.update(mapping)
    (d / ("%s.json" % code)).write_text(json.dumps(data), encoding="utf-8")


# --- loader: list_languages ------------------------------------------------

def test_list_languages_lists_files_en_first(tmp_path):
    _write_catalog(tmp_path, "en", "English", {"nav": {"dashboard": "Dashboard"}})
    _write_catalog(tmp_path, "de", "Deutsch", {"nav": {"dashboard": "Übersicht"}})
    langs = i18n.list_languages(tmp_path)
    assert [l["code"] for l in langs] == ["en", "de"]
    assert {"code": "de", "name": "Deutsch"} in langs


def test_list_languages_empty_or_missing_dir(tmp_path):
    assert i18n.list_languages(tmp_path) == []
    assert i18n.list_languages(tmp_path / "nope") == []


# --- loader: load_catalog --------------------------------------------------

def test_load_catalog_returns_lang(tmp_path):
    _write_catalog(tmp_path, "en", "English", {"nav": {"dashboard": "Dashboard"}})
    cat = i18n.load_catalog(tmp_path, "en")
    assert cat["nav"]["dashboard"] == "Dashboard"


def test_load_catalog_merges_over_english_fallback(tmp_path):
    _write_catalog(tmp_path, "en", "English",
                   {"nav": {"dashboard": "Dashboard", "config": "Config"}})
    _write_catalog(tmp_path, "de", "Deutsch",
                   {"nav": {"dashboard": "Übersicht"}})  # 'config' missing
    cat = i18n.load_catalog(tmp_path, "de")
    assert cat["nav"]["dashboard"] == "Übersicht"   # overlay wins
    assert cat["nav"]["config"] == "Config"          # falls back to en


def test_load_catalog_unknown_lang_falls_back_to_english(tmp_path):
    _write_catalog(tmp_path, "en", "English", {"nav": {"dashboard": "Dashboard"}})
    cat = i18n.load_catalog(tmp_path, "xx")
    assert cat["nav"]["dashboard"] == "Dashboard"


def test_load_catalog_none_when_no_files(tmp_path):
    assert i18n.load_catalog(tmp_path, "en") is None


def test_load_catalog_rejects_path_traversal(tmp_path):
    _write_catalog(tmp_path, "en", "English", {"nav": {"dashboard": "Dashboard"}})
    # a lang token with path separators must not escape the i18n dir
    assert i18n.load_catalog(tmp_path, "../secret") is None
    assert i18n.load_catalog(tmp_path, "..") is None


# --- endpoints -------------------------------------------------------------

@pytest.fixture
def client():
    app = create_app(static_dir=_STATIC, start_background_tasks=False)
    with TestClient(app) as c:
        yield c


def test_api_i18n_lists_languages(client):
    r = client.get("/api/i18n")
    assert r.status_code == 200
    body = r.json()
    codes = {l["code"] for l in body["languages"]}
    assert {"en", "de", "zh"} <= codes
    assert body["default"] == "en"


def test_api_i18n_catalog_en(client):
    r = client.get("/api/i18n/en")
    assert r.status_code == 200
    cat = r.json()
    assert cat["nav"]["dashboard"]            # present and non-empty
    assert cat["nav"]["printQueue"]


def test_api_i18n_catalog_de_merges_fallback(client):
    r = client.get("/api/i18n/de")
    assert r.status_code == 200
    cat = r.json()
    # every key the English catalog has must resolve (merge guarantees no blanks)
    en = client.get("/api/i18n/en").json()
    for k in en["nav"]:
        assert cat["nav"].get(k)


def test_api_i18n_bad_lang_is_404(client):
    assert client.get("/api/i18n/..%2f..%2fetc").status_code == 404
