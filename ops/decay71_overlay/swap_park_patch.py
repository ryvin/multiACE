#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Local overlay patch: dashboard swaps park the outgoing filament.

In multi mode, loading a slot into a toolhead that holds filament from the
other ACE queues ACE_UNLOAD_HEAD and then ACE_LOAD_HEAD. Upstream unloads
with the full retract_length (1950 mm on Davinci-U1), rewinding the old
filament all the way into its ACE. Only the splitter has to be cleared. The
firmware's own in-print ACE_SWAP_HEAD therefore unloads with
RETRACT_LENGTH=<swap_retract_length> (900 mm here). This patch makes the
dashboard swap do the same, which leaves the old filament parked close by so
switching back is quick.

Resolution matches the firmware's get_swap_retract_length(): per-slot, then
per-ACE, then global [ace] swap_retract_length, keyed by the OUTGOING
filament's ace/slot. The first defined value wins. 0 means a full unload, as
in firmware. If the config did not load, the swap falls back to upstream's
full unload. Plain "Unload" buttons are unchanged and still rewind fully.
Head mode is unchanged too: there the slots merge inside the ACE, so a
splitter-length park would not clear the path.

Idempotent. Refuses to write if app.js no longer matches upstream.
Usage: swap_park_patch.py <path/to/app.js> [--check]
"""
import sys

MARKER = "/* multiace-overlay: swap-park */"

HELPER = (
    "    function _ovSwapUnloadArgs(head, th) { " + MARKER + "\n"
    "      // Park the outgoing filament just past the splitter instead of a\n"
    "      // full rewind: same per-slot > per-ACE > global priority as the\n"
    "      // firmware's ACE_SWAP_HEAD. 0 / unknown -> upstream full unload.\n"
    "      const args = {HEAD: head};\n"
    "      if (state.mode !== 'multi' || !th) return args;\n"
    "      const aceCfg = configForm.perAce?.[th.ace] || {};\n"
    "      const slotCfg = aceCfg.perSlot?.[th.slot] || {};\n"
    "      for (const v of [slotCfg.swap_retract_length,\n"
    "                       aceCfg.swap_retract_length,\n"
    "                       configForm.swap_retract_length]) {\n"
    "        const n = Number(v);\n"
    "        if (v === '' || v == null || !Number.isFinite(n)) continue;\n"
    "        if (n > 0) args.RETRACT_LENGTH = Math.round(n);\n"
    "        return args;\n"
    "      }\n"
    "      return args;\n"
    "    }\n"
)

EDITS = [
    (
        "    function loadSlot(aceIdx, slotIdx) {\n",
        HELPER + "    function loadSlot(aceIdx, slotIdx) {\n",
    ),
    (
        "        enqueue(\"ACE_UNLOAD_HEAD\", {HEAD: slotIdx});\n"
        "        enqueue(\"ACE_LOAD_HEAD\",   {HEAD: slotIdx, ACE: aceIdx});\n",
        "        enqueue(\"ACE_UNLOAD_HEAD\", _ovSwapUnloadArgs(slotIdx, th)); "
        + MARKER + "\n"
        "        enqueue(\"ACE_LOAD_HEAD\",   {HEAD: slotIdx, ACE: aceIdx});\n",
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
        print("REFUSING: %s does not match the expected decay71 app.js "
              "text (upstream changed?)" % path, file=sys.stderr)
        return 1
    out = patch(text)
    if classify(out) != "patched":
        print("REFUSING: patch did not apply cleanly", file=sys.stderr)
        return 1
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)
    print("patched (%s -> patched): %s" % (state, path))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
