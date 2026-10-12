# Draft upstream issue for decay71/multiACE (NOT YET FILED)

File it after the overlay has run 2-3 clean prints on Davinci-U1, or has caught a chew. Before filing, re-derive every
number below from `test_airlog_chew_patch.py` or a fresh backtest. The "18 false fires" and
"2 of the last 3" figures come from the exploratory backtest, not from the shipped test. Use
`gh issue create -R decay71/multiACE -F <this body section saved to a file>`.

---

**Title:** Air-Print Detection: absolute 25000 chew threshold misses tangles on low-baseline heads (suggest a head-relative rule)

**Version:** 1.27b. Air-Print Detection on: `airlog: true`, web toggle armed.

**What happened:** a spool tangled at the ACE (ACE 2 slot 4, feeding T3) mid-print. The airlog
saw the chew phase clearly, but the detector never fired and T3 air-printed. Relevant `airlog head=3`
`coil_delta` windows from `multiace_wiggle.log`:

| Window time | coil_delta |
|---|---|
| normal | ~1000-2000 |
| 12:07:04 | 4315 |
| 12:08:20 | 5569 |
| 12:09:18 | 5901 |
| 12:12:51 | 6097 |
| 12:13:49 | 10591 |
| 12:16:05 | 6614 |
| from ~12:25 | flat ~300-1100 (filament no longer advancing) |

`AIRLOG_CHEW_DELTA = 25000` is never reached. Lowering it isn't an option: in the same print, the
healthy heads legitimately read up to 31620 (T0), 23093 (T1) and 21617 (T2). Head levels differ by
about 5x.

**Suggestion (we run this as a local overlay):** keep the absolute rule, and add a per-head relative
rule.
- A window counts only if it is "dynamic" (`vel_max >= 10`). Constant-feed windows read 150-700
  on a head that normally reads 8-14k. Without this gate a ratio rule false-fires 18 times on the
  first-layer to normal transition in our log.
- Baseline = median of the first 10 unsuspicious dynamic windows, then an EMA (alpha 0.1) learned
  only from unsuspicious windows.
- A window is suspect when `delta >= 4000` **and** `delta >= 2.5 x baseline`.
- It fires after `AIRLOG_CHEW_WINDOWS` consecutive suspect dynamic windows of the same head. A slow
  window, a swap/pick-check, or more than 600 s of silence on that head breaks the run.
- A fresh print re-learns the baseline; a resume keeps it.

Replayed over every airlog window of that print, this fires once, on T3 at 12:13:49. That is about
11 minutes before the filament stopped, with zero fires elsewhere.

Margins:
- Incident run ratio 4.3, against 1.6 for the largest healthy-head run above the floor.
- Every k in 2.0-3.5 × floor 3000-5000 gives the same result.
- "2 of the last 3" false-fires on pre-incident T3 data.
- A live low-side "flow lost" check was **not** separable from normal printing in this data, so it
  is not proposed.

Patch, tests and a fixture CSV of the windows: <link to ryvin/multiACE ops/decay71_overlay once pushed>.
All knobs are optional `[ace]` options (`airlog_chew_rel_ratio` etc.); `airlog_chew_rel_ratio: 0`
restores current behaviour.

Caveat: this is a single incident in one print, so the defaults are conservative and want more data.
