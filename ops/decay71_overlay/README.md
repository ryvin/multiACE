# decay71 local overlay

The Davinci-U1 runs decay71's multiACE, not this repo's fork firmware. These are
small local patches to decay71 files that we re-apply by hand after a decay71
update overwrites them. Each script is idempotent. It refuses to write if the
target no longer matches the upstream text it expects, which means you need to
re-check the patch against the new release.

| Patch | Target on printer | Why |
|---|---|---|
| `web_queue_batch_patch.py` | `/home/lava/multiace_web/frontend/app.js` | Phone browsers drop the long `/api/macro` fetch during a 1.5-2.5 min unload, so the queue marks the step `!` even though Klipper finished it cleanly. Edits 1-2 (2026-09-30) send multi-step runs to `/api/macro-batch`. Edits 3-5 (2026-10-04) handle a dropped single command: wait until Klipper is idle, then fail the step only if a new error notification arrived. Written against 1.00.1b; applies unchanged to 1.27b. |
| `airlog_chew_patch.py` | `/home/lava/klipper/klippy/extras/ace.py` | Air-Print Detection missed a real tangle on 2026-10-12. T3's chew ramp peaked at 10591, 5-7x its own normal level, but upstream pauses only at 25000. The other heads legitimately reach 21-31k, so that number can't just be lowered. The patch adds a per-head relative rule: 2 consecutive counted windows (fast, or with varying speed) at >= 3.1x the head's learned baseline and >= 4000. Replayed on that print it fires on T3 at 12:13:49 and nowhere else. On the slow 2026-10-13 Shield print, the 10-12 version left T0-T2 unarmed; the varying-speed path (added 2026-10-13) arms all four with no false fires (`test_airlog_chew_patch.py`, fixtures in `testdata/`). It cannot see a head blocked by a fragment, or a break anchored inside the ACE: only a per-head motion sensor can. Since 2026-10-14 each head keeps one baseline per speed band (<4, 4-10, >=10 mm/s vel_max), so speeding up is no longer read as resistance. That cut the replayed 10-14 Logo Shield pauses from 9 to 3 and kept the 10-12 catch. Knobs are optional `[ace]` options (`airlog_chew_rel_ratio`, `_rel_floor`, `_warmup`, `_alpha`, `_vmax_min`, `_vary_min`, `_band_lo`, `_band_hi`, `_max_gap`); `airlog_chew_rel_ratio: 0` restores upstream behaviour. Klipper only loads it on a restart. Added 2026-10-12, pending an upstream issue. |
| `swap_park_patch.py` | same `app.js` | In multi mode, a dashboard swap (loading a slot into a toolhead that holds the other ACE's filament) unloads with `RETRACT_LENGTH=<swap_retract_length>` (900 mm here; per-slot > per-ACE > global, as in the firmware's `ACE_SWAP_HEAD`) instead of a full 1950 mm rewind. The old filament stays parked just past the splitter, so switching back is quick. Plain Unload buttons and head mode are unchanged. Added 2026-10-04. |

## Current printer state (2026-10-11)

decay71 **1.27b** (installed from `multiace-v1.27b.tar.gz`, sha256 `948b508c…`).
`app.js` md5: upstream `d7500b9b…`, with both overlays `ac33ce40…`.
`klippy/extras/ace.py` md5: upstream `c5bd76c8…`. The 2026-10-12 `airlog_chew_patch` (`ed09eaa9…`) was deployed 10-12 17:35. The 10-13 revision with the varying-speed gate and ratio 3.1 gives `af888682…` and was **deployed 2026-10-13 07:57 UTC** (backup `/userdata/multiace-backups/20261013-075652-pre-rs25-vary/`). **To update an already-patched printer,** start from `ace.py.pre-overlay.bak`, which is upstream: the new patcher reports the older patched file as `unknown` by design. So: `cp -p ace.py.pre-overlay.bak ace.py`, then apply the patch, clear `__pycache__/ace.*.pyc`, and restart Klipper while idle.
Pre-upgrade backup: `/userdata/multiace-backups/20261011-103811-pre-1.27b/`.

Local settings we carry across upgrades (in `[ace]` of `extended/ace.cfg` unless noted):

| Setting | Why |
|---|---|
| `ace_device_count: 2` | two ACEs |
| `seat_overshoot_length: 20` | seating tuned for this rig |
| `retract_speed: 25` | slower rewind avoids spool tangles (default 80). Lowered from 30 on 2026-10-13 after the ACE 2 slot 4 tangle and break. |
| `[ace_tipform] pla: unloadtemp:220` | PLA unload temperature |
| `state_debug: true`, `airlog: true` | Air-Print/chew sampler on, logging coil windows to `multiace_wiggle.log`. Pausing is **armed** by the web toggle (persisted `ace__airprint_detection`, which overrides `resistance_pause` in `ace.cfg`), turned on 2026-10-11. |
| nginx `/etc/nginx/fluidd.d/multiace-web.conf`: `absolute_redirect off; port_in_redirect off;` | `/multiace` without a slash must not redirect to `http://` behind the HTTPS tunnel |

