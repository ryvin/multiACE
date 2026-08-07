# Porting decay71 0.99.6.2b into the ryvin fork

Status as of **2026-08-07**. Upstream reviewed: `decay71/multiACE` `origin/main`
= `MULTIACE_VERSION 0.99.6.2b` ("Persistent Pesterers" Hotfix 2, code drop
`77da282`, 2026-08-05).

## Why this is not a straight merge

Upstream ships every release as opaque GitHub-web "Add files via upload" blob
commits, so git ancestry is useless for cherry-picking. More importantly the
fork's `multiace/klipper/extras/ace.py` is a **0.81b-lineage rewrite** (~3.2k
lines) vs upstream's ~9.9k, and the fork carries modules upstream lacks
(`ace_keepalive.py`, `ace_status.py`, `manual_heads.py`) while lacking upstream's
(`ace_bg_swap.py`, `ace_tipform.py`). So for `ace.py` / `filament_feed_ace.py`,
upstream changes are **conceptually portable only — never patch-portable**, and
every firmware behavior change needs **hardware validation on Davinci-U1** before
release (there are no automated firmware tests).

## Done in this branch

- **`deploy/S59multiace-prewarm`** (clean drop-in) + installer wiring. Boot
  page-cache warmer that reads the klipper tree once before S60klipper, so cold
  page faults don't trip the multi-MCU homing window ("Timer too close" / 0003).
  Standalone shell, zero `ace.py` coupling, opt-out via `prewarm.disabled`. Safe
  to ship (additive, no ACE behavior change). Verified by inspection; deploy +
  reboot-time confirm still pending on hardware.

## Deferred — port next, each needs Davinci-U1 validation

| Item | Upstream files | Why / risk | Notes |
|---|---|---|---|
| **Runout suppression** | `filament_switch_sensor_ace.py` (+23) | Skip mid-print pause for recovery heads awaiting reload + unloaded heads whose load is ahead in gcode | Smallest firmware diff, but fork's copy is ~89 lines off baseline → adapt, don't cherry-pick |
| **`_reconnect_or_pause`** | `ace.py` | Unified V1+V2 comms-loss recovery, per-ACE reconnect guard, backoff, resumable PAUSE last resort | Directly targets the fork's cached-fd Errno-5 pain (see memory `pyserial-is-open-after-reenum`). Medium risk — reactor-thread marshalling must be preserved |
| **Seat press + PLA 220°C swap temp** | `ace.py`, `ace.cfg`, `filament_feed_ace.py` | `seat_overshoot_length: 20` post-load push; better swap reliability | Tune length for Davinci-U1 geometry (instinct #3: never trust upstream defaults) |
| **FA rearm backoff/health caps** | `ace.py` | `_fa_rearm_backoff_ok` (max 5), `FA_STICK_CONFIRM_TIME` — rearm-storm protection | Fork FA lifecycle differs |
| **Installer TRSYNC only-raise** | `install_multiace.sh` | Raise-only TRSYNC_TIMEOUT to 0.350 + dual-user log self-heal | Small; matters when the printer moves to PAXX 1.5.x |
| **`ace_tipform.py`** | new module + hooks | Per-material tip-forming/unload-temp tables | Parser ports clean; unload-path hook needs rework for the fork's `filament_feed_ace.py` |

## Skip — conflicts with the fork's design

- **`ace_bg_swap.py`** (parked-position background swaps) — upstream's own note
  says "not possible in multi mode with ACE Hardware"; collides head-on with the
  fork's start-ACE-pinning (instinct #1) and cross-slot drift (instinct #2).
- **Ace-per-Head mode** (1:1 ACE↔head wiring) — different topology from the
  fork's hub-based multi-ACE; the fork's `manual_heads.py` owns this space.
- **SpoolLink `identity_priority`** — overlaps the fork's FilamentHub
  `/api/slot-override` label system (deployed). Two competing slot-identity
  designs; needs a deliberate reconciliation, not a port.
- **Vue web changes** — the fork's `multiace_web/` is a from-scratch
  FastAPI/vanilla-JS app. Only steal the *idea* (Fluidd pseudo-camera panel
  registration via Moonraker webcam DB), not the code.

## Research lead

Upstream added an **airlog eddy-coil flow sampler** (diagnostics). The fork's
snag-detection dead end (memory `snag-detection-no-wheel-signal-ace-mode`)
concluded there was no usable flow-sensor channel — this may reopen that. Read
before assuming snag detection stays impossible.
