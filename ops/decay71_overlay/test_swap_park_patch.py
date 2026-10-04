# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for swap_park_patch.py, against decay71's real app.js."""
import json
import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
import swap_park_patch as sp  # noqa: E402
import web_queue_batch_patch as wq  # noqa: E402

SCRIPT = os.path.join(HERE, "swap_park_patch.py")


@pytest.fixture(scope="module")
def upstream():
    real = os.environ.get("DECAY71_APP_JS")
    if real:
        with open(real, encoding="utf-8") as f:
            return f.read()
    try:
        return subprocess.run(
            ["git", "show", "decay71/main:multiace/web/frontend/app.js"],
            cwd=HERE, check=True, capture_output=True, text=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("no decay71 app.js available")


def test_upstream_is_unpatched(upstream):
    assert sp.classify(upstream) == "unpatched"


def test_patch_applies(upstream):
    out = sp.patch(upstream)
    assert sp.classify(out) == "patched"
    assert 'enqueue("ACE_UNLOAD_HEAD", _ovSwapUnloadArgs(slotIdx, th));' in out
    # plain Unload buttons and head mode are untouched
    assert 'run("ACE_UNLOAD_HEAD", {HEAD: idx});' in out
    assert 'enqueue("ACE_UNLOAD_HEAD", {HEAD: h});' in out


def test_stacks_with_queue_patch_in_any_order(upstream):
    a = sp.patch(wq.patch(upstream))
    b = wq.patch(sp.patch(upstream))
    assert a == b
    assert sp.classify(a) == "patched" and wq.classify(a) == "patched"


def test_idempotent_and_refuses_unknown(tmp_path, upstream):
    f = tmp_path / "app.js"
    f.write_text(upstream, encoding="utf-8")
    assert subprocess.run([sys.executable, SCRIPT, str(f)]).returncode == 0
    once = f.read_text(encoding="utf-8")
    assert subprocess.run([sys.executable, SCRIPT, str(f)]).returncode == 0
    assert f.read_text(encoding="utf-8") == once
    g = tmp_path / "other.js"
    g.write_text("// other\n", encoding="utf-8")
    assert subprocess.run([sys.executable, SCRIPT, str(g)]).returncode == 1
    assert g.read_text(encoding="utf-8") == "// other\n"


NODE_CASES = [
    # (mode, th, global, perAce, perSlot, expected)
    ("multi", {"ace": 1, "slot": 2}, 900, "", "", {"HEAD": 2, "RETRACT_LENGTH": 900}),
    ("multi", {"ace": 1, "slot": 2}, 900, 800, "", {"HEAD": 2, "RETRACT_LENGTH": 800}),
    ("multi", {"ace": 1, "slot": 2}, 900, 800, 750, {"HEAD": 2, "RETRACT_LENGTH": 750}),
    ("multi", {"ace": 1, "slot": 2}, 900, 0, "", {"HEAD": 2}),       # explicit 0 = full
    ("multi", {"ace": 1, "slot": 2}, "", "", "", {"HEAD": 2}),       # config not loaded
    ("head", {"ace": 1, "slot": 2}, 900, "", "", {"HEAD": 2}),       # head mode unchanged
    ("multi", None, 900, "", "", {"HEAD": 2}),
]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("mode,th,glob,per_ace,per_slot,expected", NODE_CASES)
def test_helper_resolution(mode, th, glob, per_ace, per_slot, expected):
    slots = [{"swap_retract_length": ""} for _ in range(4)]
    slots[2]["swap_retract_length"] = per_slot
    form = {"swap_retract_length": glob,
            "perAce": [{"swap_retract_length": "", "perSlot": slots},
                       {"swap_retract_length": per_ace, "perSlot": slots}]}
    js = (sp.HELPER
          + "const state = {mode: %s};\n" % json.dumps(mode)
          + "const configForm = %s;\n" % json.dumps(form)
          + "console.log(JSON.stringify(_ovSwapUnloadArgs(2, %s)));\n"
          % json.dumps(th))
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == expected


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_fully_patched_is_valid_js(tmp_path, upstream):
    f = tmp_path / "app.js"
    f.write_text(sp.patch(wq.patch(upstream)), encoding="utf-8")
    assert subprocess.run(["node", "--check", str(f)]).returncode == 0
