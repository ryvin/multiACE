#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Local overlay patch for the decay71 multiACE web console (app.js).

Problem: the command queue POSTs /api/macro once per step and holds the
request open until Klipper finishes that step. An unload takes 1.5-2.5 min.
A phone drops that fetch when the screen dims, the user switches apps, or
Wi-Fi power save kicks in. The queue then marks the step "!" and pauses,
although Klipper has already finished the unload cleanly. A queued load is
never sent.

Edits:
  batch  (1, 2)  Use the existing /api/macro-batch path whenever more than one
                 command is queued, not only in the Fluidd panel. Klipper then
                 owns the whole unload->load sequence. A failing step aborts
                 the rest of the gcode script.
  drop   (3-5)   For a single command: if the fetch throws after it was
                 clearly in flight (>5s), treat it as a dropped connection,
                 not a failure. Wait until Klipper is idle again, then mark
                 the step failed only if a NEW error notification arrived
                 meanwhile. Notification ids are monotonic on the server.

Idempotent and partial-aware. Each edit is applied only if its upstream text
is present and its patched text is absent. The script refuses to write if any
edit matches neither, e.g. after a decay71 update changed app.js. Re-check
the patch then.

Usage: web_queue_batch_patch.py <path/to/app.js> [--check]
  --check  print patched / partial / unpatched / unknown, write nothing.
"""
import sys

MARKER = "/* multiace-overlay: web-queue-batch */"
DROP_MARKER = "/* multiace-overlay: web-queue-drop */"

DROP_HELPER = (
    "    async function _ovAwaitDrop(t0, base) { " + DROP_MARKER + "\n"
    "      // The phone dropped the long POST but Klipper keeps running the\n"
    "      // command. A throw within 5s means it never got going: report it.\n"
    "      if (Date.now() - t0 < 5000) return '';\n"
    "      setMacroLog('connection dropped - waiting for the printer to "
    "finish...');\n"
    "      const deadline = Date.now() + 30 * 60 * 1000;\n"
    "      while (Date.now() < deadline) {\n"
    "        await new Promise(res => setTimeout(res, 3000));\n"
    "        await reloadState();\n"
    "        if (loadError.value) continue;\n"
    "        if (state.printer_state === 'busy' || state.swap_in_progress) "
    "continue;\n"
    "        await loadNotifications();\n"
    "        const err = notifications.value.find(\n"
    "          n => n.level === 'error' && Number(n.id) > base);\n"
    "        setMacroLog('');\n"
    "        return err ? String(err.msg) : null;\n"
    "      }\n"
    "      return 'printer still busy after 30 min';\n"
    "    }\n"
)

EDITS = [
    (  # batch 1: defer dispatch so an unload+load burst is seen together
        "        if (panelMode) queueMicrotask(_scheduleAdvance);\n"
        "        else _scheduleAdvance();\n",
        "        queueMicrotask(_scheduleAdvance); " + MARKER + "\n",
    ),
    (  # batch 2: batch whenever more than one command is queued
        "      if (panelMode && arr.filter(it => it.status === 'queued')"
        ".length > 1) {\n",
        "      if (arr.filter(it => it.status === 'queued').length > 1) { "
        + MARKER + "\n",
    ),
    (  # drop 3: helper before _runItem
        "    async function _runItem(it) {\n",
        DROP_HELPER + "    async function _runItem(it) {\n",
    ),
    (  # drop 4: record send time + newest known notification id
        "      const script = parts.join(' ');\n"
        "      try {\n"
        "        const r = await fetch(`${API}/macro`, {\n",
        "      const script = parts.join(' ');\n"
        "      await loadNotifications(); " + DROP_MARKER + "\n"
        "      const _ovBase = notifications.value.reduce(\n"
        "        (m, n) => Math.max(m, Number(n.id) || 0), 0);\n"
        "      const _ovT0 = Date.now();\n"
        "      try {\n"
        "        const r = await fetch(`${API}/macro`, {\n",
    ),
    (  # drop 5: dropped connection -> wait for the printer, then decide
        "      } catch (e) {\n"
        "        it.status = 'error';\n"
        "        it.error = String(e);\n"
        "        it.silent = false;\n"
        "        cmdPaused.value = true;\n"
        "        cmdPausedByError = true;\n"
        "      } finally {\n",
        "      } catch (e) {\n"
        "        const drop = await _ovAwaitDrop(_ovT0, _ovBase); "
        + DROP_MARKER + "\n"
        "        if (drop === null) {\n"
        "          const idx = cmdQueue.value.indexOf(it);\n"
        "          if (idx >= 0) cmdQueue.value.splice(idx, 1);\n"
        "          it.status = 'done';\n"
        "        } else {\n"
        "          it.status = 'error';\n"
        "          it.error = drop || String(e);\n"
        "          it.silent = false;\n"
        "          cmdPaused.value = true;\n"
        "          cmdPausedByError = true;\n"
        "        }\n"
        "      } finally {\n",
    ),
]


def edit_states(text):
    """Per edit: 'new' (applied), 'old' (applicable once), or 'none'."""
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
