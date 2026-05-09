# 8-color post-processor for Snapmaker Orca + multiACE

**Status:** approved 2026-05-08 (in-chat); ready for implementation plan.
**Branch (work):** `feat/multiace-postprocess-8color` (off `main`, after this spec lands).
**Scope:** Operator-side Python post-processor + sample slicer profile + new web UI Print queue tab. Enables slicer-driven 8-color prints on the U1 with auto-derived filament-to-slot mapping.

## Goal

Let an operator slice an 8-color print in Snapmaker Orca and print it without manual gcode editing or hand-authoring filament-to-slot mapping. Post-processor auto-resolves slicer's per-tool filament metadata against current Spoolman / FilamentHub bindings, generates gcode with `ACE_PARK_HEAD` + `ACE_LOAD_HEAD` calls at swap boundaries, and writes a sidecar that the multiACE web Print queue tab surfaces. Operator only intervenes when the system genuinely can't decide.

## Architecture

Three loosely coupled layers:

1. **Slicing time** — `multiace_web/tools/multiace_postprocess.py` runs as Snapmaker Orca's post-script. Stdlib only (`urllib.request`). Reads slicer's per-tool filament metadata, queries multiACE web (`/api/state`) and Spoolman for bindings, auto-matches by `(type, color_hex)`, plans physical-head assignment to minimize swap count, rewrites gcode in place to use physical T0–T3 with `ACE_PARK_HEAD` / `ACE_LOAD_HEAD` at swap boundaries, writes a `<gcode>.multiace.json` sidecar.

2. **Launch time** — new "Print queue" tab in multiACE web (`multiace_web/src/multiace_web/static/app.js`). Lists Moonraker gcodes with sidecars; surfaces the resolution table; provides "Fix loadout" wizard that delegates to the smart-swap UI from the multiACE-web-operations spec.

3. **Print time** — Klipper executes the rewritten gcode. Standard T0–T3 toolchanges + inline `ACE_PARK_HEAD HEAD=N` / `ACE_LOAD_HEAD HEAD=N ACE=M SLOT=S` calls fire at swap boundaries. Wheel-encoder Tier-2 fallback already covers per-load failures. **No firmware changes needed.**

## Dependencies

- **Snapmaker Orca slicer** — operator installs the bundled 8-extruder profile.
- **Spoolman bindings via FilamentHub** — at least one bound spool per (type, color) the print uses. Bindings are the source of truth for what's in each slot. (FilamentHub deep-link work shipped in PR ryvin/FilamentHub#5.)
- **multiACE web Operations spec** (smart-swap UI) — required for the "Fix loadout" wizard. Ship together or in close sequence.
- **swap-park firmware** (`feat/swap-park`) — strongly preferred but not strictly required. Without it, swaps fall back to full unload + load (slower).

## Convention pinning

- ACE letter / slot label: same as FilamentHub picker (per multiACE-web-operations spec).
- Filament color hex: lowercase 6-char `#RRGGBB`. Match is byte-exact in v1 (no fuzzy match).
- Type-name match: exact string. ("PLA" ≠ "PLA Basic" ≠ "PLA+"). Vendor-specific PLA variants need separate Spoolman entries with distinct types or operator-friendly aliases. v2 spec can introduce normalization.

## Components

### 1. Sample 8-extruder Snapmaker Orca profile

`multiace_web/install/snapmaker-orca-profile-multiace.json` — derived from the stock U1 profile but with `extruders=8` plus 8 fake extruder definitions. Operator copies to `~/.config/SnapmakerOrca/printer/` (or the OS-equivalent profile dir) and selects in slicer's printer dropdown.

README walkthrough (`multiace_web/install/SLICER_SETUP.md`):
1. Backup existing U1 profile
2. Copy bundled profile
3. Restart slicer
4. Select "Snapmaker U1 + multiACE 8-color" as the printer
5. Configure post-script: `python3 /path/to/multiace_postprocess.py [output_filepath]` in `Filament → Process G-code → Custom Post-processing Scripts`
6. Set env var `MULTIACE_PRINTER_URL=http://192.168.1.136` (or rely on `DAVINCI_U1_HOST` if exported per repo convention)

### 2. Post-processor: `multiace_web/tools/multiace_postprocess.py`

Single-file Python 3.11+ script. Stdlib only (`urllib.request`, `json`, `re`, `sys`, `os`, `dataclasses`, `pathlib`).

**CLI:**
```
multiace_postprocess.py <gcode_path>
```

**Env vars:**
- `DAVINCI_U1_HOST` — printer host (default `192.168.1.136` per repo convention; falls back to `MULTIACE_PRINTER_URL` if both set)
- `MULTIACE_POSTPROCESS_DEBUG=1` — verbose stderr logging

