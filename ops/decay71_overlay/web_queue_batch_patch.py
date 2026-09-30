#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Local overlay patch for the decay71 multiACE web console (app.js).

Problem: outside the Fluidd panel, the command queue runs a multi-step swap
(e.g. Unload T2 -> Load T2 from ACE 2) one POST /api/macro at a time. Each
POST is held open until Klipper finishes that step (2+ minutes for an
unload). If a phone drops that fetch (screen dim, app switch, Wi-Fi power
save), the queue marks the step failed with "!" and pauses. The unload itself
has already succeeded, but the load is never sent.

Fix: use the batch path in every mode, not only the panel. The batch path
already exists for the panel, and it posts the whole run to /api/macro-batch
(202) as a single gcode script, so Klipper owns the sequence. If one step
raises, Klipper aborts the rest of the script, so a failed unload is never
followed by a load.

The patch makes two edits:
  1. enqueue() always defers the dispatch by one microtask, so an
     unload+load burst reaches the dispatcher together.
  2. _scheduleAdvance() batches whenever more than one command is queued.

Idempotent. Exits non-zero if app.js does not match the expected upstream
text, e.g. after a decay71 update changed it. Re-check the patch then.

Usage: web_queue_batch_patch.py <path/to/app.js> [--check]
  --check  report patched / unpatched / unknown without writing.
"""
import sys

MARKER = "/* multiace-overlay: web-queue-batch */"

EDITS = [
    (
        "        if (panelMode) queueMicrotask(_scheduleAdvance);\n"
        "        else _scheduleAdvance();\n",
        "        queueMicrotask(_scheduleAdvance); " + MARKER + "\n",
    ),
    (
        "      if (panelMode && arr.filter(it => it.status === 'queued')"
        ".length > 1) {\n",
        "      if (arr.filter(it => it.status === 'queued').length > 1) { "
        + MARKER + "\n",
    ),
]


def classify(text):
    if all(new in text for _, new in EDITS):
        return "patched"
    if all(text.count(old) == 1 for old, _ in EDITS):
        return "unpatched"
    return "unknown"


def patch(text):
    for old, new in EDITS:
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
        return 0 if state in ("patched", "unpatched") else 1
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
    print("patched: %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
