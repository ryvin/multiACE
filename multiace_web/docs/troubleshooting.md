# Troubleshooting

Field-tested recipes for the failure modes we've actually hit. Each entry is
*symptom → cause → fix*. Use the in-app `?` button (next to the tabs) for a
quick reference of every control on the page; this guide covers things that
need more than a sentence.

## Unload All leaves filament in the bowden tubes

**Symptom**

After `Unload All` (or `Unload Tx`), the toolhead sensors clear (`False`) but
filament is still visible in the long bowdens between the ACE Pro and the
printer's selector. ACE `gate_status` may stay at `1` for the affected slots.

**Cause**

`retract_length` in `ace.cfg` is shorter than the *full* path the filament has
to travel back: toolhead extruder (~50 mm) + selector→toolhead bowden +
selector internals + ACE→selector bowden. With 80 cm bowdens the total path
is roughly 1100–1300 mm.

A retract of 700 mm clears the toolhead path but strands the filament in the
ACE-side bowden. The ACE motor reports the commanded length as moved (encoder
counts), but the filament can't physically be pulled past the gate if the
length is too short.

**Fix**

1. Edit `ace.cfg`:
   ```ini
   retract_length: 1800       # ≈ longest expected path + ~500 mm margin
   ```
2. Save & Restart from the Config tab (or `RESTART` g-code).
3. Verify the new value loaded:
   ```bash
   curl -s "http://<printer-ip>:7125/printer/objects/query?configfile" \
     | jq '.result.status.configfile.settings.ace.retract_length'
   ```

If filament is currently stranded, do an additional `ACE_RETRACT INDEX=N
LENGTH=300 SPEED=30` per affected slot to clear it before the unloads start
working cleanly.

## Load fails with `move_extrude!raw msg:logic error!`

**Symptom**

```
[feed_loading] phase3: extrude[2] retry:0..19, coil_freq_delta:55-273
[feed_loading] phase3: wheel, cnt_a_1:1, cnt_a_2:1   (wheel never moves)
[feed] extruder[2] hanging neutral, try: 0..1
[feed][load] channel[0] auto load error: logic error!
extruder[2]: state: load_extruding, error: move_extrude!raw msg:logic error!
```

LOAD_HEAD takes ~90 s of phase3 retries, then fails. The toolhead extruder
gear can't get a grip on the incoming filament tip.

**Cause**

The filament tip at the ACE gate is **deformed** — usually a cooled blob or a
cone shaped for the wrong nozzle diameter, left over from a previous unload's
tip-shaping retract.

**Fix**

The right fix is *built in* now: enable pre-load tip refresh. See
[`tip-refresh.md`](tip-refresh.md). Default config sequence is:

```
ACE_RETRACT INDEX=N LENGTH=30 SPEED=20      # back off the deformed tip
ACE_FEED    INDEX=N LENGTH=90 SPEED=30      # push fresh filament past the gate
```

If you're recovering a stuck head right now and don't want to wait for the
config change to take effect, run those two lines manually then retry
`ACEC__Load_Tx`.

## `LOAD_HEAD_SUSPICIOUS` with `wheel_not_moving` (false positive)

**Symptom**

Activity feed shows `LOAD_HEAD_SUSPICIOUS` with `wheel_delta_a=0,
wheel_delta_b=0, reason: wheel_not_moving` immediately before every successful
`LOAD_HEAD`.

**Cause**

The wheel-encoder check fires *after* `FEED_AUTO LOAD` returns. By that point
the toolhead-side phase has finished and the ACE feed wheel is idle. Reading
zero delta on a finished feed is the usual case — the heuristic is too
conservative.

**Fix**

Treat as benign when followed by a clean `LOAD_HEAD`. Investigate only if the
suspicious flag correlates with an actual `LOAD_HEAD_FAILED`. The two are
independent failure modes; `move_extrude logic error` is the real signal.

## Status banner stuck on `SERIAL_WRITE_FAILED reconnect_failed`

**Symptom**