**Behavior (in order):**

1. Parse gcode header for slicer's per-tool filament metadata. Snapmaker Orca emits comments like:
   ```
   ; filament_type = PLA;PLA;PETG;TPU;PLA;PETG;TPU;PETG
   ; filament_colour = #FF0000;#FFFFFF;#0080FF;#FFFF00;#000000;#00FFFF;#FF00FF;#80FF00
   ```
   Parse into `tools[i] = {type, color_hex_normalized}` (lowercase, leading `#` stripped if needed, normalized to 6-hex form).

2. Query `http://<MULTIACE_HOST>/multiace/api/state` (web's existing `/api/state`) for `device_count` + per-slot bindings (the web's state model already merges Spoolman bindings into the slot data via SpoolmanClient — verify by reading current state schema).

3. **Auto-match each tool 0–7** against bindings:
   - Iterate over (ace, slot) pairs with bindings; collect candidates whose `(type, color_hex)` matches tool's metadata
   - 1 candidate → `match_quality=exact`, resolved to that slot
   - 2+ candidates → `match_quality=ambiguous`, all candidates listed; resolution deferred to web UI
   - 0 candidates → `match_quality=none`, slot unresolved; resolution requires operator action

4. **Plan physical head assignment** (greedy, minimize swap count):
   - Initialize: `head_assignments[0..3] = first_4_distinct_filaments_used`
   - For each toolchange in gcode order: if the new tool's filament is on a head that's still printing the same filament → use that head (no swap). Else find an "available" head (no longer needed by upcoming toolchanges) → assign new filament to it (swap needed).
   - Output: list of swap events `{at_line: N, head: physical, from: (ace, slot), to: (ace, slot)}`.

5. **Rewrite gcode in place:**
   - Replace fake `T4`–`T7` references with their planned physical `T0`–`T3` indices
   - Insert `ACE_PARK_HEAD HEAD=<phys>` + `ACE_LOAD_HEAD HEAD=<phys> ACE=<m> SLOT=<s>` immediately before the toolchange line at swap boundaries
   - Preserve slicer's wipe-tower and prime sequences (option A=(i) — no purge geometry rewriting)
   - Add header comment block listing the resolved mapping for audit:
     ```
     ; multiace.tool0: type=PLA color=#ff0000 head=0 source=ace0/slot0 spool_id=42
     ; multiace.tool1: type=PLA color=#ffffff head=1 source=ace0/slot1 spool_id=44
     ; ... etc ...
     ; multiace.swaps: 4 (at layers 12, 28, 41, 58)
     ; multiace.status: ready
     ```

6. **Write sidecar `<gcode>.multiace.json`** (atomic write via tempfile + rename).

7. Exit 0 even on `pending`/`error` status — slicer continues. The web UI is the surface for non-ready states. Stderr captures progress + warnings.

**Sidecar JSON schema:**
```json
{
  "schema": 1,
  "generated_at": "2026-05-08T14:30:00Z",
  "gcode_path": "/abs/path/to/print.gcode",
  "status": "ready" | "pending" | "error",
  "reason": null | "moonraker_unreachable" | "missing_bindings" | "ambiguous_match" | "no_8_tool_header" | ...,
  "tools": {
    "0": {
      "type": "PLA",
      "color": "#ff0000",
      "match_quality": "exact" | "ambiguous" | "none",
      "candidates": [{"ace": 0, "slot": 0, "spool_id": 42, "spool_name": "PolyMaker PLA Red"}],
      "resolved": {"ace": 0, "slot": 0, "spool_id": 42},
      "physical_head": 0
    }
  },
  "swaps": [
    {"line": 18432, "layer": 12, "head": 1, "from": {"ace": 0, "slot": 1}, "to": {"ace": 1, "slot": 0}}
  ],
  "errors": []
}
```

**Error handling at slicing time:**
- **Moonraker unreachable** → `status=pending, reason=moonraker_unreachable`. Gcode header gets `; multiace.error: moonraker_unreachable`. Tool-level resolution shows all tools `match_quality=none`, `candidates=[]`. Web UI's Re-validate button retries.
- **Spoolman bindings empty / partial** → `status=pending, reason=missing_bindings` if any tool unresolved. Per-tool details show `match_quality=none`. Web UI surfaces missing types/colors with one-click links to FilamentHub picker.
- **No 8-tool slicer header detected** → `status=error, reason=no_8_tool_header`. Operator probably picked wrong slicer profile. Sidecar tools dict empty. Web UI shows red banner with the diagnostic.
- **Ambiguous match** (two slots have same type+color) → `status=pending, reason=ambiguous_match`. Per-tool `match_quality=ambiguous, candidates=[...]`. Web UI shows operator a picker to disambiguate.
- **Gcode malformed** (unparseable, no T-changes, etc.) → exit non-zero with stderr message. Slicer surfaces this to operator. No sidecar written.

