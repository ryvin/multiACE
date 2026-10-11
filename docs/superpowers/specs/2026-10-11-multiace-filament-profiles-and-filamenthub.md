# How multiACE 1.27b uses filament profiles, and FilamentHub integration options

Fable research, 2026-10-11. Sources: `decay71/main` via `git show`, and read-only GETs against FilamentHub's Spoolman. Line numbers refer to decay71/main `c277e82`.

# PART 1 — How multiACE 1.27b models "filament profile"

## a) Where an identity lives

| Home | Fields | Source |
|---|---|---|
| **Slot override** `slot_overrides.json` | `{ace,slot,material,brand,subtype,color}` | ace.py:704, :1381-1384, `_override_for` :11187 (needs material or color), `_save_slot_overrides` :11213; backend `SlotOverride` main.py:979-985, routes :4445-4475 |
| **RFID slot info** `_info_per_ace[ace].slots[]` | `rfid` (0/1/2/3), `type`/`material`, `subtype`, `brand`, `color` (RGB list), `sku`, `tag_format` | ace.py:3386-3400, :7506-7530. OpenSpool NDEF decode returns only `material,color,vendor,min_temp,max_temp` — **no subtype** (ace_rc522.py:469-515) |
| **Local spool table** `persistent/multiace_spools.json` | `id,material,color,vendor,subtype,spoolman_id,label,sku,used_mm,weight_g,weight_initial_g,density,pa_matrix,spoolman_synced_mm` | ace.py:706, `ACE_SPOOL_ADD` :15841-15872, `_spool_pa_store` :14781; bindings keyed `_spool_key(ace,slot)` :13126 |
| **head_source capture** | `{ace_index,slot,type,subtype,color,brand}` per head; persisted `ace__head_source` | ace.py:719, :1519; override folded at load :12860-12890; display edits captured :11955-11985 |
| **Firmware PTC** (`print_task_config`) | `filament_type, filament_vendor, filament_sub_type, filament_color, filament_spool_id, filament_official` | pushed as `SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER= FILAMENT_TYPE= FILAMENT_COLOR_RGBA= VENDOR= FILAMENT_SUBTYPE=` ace.py:3408-3411, :11276; wrapper :3086-3108, handler :11614; read back :20272-20277 |
| **Spoolman import** | `_spoolman_to_local` main.py:2586-2650: material (split via `_spoolman_material_split` :2327), vendor, color_hex, label=filament.name, weight=remaining_weight, density, sku=`SM<id>`, subtype=guess(:2354), pa_matrix from extra `pressure_advance_matrix` (:2497) | search :2766-2812, adopt :2847, push `/spool/{id}/use` :3457-3500, `card_uids` extra :2390-2480 |
| **SpoolLink** | spool id only, via `spoollink_resolve_spool` remote method | ace.py:541-547, :11443 |

World switch: `spool_mode` ace.py:1485-1494; `_spool_in_world` :13177 hides local rows in spoolman mode.

## b) What the identity is used for

