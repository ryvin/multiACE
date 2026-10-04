# decay71 local overlay

The Davinci-U1 runs decay71's multiACE, not this repo's fork firmware. These are
small local patches to decay71 files that we re-apply by hand after a decay71
update overwrites them. Each script is idempotent. It refuses to write if the
target no longer matches the upstream text it expects, which means you need to
re-check the patch against the new release.

| Patch | Target on printer | Why |
|---|---|---|
| `web_queue_batch_patch.py` | `/home/lava/multiace_web/frontend/app.js` | Phone browsers drop the long `/api/macro` fetch during a 1.5-2.5 min unload, so the queue marks the step `!` even though Klipper finished it cleanly. Edits 1-2 (2026-09-30) send multi-step runs to `/api/macro-batch`. Edits 3-5 (2026-10-04) handle a dropped single command: wait until Klipper is idle, then fail the step only if a new error notification arrived. Against decay71 1.00.1b (upstream md5 `2bc08969…`, patched `4b35b46a…`). |
| `swap_park_patch.py` | same `app.js` | In multi mode, a dashboard swap (loading a slot into a toolhead that holds the other ACE's filament) unloads with `RETRACT_LENGTH=<swap_retract_length>` (900 mm here; per-slot > per-ACE > global, as in the firmware's `ACE_SWAP_HEAD`) instead of a full 1950 mm rewind. The old filament stays parked just past the splitter, so switching back is quick. Plain Unload buttons and head mode are unchanged. Added 2026-10-04 (patched with both overlays: md5 `c3c365e7…`). |

## Re-apply after a decay71 update

Run these with the printer idle (`print_stats.state` not `printing`/`paused`).

```bash
scp ops/decay71_overlay/web_queue_batch_patch.py ops/decay71_overlay/swap_park_patch.py root@$DAVINCI_U1_HOST:/tmp/
ssh root@$DAVINCI_U1_HOST 'for p in web_queue_batch_patch swap_park_patch; do python3 /tmp/$p.py /home/lava/multiace_web/frontend/app.js --check; done'
ssh root@$DAVINCI_U1_HOST 'cd /home/lava/multiace_web/frontend && cp -p app.js app.js.pre-overlay.bak && python3 /tmp/web_queue_batch_patch.py app.js && python3 /tmp/swap_park_patch.py app.js'
```

`--check` prints `patched`, `partial` (older overlay, upgradable), `unpatched`, or `unknown`. `unknown` means upstream
changed that code, so do not force the patch. No service restart is needed.
Hard-refresh the browser afterwards.

To roll back, restore a backup: `app.js.pre-queue-batch.bak` is pristine upstream, `app.js.pre-queue-drop.bak` has the batch edits only, and `app.js.pre-swap-park.bak` has both queue patches but not swap-park.

Tests: `python -m pytest ops/decay71_overlay`. They run against the real upstream
`app.js`, read from `DECAY71_APP_JS` or the `decay71/main` git remote, and skip
if neither is available.
