# Dashboard guide

The Dashboard is the home tab. One scrolling view. Designed to answer five
questions in <2 seconds:

1. **Is everything OK?** — status banner + overall print state pill
2. **What's printing?** — filename, layer, % done, ETA, current toolhead
3. **What's loaded where?** — 4 toolheads with filament identity + slot mapping
4. **What just happened?** — last 5 events inline
5. **What can I do?** — only the buttons that make sense for the current state

Open it at `http://<printer-ip>/multiace/`. On phones it stacks; on desktop
it's still single-column but wider tiles.

## Section by section

### Topbar

```
[mark] multiACE   ● Connected   Active: ACE 0     Auto-feed: OFF   Mode: Multi
```

- Brand / connection dot (green = WS connected, amber pulsing = reconnecting, red = disconnected).
- Active ACE: a pill when there's one device, a button cluster for multi-ACE switching.
- Auto-feed and Mode toggle pills: `aria-pressed` reflects state. Tap toggles via the standard `data-cmd` flow with confirm dialogs where appropriate.

### Status banner

Hidden when state is healthy. Auto-shows in priority order:

1. **Klipper exception (red)** — e.g. "Print paused · Extruder pickup failed". Pulled from `print_stats.exception` via `/api/print`.
2. **Tool change in progress (amber)** — when `swap_in_progress=true`. Tells you to hold actions.
3. **multiACE last_error (red)** — e.g. "T2 LOAD_HEAD_FAILED · feed_auto_error timeout!". Surfaced from the multiACE state model.

### Environment strip

A compact row of tiles, one per available environment signal:

- **Cavity** — U1 enclosure temp from Klipper's `temperature_sensor cavity`. Amber when >50 °C.
- **Humidity** (when configured) — from the external sensor URL. Color-coded: <25% green, 25–45% neutral, 45–60% amber, ≥60% red. Shows "sensor offline" if the upstream fetch fails.

Strip hides entirely when no tile has data.

### Print panel

```
[Print] [PRINTING] [EXTRUDING T0]
plate_1.gcode
[████████░░░░░░░░░░░░░░░░░░░░░] 32.8%
LAYER         ELAPSED
115 / 417     6h 58m
REMAINING     ETA
14h 14m       19:45
[       Pause       ]   [       Cancel       ]
```

Pulled from `/api/print` (polled every 4 s). The top color band tints to the
currently extruding filament's color. Action buttons reveal contextually:

| Print state | Buttons shown |
|---|---|
| `printing` | Pause, Cancel |
| `paused` | Resume (with confirm), Cancel (with confirm) |
| `complete` / `cancelled` / `standby` | (none) |
| `error` | Cancel (with confirm) |

Pause / Resume / Cancel POST directly to Moonraker `/printer/print/{verb}` —
same origin via nginx, no proxy in between.

When `_last_fetch_ok` is false (`/api/print` failed), shows "(print state unavailable)".

### Dryer card (when active)

Hidden when `dryer.status === "stop"`. Otherwise:

```
[ACE Dryer] [DRYING]
TARGET        DURATION
50°C          4h
REMAINING     DONE AT
3h 52min      09:39
[████░░░░░░░░░░░░░░░░░░░░░░░░░░] 3%
[    Stop drying    ]
```

The amber color band signals it's a heating operation. "Done at" is the
client clock + remaining minutes (frontend computes; not a backend field).
Stop button hits `ACED__Dry_Stop` via `/api/command`.

### Toolheads (filament grid)

Four cards in a 2×2 grid (4-up at ≥1024px). Each card shows:

- Top color band tinted by `print_task_config[h].color` (uint32 ARGB; `0xFFFFFFFF` renders striped to indicate "no color").
- Big `T<n>` identifier, color swatch, status pill.
- Status pill: `EXTRUDING` (green, pulsing — when the print is currently using this head), `LOADED` (green), `NO FILAMENT` (amber — has a slot source but the runout sensor reports empty), `ERROR` (red), `IDLE` (neutral).
- Material / Vendor / Source slot / Sensor state.
- Load (primary) and Unload (danger, with confirm) buttons. Disabled during `swap_in_progress`.

Active head emphasis: card border turns green and gets a soft outer ring.
Driven by `printState.current_extruder` from `/api/print`, refreshed on each 4s poll.

### ACE slots strip

Compact row beneath toolheads. Four small cards, one per slot of the active
ACE Pro. Each shows: gate state pill (`FILLED` / `EMPTY`), feeding-to-T number,
material, vendor, color band. Load → T<n> and Unload T<n> buttons inline.

### Recent activity preview

Last 5 events inline. Each row: timestamp, action name (bold, colored),
JSON-stringified params (muted). Red side-stripe for `*_FAILED` actions, green
for `LOAD_HEAD` / `UNLOAD_HEAD` / `UNLOAD_ALL` / `ACE_SWITCH` matches.

"View all →" link switches to the Activity tab.

When no events yet: "No multiACE events yet — load or unload a toolhead to see activity here."

### Floating Unload All

Bottom-right card on desktop, sticky bottom on mobile. Red destructive button
with confirm. Hits `ACEC__Unload_All` macro.

## Polling and live updates

- **WebSocket**: pushes multiACE state and events as they happen (poller writes a STATE line → tailer reads → state model updates → broadcast). Reconnects with exponential backoff up to 30 s.
- **`GET /api/print`**: polled every 4 s by the dashboard. Independent from the WebSocket — Klipper print state isn't part of multiACE's state model.

The dashboard can lag the print by up to ~4 s on tool changes. The poller keeps
the multiACE state fresh more aggressively (5 s ACE_HEAD_STATUS poll).

## Mobile considerations

- Single column layout. Tabs are a horizontal scroller (no scrollbar visible).
- All buttons ≥44 px tall (iOS HIG touch target).
- `prefers-color-scheme: dark` swaps the token palette automatically; no manual toggle.
- The Unload All bar pins to the bottom; content has 6rem of padding-bottom so the bar doesn't cover the last card.

## Accessibility notes

- `aria-live="polite"` on the toast container so VoiceOver / TalkBack read out command results.
- `aria-pressed` on the Auto-feed and Mode toggles.
- `role="dialog" aria-modal="true"` on the confirm modal, `aria-labelledby` pointing at the prompt text.
- `:focus-visible` outlines on all interactive elements (CSS).
- Status pills carry their meaning in text, not just color.

The active-head pulsing animation respects `prefers-reduced-motion` (CSS rule
disables all animations / transitions when set).
