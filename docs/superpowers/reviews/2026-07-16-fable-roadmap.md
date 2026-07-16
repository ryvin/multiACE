# multiACE — Planning-Artifact & Roadmap Review (TPL lens)

**Date:** 2026-07-16
**Reviewer objective:** does the written plan corpus drive toward "best-of-class, ease of use, least errors" for ACE Pro × Snapmaker U1 — and what MUST be planned that isn't?
**Corpus:** 16 plans + 15 specs + 3 reference docs under `docs/superpowers/`, README, CLAUDE.md instincts, project memory.

---

## 1. Critical assessment of the existing specs/plans

### Strengths (real, and better than most planning corpora)

1. **Scope discipline is systematic.** Nearly every spec carries explicit Non-goals AND Out-of-scope lists, a live-verified hardware-contract section, and a risk register (e.g. `specs/2026-05-04-auto-dry-design.md` has a separate "Out-of-scope explicit list (so reviewer doesn't flag)"; `specs/2026-05-17-swaptimizer-design.md` has cost model + test manifest + deferred Approach B). Gold-plating risk across the corpus is LOW — the docs repeatedly kill their own gold-plating (swap-park killed its new verb; dual-ACE GUI deep-links instead of building a picker; puller defers background polling as YAGNI).
2. **Error handling and reconciliation are pervasive as *implementation* concerns.** Auto-dry has boot-time DRYING reconciliation + latched FAULTED; wheel-encoder fallback codifies "sensor wins over bookkeeping"; SP1's `get_status` "never raises"; the 8-color postprocessor guarantees "never silently produces broken gcode"; `reference/2026-05-07-sensor-observability.md` §3–4 is a genuine tiered audit-event taxonomy mapped to failure classes.
3. **Unusually honest self-correction.** SP1 admits a wrong premise mid-spec; SP3 carries two dated post-audit corrections; the Kindred-Allies plan distinguishes shipped/blocked/unverified; the puller memory documents a phantom fix (`ba431b8`) as a phantom. This is a healthy engineering culture signal.

### Weaknesses / where the corpus fails the objective

1. **The corpus is feature-plan-rich and program-plan-poor.** There are 16 excellent *feature* plans and **zero** program-level documents: no error budget, no release plan, no calibration/onboarding plan, no reconciliation architecture. README's actual Roadmap section (`README.md:473`) is three bullets — "Bug fixes based on community feedback / Custom Fluidd UI panel / Maybe one day: [video link]". For a project whose objective is "least errors," the roadmap is effectively **unwritten**; the plans directory is a chronicle of sprints, not a strategy.
2. **Strategic schizophrenia: two contradictory strategies coexist, undecided in writing.**
   - `plans/2026-06-20-decay71-098b-kindred-allies-port.md` = Direction B ("graft decay71's wins onto the fork") — waves 1–3 shipped, waves 4–5 blocked on hardware gates.
   - `reference/2026-07-07-decay71-vs-ryvin-comparison.md` = an explicit, well-argued reversal: "**keep decay71 as the base; graft the fork's few genuine wins onto it**" (Direction A, ~1–2 weeks, bounded), and the printer has *actually run decay71 0.99.2b since 2026-06-29* (memory: `davinci-u1-clean-redeploy-backup.md`).
   - Nothing supersedes the Kindred-Allies plan; README still promises "v0.82 will lift" the start-ACE restriction (`README.md:406`) on a fork the printer no longer runs; `multiace/VERSION` reads 0.80b while docs reference deployed 0.8.7 and the live box runs decay71 0.99.2b. **Three version truths, no decision record.** Every week this stays unresolved, plans are written against a base that may be abandoned (the FilamentHub plugin targets decay71; the Kindred-Allies waves target the fork).
   - The comparison doc itself flags an unresolved fact ("60-second keepalive tiebreak … before banking", line 13/87) that was never done — a decision-blocking open item aging a week+.