The dashboard's status banner shows `SERIAL_WRITE_FAILED reconnect_failed`
(or, before the v0.5.1 web fix, `Tnull SERIAL_WRITE_FAILED reconnect_failed`)
and won't clear even after the underlying USB issue is resolved.

**Cause**

`SERIAL_WRITE_FAILED` is a transport-level event with no head/slot. The state
model's `last_error` field used to capture it, but the only auto-clear path
was a successful `LOAD_HEAD` for a *matching* head — and there's no matching
head, so the banner stuck forever.

**Fix**

Already in `state.py` (v0.5.1+):

- Transport-level failures (no `head` field) no longer populate `last_error`.
  They're still in the activity feed.
- `UNLOAD_HEAD` for a matching head now also clears `last_error`, not just
  `LOAD_HEAD`.

If you're on an older deploy, kill and restart the multiace-web service:
```bash
kill $(pgrep -f multiace_web)
# then either init script or manual relaunch (see install/S62multiace-web)
```

## ACE Pro USB hangs (`status=busy`, `gate_status=[-1,-1,-1,-1]`)

**Symptom**

`ACE` object reports `status=busy` indefinitely, all `gate_status` values are
`-1`, and any g-code that talks to the ACE (`ACE_HEAD_STATUS`,
`ACEC__Unload_All`, etc.) times out at 30+ seconds.
`multiace_state.log` shows a recent `SERIAL_WRITE_FAILED reconnect_failed`.

**Cause**

The USB CDC connection between the host and the ACE Pro hung. Klipper's
serial handle is pinned to the dead device path. Hot-replugging the ACE
re-enumerates the USB device but Klipper doesn't rescan on its own — the
multiACE driver's `permanent error=reconnect_failed` state means it stops
trying.

**Fix**

1. Power-cycle the ACE Pro unit (not the printer).
2. Wait 10–15 s for `/dev/serial/by-path/...usb-0:1.3.3:1.0` to reappear:
   ```bash
   ssh lava@<printer> ls -la /dev/serial/by-path/
   ```
3. If Klipper's gcode queue is jammed (Moonraker shows many `Request
   'gcode/script' pending: Ns` lines), a plain `RESTART` won't get through.
   You need to fully restart the klippy service:
   ```bash
   ssh lava@<printer> /etc/init.d/S60klipper restart
   ```
4. Once Klipper is `ready` again, multiACE will rescan at startup and connect.

## Klipper config errors on options you just added to ace.py

**Symptom**

After deploying a new `ace.py` with a new `config.getboolean(...)` /
`config.getint(...)` call, Klipper reports:

```
configfile.ConfigError: Option 'foo' is not valid in section 'ace'
```

…even though `grep` confirms the call exists in the deployed file and the
`__pycache__/ace.cpython-NNN.pyc` mtime is newer than the source.

**Cause**

Klipper's `RESTART` g-code does a *soft* restart: it re-reads `printer.cfg`
but does not reload Python modules — the running `klippy.py` still holds the
old `ace` module in `sys.modules`. Result: the new config option is in the
file but the old module never registered it via `config.get*`, so
`check_unused_options` rejects it.

**Fix**

Hard-restart the klippy service:

```bash
ssh lava@<printer> '
  pgrep -f klippy.py | xargs -r kill
  sleep 2
  rm -rf /home/lava/klipper/klippy/extras/__pycache__ \
         /home/lava/klipper/klippy/__pycache__
  /etc/init.d/S60klipper start
'
```

Verify:
```bash
curl -s "http://<printer>:7125/printer/info" | jq '.result.process_id'   # should be a NEW pid
curl -s "http://<printer>:7125/printer/objects/query?configfile" \
  | jq '.result.status.configfile.settings.ace.foo'   # should equal what's in ace.cfg
```

## Unload aborts: `CONTROL_RETRACT_ACTION` Jinja error

**Symptom**

Every `Unload Tx` (and therefore `Unload All`) aborts with:

```
Error evaluating 'gcode_macro CONTROL_RETRACT_ACTION:gcode':
jinja2.exceptions.UndefinedError: 'dict object' has no attribute 'nozzle_diameter'

