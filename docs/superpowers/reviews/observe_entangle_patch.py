#!/usr/bin/env python3
# License: GPL-3.0
"""Log-only OBSERVATION patch for the stock filament_entangle_detect on a
decay71 0.99.2b Snapmaker U1 (Davinci-U1).

Purpose: gather evidence for whether the stock entangle detector's wheel-channel
mapping produces a VALID per-head signal under multi-ACE topology, WITHOUT any
risk of pausing a good print. It:

  1. Neuters the pause path: when the detector would declare a tangle it logs
     `[entangle-OBSERVE] WOULD-PAUSE e[N] ...` and continues (never pauses,
     never raises the 523/38 exception).
  2. Bypasses decay71's blanket skip so the detector actually EVALUATES and emits
     its periodic `[entangle] e[N] ... whl:...` lines for the active head.
  3. No-ops decay71's `_disable_stock_entangle_detect` so the detector follows its
     normal timer lifecycle (guarantees the periodic logs run during a print).

Safety: OBSERVE mode is the default and the ONLY behavior this patch enables.
It cannot pause, cancel, or move anything. Fully reverted by `--revert`.

Idempotent. Backs up each file once to <file>.pre-observe.bak. Refuses to patch
if an anchor string is not found (fails loud, never writes a half-patched file).

Run on the printer as root (files are root-owned):
    python3 observe_entangle_patch.py            # apply
    python3 observe_entangle_patch.py --revert    # restore originals
Then restart Klipper:  /etc/init.d/S60klipper restart   (BusyBox; Moonraker
RESTART does NOT reload Python modules).
"""
import os
import sys

EXTRAS = "/home/lava/klipper/klippy/extras"
DETECTOR = os.path.join(EXTRAS, "filament_entangle_detect.py")
ACE = os.path.join(EXTRAS, "ace.py")

# --- Detector patch 1: module-level observe flag (anchored on the MIN_CNT const) ---
DET_ANCHOR_1 = "ENTANGLE_DETECT_MIN_CNT"
DET_FLAG_LINE = "ENTANGLE_OBSERVE_ONLY = True  # log-only: never pause (observation build)\n"

# --- Detector patch 2: evaluate even when decay71 set skip_check_flag ---
DET_FIND_2 = ("            if self.skip_check_flag == True:\n"
              "                return False\n")
DET_REPL_2 = ("            if self.skip_check_flag == True and not ENTANGLE_OBSERVE_ONLY:\n"
              "                return False\n")

# --- Detector patch 3: in observe mode, log the would-be trip and DO NOT pause ---
DET_FIND_3 = ('            if is_tangled:\n'
              '                self.printer.send_event("print_stats:update_exception_info",\n')
DET_REPL_3 = (
    '            if is_tangled and ENTANGLE_OBSERVE_ONLY:\n'
    '                import logging as _lg\n'
    '                _lg.warning("[entangle-OBSERVE] WOULD-PAUSE e[%d] d_pos:%.2f "\n'
    '                            "d_cnt:%d d_cnt2:%d dest:%d new_cnt:%d new_cnt2:%d" % (\n'
    '                            self.extruder_index, delta_position, delta_count,\n'
    '                            delta_count_2, dest_delta_count, new_wheel_counts,\n'
    '                            new_wheel_2_counts))\n'
    '                self.last_position = new_position\n'
    '                self.last_wheel_counts = new_wheel_counts\n'
    '                self.last_wheel_2_counts = new_wheel_2_counts\n'
    '                return self.reactor.monotonic() + CHECK_ENTANGLE_INTERVAL\n'
    '            if is_tangled:\n'
    '                self.printer.send_event("print_stats:update_exception_info",\n')

# --- Detector patch 4: dump ALL wheel channels for the active head every 5s,
#     to find whether any channel actually advances during extrusion ---
DET_FIND_4 = (
    '                logging.info(f"[entangle] e[{self.extruder_index}], pos:{new_position}, whl:{new_wheel_counts}, whl2:{new_wheel_2_counts} "\n'
    '                             f"whl_time:{wheel_data_update_time:0.4f}, whl2_time:{wheel_2_data_update_time:0.4f}, cur_time:{self.reactor.monotonic():0.4f}")')