- **Print-start**: `_on_print_start` ace.py:4350-4480 checks only head_source consistency. **No material/colour-vs-gcode check at print start.** Slicer matching is the web **preflight**: `check_material_availability` (lowercase material equality) post_process_virtual_toolheads.py:303; colour tiers `exact_hex > name > synonym > fuzzy` :331-370; `parse_filament_types` :2119; flush matrix :1569 becomes per-swap `ACE_SET_PURGE LENGTH=` stamps (ace.py:17884-17960, gated by `purge_matrix` :1823).
- **Quad replenish**: `_quad_replenish_candidate` :16806 — type equal (lowercase) + colour equal when both declared; brand/subtype ignored. Identity = head_source capture, else `_slot_declared_identity` :13034 (override > RFID). In multi mode candidates are **(other ACE, same slot index)** (:16838-16841).
- **Temps**: swap/load temp `_get_swap_temp` :20262-20336 = firmware `filament_parameters.get_load_temp(vendor,type,subtype)` from PTC, capped to print target, fallback `swap_default_temp` 250 (:815). Unload/load per material via `[ace_tipform]` `<vendor>_<material>`/`<material>` keys with `unloadtemp:`/`loadtemp:` (ace_tipform.py:14-62, ace.cfg:501-507). Subtype matters because it selects the DB row (:14545-14547). multiACE never sets print temps.
- **PA**: `pa_sync` :1086; `FLOW_CALIBRATE` hook :14875-14900, precedence spool > calibrated > slicer; stored **per spool** `pa_matrix[nozzle]`; mirrored to Spoolman extra.
- **Purge**: `swap_purge_length` :812 or slicer stamps; no per-material volumes.
- **Drying**: per-ACE `dryer_temp/duration` :891-892, :997-1001, auto_dry RH :1125-1145; **no material-keyed presets** (grep of ace.py/app.js found none).
- **Booking**: `_spool_sample_tick` :15751 net E delta -> `book_spool_use` :15109 -> `used_mm`; backend pushes `use_length` to Spoolman.
- **Tag write**: `ACE_TAG_WRITE ACE= SLOT= MATERIAL= COLOR= BRAND= SUBTYPE= MINTEMP= MAXTEMP= SKU= WEIGHT=` :16410-16460, idle-only guard :16417; OpenSpool encode writes `protocol,version,type,color_hex,brand,min_temp,max_temp` (ace_rc522.py:521-535) — no subtype/bed temps.
- **Web DB/validation**: `/api/materials` parses firmware `filament_parameters.py` `FILAMENT_PARA_CFG_DEFAULT` keys to `{type:{vendor:[subtypes]}}` (main.py:128-160, :5155-5270); picker snaps unknown vendor->`Generic`, subtype->`Basic` (app.js:4165-4181), tipform vendors added :4079-4120.

## c) Profile vs spool vs slot

- **Profile (material/vendor/subtype)**: load temp (firmware DB), tipform unload/load temp, display label. Owned by firmware DB + ace.cfg.
- **Spool**: colour, weight/used_mm, density, sku/card_uid, spoolman_id, `pa_matrix`. Owned by multiace_spools.json / Spoolman.
- **Slot**: override (user truth), RFID read, gate state, binding. Owned by slot_overrides.json / ace.py.
- Flush volumes: slicer only. Print temps: slicer only.

# PART 2 — FilamentHub inventory (live, 2026-10-11)

- `filament`: **83** records; all have `name, vendor, material, density, diameter, settings_extruder_temp, color_hex`; `settings_bed_temp` 82; `weight` 4; `extra` empty; **no filament extra fields registered** (`/field/filament` = []). Materials PLA 68, PETG 11, PC 2, TPU 2. Vendors 28 (Snapmaker 11, Polymaker 10, ...).
- `spool`: **89**; `remaining_weight/used_length` all; `location` 14 (strings like `u1-a T1`); `extra` on 56: `filamenthub` 54, `card_uid` 39, `tags` 17. `extra.filamenthub` = double-JSON `{schema,location{printer,ace,slot,asserted_at,asserted_by},td,color_measured,virtual_tag,conflict}` (docs/ARCHITECTURE.md:170-190; `write_location` scripts/spool_watcher.py:506-545).
- `/fleet/api/ace-state?printer=u1-b`: schema 2, `aces_covered [0,1]`, 6 winners all `user:assign` dated **July** plus `disputed` rows — stale relative to today's slots.
- `config/printers.json` u1-b **already has `"ace_count": 2`** (brief said missing; WP7 may be done).
- Orca index `public/orca-profiles.json`: 169 **names only** (`{brand} {type} {subtype}`), used by `pickBestOrcaSubtype` app.js:472-490. Review JSONs carry only `nozzle_temperature_range_low/high`.
- OpenSpool tags written by FilamentHub include `subtype, color_name, bed_min/max_temp` (docs/OPENSPOOL.md, app.js:4841-4847); tag->filament uses mid-temp (app.js:1600).
- TD: `extra.filamenthub.td` (spool 21 shows 3.3). No PA, flush, or drying data anywhere.

# PART 3 — Integration options