[feed][unload] channel[N]: auto unload error: custom gcode error!
[feed][unload] channel[N]: auto unload error: state mismatch!
extruder[N]: state: unload_fail, error: state_mismatch!
```

**Cause**

Stock Snapmaker `fluidd.cfg` has three lines in the `CONTROL_RETRACT_ACTION`
macro that read `nozzle_diameter` as a runtime field on the extruder object:

```jinja
{% set nozzle_diameter = printer[printer.toolhead.extruder].nozzle_diameter %}
{% set nozzle_diameter = printer['extruder'].nozzle_diameter %}
{% set nozzle_diameter = printer['extruder%d' % (extruder_index)].nozzle_diameter %}
```

`nozzle_diameter` is a *config-only* field (`printer.configfile.settings`),
not a runtime status field. `printer.extruder.nozzle_diameter` is undefined,
so the Jinja `UndefinedError` aborts the macro mid-tip-shape — and the
unload's state machine reports `state_mismatch`.

**Fix**

Append `| default(0.4)` to each of the three `nozzle_diameter` reads (0.4 mm
is the U1 stock nozzle):

```jinja
{% set nozzle_diameter = printer[printer.toolhead.extruder].nozzle_diameter | default(0.4) %}
{% set nozzle_diameter = printer['extruder'].nozzle_diameter | default(0.4) %}
{% set nozzle_diameter = printer['extruder%d' % (extruder_index)].nozzle_diameter | default(0.4) %}
```

(Two copies of the macro typically exist — patch both, six lines total.)
Then `RESTART`. This is a stock-fluidd bug, not a multiACE bug, but multiACE
unloads call this macro so it bites multiACE users hard. If your nozzle is
0.6 or 0.8 mm, change the default accordingly.

`tools/patch_fluidd_nozzle.py` automates the backup + sed + RESTART.

## Filament wedged in the toolhead after a failed load

**Symptom**

`LOAD_HEAD_FAILED` event but the runout sensor for that head reads `True` —
filament made it past the sensor but the toolhead extruder couldn't extrude
it. `head_source[N]` is `null` (multiACE knows the load failed).

**Recovery**

1. **Unload** that single head:
   ```
   ACEC__Unload_Tn          # via web /api/command  or  Diag tab
   ```
   With `retract_length` correctly tuned this clears the filament back to the
   gate.
2. **Tip refresh** the slot (skip if `tip_refresh_before_load: 1` — the next
   load will do it automatically):
   ```
   ACE_RETRACT INDEX=N LENGTH=30  SPEED=20
   ACE_FEED    INDEX=N LENGTH=150 SPEED=30
   ```
3. **Reload**:
   ```
   ACEC__Load_Tn
   ```

If the load fails a second time with the same `move_extrude` error, the issue
isn't the tip — check the toolhead extruder gear, hotend temp, and that the
selector→toolhead bowden isn't kinked.

## Misleading `gate_status=1` after a successful retract

**Symptom**

You ran a long `ACE_RETRACT` (e.g. 1800 mm), filament physically left the
bowden, but `gate_status` still reads `1` for that slot.

**Cause**

The gate sensor sits at the slot opening, *upstream* of the bowden output. As
long as filament is threaded through the ACE feed gear and the gate region —
even with the *tip* deep on the spool side — the sensor reports `1`. The
sensor reads `0` only when the filament tip is fully past the gate and into
the spool.

**Implication**

`gate_status` is **not** a reliable signal for "filament cleared the bowden."
Use the toolhead runout sensors (`sensors[N]`) for "head is empty," and
physical inspection / the multiACE state log for everything else.

## "Tool change in progress" banner is stuck (no swap actually running)

**Symptom**

The dashboard's status banner shows `Tool change in progress · Hold actions
until this completes.` and never clears, even though no swap is running and
nothing is being printed.

```
$ curl /multiace/api/state | jq .swap_in_progress       # true
$ curl :7125/printer/objects/query?ace | jq '.result.status.ace.status'   # ready
```

The most recent state log entries look like:
```
... STATE action=SWITCH_AUTO_PASSIVE swap=False
... STATE action=SWITCH_NOOP swap=True
... STATE action=SWITCH_NOOP swap=True
```

**Cause**

`cmd_ACE_SWITCH` in `ace.py` sets `_swap_in_progress = True` before calling
`_do_ace_switch`, then resets it in a `finally`. When the target ACE is
already active, `_do_ace_switch` audits `SWITCH_NOOP` and returns — *while
`_swap_in_progress` is still `True`*. That audit is the last STATE log entry
the web console sees, so its cached state stays `swap_in_progress: True`
forever. Subsequent g-code commands that emit STATE events (with the flag
correctly `False`) eventually overwrite it, but if no such event fires for a
while, the banner sticks.

**Fix (web console, already deployed)**

`state.py` defensively forces `swap_in_progress = False` whenever it ingests
a `SWITCH_NOOP` event — a no-op by definition isn't a swap. Restart the
service and the bootstrap replay clears the stuck flag.

**Fix (Klipper-side, not yet patched)**

Move the `SWITCH_NOOP` audit *outside* the swap-flag scope, or capture the
swap state explicitly in the audit payload rather than reading the (still
True) flag at the time of audit. Until that ships, the web-side guard above
is enough to keep the banner sane.

## Web console returns 502 Bad Gateway after reboot

**Symptom**

`http://<printer>/multiace/` returns nginx's `502 Bad Gateway`. Direct probe
from the printer:

