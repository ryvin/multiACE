# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for web_queue_batch_patch.py against a decay71 app.js fixture.

Set DECAY71_APP_JS to a real upstream app.js to run against it, e.g.
  git show decay71/main:multiace/web/frontend/app.js > /tmp/app.js
"""
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import web_queue_batch_patch as p  # noqa: E402

SCRIPT = os.path.join(os.path.dirname(__file__), "web_queue_batch_patch.py")

UPSTREAM_SNIPPET = (
    "        cmdQueue.value.unshift(it);\n"
    "        if (panelMode) queueMicrotask(_scheduleAdvance);\n"
    "        else _scheduleAdvance();\n"
    "      });\n"
    "      if (panelMode && arr.filter(it => it.status === 'queued')"
    ".length > 1) {\n"
    "        sendAllToPrinter();\n"
)


def _upstream():
    real = os.environ.get("DECAY71_APP_JS")
    if real:
        with open(real, encoding="utf-8") as f:
            return f.read()
    return UPSTREAM_SNIPPET


def test_unpatched_upstream_is_recognised():
    assert p.classify(_upstream()) == "unpatched"


def test_patch_removes_panel_only_gates():
    out = p.patch(_upstream())
    assert p.classify(out) == "patched"
    assert "if (panelMode) queueMicrotask(_scheduleAdvance);" not in out
    assert "if (panelMode && arr.filter(" not in out
    assert "else _scheduleAdvance();" not in out


def test_patch_is_idempotent(tmp_path):
    f = tmp_path / "app.js"
    f.write_text(_upstream(), encoding="utf-8")
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


@pytest.mark.parametrize("state_text,expected", [
    (UPSTREAM_SNIPPET, "unpatched"),
    (p.patch(UPSTREAM_SNIPPET), "patched"),
    ("nothing", "unknown"),
])
def test_classify(state_text, expected):
    assert p.classify(state_text) == expected
