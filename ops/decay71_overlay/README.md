# decay71 local overlay

The Davinci-U1 runs decay71's multiACE, not this repo's fork firmware. These are
small local patches to decay71 files that we re-apply by hand after a decay71
update overwrites them. Each script is idempotent. It refuses to write if the
target no longer matches the upstream text it expects, which means you need to
re-check the patch against the new release.

| Patch | Target on printer | Why |
|---|---|---|
| `web_queue_batch_patch.py` | `/home/lava/multiace_web/frontend/app.js` | Phone browsers drop the long `/api/macro` fetch during a 2+ min unload. The queue then marks the step failed and never sends the queued load. The patch sends multi-step runs to `/api/macro-batch` so Klipper owns the sequence. Applied 2026-09-30 against decay71 1.00.1b (upstream md5 `2bc08969…`). |

## Re-apply after a decay71 update

Run these with the printer idle (`print_stats.state` not `printing`/`paused`).

```bash
scp ops/decay71_overlay/web_queue_batch_patch.py root@$DAVINCI_U1_HOST:/tmp/
ssh root@$DAVINCI_U1_HOST 'python3 /tmp/web_queue_batch_patch.py /home/lava/multiace_web/frontend/app.js --check'
ssh root@$DAVINCI_U1_HOST 'cd /home/lava/multiace_web/frontend && cp -p app.js app.js.pre-queue-batch.bak && python3 /tmp/web_queue_batch_patch.py app.js'
```

`--check` prints `patched`, `unpatched`, or `unknown`. `unknown` means upstream
changed that code, so do not force the patch. No service restart is needed.
Hard-refresh the browser afterwards.

To roll back, run `cp -p app.js.pre-queue-batch.bak app.js`.

Tests: `python -m pytest ops/decay71_overlay`. To test against a real upstream
file, set `DECAY71_APP_JS`:
`git show decay71/main:multiace/web/frontend/app.js > /tmp/app.js`, then run
`DECAY71_APP_JS=/tmp/app.js python -m pytest ops/decay71_overlay`.