3. **Reconciliation is solved per-feature, never as an architecture — which is exactly what today's defect exploits.** The system now has **at least four sources of filament truth**: FilamentHub `extra.filamenthub.location` records (desired), multiACE slot-overrides (labels), firmware `head_source` (bookkeeping), and physical sensors/RFID (`gate_status`, `e<N>_filament`). Each pairwise seam got its own partial fix (puller's scoped label reconcile; wheel-encoder sensor-fallback; SP3's loadout drift classes; decay71's print-start auto-heal that the fork lacks). **No document owns the whole lattice.** The 2026-07-15 defect — Pull applied labels to ACE0 slots 2–3 while spools physically sit in 0–1, and the grid hides labels on empty slots — falls squarely in the unowned seam: `specs/2026-07-09-filamenthub-ace-state-puller-design.md` reconciles FilamentHub-labels-vs-multiACE-labels but **never consults physical occupancy**, and the "grid hides label on empty slot" behavior was DECIDED-keep-as-is (memory: `filamenthub-ace-state-puller.md`) on the theory that it "surfaces the mismatch" — in practice it surfaces as *silence* ("wrong filaments") because nothing tells the operator *why* the slot looks empty or that a label exists but is suppressed. The decision was reasonable per-feature and wrong system-wide. Related open gap: `ryvin/FilamentHub#18` (vacated ACE drops out of the seam → stale labels never cleared).
4. **Plans drift from specs and from reality with no mechanism.** Swap-park's spec warns its own plan is stale ("needs regeneration"); SP3 shipped Tasks 1–3 then stalled on a scaffolding gap SP2 never built (memory: `sp3-plan-defects-2026-05-30.md`) and has sat since 2026-05-30; CLAUDE.md instinct #7 and the `state_debug` claim are contradicted by the comparison doc's own corrections. Status truth lives in git + memory files, not in the planning docs — fine for the author, hostile to "best-of-class."
5. **Calibration constants are the actual error source and have no home.** The post-merge pain in swap-park (600→700mm, three fix commits), `COIL_FALLBACK_K/THRESHOLD`, autodry `target_pct=15` FAULTED-below-floor (memory: `autodry-target-pct-too-aggressive.md`), instinct #3 ("never bake a default for arbitrary printers") — every one is an operator-facing error mode caused by an uncalibrated geometry/environment constant, and no doc plans how a new user would ever discover the right values.

### Per-spec verdicts (compact)

| Doc | Verdict |
|---|---|
| Web console (04-27) | Shipped, well-scoped, dedicated failure-modes table. Foundation. |
| Hardware twin (05-02) | Shipped, lean, honestly flags inactive-ACE occupancy as *unknowable* — a flag nobody acted on, now central to today's defect. |
| Auto-dry (05-04) | Best spec in corpus (FAULTED latching, boot reconciliation). Superseded twice (per-ACE, then plugin); the *plugin* is built (95 tests) but **not deployed** — value stranded. |
| Dual-ACE GUI (05-05) | Shipped; strongest web error-handling section; round-robin crutch later reverted to opt-in — honest. |
| Swap-park (05-07) | Shipped after churn; spec self-flags stale plan; case study in calibration-constant pain. |
| Wheel-encoder fallback (05-07) | Shipped; the model error-handling spec. |
| 8-color postprocessor + planner improvements (05-08/09) | Shipped, disciplined, provenance-commented decay71 ports. No further investment needed. |
| Web operations / smart-swap (05-08) | Shipped; leg-2 race fix embodied in instinct #5. |
| Swaptimizer (05-17) | Shipped, tightest TDD plan in corpus. |
| HelixScreen SP1 (05-27) | Shipped + live-verified; the contract seam is the corpus's best cross-repo artifact. |
| HelixScreen SP2/SP3 (05-27/30) | SP2 out-of-tree/unverifiable; SP3 stalled 6+ weeks at the Task-0 scaffolding amendment. **Effectively parked without saying so.** |
| decay71 feature port (05-13) → Kindred Allies (06-20) | Superseded then partially executed; now contradicted by the 07-07 comparison. Needs an explicit disposition. |
| FilamentHub plugin (07-06) + ace-state puller (07-09) | Testable, scoped, deployed, live-verified — genuinely good specs. But both are **label-plane only**; the puller's "Safety: label-only, zero motion" framing let it skip the desired-vs-*physical* question entirely. |

**Objective alignment:** individually, ~80% of these plans serve "least errors" directly (reconnect hardening, sensor fallback, FAULTED latching). Collectively they do NOT — because the highest-error surfaces left today (state reconciliation across seams, calibration onboarding, fork disposition) have no plan at all, and effort keeps flowing to new seams (HelixScreen, FilamentHub phases) faster than old seams get closed.

---

## 2. Missing plans the objective demands