The installer's config merge keeps the `[ace]` values, but check them anyway. It
always overwrites the nginx file and `app.js`.

## Checking for a new release

```bash
ops/decay71_overlay/decay71_check.sh        # host: $DAVINCI_U1_HOST or 192.168.1.136
```

This is report-only. It reads the printer's `/multiace/api/update/check`, and when a newer
tag exists it runs both overlay `--check`s against that tag's `app.js`. Exit codes: 0 up to date,
10 update available (overlays apply), 11 update available but an overlay reports
`unknown` (fix the patch before upgrading), 1 error.

## Upgrading decay71

Use SSH as root, not the web Update button. The button runs the *installed* version's updater,
and a non-root install skips the init and nginx steps. Do it with the printer idle
(`print_stats.state` not `printing`/`paused`). Unloading the heads is not required.

1. `decay71_check.sh`. If it exits 11, fix the overlay first.
2. Back up to `/userdata/multiace-backups/<ts>-pre-<ver>/`:
   - `extended/ace.cfg` and `extended/multiace/{ace_vars.cfg,slot_overrides.json}`
   - `persistent/multiace_spools.json` and `frontend/app.js`
   - the nginx conf, `/etc/init.d/S9{8,9}multiace-web*`, `/etc/inittab` and `extras/ace.py`
3. On your PC, run `gh release download <tag> -R decay71/multiACE -p 'multiace-<tag>.tar.gz*'`
   and check the hash with `sha256sum`. Copy the tarball to the printer's `/tmp`.
4. On the printer, run
   `cd /tmp && tar xzf multiace-<tag>.tar.gz && bash /tmp/multiace/install_multiace.sh --install-web`.
   Its log reports `ace.cfg merged with shipped defaults` and leaves a `.bak.<ts>`.
5. Re-check the settings table above. Re-add the nginx lines after `auth_request`,
   then run `nginx -t && nginx -s reload`.
6. Re-apply the overlays:

   ```bash
   scp ops/decay71_overlay/web_queue_batch_patch.py ops/decay71_overlay/swap_park_patch.py root@$DAVINCI_U1_HOST:/tmp/
   ssh root@$DAVINCI_U1_HOST 'cd /home/lava/multiace_web/frontend && cp -p app.js app.js.pre-overlay.bak && python3 /tmp/web_queue_batch_patch.py app.js && python3 /tmp/swap_park_patch.py app.js'
   ```

   Then the Klipper-side overlay (Klipper loads it on the reboot in step 8):

   ```bash
   scp ops/decay71_overlay/airlog_chew_patch.py root@$DAVINCI_U1_HOST:/tmp/
   ssh root@$DAVINCI_U1_HOST 'cd /home/lava/klipper/klippy/extras && cp -p ace.py ace.py.pre-overlay.bak && python3 /tmp/airlog_chew_patch.py ace.py && rm -rf __pycache__/ace.*.pyc'
   ```

7. Re-run `multiace_plugins/filamenthub/install/restore.sh` if `/etc` was wiped.
   A PAXX firmware update can wipe the overlay, as it did in Aug 2026. That removes
   `/etc/init.d/S66filamenthub-plugin` and the nginx `/plugin/` drop-in. The plugin's
   code, deps and state live on `/userdata` and survive, so restore only re-creates
   the `/etc` pieces:

   ```bash
   ssh root@$DAVINCI_U1_HOST 'sh /userdata/filamenthub-plugin/install/restore.sh'
   ```

   It is idempotent and refuses to run while a print is `printing` or `paused`.
   See `multiace_plugins/filamenthub/README.md`.
8. Reboot. Verify:
   - klippy.log shows `[multiACE] Version <ver>` and `Connected to ACE 0` / `Connected to ACE 1`.
   - `head_source` is preserved.
   - `app.js` md5 equals the post-patch md5.
   - `curl http://$DAVINCI_U1_HOST/multiace/` returns 200.
   - `decay71_check.sh` prints `up to date`.

   Update the md5 values in this README.

`--check` prints `patched`, `partial` (older overlay, upgradable), `unpatched`, or `unknown`.
`unknown` means upstream changed that code, so do not force the patch. Hard-refresh the browser
after patching.

**Rollback:**
- **`app.js` only:** restore `app.js.pre-overlay.bak`.
- **`ace.py` only:** restore `ace.py.pre-overlay.bak`, delete `__pycache__/ace.*.pyc`, then
  `/etc/init.d/S60klipper restart` while no print is active. Alternatively, keep the patch and set
  `airlog_chew_rel_ratio: 0` in `[ace]`.
- **Whole release:** run the release's `uninstall_multiace.sh`, or reinstall the previous tag's
  tarball. Then copy back the backed-up files and run `/etc/init.d/S60klipper restart`.

Tests: `python -m pytest ops/decay71_overlay`. They run against the real upstream
`app.js` / `ace.py`, read from `DECAY71_APP_JS` / `DECAY71_ACE_PY` or the `decay71/main`
git remote, and skip if neither is available.
