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


def test_index_links_back_to_multiace_console():
    # Opened standalone (/plugin/filamenthub/) there is no console nav, and on
    # a phone the console's tab bar scrolls away. The link must leave the
    # iframe (target=_top) and stay relative so it works behind any prefix.
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    m = re.search(r'<a[^>]*id="back-multiace"[^>]*>', html)
    assert m, "missing back link"
    tag = m.group(0)
    assert 'href="../../multiace/"' in tag
    assert 'target="_top"' in tag


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


def test_app_js_never_pulls_on_open():
    # FilamentHub's ace-state can be months stale (u1-b winners were July data on
    # 2026-10-11); an automatic pull would overwrite correct slot labels that quad
    # replenish matches backups on. Pull must be an explicit user action only.
    js = _app_js()
    assert "pull(false)" not in js
    assert "render().then(() => pull" not in js


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
