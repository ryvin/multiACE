# License: GPL-3.0
"""Static-UI contract checks (WP5 picker): markup + relative fetch paths.

The UI runs inside decay71's console as an iframe at /plugin/filamenthub/, so
every plugin fetch must be RELATIVE ("filaments", not "/filaments") or it
would hit decay71/Fluidd at the site root instead of this sidecar.
"""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "filamenthub_plugin" / "static"


def _app_js() -> str:
    return (STATIC / "app.js").read_text(encoding="utf-8")


def test_root_serves_picker_with_profiles_tab(client):
    r = client.get("/")
    assert r.status_code == 200
    html = r.text
    assert 'role="tablist"' in html
    assert 'id="tab-spools"' in html and 'id="tab-profiles"' in html
    assert 'id="panel-profiles"' in html
    assert 'id="create-spool"' in html and 'type="checkbox"' in html
    assert 'id="profile-filter"' in html
    assert 'id="toast"' in html


def test_index_has_no_external_assets():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert not re.search(r'(src|href)="(https?:)?//', html)


def test_app_js_uses_relative_plugin_endpoints():
    js = _app_js()
    for ep in ("filaments", "assign-profile", "assign", "unassign", "slots",
               "spools", "pull"):
        assert re.search(rf'["`]{re.escape(ep)}["`]', js), ep
    # No absolute-root plugin fetches (would escape /plugin/filamenthub/).
    absolute = re.compile(
        r'["`]/(filaments|assign-profile|assign|unassign|slots|spools|pull)\b')
    assert absolute.search('fetch("/filaments")')  # known-bad input must trip
    assert not absolute.search(js)


def test_app_js_keeps_auto_pull_additive():
    js = _app_js()
    assert "pull(false)" in js


def test_app_js_never_assigns_innerhtml_with_data():
    # Only the literal clear-out form is allowed; remote text goes via
    # textContent. A guard that cannot fail is worthless, so the regex is
    # exercised against a known-bad line below.
    bad = re.compile(r'\.innerHTML\s*[+]?=(?!\s*""\s*;)')
    assert bad.search('el.innerHTML = `<b>${name}</b>`;')
    assert not bad.search(_app_js())


def test_app_js_converts_to_one_based_labels():
    js = _app_js()
    assert "ACE ${" in js and "+ 1" in js
