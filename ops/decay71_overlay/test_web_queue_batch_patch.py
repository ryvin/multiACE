# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for web_queue_batch_patch.py, run against decay71's real app.js.

The upstream file comes from DECAY71_APP_JS if that is set. Otherwise it is
read from the `decay71` git remote (decay71/main), and the tests skip if
neither is available.
"""
import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
import web_queue_batch_patch as p  # noqa: E402

SCRIPT = os.path.join(HERE, "web_queue_batch_patch.py")
UPSTREAM_PATH = "multiace/web/frontend/app.js"


@pytest.fixture(scope="module")
def upstream():
    real = os.environ.get("DECAY71_APP_JS")
    if real:
        with open(real, encoding="utf-8") as f:
            return f.read()
    try:
        return subprocess.run(
            ["git", "show", "decay71/main:" + UPSTREAM_PATH],
            cwd=HERE, check=True, capture_output=True, text=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("no decay71 app.js available")


def _v1_batch_only(text):
    """What was deployed on 2026-09-30: only the two batch edits."""
    for old, new in p.EDITS[:2]:
        text = text.replace(old, new, 1)
    return text


def test_upstream_is_unpatched(upstream):
    assert p.classify(upstream) == "unpatched"


def test_full_patch_applies_every_edit(upstream):
    out = p.patch(upstream)
    assert p.classify(out) == "patched"
    assert "if (panelMode) queueMicrotask(_scheduleAdvance);" not in out
    assert "if (panelMode && arr.filter(" not in out
    assert out.count("async function _ovAwaitDrop(") == 1
    assert "const drop = await _ovAwaitDrop(_ovT0, _ovBase);" in out


def test_upgrades_v1_deploy(upstream):
    v1 = _v1_batch_only(upstream)
    assert p.classify(v1) == "partial"
    assert p.classify(p.patch(v1)) == "patched"
    assert p.patch(v1) == p.patch(upstream)


def test_script_is_idempotent(tmp_path, upstream):
    f = tmp_path / "app.js"
    f.write_text(upstream, encoding="utf-8")
    assert subprocess.run([sys.executable, SCRIPT, str(f)]).returncode == 0
    once = f.read_text(encoding="utf-8")
    assert subprocess.run([sys.executable, SCRIPT, str(f)]).returncode == 0
    assert f.read_text(encoding="utf-8") == once


def test_refuses_unknown_file(tmp_path):
    f = tmp_path / "app.js"
    f.write_text("// some other upstream version\n", encoding="utf-8")
    r = subprocess.run([sys.executable, SCRIPT, str(f)])
    assert r.returncode == 1
    assert f.read_text(encoding="utf-8") == "// some other upstream version\n"


def test_refuses_when_one_anchor_changed(tmp_path, upstream):
    changed = upstream.replace("    async function _runItem(it) {\n",
                               "    async function _runItemV2(it) {\n", 1)
    f = tmp_path / "app.js"
    f.write_text(changed, encoding="utf-8")
    assert subprocess.run([sys.executable, SCRIPT, str(f)]).returncode == 1
    assert f.read_text(encoding="utf-8") == changed


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_patched_output_is_valid_js(tmp_path, upstream):
    f = tmp_path / "app.js"
    f.write_text(p.patch(upstream), encoding="utf-8")
    assert subprocess.run(["node", "--check", str(f)]).returncode == 0