| # | Option | Value (this user) | Effort | Safety risk | Depends on | Phase-1 conflict |
|---|---|---|---|---|---|---|
| 1 | Native `ACE_SET_SPOOLMAN URL=http://192.168.1.174:3000 MODE=spoolman` for booking + PA/card_uid roundtrip | M (weights stay true) | S config, M reconcile | L, but world switch hides local rows (`_spool_in_world`), backend creates extra fields on FH's Spoolman | printer idle | Two writers of subtype/label; adopt creates local rows the plugin doesn't know |
| 2 | Push `settings_extruder_temp` into `[ace_tipform] <vendor>_<material>: unloadtemp/loadtemp` + `ACE_TIPFORM_RELOAD` | L (mono-PLA; one `pla:` line covers) | M | **M** (bad temp = stuck tip) | config write path | none |
| 3 | Per-slot live temps via gcode | L | L (needs ace.py patch; no hook) | M | fork | none |
| 4 | Register filament extra field `subtype` (+`orca_profile`) in FH; plugin uses it before guessing | **H** (correct labels, DB-valid subtype -> right load temp) | S | none | FH schema | replaces WP3 guess as first choice |
| 5 | Plugin "write tag from profile" -> `ACE_TAG_WRITE ... SKU=SM<id>` | M (persistent identity) | S-M | L (idle guard) | NTAG tags, idle | FH phone-written tags carry subtype decay71 ignores |
| 6 | Plugin books `used_mm` deltas to FH itself | L (duplicates #1, double-count risk) | M | L | state API | conflicts with #1 |
| 7 | Keep Orca `filament_colour` == Spoolman `color_hex` so preflight hits `exact_hex` | M | S (FH script) | none | sync_orca_profiles.ps1 | none |
| 8 | FH fleet view reads multiACE `head_source`/bindings via Moonraker | M (truthful fleet, visible swaps) | M | none (read-only) | ace `get_status` exposing them (unverified) | none |
| 9 | Auto-apply desired entry on gate 0->1 insert | M | M | **M** (wrong label -> wrong quad match) | state polling, WP0 store | extends WP4 |
| 10 | "Next spool" queue per slot: on eject promote next -> desired | H (end-of-spool workflow) | S | L | WP0 | extends |
| 11 | **Backup advisor**: per loaded slot show remaining_weight and whether (other ACE, same slot idx) holds gate-available same type+colour | **H** | S | none | overrides + Spoolman weights | none |
| 12 | Material drying button (`ACE_START_DRYING TEMP= DURATION=` :9240) | L (PLA) | S | M if run mid-print | guard | none |
| 13 | Show TD/HueForge in picker | L | S | none | — | none |

# PART 4 — Roadmap

1. **#4 explicit subtype/profile on FH filaments** (S): register `/api/v1/field/filament` keys `subtype`, `orca_profile`; `filaments.py` prefers them, falls back to WP3 guess. Validate against `/api/materials` so the override never snaps to `Basic`.
2. **#11 + #10 backup advisor and next-spool queue** (S): in the plugin's slot grid, flag "no backup for ACE0/S2 Black; load in ACE1/S2" using the quad rule (:16838-16841) and FH `remaining_weight`. Pure read + UI; zero printer risk.
3. **#7 colour parity** (S, FH side): audit Orca profiles vs Spoolman `color_hex`.
4. **#5 tag write from profile** (M, idle only): plugin button -> `ACE_TAG_WRITE` with `SKU=SM<id>`; note decay71 drops subtype on OpenSpool.
5. **#1 native Spoolman mode** later, only after a quiet-day test of the world switch; keep plugin as override/location owner, decay71 as `/use` owner (as the plan already states).

**Unverified assumptions**: `/api/plugin-api/state` exposes gate status and spool `used_mm`; ace `get_status` exposes head_source/bindings to Moonraker; `ACE_START_DRYING` print guard; spoolman-mode switch dropping bindings (from plan, not re-read); which `filament_parameters.py` layout (1.6 flattened vs older) the printer runs; whether the ACE-2 native decoder (ace.py:8552-8560) carries subtype where the RC522 path does not; TD unit of 3.3.

### Critical Files for Implementation
- /mnt/e/Code/multiACE/multiace_plugins/filamenthub/src/filamenthub_plugin/filaments.py
- /mnt/e/Code/multiACE/multiace_plugins/filamenthub/src/filamenthub_plugin/mapping.py
- /mnt/e/Code/multiACE/multiace_plugins/filamenthub/src/filamenthub_plugin/app.py
- /mnt/e/Code/FilamentHub/public/app.js (profile/subtype/colour and tag-write paths)
- /mnt/e/Code/FilamentHub/scripts/spool_watcher.py (location schema the plugin must mirror)