#!/usr/bin/env python3
# License: GPL-3.0
"""Raw ACE-serial logging patch for decay71 0.99.2b (Davinci-U1).

Definitive experiment for the snag question: does the ACE Pro itself send ANY
unsolicited buffer-empty / feed-failure message over serial when the filament is
snagged? decay71's `_process_data_for` only dispatches frames whose id matches a
pending callback and SILENTLY DROPS everything else — so an unsolicited status
would never be seen. This patch logs EVERY decoded frame the ACE sends to
`multiace_fa.log` with an `[ACE-RAW]` marker (that logger is at WARNING level by
default, so no debug flag is needed).

Read-only in effect: it only adds a log line; it changes no control flow, moves
nothing, pauses nothing. `--revert` restores the original.

Idempotent, backs up once to ace.py.pre-rawlog.bak, refuses on anchor mismatch,
preserves the file's owner+mode (Klipper runs as `lava`; a root-owned
mkstemp+replace would make it root:root 0600 and break Klipper startup).

Run on the printer as root, only when NO print is active (needs a Klipper reload):
    python3 ace_raw_serial_log_patch.py            # apply
    python3 ace_raw_serial_log_patch.py --revert    # restore
    /etc/init.d/S60klipper restart                  # BusyBox; reloads Python modules
Then run a print, induce a snag, and:
    grep -a '[ACE-RAW]' /home/lava/printer_data/logs/multiace_fa.log | tail -60
"""
import os
import sys

ACE = "/home/lava/klipper/klippy/extras/ace.py"

FIND = ("        for ret in protocol.decode_frames(buf):\n"
        "            msg_id = ret.get('id')\n")
REPL = ("        for ret in protocol.decode_frames(buf):\n"
        "            msg_id = ret.get('id')\n"
        "            try:\n"
        "                self._fa_log.warning('[ACE-RAW] idx=%d resp=%s' % (idx, ret))\n"
        "            except Exception:\n"
        "                pass\n")
MARK = "[ACE-RAW] idx=%d resp=%s"


def _write(path, text):
    st = os.stat(path) if os.path.exists(path) else None
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    if st is not None:
        try:
            os.chmod(tmp, st.st_mode & 0o777)
            os.chown(tmp, st.st_uid, st.st_gid)
        except OSError:
            pass
    os.replace(tmp, path)


def apply():
    if not os.path.exists(ACE):
        sys.exit("ERROR: not found: %s" % ACE)
    src = open(ACE).read()
    if MARK in src:
        print("Already patched; nothing to do (use --revert to undo).")
        return
    if FIND not in src:
        sys.exit("ERROR: anchor not found — deployed ace.py differs from expected "
                 "decay71 0.99.2b. Aborting (no changes written).")
    bak = ACE + ".pre-rawlog.bak"
    if not os.path.exists(bak):
        with open(ACE) as s, open(bak, "w") as d:
            d.write(s.read())
        print("  backed up -> %s" % bak)
    _write(ACE, src.replace(FIND, REPL, 1))
    import py_compile
    py_compile.compile(ACE, doraise=True)
    print("OK: raw-serial logging applied and ace.py compiles. "
          "Restart Klipper: /etc/init.d/S60klipper restart")


def revert():
    bak = ACE + ".pre-rawlog.bak"
    if os.path.exists(bak):
        with open(bak) as s:
            _write(ACE, s.read())
        print("  restored %s" % ACE)
    else:
        print("  no backup (%s) — unchanged?" % bak)
    print("OK: reverted. Restart Klipper: /etc/init.d/S60klipper restart")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--revert":
        revert()
    else:
        apply()