**M1 — Desired-vs-Actual Filament State Reconciliation (the defect's plan). HIGHEST PRIORITY.**
Scope: define the authority lattice across the four truth sources (FilamentHub location records → multiACE slot labels → firmware `head_source` → physical sensors/RFID), a per-slot composite state (`expected+present+match / expected+absent / present+unexpected / disputed / unknown`), the reconciliation triggers (pull, load/unload audit events, print-start — port decay71's print-start integrity audit the comparison doc says the fork "lacks entirely"), and the **presentation contract**: a mismatch is never rendered as silent "empty" — it renders as an explicit conflict badge with the suppressed label and a one-tap resolution (re-pull, move-label, mark-loaded, fix-in-FilamentHub). Must subsume `ryvin/FilamentHub#18` (`aces_covered` envelope) and resolve the hardware-twin doc's "inactive-ACE occupancy unknowable" data gap against decay71's actual `ace.aces[]` object.
Load-bearing because: today's "wrong filaments" incident is the exact failure class "least errors" means for a filament changer — printing the wrong material/color. Every future FilamentHub/RFID/auto-load feature stacks on this lattice; without it each feature re-litigates authority ad hoc (three times already: puller prune semantics, grid-hide decision, #18).

**M2 — Fork Disposition & Upstream Convergence Plan.**
Scope: a one-page ADR that formally adopts Direction A (or refutes the comparison doc), then the mechanical consequences: mark Kindred-Allies Waves 4–5 CANCELLED/SUPERSEDED in the plan doc; execute Direction-A items 2–4 (Mark_Loaded macros, park-retract knob, reconnect hardening — the last as an upstream PR since it's "the actual cause of the observed incident", comparison §1); run the 60-second keepalive tiebreak; reconcile version stamps (VERSION file, README v0.82 promises, CLAUDE.md instincts #1/#7 and the `state_debug` overstatement); define how decay71 releases are tracked henceforth (the corpus's own cross-cutting finding: divergence handled per-spec, never systematically).
Load-bearing because: every plan written since 07-06 quietly assumes decay71-as-base while the README/roadmap/instincts assume the fork. Undecided strategy is the single largest source of wasted planning effort and doc-reality error in the project — and doc-reality error IS an error class under the objective.

**M3 — Error Taxonomy, Observability & Operator-Facing Error Contract.**
Scope: promote `sensor-observability.md` from reference catalog to a versioned contract: (a) an enumerated, versioned audit-event vocabulary (the state-log JSON schema is flagged "unversioned, unmonitored breakage risk" in the web-console spec's risks and never fixed); (b) a user-facing error taxonomy — every FAULTED/SUSPICIOUS/409/dispute maps to a plain-language message + recovery action in the GUI (the recovery knowledge currently lives in memory files like `head-source-null-mark-loaded-recovery.md`, i.e. in the maintainer's head); (c) fix the sidecar logging black hole (instinct #10: uvicorn under start-stop-daemon swallows `log.exception`) with a standard FileHandler in every sidecar; (d) an error-frequency ledger (which audit events fired last 30 days) so "least errors" becomes measurable instead of vibes.
Load-bearing because: "least errors" is unfalsifiable without counting errors, and "ease of use" dies when recovery procedures are tribal knowledge.

**M4 — First-Run Onboarding & Calibration Plan.**
Scope: a guided first-run flow (docs first, GUI wizard later) covering: unload-before-first-use (currently a Known-Limitations footnote), `ace_device_count`, park-retract length measurement (the README's "measure and subtract ~100mm" buried at line ~400), autodry `target_pct` floor discovery (run-to-floor probe instead of guessing 15%), start-ACE selection guidance, and a provenance table of every geometry/environment constant with its calibration procedure (docs-provenance-discipline pattern). Include the cross-slot drift reseat cadence (instinct #2) as scheduled maintenance guidance.
Load-bearing because: the project's worst field failures (700mm saga, FAILED_LIMIT fault-loop, "unload before first use" surprises) were all mis-calibration, not code bugs. Best-of-class means a second printer/user can succeed without the maintainer's memory files.

**M5 — Release Engineering & Deployment Integrity Plan.**
Scope: single version stamp propagated to firmware banner/web footer/plugin manifests; a release checklist that executes (not just cites) the "Verification before shipping" list in CLAUDE.md; deployment docs reconciled with reality (CLAUDE.md's on-printer path is wrong per memory `davinci-u1-multiace-web-deploy.md`; root-owned 0700 installs, wget-not-curl — all discovered at deploy time, repeatedly); a scripted backup/rollback drill (backups exist ad hoc; restore is untested prose).
Load-bearing because: three of the last month's incidents (orjson shutdown loop, stale-extras installer, host-key churn) were deploy-integrity failures, not logic failures.

**M6 — Hardware Regression & Demo-Artifact Plan (small).**
Scope: actually execute the 8-color demo runbook (`reference/2026-05-07-eight-color-demo-test-plan.md` — written, prereqs ✓, never run to a captured artifact) and define a minimal recurring hardware regression (one multi-ACE print with ≥1 cross-ACE toolchange per release, per CLAUDE.md's own verification rule) with archived logs/screenshots as the artifact.
Load-bearing because: the corpus's systematic blind spot is hardware-gated validation; every "blocked on hardware" item (Wave 4 G1 gate, manual-heads bypass, SP3) ages indefinitely because there is no scheduled hardware-time budget.

---

## 3. Prioritized roadmap

### NOW (≈2 weeks) — close the truth gaps
| # | Item | Effort |
|---|---|---|
| 1 | **M1 spec + implementation, phase 1:** puller consults physical occupancy; conflict badge UI replaces silent-empty; `#18` `aces_covered` filed/landed FilamentHub-side; print-start `head_source` integrity audit ported | 4–6 days |
| 2 | **M2 ADR:** Direction-A decision memo + keepalive tiebreak + version/README/instinct cleanup + mark Kindred Waves 4–5 superseded | 1–2 days |
| 3 | **Deploy autodry plugin** (built, 95 tests, stranded) with calibrated `target_pct` (run-to-floor first) | 1 day + idle window |
| 4 | Direction-A item 4: **reconnect hardening** (by-path rescan before retry; don't pause print for non-active ACE) as upstream PR — fixes the comparison doc's "actual cause of the observed incident" | 2–3 days |

**Riskiest assumption (NOW):** that physical occupancy of *inactive* ACEs is actually observable under decay71's engine (the hardware-twin spec said it's unknowable from `gate_status` under the fork's single-USB model; decay71's per-ACE threads may or may not fix this). **Verify with a 30-minute live probe before writing M1's spec** — if inactive-ACE occupancy is stale, M1's design must carry explicit staleness semantics, not pretend freshness.

### NEXT (≈3–6 weeks) — make "least errors" measurable and repeatable
| # | Item | Effort |
|---|---|---|
| 5 | **M3 error taxonomy/observability:** versioned audit vocabulary, GUI error→recovery mapping, sidecar FileHandler fix, error-frequency ledger | 1–1.5 weeks |
| 6 | **M4 onboarding/calibration guide** (docs + provenance table; wizard deferred) | 3–5 days |
| 7 | Direction-A items 2–3: Mark_Loaded macros + park-retract knob into decay71 base | 2–3 days + calibration |
| 8 | **M5 release/deploy integrity:** version stamp unification, restore drill, deploy-doc reconciliation | 3–4 days |
| 9 | **M6:** run the 8-color demo to an archived artifact; institute per-release hardware smoke | 1 day per run |

**Riskiest assumption (NEXT):** that decay71 upstream will accept (or at least not conflict with) the ported wins — if the plugin/macro seams churn under decay71 releases, Direction A's "bounded, then done" promise degrades back into the fork treadmill. Mitigate by pinning to a decay71 tag and re-validating plugins per upstream release (part of M2's tracking mechanism).

### LATER (quarter+) — optional surface area, only after NOW/NEXT hold
| # | Item | Note |
|---|---|---|
| 10 | HelixScreen SP3 resume | Only after the Task-0 scaffolding amendment (memory `sp3-plan-defects-2026-05-30.md`) and only if the touchscreen is a real usage surface; its Recovery panel should *consume* M1's lattice, not re-derive it |
| 11 | Onboarding wizard in GUI (M4 phase 2) | After the doc version proves out |
| 12 | Full i18n, background auto-pull, remote access (Cloudflare) | Convenience, not error-reduction |

**Riskiest assumption (LATER):** that the HelixScreen surface still matters post-Direction-A — decay71's own GUI + plugins may make the C++ panel redundant. Re-justify before resuming, don't resume by inertia.

---

## 4. Cut / defer list (doesn't serve the objective)

1. **CUT: Kindred-Allies Waves 4–5** (ACE 2 / Protocol V2 threaded engine, virtual toolheads on the fork). Running decay71 as base *already provides* V2 protocol and hardware breadth — re-implementing them on the fork is the comparison doc's own definition of the treadmill. Mark the plan doc superseded explicitly (M2).
2. **DEFER indefinitely: HelixScreen SP3 C++ panels** (and any SP4). Stalled 6+ weeks on scaffolding SP2 never built, out-of-tree, unverifiable from this repo, and its user value (touchscreen recovery UX) duplicates what the web console + M1 will deliver. Keep the shipped SP1 firmware contract (cheap, useful); park the C++.
3. **DEFER: Cloudflare Access / remote exposure** (memory: staged, blocked on expired creds). Zero contribution to print-error reduction; adds an attack surface to a printer-safety-critical system. Do after M3/M5.
4. **STOP INVESTING: postprocessor optimizations** (swaptimizer Approach B/E, further planner work). Shipped tier is good; marginal swap savings are not the current error bottleneck.
5. **REVERSE one "decided" decision:** the 2026-07-09 "grid hides labels on empty slots — keep as-is, do not re-open without a new reason" memory entry. Today's defect **is** the new reason: the chosen presentation made a real mismatch read as "wrong filaments" with no explanation. M1 replaces hide-silently with conflict-explicitly.

---

## 5. One-line verdict

The feature-level planning is genuinely excellent — testable, scoped, self-correcting, anti-gold-plated — but the project has been sprinting sideways: strategy (which codebase is the base?), state authority (which source of filament truth wins?), and calibration/onboarding are all unwritten, and those three unwritten plans are where nearly all of the recent real-world errors (today's included) actually come from.
