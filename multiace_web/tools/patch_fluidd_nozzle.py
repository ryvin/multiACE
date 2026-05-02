"""One-shot patcher for the broken CONTROL_RETRACT_ACTION macro on the printer.

Adds `| default(0.4)` to three `nozzle_diameter` Jinja set statements in
fluidd.cfg so the macro doesn't crash with UndefinedError on filament unload.

ASCII-only output (Windows PowerShell defaults to CP1252).
"""
import json
import sys
import time
import urllib.request
from datetime import datetime

import paramiko

HOST = "192.168.1.171"
USER = "lava"
PASSWORD = "snapmaker"
CFG = "/home/lava/printer_data/config/fluidd.cfg"
WEB_BASE = "http://192.168.1.171/multiace"


def http_get_json(path):
    with urllib.request.urlopen(f"{WEB_BASE}{path}", timeout=10) as r:
        return json.load(r)


def http_post_json(path, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{WEB_BASE}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def ssh_run(client, cmd):
    stdin, stdout, stderr = client.exec_command(cmd)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    rc = stdout.channel.recv_exit_status()
    return rc, out, err


def main():
    # 1. Pre-flight: only patch when printer is idle
    print("0. Pre-flight check")
    pre = http_get_json("/api/print")
    state = pre.get("state")
    print(f"   PRE: print state = {state}")
    if state == "printing":
        print("   ABORT: print is active. Aborting to avoid disruption.")
        sys.exit(1)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASSWORD, timeout=10)

    try:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = f"{CFG}.bak-{ts}"

        # 2. Backup
        print(f"\n1. Backing up fluidd.cfg -> {backup}")
        rc, out, err = ssh_run(client, f"cp -p {CFG} {backup} && ls -l {backup}")
        if rc != 0:
            print(f"   FAIL: {err.strip()}")
            sys.exit(2)
        print(f"   OK: {out.strip()}")

        # 3. Apply three substitutions (only if pattern present)
        print("\n2. Applying patches")
        # The three target lines use slightly different left-hand expressions but
        # all end with `.nozzle_diameter %}`. We match each unique line and
        # append `| default(0.4)` before the closing %}.
        seds = [
            # printer[printer.toolhead.extruder].nozzle_diameter
            (
                r"printer\[printer\.toolhead\.extruder\]\.nozzle_diameter %}",
                "printer[printer.toolhead.extruder].nozzle_diameter | default(0.4) %}",
            ),
            # printer['extruder'].nozzle_diameter
            (
                r"printer\['extruder'\]\.nozzle_diameter %}",
                "printer['extruder'].nozzle_diameter | default(0.4) %}",
            ),
            # printer['extruder%d' % (extruder_index)].nozzle_diameter
            (
                r"printer\['extruder%d' % \(extruder_index\)\]\.nozzle_diameter %}",
                "printer['extruder%d' % (extruder_index)].nozzle_diameter | default(0.4) %}",
            ),
        ]

        for pattern, replacement in seds:
            # Skip if already patched
            check_cmd = f"grep -F {json.dumps(replacement)} {CFG} | wc -l"
            rc, out, err = ssh_run(client, check_cmd)
            count = int(out.strip() or "0")
            if count > 0:
                print(f"   SKIP (already patched): {replacement[:60]}...")
                continue

            # Use python on the printer to do an in-place regex replace; safer than escaping sed
            py = (
                "import re,sys,io;"
                f"p=open({json.dumps(CFG)},'r',encoding='utf-8');"
                "s=p.read();p.close();"
                f"new,n=re.subn({json.dumps(pattern)},{json.dumps(replacement)},s);"
                f"open({json.dumps(CFG)},'w',encoding='utf-8').write(new);"
                "print('replacements:',n)"
            )
            cmd = f"python3 -c {json.dumps(py)}"
            rc, out, err = ssh_run(client, cmd)
            if rc != 0:
                print(f"   FAIL pattern {pattern}: {err.strip()}")
                sys.exit(3)
            print(f"   {out.strip()} for {replacement[:50]}...")

        # 4. Verify all three patched lines are present
        print("\n3. Verifying patched lines")
        rc, out, err = ssh_run(client, f"grep -n 'nozzle_diameter | default(0.4)' {CFG}")
        if rc != 0 or not out.strip():
            print(f"   FAIL: no patched lines found")
            print(f"   stdout: {out!r}")
            print(f"   stderr: {err!r}")
            sys.exit(4)
        print(out.strip())
        line_count = len([l for l in out.strip().splitlines() if l.strip()])
        if line_count != 3:
            print(f"   WARN: expected 3 patched lines, found {line_count}")

        # 5. Trigger Klipper RESTART via web console
        print("\n4. Triggering Klipper RESTART via /api/command")
        try:
            resp = http_post_json("/api/command", {"command": "RESTART"})
            print(f"   POST /api/command -> {resp}")
        except urllib.error.HTTPError as e:
            print(f"   HTTP error: {e.code} {e.reason}")
            body = e.read().decode("utf-8", errors="replace")
            print(f"   body: {body}")
            sys.exit(5)
        except Exception as e:
            # RESTART tears down Moonraker briefly; expected
            print(f"   (expected disconnect during restart): {e}")

        # 6. Poll for ready
        print("\n5. Waiting for Klipper ready")
        deadline = time.time() + 60
        last_state = None
        while time.time() < deadline:
            time.sleep(2)
            try:
                s = http_get_json("/api/print")
                last_state = s.get("state")
                if last_state and last_state != "error":
                    print(f"   READY: state = {last_state}")
                    break
            except Exception as e:
                pass
        else:
            print(f"   TIMEOUT waiting for ready (last state: {last_state})")
            sys.exit(6)

        # 7. Final summary
        print("\n6. Final state")
        s = http_get_json("/api/print")
        print(f"   state = {s.get('state')}")
        print(f"   filament = {s.get('filament_present')}")
        print(f"\nDONE. To revert: cp -p {backup} {CFG} && trigger RESTART")
    finally:
        client.close()


if __name__ == "__main__":
    main()
