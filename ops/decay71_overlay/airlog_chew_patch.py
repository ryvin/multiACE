#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Local overlay patch: head-relative airlog chew detector.

Upstream's airlog chew detector pauses only on >= 2 consecutive windows of
coil_delta >= 25000 on the active head. On 2026-10-12 the T3 spool on ACE 2
slot 4 tangled. T3 normally reads ~1000-2000 and its chew ramp peaked at
10591 (5-7x its own level), so the detector never fired and T3 air-printed.
The other heads legitimately reach 21-31k in the same print, so the absolute
threshold can't simply be lowered.

This adds a RELATIVE rule next to the unchanged absolute one. Each head learns
its own baseline from DYNAMIC windows (vel_max >= airlog_chew_vmax_min). The
seed is the median of the first airlog_chew_warmup unsuspicious windows,
followed by an EMA with weight airlog_chew_alpha. A window is suspicious when
coil_delta >= airlog_chew_rel_floor AND >= airlog_chew_rel_ratio x that
baseline. AIRLOG_CHEW_WINDOWS consecutive suspicious dynamic windows of the
same head fire the existing warn / pause path (pause only with Air-Print
Detection on).

The following break a run:
- a slow (constant-feed) window
- a swap or pick-check
- more than airlog_chew_max_gap s since the head's last counted window

Suspicious windows and absolute-chew windows (>= 25000) never train the
baseline. A fresh print re-learns all baselines. A resume from our own
relative-rule pause re-learns that head's baseline, so a legitimate level
shift can't pause the print again after every resume. After an absolute
pause the baseline is kept.

Backtest on that print (testdata/, test_airlog_chew_patch.py): fires on T3 at
12:13:49, ~11 min before the filament stopped, and nowhere else. Every knob
is an optional [ace] option. Set airlog_chew_rel_ratio: 0 to get upstream
behaviour back. No live low-side ("flow lost") check is included, because
the low side was not separable from normal printing in that data.