The script never silently produces broken gcode — failure modes either prevent generation or flag the sidecar as not-ready.

### 3. Web UI — Print queue tab

New tab in multiACE web alongside Dashboard / Activity / Dryer / Config / Diag. Renders gcodes with sidecars:

**Listing:**
- Polls Moonraker `/server/files/list?root=gcodes` every 10s
- For each `.gcode` file: check if a `<file>.multiace.json` sidecar exists (Moonraker's file list includes sidecars; we filter)
- Render rows sorted by sidecar's `generated_at` descending

**Per-row UI:**
- Filename + slice timestamp
- Status chip:
  - **`Ready`** (green) — all tools `resolved`, all swaps planned
  - **`Pending`** (yellow) — at least one tool ambiguous or unresolved with bindings present
  - **`Needs loadout`** (orange) — tools with `match_quality=none` and no bound candidates
  - **`Error`** (red) — sidecar status=error
- Expandable resolution table:
  - One row per logical tool (0–7)
  - Columns: tool index, expected (type + color swatch), resolved slot (ACE letter + slot label), spool name, match quality
  - Visual: green checkmark for exact, yellow ?  for ambiguous, red ! for none
- Per-row buttons:
  - **Print** — enabled only when status=Ready. Issues Moonraker `start_print` for the gcode file
  - **Fix loadout** — opens wizard
  - **Re-validate** — re-runs the slicing-time matching against current state without re-slicing (calls a small helper endpoint that re-invokes the post-processor logic against the existing gcode)

**"Fix loadout" wizard:**
- For each pending tool: shows expected (type, color) + closest matching slot OR "no match"
- Per-row choices:
  - **"Load matching spool to free slot"** — opens a slot picker; clicking issues `ACE_LOAD_HEAD` via the smart-swap UI from the web-ops spec (which handles the full unload + load chain)
  - **"Override — accept substitute"** — operator manually maps the tool to a slot with non-matching filament; sidecar is updated, status flips to Ready (with a `match_quality=overridden` flag)
  - **"Bind a new spool"** — opens FilamentHub deep-link picker for a slot; once spool is bound, re-validate runs automatically
- When all tools resolve: closes wizard, status flips to Ready

**Backend changes:**
- New `/api/print_queue` endpoint that returns the list of gcodes + sidecars (server-side aggregator; reuses existing Moonraker client)
- New `/api/print_queue/{gcode}/revalidate` endpoint that re-runs the matching logic for a given sidecar (without re-slicing)

### 4. Print time — Klipper

No changes. The rewritten gcode is standard from Klipper's perspective: T0–T3 toolchanges + ACE_PARK_HEAD / ACE_LOAD_HEAD as gcode commands. Wheel-encoder Tier-2 fallback already covers per-load failure paths.

## Phased flow (operator's POV)

1. Bind spools in FilamentHub for the slots the print uses (deep-link picker, already shipped)
2. Slice in Snapmaker Orca with the multiACE 8-color profile
3. Slicer's post-script auto-runs `multiace_postprocess.py`; status reported via stderr + sidecar
4. Open multiACE web → Print queue tab
5. Verify the resolution table is all green; if not, click `Fix loadout` → smart-swap as needed
6. Click `Print`
7. Watch Activity tab during the print — swaps fire automatically at color boundaries
8. Print completes; spools remain in their slots; loadout state persists; sidecar can be deleted or kept for audit

## Out of scope (explicit)

- Slicers other than Snapmaker Orca (Bambu Studio, PrusaSlicer, etc.)
- Filament identity matching beyond exact `(type, color_hex)` — fuzzy matching, vendor matching, RFID UID matching are all v2
- Wipe-tower geometry rewriting (option A=(ii) and (iii) deferred)
- Mid-print loadout changes (M0 pause workflow) — same deferral as web-ops spec
- Logical→physical mapping editor in the web UI — manifest is computed automatically; no manual override beyond Fix loadout / Override
- Multi-printer support — env var hardcodes one printer URL; multi-printer scope is v2
- Klipper firmware changes — none needed; spec uses existing macros
- Multi-color prints with N>8 — slicer profile is fixed at 8 extruders for v1
- Re-running post-processor after manual gcode edits — sidecar is the source of truth; manual gcode edits invalidate it

## Testing

**Automated (Python pytest, in `multiace_web/tests/`):**
- `test_postprocess_parser.py` — parse Snapmaker Orca gcode headers (sample fixtures), assert tool metadata extraction
- `test_postprocess_matching.py` — given (slicer tools, current bindings) → assert resolution table for exact / ambiguous / none cases
- `test_postprocess_planning.py` — given resolved tools + toolchange sequence → assert swap plan minimizes count
- `test_postprocess_rewrite.py` — given gcode + plan → assert output gcode has correct T-rewrites + ACE_PARK/LOAD insertions; snapshot test against a known-good output

**Automated (Playwright, against multiACE web):**
- Print queue tab structural test against synthetic state (sidecar fixtures)
- Fix loadout wizard launches the smart-swap UI correctly (uses mocked smart-swap)
- Status chip transitions on Re-validate

**Live hardware (manual smoke):**
- Slice a small 2-color print first using the new profile (sanity check that profile spoofing works)
- Slice a 4-color print (still no swaps needed if all 4 fit in heads)
- Slice a 5-color print (forces 1 swap)
- Slice the full 8-color demo
- Validate each step: post-processor invokes, sidecar generated, web UI surfaces correctly, print runs to completion

**Pre-flight (per CLAUDE.md):**
- Davinci-U1 reports `device_count=2`
- Spools bound for all required (type, color) combos
- multiACE web responding at the configured URL
- swap-park firmware deployed (or fallback path validated separately first)
- Print state safe (`standby`, `complete`, etc.)

## Risks and follow-ups

1. **Snapmaker Orca profile spoofing** — making a 4-toolhead machine appear as 8 may break some slicer assumptions (kinematic limits, cooling, prime tower geometry, retraction tuning). Validate via a 2-color print on the modified profile FIRST. If the profile fundamentally doesn't work, fall back to manually configuring slicer to "act like 8 extruders" via custom scripts.
2. **Spoolman binding staleness** — operator binds at slicing time, swaps physical filament without re-binding. v1 fix: web UI's "Re-validate" button reads current bindings; operator clicks it before printing if they changed loadout. v2: tighter integration where binding changes auto-revalidate queued gcodes.
3. **Color hex mismatch** — slicer's color picker may emit hex slightly differently than Spoolman's stored color (e.g. uppercase vs lowercase, with vs without `#`). Mitigation: normalize on both sides before comparison; if it still drifts, "Override" button accepts close-but-not-exact matches.
4. **Type-name normalization** — "PLA" vs "PLA Basic" vs "PLA+" are different types in Spoolman but may slice equivalently. v1: surface via match_quality=none; operator overrides explicitly. v2: alias table.
5. **Multi-printer support deferred** — env var hardcodes one printer URL. Operators with multiple multiACE rigs need v2 / a printer-picker in the slicer config.
6. **Post-processor distribution** — single-file script copied by operator. v1 doc walks operator through manual copy. v2: bundled installer or auto-update.
7. **Sidecar lifecycle** — sidecars accumulate alongside gcode files in Moonraker's gcode dir. v1: operator manually deletes when no longer needed. v2: auto-cleanup matching gcode deletions.
8. **Swap planning is greedy, not optimal** — minimizes swaps locally, may not produce the global optimum for complex prints. Acceptable for demo prints; v2 can introduce a more sophisticated planner.
9. **Race between Re-validate and an in-progress smart-swap** — operator clicks Re-validate while a Fix loadout swap is mid-execution. Re-validate reads stale state. Mitigation: backend re-validate endpoint checks `swap_in_progress` and refuses with a "swap in progress" response; UI shows that status.

## Self-review

**Placeholder scan:** No "TBD"/"TODO" in the spec. CLI is concrete (single-arg, env vars named). Sidecar schema is concrete with example values. Match algorithm specified (exact `(type, color_hex)` only). Planning algorithm specified (greedy, minimize swap count).

**Internal consistency:** Sidecar JSON schema referenced consistently between post-processor (writer) and web UI (reader). Status states (`ready` / `pending` / `needs_loadout` / `error`) used consistently. The smart-swap dependency on the web-ops spec is explicit. Convention pinning to FilamentHub picker conventions is explicit.

**Scope check:** Three components (post-processor, slicer profile, web tab) all serve the same end-to-end flow. Single feature, single PR-tree. Print queue tab could grow into a separate spec if it gains scope (multi-printer, queueing semantics, etc.) but for v1 it's tightly scoped.

**Ambiguity check:** "Auto-match" defined explicitly as exact `(type, color_hex)`. "Fix loadout" delegates to the web-ops spec's smart-swap (existing UI). "Status flips to ready" defined as: all tools resolve. Re-validate endpoint defined. Race conditions noted with mitigations.