DET_REPL_4 = DET_FIND_4 + (
    '\n'
    '                try:\n'
    '                    _fm = self.filament_feed_module\n'
    '                    _all = []\n'
    '                    for _ch in range(8):\n'
    '                        try:\n'
    '                            _all.append("w%d=%d/w2=%d" % (_ch, _fm.wheel[_ch].get_counts(), _fm.wheel_2[_ch].get_counts()))\n'
    '                        except Exception:\n'
    '                            break\n'
    '                    logging.warning("[entangle-ALLCH] e[%d] sel_ch=%s pos:%.2f | %s" % (\n'
    '                        self.extruder_index, self.filament_feed_channel, new_position, " ".join(_all)))\n'
    '                except Exception as _e:\n'
    '                    logging.warning("[entangle-ALLCH] e[%d] dump-failed: %s" % (self.extruder_index, _e))')

# --- ace.py patch: no-op decay71's blanket disable so the detector stays live ---
ACE_FIND = ("    def _disable_stock_entangle_detect(self):\n"
            "        for head in range(4):\n")
ACE_REPL = ("    def _disable_stock_entangle_detect(self):\n"
            "        import logging as _lg\n"
            "        _lg.info('[multiACE] OBSERVE build: leaving stock entangle detect ENABLED (log-only)')\n"
            "        return\n"
            "        for head in range(4):\n")

APPLIED_MARK = "ENTANGLE_OBSERVE_ONLY = True"


def _read(path):
    with open(path) as f:
        return f.read()


def _write(path, text):
    # Preserve the existing file's owner+mode. Klipper runs as `lava`; a naive
    # mkstemp+replace leaves the file root:root 0600 -> Klipper can't read it ->
    # PermissionError on the next restart. Copy the original stat onto the temp
    # before the atomic replace.
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


def _backup(path):
    bak = path + ".pre-observe.bak"
    if not os.path.exists(bak):
        with open(path) as s, open(bak, "w") as d:
            d.write(s.read())
        print("  backed up -> %s" % bak)


def apply():
    for path in (DETECTOR, ACE):
        if not os.path.exists(path):
            sys.exit("ERROR: not found: %s" % path)
    det = _read(DETECTOR)
    if APPLIED_MARK in det:
        print("Detector already patched; nothing to do. (use --revert to undo)")
        return
    # detector
    if (DET_ANCHOR_1 not in det or DET_FIND_2 not in det
            or DET_FIND_3 not in det or DET_FIND_4 not in det):
        sys.exit("ERROR: detector anchors not found — deployed file differs from "
                 "expected decay71 0.99.2b. Aborting (no changes written).")
    # insert flag on the line after the MIN_CNT constant's line
    lines = det.splitlines(keepends=True)
    out = []
    inserted = False
    for ln in lines:
        out.append(ln)
        if not inserted and ln.startswith(DET_ANCHOR_1):
            out.append(DET_FLAG_LINE)
            inserted = True
    det = "".join(out)
    det = det.replace(DET_FIND_2, DET_REPL_2, 1)
    det = det.replace(DET_FIND_3, DET_REPL_3, 1)
    det = det.replace(DET_FIND_4, DET_REPL_4, 1)
    # ace.py
    ace = _read(ACE)
    if ACE_FIND not in ace:
        sys.exit("ERROR: ace.py anchor not found. Aborting (no changes written).")
    ace = ace.replace(ACE_FIND, ACE_REPL, 1)
    # commit
    _backup(DETECTOR)
    _write(DETECTOR, det)
    print("  patched %s" % DETECTOR)
    _backup(ACE)
    _write(ACE, ace)
    print("  patched %s" % ACE)
    # compile check
    import py_compile
    for path in (DETECTOR, ACE):
        py_compile.compile(path, doraise=True)
    print("OK: observe patch applied and both files compile. "
          "Restart Klipper: /etc/init.d/S60klipper restart")


def revert():
    for path in (DETECTOR, ACE):
        bak = path + ".pre-observe.bak"
        if os.path.exists(bak):
            with open(bak) as s, open(path, "w") as d:
                d.write(s.read())
            print("  restored %s" % path)
        else:
            print("  no backup for %s (unchanged?)" % path)
    print("OK: reverted. Restart Klipper: /etc/init.d/S60klipper restart")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--revert":
        revert()
    else:
        apply()