```bash
ssh lava@<printer> 'pgrep -af multiace_web'   # empty
ssh lava@<printer> '/etc/init.d/S62multiace-web start'
# Starting multiace-web on port 7126...
# /etc/init.d/S62multiace-web: line 69: /var/log/multiace-web.log: Permission denied
```

**Cause**

The init script redirects daemon output to `/var/log/multiace-web.log` —
created root-owned `0600` at install time. Init (running as root) can write
to it. `lava` running the same script manually cannot, so the redirect fails
and `start-stop-daemon` never fires.

After a reboot, if init *also* didn't start S62 (BusyBox sysvinit ordering, a
prior install's stale pidfile, etc.), the service stays down with no
recovery path that doesn't touch root-owned files.

**Quick fix (no root)**

Start uvicorn directly with the log redirected to a lava-writable path:

```bash
ssh lava@<printer> '
  cd /userdata/multiace-web/app
  set -a; [ -f .env ] && . .env; set +a
  export MULTIACE_LOG_DIR=/home/lava/printer_data/logs \
         MULTIACE_CONFIG=/home/lava/printer_data/config/extended/ace.cfg \
         MOONRAKER_URL=http://127.0.0.1:7125 \
         MULTIACE_WEB_PORT=7126
  setsid /userdata/multiace-web/venv/bin/python3 -m uvicorn \
    multiace_web.server:app --host 127.0.0.1 --port 7126 \
    >>/home/lava/printer_data/logs/multiace-web.log 2>&1 </dev/null &
  disown 2>/dev/null
'
```

Verify: `curl -s http://<printer>/multiace/api/state | jq .connected` → `true`.

**Permanent fix (needs root once)**

Either:

```bash
chmod 0666 /var/log/multiace-web.log
# or
chown lava:lava /var/log/multiace-web.log
```

Or edit `/etc/init.d/S62multiace-web` line ~69 to redirect to
`/home/lava/printer_data/logs/multiace-web.log` (lava-writable, persistent,
sits next to the other multiACE logs).

Note: uvicorn binds `127.0.0.1` only; nginx proxies port 80 `/multiace/*` to
it. A direct `curl http://<printer>:7126/...` from another host **will**
fail even when the service is healthy. Probe via nginx, not the upstream
port.

## Mode switch (Multi → Normal) doesn't take effect

**Symptom**

Toggled the Mode pill in the topbar; nothing changes immediately.

**Cause**

By design — switching modes copies different Python modules into Klipper's
extras path and updates `printer.cfg` includes. A printer reboot is required
for Klipper to pick up the new module set.

**Fix**

Reboot the printer (not just klippy). The toggle's confirm dialog says
"Reboot required to take effect" for this reason.