Idempotent. Refuses to write if ace.py no longer matches upstream.
Usage: airlog_chew_patch.py <path/to/klippy/extras/ace.py> [--check]
"""
import sys

MARKER = "# multiace-overlay: rel-chew"

CONSTS = (
    "AIRLOG_CHEW_MIN_MOVING = 3\n"
    + MARKER + " (defaults; each is an optional [ace] option, lower-case).\n"
    "# Head-relative rule: see ops/decay71_overlay/airlog_chew_patch.py.\n"
    "AIRLOG_CHEW_REL_RATIO = 2.5   # x head baseline; 0 = relative rule off\n"
    "AIRLOG_CHEW_REL_FLOOR = 4000.  # delta must also clear this\n"
    "AIRLOG_CHEW_WARMUP = 10       # dynamic windows before the rule arms\n"
    "AIRLOG_CHEW_ALPHA = 0.1       # baseline EMA weight after warm-up\n"
    "AIRLOG_CHEW_VMAX_MIN = 10.    # window is dynamic iff vel_max >= this\n"
    "AIRLOG_CHEW_MAX_GAP = 600.    # s of head silence that breaks a run\n"
)

INIT = (
    "        self._airlog_chew_latched = set()  # heads warned/paused this print\n"
    "        self._ov_chew_init(config)  " + MARKER + "\n"
)

METHODS = (
    "    def _ov_chew_init(self, config):  " + MARKER + "\n"
    "        self.airlog_chew_rel_ratio = config.getfloat(\n"
    "            'airlog_chew_rel_ratio', AIRLOG_CHEW_REL_RATIO, minval=0.)\n"
    "        self.airlog_chew_rel_floor = config.getfloat(\n"
    "            'airlog_chew_rel_floor', AIRLOG_CHEW_REL_FLOOR, minval=0.)\n"
    "        self.airlog_chew_warmup = config.getint(\n"
    "            'airlog_chew_warmup', AIRLOG_CHEW_WARMUP, minval=1)\n"
    "        self.airlog_chew_alpha = config.getfloat(\n"
    "            'airlog_chew_alpha', AIRLOG_CHEW_ALPHA, minval=0., maxval=1.)\n"
    "        self.airlog_chew_vmax_min = config.getfloat(\n"
    "            'airlog_chew_vmax_min', AIRLOG_CHEW_VMAX_MIN, minval=0.)\n"
    "        self.airlog_chew_max_gap = config.getfloat(\n"
    "            'airlog_chew_max_gap', AIRLOG_CHEW_MAX_GAP, minval=0.)\n"
    "        self._ov_chew_base = {}     # head -> baseline (after warm-up)\n"
    "        self._ov_chew_warm = {}     # head -> warm-up deltas\n"
    "        self._ov_chew_rel_run = {}  # head -> consecutive suspicious\n"
    "        self._ov_chew_last = {}     # head -> time of last dynamic window\n"
    "        self._ov_chew_kind = {}     # head -> 'abs'/'rel' of the run\n"
    "\n"
    "    def _ov_chew_rel_reset(self, head):  " + MARKER + "\n"
    "        self._ov_chew_rel_run.pop(head, None)\n"
    "        self._ov_chew_last.pop(head, None)\n"
    "\n"
    "    def _ov_chew_resume(self, head):  " + MARKER + "\n"
    "        # Resume from OUR pause on this head. After a relative-rule pause\n"
    "        # the head re-learns its baseline: the user has inspected the lane,\n"
    "        # and a legit regime shift must not re-pause after every resume\n"
    "        # (the absolute rule still guards it while it re-warms).\n"
    "        self._ov_chew_rel_reset(head)\n"
    "        if self._ov_chew_kind.pop(head, None) == 'rel':\n"
    "            self._ov_chew_base.pop(head, None)\n"
    "            self._ov_chew_warm.pop(head, None)\n"
    "\n"
    "    def _ov_chew_window(self, head, delta, nmove, vmax, eventtime):\n"
    "        \"\"\"" + MARKER.lstrip('# ') + ": score one closed window. Returns the\n"
    "        longer of the absolute run (upstream rule, unchanged) and the\n"
    "        head-relative run (patch docstring). Never raises.\"\"\"\n"
    "        if delta is None or nmove < AIRLOG_CHEW_MIN_MOVING:\n"
    "            self._airlog_chew_run.pop(head, None)\n"
    "            self._ov_chew_rel_reset(head)\n"
    "            return 0\n"
    "        if delta >= AIRLOG_CHEW_DELTA:\n"
    "            abs_run = self._airlog_chew_run.get(head, 0) + 1\n"
    "            self._airlog_chew_run[head] = abs_run\n"
    "        else:\n"
    "            abs_run = 0\n"
    "            self._airlog_chew_run.pop(head, None)\n"
    "        rel_run = 0\n"
    "        try:\n"
    "            rel_run, base = self._ov_chew_rel(head, delta, vmax, eventtime)\n"
    "        except Exception:\n"
    "            logging.exception('[multiACE] airlog rel-chew eval failed')\n"
    "            self._ov_chew_rel_reset(head)\n"
    "            base = None\n"
    "        run = max(abs_run, rel_run)\n"
    "        if run:\n"
    "            kind = 'abs' if abs_run >= rel_run else 'rel'\n"
    "            self._ov_chew_kind[head] = kind\n"
    "            logging.warning(\n"
    "                '[multiACE] [airlog] CHEW window head=%d delta=%.0f '\n"
    "                'base=%s run=%d/%d (%s)' % (\n"
    "                    head, delta, '-' if base is None else '%.0f' % base,\n"
    "                    run, AIRLOG_CHEW_WINDOWS, kind))\n"
    "        return run\n"
    "\n"
    "    def _ov_chew_rel(self, head, delta, vmax, eventtime):\n"
    "        \"\"\"Head-relative rule. Returns (run, baseline).\"\"\"\n"
    "        base = self._ov_chew_base.get(head)\n"
    "        if (self.airlog_chew_rel_ratio <= 0 or vmax is None\n"
    "                or vmax < self.airlog_chew_vmax_min):\n"
    "            # Constant-feed windows read structurally low: they neither\n"
    "            # learn nor count, and they BREAK a run (reset, not skip).\n"
    "            self._ov_chew_rel_run.pop(head, None)\n"
    "            return 0, base\n"
    "        last = self._ov_chew_last.get(head)\n"
    "        if eventtime is not None:\n"
    "            self._ov_chew_last[head] = eventtime\n"
    "            if last is not None and (eventtime - last\n"
    "                                     > self.airlog_chew_max_gap):\n"
    "                self._ov_chew_rel_run.pop(head, None)\n"
    "        if (base is not None and delta >= self.airlog_chew_rel_floor\n"
    "                and delta >= self.airlog_chew_rel_ratio * base):\n"
    "            run = self._ov_chew_rel_run.get(head, 0) + 1\n"
    "            self._ov_chew_rel_run[head] = run\n"
    "            return run, base      # suspicious windows never train\n"
    "        self._ov_chew_rel_run.pop(head, None)\n"
    "        if delta >= AIRLOG_CHEW_DELTA:\n"
    "            return 0, base        # absolute-chew windows never train\n"
    "        if base is None:\n"
    "            warm = self._ov_chew_warm.setdefault(head, [])\n"
    "            warm.append(delta)\n"
    "            if len(warm) >= self.airlog_chew_warmup:\n"
    "                # Median seed: one early spike must not become the\n"
    "                # reference (T3's first dynamic window read 5724).\n"
    "                self._ov_chew_base[head] = sorted(warm)[len(warm) // 2]\n"
    "                self._ov_chew_warm.pop(head, None)\n"
    "                logging.info('[multiACE] [airlog] chew baseline armed '\n"
    "                             'head=%d base=%.0f'\n"
    "                             % (head, self._ov_chew_base[head]))\n"
    "        else:\n"
    "            a = self.airlog_chew_alpha\n"
    "            self._ov_chew_base[head] = (1. - a) * base + a * delta\n"
    "        return 0, self._ov_chew_base.get(head)\n"
    "\n"
)

EDITS = [
    ("AIRLOG_CHEW_MIN_MOVING = 3\n", CONSTS),
    ("        self._airlog_chew_latched = set()  # heads warned/paused this print\n",
     INIT),
    (
        "                self._airlog_chew_eval(head, delta, st['nmove'])\n",
        "                self._airlog_chew_eval(head, delta, st['nmove'],\n"
        "                                       st['vmax'], eventtime)  "
        + MARKER + "\n",
    ),
    (
        "    def _airlog_chew_eval(self, head, delta, nmove):\n",
        METHODS
        + "    def _airlog_chew_eval(self, head, delta, nmove, vmax=None,\n"
        "                          eventtime=None):  " + MARKER + "\n",
    ),
    (
        "                # phases have their own verdicts. Reset, don't count.\n"
        "                self._airlog_chew_run.pop(head, None)\n",
        "                # phases have their own verdicts. Reset, don't count.\n"
        "                self._airlog_chew_run.pop(head, None)\n"
        "                self._ov_chew_rel_reset(head)  " + MARKER + "\n",
    ),
    (
        "            if (delta is None or nmove < AIRLOG_CHEW_MIN_MOVING\n"
        "                    or delta < AIRLOG_CHEW_DELTA):\n"
        "                self._airlog_chew_run.pop(head, None)\n"
        "                return\n"
        "            run = self._airlog_chew_run.get(head, 0) + 1\n"
        "            self._airlog_chew_run[head] = run\n"
        "            logging.warning(\n"
        "                '[multiACE] [airlog] CHEW window head=%d delta=%.0f '\n"
        "                'run=%d/%d' % (head, delta, run, AIRLOG_CHEW_WINDOWS))\n"
        "            if run < AIRLOG_CHEW_WINDOWS or head in self._airlog_chew_latched:\n"
        "                return\n"
        "            self._airlog_chew_latched.add(head)\n"
        "            self._ace_event('airlog_chew', head=head, delta=int(delta),\n"
        "                            run=run)\n",
        "            run = self._ov_chew_window(head, delta, nmove, vmax,\n"
        "                                       eventtime)  " + MARKER + "\n"
        "            if run < AIRLOG_CHEW_WINDOWS or head in self._airlog_chew_latched:\n"
        "                return\n"
        "            self._airlog_chew_latched.add(head)\n"
        "            self._ace_event('airlog_chew', head=head, delta=int(delta),\n"
        "                            run=run, kind=self._ov_chew_kind.get(head))\n",
    ),
    (
        "            self._airlog_chew_latched.clear()\n",
        "            self._airlog_chew_latched.clear()\n"
        "            self._ov_chew_base.clear()  " + MARKER + "\n"
        "            self._ov_chew_warm.clear()\n"
        "            self._ov_chew_rel_run.clear()\n"
        "            self._ov_chew_last.clear()\n",
    ),
    (
        "            self._airlog_chew_latched.discard(_src_head)\n",
        "            self._airlog_chew_latched.discard(_src_head)\n"
        "            self._ov_chew_resume(_src_head)  " + MARKER + "\n",
    ),
]


def edit_states(text):
    out = []
    for old, new in EDITS:
        if new in text:
            out.append("new")
        elif text.count(old) == 1:
            out.append("old")
        else:
            out.append("none")
    return out


def classify(text):
    s = edit_states(text)
    if "none" in s:
        return "unknown"
    if all(x == "new" for x in s):
        return "patched"
    if all(x == "old" for x in s):
        return "unpatched"
    return "partial"


def patch(text):
    for (old, new), st in zip(EDITS, edit_states(text)):
        if st == "old":
            text = text.replace(old, new, 1)
    return text


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    path = argv[1]
    with open(path, encoding="utf-8") as f:
        text = f.read()
    state = classify(text)
    if "--check" in argv:
        print(state)
        return 0 if state != "unknown" else 1
    if state == "patched":
        print("already patched: %s" % path)
        return 0
    if state == "unknown":
        print("REFUSING: %s does not match the expected decay71 ace.py "
              "text (upstream changed?)" % path, file=sys.stderr)
        return 1
    out = patch(text)
    if classify(out) != "patched":
        print("REFUSING: patch did not apply cleanly", file=sys.stderr)
        return 1
    try:
        compile(out, path, "exec")
    except SyntaxError as e:
        print("REFUSING: patched ace.py does not compile: %s" % e,
              file=sys.stderr)
        return 1
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)
    print("patched (%s -> patched): %s" % (state, path))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
