# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for airlog_chew_patch.py against decay71's real ace.py.

The replay test runs the PATCHED detector methods, extracted from the patched
source, over every airlog window of the 2026-10-11/12 print in which the T3
spool tangled (ACE 2 slot 4). Unpatched upstream never fires on that print;
the patch must fire on T3 during the chew ramp and nowhere else.
"""
import ast
import csv
import gzip
import logging
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
import airlog_chew_patch as cp  # noqa: E402

FIXTURE = os.path.join(HERE, "testdata", "airlog_2026-10-12_t3_tangle.csv.gz")
# 2026-10-13 Shield print, two attempts: T2 blocked by a leftover fragment, T3
# broken + anchored inside ACE 2. Slow print: almost no vel_max >= 10 windows.
# T0/T1 were healthy -> any latch on them is a false positive. The blockages
# give the coil no back-pressure, so firing on T2/T3 is not required.
FIXTURE_B = os.path.join(HERE, "testdata", "airlog_2026-10-13_shield_blockages.csv.gz")
# User pauses: windows inside these are manual handling, not printing.
PAUSES_B = [("2026-10-13 03:26:53", "2026-10-13 03:49:38"),
            ("2026-10-13 05:22:26", "2026-10-13 23:59:59")]
ACE_PY = "multiace/klipper/extras/ace.py"


@pytest.fixture(scope="module")
def upstream():
    real = os.environ.get("DECAY71_ACE_PY")
    if real:
        with open(real, encoding="utf-8") as f:
            return f.read()
    for ref in ("decay71/main", "v1.27b"):
        try:
            return subprocess.run(
                ["git", "show", "%s:%s" % (ref, ACE_PY)], cwd=HERE,
                check=True, capture_output=True, text=True).stdout
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
    pytest.skip("no decay71 ace.py available")


# ---------------------------------------------------------------- harness

def _detector(source):
    """Build a minimal object carrying the detector methods of `source`."""
    tree = ast.parse(source)
    consts = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id.startswith("AIRLOG_CHEW")):
            consts[node.targets[0].id] = ast.literal_eval(node.value)
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and any(
                   isinstance(f, ast.FunctionDef)
                   and f.name == "_airlog_chew_eval" for f in n.body))
    wanted = {"_airlog_chew_eval"} | {
        f.name for f in cls.body
        if isinstance(f, ast.FunctionDef) and f.name.startswith("_ov_chew")}
    ns = dict(consts, logging=logging)
    for f in cls.body:
        if isinstance(f, ast.FunctionDef) and f.name in wanted:
            exec(compile(ast.Module([f], []), "<ace.py>", "exec"), ns)

    class Cfg:
        def __init__(self, values=None):
            self.values = values or {}

        def get(self, name, default=None, **_):
            return self.values.get(name, default)

        getfloat = getint = get

    class Det:
        _swap_in_progress = False
        _pickcheck_active = False
        _swap_phase = "idle"
        resistance_pause = False

        def __init__(self):
            self._airlog_chew_run = {}
            self._airlog_chew_latched = set()
            self.events = []

        def _ace_event(self, name, **fields):  # upstream signature
            self.events.append((name, fields))

    for name in wanted:
        setattr(Det, name, ns[name])
    det = Det()
    if hasattr(det, "_ov_chew_init"):
        det._ov_chew_init(Cfg())
    det.Cfg = Cfg
    return det


def _rows(fixture=FIXTURE, pauses=()):
    with gzip.open(fixture, "rt") as f:
        for r in csv.DictReader(f):
            if any(a <= r["ts"] < b for a, b in pauses):
                continue
            d = None if r["coil_delta"] == "-" else float(r["coil_delta"])
            yield (r["ts"], int(r["head"]), int(r["moving"]), float(r["vel_max"]),
                   d, float(r["vel_mean"]))


def _replay(det, patched, fixture=FIXTURE, pauses=()):
    """Feed every window; return [(ts, head)] of each new latch."""
    from datetime import datetime
    fired, seen = [], set()
    t0 = None
    for ts, head, moving, vmax, delta, vmean in _rows(fixture, pauses):
        t = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S.%f").timestamp()
        t0 = t0 if t0 is not None else t
        if patched:
            det._airlog_chew_eval(head, delta, moving, vmax, t - t0, vmean)
        else:
            det._airlog_chew_eval(head, delta, moving)
        for h in det._airlog_chew_latched - seen:
            fired.append((ts, h))
            seen.add(h)
    return fired


# ---------------------------------------------------------------- patcher

def test_upstream_is_unpatched(upstream):
    assert cp.classify(upstream) == "unpatched"


def test_patch_applies_and_is_idempotent(upstream):
    out = cp.patch(upstream)
    assert cp.classify(out) == "patched"
    assert cp.patch(out) == out
    compile(out, "ace.py", "exec")


def test_unknown_text_is_refused(upstream):
    broken = upstream.replace("AIRLOG_CHEW_MIN_MOVING = 3", "AIRLOG_CHEW_MIN_MOVING = 4")
    assert cp.classify(broken) == "unknown"


def test_cli_check_and_apply(tmp_path, upstream):
    p = tmp_path / "ace.py"
    p.write_text(upstream, encoding="utf-8")
    script = os.path.join(HERE, "airlog_chew_patch.py")
    chk = subprocess.run([sys.executable, script, str(p), "--check"],
                         capture_output=True, text=True)
    assert chk.stdout.strip() == "unpatched"
    subprocess.run([sys.executable, script, str(p)], check=True,
                   capture_output=True, text=True)
    assert cp.classify(p.read_text(encoding="utf-8")) == "patched"


# ---------------------------------------------------------------- behaviour

def test_upstream_misses_the_tangle(upstream):
    # Known-bad baseline: the shipped absolute rule never fires on this print.
    assert _replay(_detector(upstream), patched=False) == []


def test_patched_fires_on_t3_tangle_only(upstream):
    fired = _replay(_detector(cp.patch(upstream)), patched=True)
    assert fired == [("2026-10-12 12:13:49.390", 3)]


def test_slow_print_arms_every_head_without_false_positives(upstream):
    # 2026-10-13: the vel_max >= 10 gate left T0-T2 with 2-8 windows, so their
    # baselines never armed and the relative rule was blind. Varying-speed
    # windows must count too; healthy T0/T1 must still never latch.
    det = _detector(cp.patch(upstream))
    fired = _replay(det, patched=True, fixture=FIXTURE_B, pauses=PAUSES_B)
    # T2 (the blocked head) logged only ~16 counted windows, split across
    # speed bands, so no band reaches warm-up; every head that printed a
    # normal amount arms.
    assert {0, 1, 3} <= {h for h, _ in det._ov_chew_base}
    assert [f for f in fired if f[1] in (0, 1)] == []


def test_vary_gate_off_is_blind_on_slow_print(upstream):
    # Known-bad baseline for the test above: with the varying-speed path
    # disabled (deployed 10-12 behaviour) T0-T2 never arm.
    det = _detector(cp.patch(upstream))
    det._ov_chew_init(det.Cfg({"airlog_chew_vary_min": 0.,
                               "airlog_chew_rel_ratio": 2.5}))
    _replay(det, patched=True, fixture=FIXTURE_B, pauses=PAUSES_B)
    assert sorted({h for h, _ in det._ov_chew_base}) == [3]


def test_constant_feed_window_neither_learns_nor_counts(upstream):
    det = _detector(cp.patch(upstream))
    for i in range(10):
        det._airlog_chew_eval(2, 1000., 5, 5., float(i * 10), 2.0)  # varying
    base = det._ov_chew_base[(2, 1)]
    # Two constant-speed windows at a level that WOULD be suspicious (6x)
    # if counted: no learning, no run, no latch.
    det._airlog_chew_eval(2, 6000., 5, 5., 100., 5.0)
    det._airlog_chew_eval(2, 6000., 5, 5., 110., 5.0)
    assert det._ov_chew_base[(2, 1)] == base
    assert det._ov_chew_rel_run.get(2) is None
    assert det._airlog_chew_latched == set()
    # The same level with varying speed does count.
    det._airlog_chew_eval(2, 6000., 5, 5., 120., 2.0)
    assert det._ov_chew_rel_run.get(2) == 1


FIXTURE_C = os.path.join(HERE, "testdata", "airlog_2026-10-14_logo_shield_fp.csv.gz")
# Fresh print starts (UTC, Moonraker history) inside fixture C.
STARTS_C = ["2026-10-13 09:10:19", "2026-10-14 00:31:07", "2026-10-14 16:15:14"]


def _replay_with_resume(det, fixture, starts):
    """Like a user: every latch is a pause that is resumed straight away."""
    from datetime import datetime
    starts, fires = list(starts), []
    for ts, head, moving, vmax, delta, vmean in _rows(fixture):
        while starts and ts >= starts[0]:
            for d in (det._ov_chew_base, det._ov_chew_warm, det._ov_chew_rel_run,
                      det._ov_chew_last, det._airlog_chew_run):
                d.clear()
            det._airlog_chew_latched.clear()
            starts.pop(0)
        t = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S.%f").timestamp()
        det._airlog_chew_eval(head, delta, moving, vmax, t, vmean)
        if head in det._airlog_chew_latched:
            fires.append((ts, head))
            det._airlog_chew_latched.discard(head)
            det._ov_chew_resume(head)
    return fires


def test_speed_jumps_do_not_pause_logo_shield(upstream):
    # 2026-10-14: the single-baseline rule paused 9 times, 8 on T1, each when
    # T1 sped up (vel_max ~2 -> 6-8) after learning its baseline at ~1 mm/s.
    # Per-speed-band baselines cut that to 3 while still catching 10-12's
    # tangle (test_patched_fires_on_t3_tangle_only).
    fires = _replay_with_resume(_detector(cp.patch(upstream)), FIXTURE_C, STARTS_C)
    assert len(fires) <= 3, fires


def test_single_baseline_known_bad_on_logo_shield(upstream):
    # Known-bad for the test above: bands off (one baseline per head).
    det = _detector(cp.patch(upstream))
    det._ov_chew_init(det.Cfg({"airlog_chew_band_lo": 0., "airlog_chew_band_hi": 0.}))
    assert len(_replay_with_resume(det, FIXTURE_C, STARTS_C)) >= 8


def test_slow_band_baseline_never_judges_faster_windows(upstream):
    det = _detector(cp.patch(upstream))
    for i in range(10):                     # learn at ~2 mm/s: base 1000
        det._airlog_chew_eval(1, 1000., 5, 2., float(i * 10), 1.0)
    for i in range(2):                      # speeds up: 7000 at 7 mm/s
        det._airlog_chew_eval(1, 7000., 5, 7., 100. + i * 10, 3.0)
    assert det._airlog_chew_latched == set()
    assert (1, 1) not in det._ov_chew_base  # mid band still warming up


def test_rel_ratio_zero_restores_upstream_behaviour(upstream):
    det = _detector(cp.patch(upstream))
    det._ov_chew_init(det.Cfg({"airlog_chew_rel_ratio": 0.}))
    assert _replay(det, patched=True) == []


def test_swap_window_resets_relative_run(upstream):
    det = _detector(cp.patch(upstream))
    for i in range(10):                      # warm a baseline of 1000
        det._airlog_chew_eval(3, 1000., 5, 30., float(i * 10))
    det._airlog_chew_eval(3, 6000., 5, 30., 100.)   # suspicious #1
    det._swap_in_progress = True
    det._airlog_chew_eval(3, 6000., 5, 30., 110.)   # swap: reset, not counted
    det._swap_in_progress = False
    det._airlog_chew_eval(3, 6000., 5, 30., 120.)   # suspicious #1 again
    assert det._airlog_chew_latched == set()
    det._airlog_chew_eval(3, 6000., 5, 30., 130.)   # #2 -> fires
    assert det._airlog_chew_latched == {3}
    assert det.events[-1][1].get("kind") == "rel"


def test_slow_window_breaks_run_and_never_learns(upstream):
    det = _detector(cp.patch(upstream))
    for i in range(10):
        det._airlog_chew_eval(3, 1000., 5, 30., float(i * 10))
    base = det._ov_chew_base[(3, 2)]
    det._airlog_chew_eval(3, 6000., 5, 30., 100.)
    det._airlog_chew_eval(3, 200., 5, 5., 110.)     # constant feed, vmax<10
    det._airlog_chew_eval(3, 6000., 5, 30., 120.)
    assert det._airlog_chew_latched == set()
    assert det._ov_chew_base[(3, 2)] == base


def test_print_start_and_resume_reset(upstream):
    out = cp.patch(upstream)
    # Fresh print forgets baselines; resume from our pause keeps them.
    fresh = out.split("if not _is_resume:", 1)[1].split("elif _src_head", 1)[0]
    assert "self._ov_chew_base.clear()" in fresh
    resume = out.split("elif _src_head is not None:", 1)[1].split(
        "self._resistance_pause_source_head = None", 1)[0]
    assert "self._ov_chew_resume(_src_head)" in resume


def _warm(det, head=3, level=1000.):
    for i in range(10):
        det._airlog_chew_eval(head, level, 5, 30., float(i * 10))


def test_resume_after_rel_pause_relearns_instead_of_looping(upstream):
    # Review HIGH: a legit regime shift to >=2.5x must not re-pause after
    # every resume. A rel pause re-learns that head's baseline on resume.
    det = _detector(cp.patch(upstream))
    _warm(det)
    t = 100.
    for _ in range(2):
        det._airlog_chew_eval(3, 6000., 5, 30., t); t += 10
    assert det._airlog_chew_latched == {3}
    det._airlog_chew_latched.discard(3)      # what upstream's resume does
    det._ov_chew_resume(3)
    for _ in range(12):                      # same elevated level continues
        det._airlog_chew_eval(3, 6000., 5, 30., t); t += 10
    assert det._airlog_chew_latched == set()
    assert det._ov_chew_base[(3, 2)] == 6000.


def test_resume_after_abs_pause_keeps_baseline(upstream):
    det = _detector(cp.patch(upstream))
    _warm(det)
    det._ov_chew_kind[3] = "abs"
    det._ov_chew_resume(3)
    assert det._ov_chew_base[(3, 2)] == 1000.


def test_abs_chew_windows_never_train(upstream):
    # Review MEDIUM: an abs-chew window (>= 25000) must not seed or drag
    # the baseline, during warm-up or after.
    det = _detector(cp.patch(upstream))
    for i in range(9):
        det._airlog_chew_eval(2, 12000., 5, 30., float(i * 10))
    det._airlog_chew_eval(2, 30000., 5, 30., 90.)
    assert (2, 2) not in det._ov_chew_base        # 30000 not counted to warm-up
    det._airlog_chew_eval(2, 12000., 5, 30., 100.)
    assert det._ov_chew_base[(2, 2)] == 12000.
    det._airlog_chew_eval(2, 29000., 5, 30., 110.)
    assert det._ov_chew_base[(2, 2)] == 12000.
