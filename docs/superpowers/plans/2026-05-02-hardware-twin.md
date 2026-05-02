# Hardware Twin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a new "Hardware" tab to the multiACE web console that renders an animated SVG physical-twin of the ACE Pro stack and the Snapmaker U1 with per-slot bowden tubes meeting at per-toolhead couplers. Existing Dashboard tab is untouched.

**Architecture:** New IIFE module `static/hardware-twin.js` exposes `window.HardwareTwin = { mount, render }`. `mount()` builds the static U1 + couplers + toolhead button row; `render(state, printState, workflow)` is called from `app.js`'s existing `renderAll()` and `fetchPrint()`, takes all three state objects as parameters, and only mutates classes / attributes / text nodes on persistent SVG nodes (never `innerHTML`). All button clicks reuse the existing global `[data-cmd]` listener at `app.js:1393`.

**Tech Stack:** Vanilla HTML / CSS / SVG / JavaScript (no framework, no build step). The web app already runs FastAPI + uvicorn on the printer; this plan touches only the static assets except for one constant in `tools/visual_regression.py` and a version bump.

**Spec:** See `docs/superpowers/specs/2026-05-02-hardware-twin-design.md` for the locked design decisions.

---

## File structure

| Path | Action | Responsibility |
|---|---|---|
| `multiace_web/src/multiace_web/static/hardware-twin.js` | **create** | Whole module — `mount`, `render`, animation lifecycle, ACE block factory |
| `multiace_web/src/multiace_web/static/index.html` | modify | Add nav button, view section, script tag, cache-bust query |
| `multiace_web/src/multiace_web/static/style.css` | modify | Append `/* hardware-twin */` block with CSS variables, classes, keyframes |
| `multiace_web/src/multiace_web/static/app.js` | modify | Hoist 3 shared helpers to `window.MultiACEUtil`; call `HardwareTwin.mount` on first tab activation; call `HardwareTwin.render` from `renderAll` and `fetchPrint` |
| `multiace_web/src/multiace_web/__init__.py` | modify | `__version__ = "0.6.0"` |
| `multiace_web/tools/visual_regression.py` | modify | Append `"hardware"` to `READ_ONLY_TABS`; update docstring |
| `multiace_web/README.md` | modify | One paragraph in Features; one changelog line |

The whole feature lives in one new JS file; everything else is wiring or documentation.

---

## Local validation environment (used by every task)

The project has no JavaScript unit-test framework — that's a deliberate, established choice (`README.md` calls it out). Validation per task is:

1. Run the local dev server with the synthetic-log fixture:
   ```bash
   cd multiace_web
   . .venv/bin/activate
   MOONRAKER_URL=http://127.0.0.1:7125 \
     MULTIACE_LOG_DIR=tests/fixtures/logs \
     uvicorn multiace_web.server:app --port 7126 --reload
   ```
2. Open `http://localhost:7126/` in a real browser.
3. Click the **Hardware** tab.
4. Verify the visual checks listed in the task; check `DevTools → Console` for errors.
5. After Task 11 only, run the full Playwright visual-regression sweep.

If `tests/fixtures/logs` does not exist locally, copy a recent `multiace_state.log` and `multiace_usb.log` from `/home/lava/printer_data/logs/` on the printer into a local directory and point `MULTIACE_LOG_DIR` there.

---

## Task 1: Scaffold the new tab and create the empty module

**Files:**
- Modify: `multiace_web/src/multiace_web/static/index.html` (lines 8-9, 39-46, end of `<main>`)
- Create: `multiace_web/src/multiace_web/static/hardware-twin.js`

- [ ] **Step 1: Create the empty module**

`multiace_web/src/multiace_web/static/hardware-twin.js`:

```javascript
// multiACE Web Console — Hardware tab
// Vanilla JS, no framework, no build step.
//
// Public API on window.HardwareTwin:
//   mount(rootEl)                          — once, builds static skeleton
//   render(state, printState, workflow)    — every state push; mutates only

window.HardwareTwin = (function () {
  function mount(rootEl) {
    if (rootEl.dataset.htwMounted === "1") return;
    rootEl.dataset.htwMounted = "1";
    // Skeleton built in Task 3.
  }

  function render(state, printState, workflow) {
    // Implemented incrementally across Tasks 3-9.
  }

  return { mount, render };
})();
```

- [ ] **Step 2: Add the nav button to index.html**

Edit `multiace_web/src/multiace_web/static/index.html` at line 44 (the `<button data-view="diag">` line). Insert a new button immediately after Diag and before the help button:

```html
    <button data-view="diag" class="tab">Diag</button>
    <button data-view="hardware" class="tab">Hardware</button>
    <button id="help-btn" class="tab help-btn" type="button" aria-label="Help" title="What does each control do?">?</button>
```

- [ ] **Step 3: Add the view section to index.html**

In the same file, add the new `<section>` after the Diag section's closing `</section>` (after line 136) and before the closing `</main>`:

```html
    <section data-view="hardware" class="view">
      <div id="htw-banner" class="htw-banner hidden"></div>
      <div id="htw-root" class="htw-root"></div>
    </section>
```

- [ ] **Step 4: Add the script tag and bump cache-bust query**

In `index.html` head, change line 8-9 from:

```html
  <link rel="stylesheet" href="static/style.css?v=0.5.3" />
  <script defer src="static/app.js?v=0.5.3"></script>
```

to:

```html
  <link rel="stylesheet" href="static/style.css?v=0.6.0" />
  <script defer src="static/app.js?v=0.6.0"></script>
  <script defer src="static/hardware-twin.js?v=0.6.0"></script>
```

- [ ] **Step 5: Validate**

Reload the dev server and open `http://localhost:7126/`. Click the **Hardware** tab.

Expected: nav switches; the page shows an empty pane (the existing tab-switching code already handles `data-view="hardware"` because it's purely class-toggling). DevTools Console: no errors. `window.HardwareTwin` exists with `mount` and `render` methods (check from console).

- [ ] **Step 6: Commit**

```bash
git add multiace_web/src/multiace_web/static/hardware-twin.js \
        multiace_web/src/multiace_web/static/index.html
git commit -m "feat(web): scaffold Hardware tab with empty module"
```

---

## Task 2: Hoist the three shared helpers from app.js

The new module needs `rgbFromUint`, `tName`, and `slotName`. Spec mandates reuse, not reinvention. Add a tiny export shim at the bottom of `app.js`.

**Files:**
- Modify: `multiace_web/src/multiace_web/static/app.js` (append after the `renderAll()` block and helpers, around line 925)

- [ ] **Step 1: Add the hoist block in app.js**

Find the existing helper block at `app.js:905-918`. Immediately after `slotName` (line 917) and before `setEl` (line 919), no — actually we want this hoist to run AFTER all three are declared. Append it after `slotName`'s closing brace at line 917:

Edit `app.js` at the line that reads `function slotName(i) { return \`Slot ${(+i) + 1}\`; }` — append the hoist right after:

```javascript
function tName(i)    { return `T${(+i) + 1}`; }
function slotName(i) { return `Slot ${(+i) + 1}`; }

// Expose helpers to hardware-twin.js (vanilla project, no module system).
window.MultiACEUtil = { rgbFromUint, tName, slotName };
```

- [ ] **Step 2: Validate**

Reload `http://localhost:7126/`. In DevTools Console:

```javascript
window.MultiACEUtil.rgbFromUint(0xff22c55e)  // should return "rgb(34,197,94)"
window.MultiACEUtil.tName(0)                  // should return "T1"
window.MultiACEUtil.slotName(2)               // should return "Slot 3"
```

Expected: all three return correctly. No regressions on Dashboard.

- [ ] **Step 3: Commit**

```bash
git add multiace_web/src/multiace_web/static/app.js
git commit -m "feat(web): hoist rgbFromUint/tName/slotName to window.MultiACEUtil"
```

---

## Task 3: mount() — U1 SVG skeleton, couplers, toolhead button row

Build everything that exists once-and-doesn't-change. Per ACE blocks come later.

**Files:**
- Modify: `multiace_web/src/multiace_web/static/hardware-twin.js`
- Modify: `multiace_web/src/multiace_web/static/style.css`

- [ ] **Step 1: Replace the empty `mount` body in `hardware-twin.js`**

```javascript
window.HardwareTwin = (function () {
  // ---- DOM helpers ----
  const SVG_NS = "http://www.w3.org/2000/svg";
  function svgEl(tag, attrs) {
    const el = document.createElementNS(SVG_NS, tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    }
    return el;
  }
  function htmlEl(tag, attrs) {
    const el = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (k === "className") el.className = v;
        else if (k === "textContent") el.textContent = v;
        else if (k === "dataset") {
          for (const [dk, dv] of Object.entries(v)) el.dataset[dk] = dv;
        } else el.setAttribute(k, v);
      }
    }
    return el;
  }

  // ---- Public ----
  function mount(rootEl) {
    if (rootEl.dataset.htwMounted === "1") return;
    rootEl.dataset.htwMounted = "1";

    // Empty-state placeholder (visible until first render with device_count > 0)
    const empty = htmlEl("div", { className: "htw-empty", id: "htw-empty" });
    empty.textContent = "Waiting for ACE…";
    rootEl.appendChild(empty);

    // Per-ACE blocks live in this container; they are appended/removed by render().
    const aceStack = htmlEl("div", { className: "htw-ace-stack", id: "htw-ace-stack" });
    rootEl.appendChild(aceStack);

    // Coupler row + post-coupler tubes (a single SVG, full-width, between ACE stack and U1).
    const couplerSvg = svgEl("svg", {
      viewBox: "0 0 360 80",
      preserveAspectRatio: "none",
      class: "htw-coupler-svg",
    });
    const couplerGroup = svgEl("g", { fill: "none", "stroke-linecap": "round" });
    couplerSvg.appendChild(couplerGroup);
    for (let i = 0; i < 4; i++) {
      const x = 110 + i * 60;
      // Coupler bar
      const bar = svgEl("rect", {
        x: x - 12, y: 12, width: 24, height: 16, rx: 4,
        fill: "#0f172a", id: `htw-coupler-${i}`,
      });
      couplerGroup.appendChild(bar);
      // Post-coupler tube
      const post = svgEl("line", {
        x1: x, y1: 28, x2: x, y2: 80,
        stroke: "var(--htw-tube-grey)", "stroke-width": 6,
        id: `htw-postcoupler-${i}`,
      });
      couplerGroup.appendChild(post);
    }
    rootEl.appendChild(couplerSvg);

    // U1 SVG (static skeleton; toolhead colors/labels mutated by render).
    const u1 = svgEl("svg", {
      viewBox: "0 0 360 240",
      class: "htw-u1-svg",
    });
    // Header band
    const hdr = svgEl("rect", { x: 4, y: 0, width: 352, height: 14, rx: 6, fill: "#1e293b" });
    const hdrText = svgEl("text", { x: 12, y: 11, "font-size": 9, fill: "#cbd5e1" });
    hdrText.textContent = "Snapmaker U1";
    u1.appendChild(hdr);
    u1.appendChild(hdrText);
    // Chassis
    u1.appendChild(svgEl("rect", {
      x: 4, y: 14, width: 352, height: 222, rx: 14,
      fill: "#fff", stroke: "var(--htw-stroke)", "stroke-width": 2,
    }));
    // Input strip
    u1.appendChild(svgEl("rect", {
      x: 60, y: 14, width: 240, height: 14, rx: 4,
      fill: "#cbd5e1", "fill-opacity": 0.55,
    }));
    // 4 toolheads in 2×2 grid: T1(28,38), T2(186,38), T3(28,124), T4(186,124)
    const toolPositions = [
      { x: 28,  y: 38  },  // T1
      { x: 186, y: 38  },  // T2
      { x: 28,  y: 124 },  // T3
      { x: 186, y: 124 },  // T4
    ];
    toolPositions.forEach((p, i) => {
      const g = svgEl("g", { id: `htw-tool-${i}`, class: "htw-tool" });
      g.appendChild(svgEl("rect", {
        x: p.x, y: p.y, width: 146, height: 74, rx: 8,
        class: "htw-tool-body",
        fill: "transparent", stroke: "var(--htw-stroke-empty)", "stroke-dasharray": "4 3",
      }));
      const label = svgEl("text", {
        x: p.x + 73, y: p.y + 41,
        "text-anchor": "middle", "font-size": 18, "font-weight": 700,
        fill: "var(--htw-stroke-empty)", class: "htw-tool-label",
      });
      label.textContent = `T${i + 1}`;
      g.appendChild(label);
      const sublabel = svgEl("text", {
        x: p.x + 73, y: p.y + 57,
        "text-anchor": "middle", "font-size": 9,
        fill: "#cbd5e1", class: "htw-tool-source",
        id: `htw-tool-source-${i}`,
        "fill-opacity": 0,
      });
      sublabel.textContent = "";
      g.appendChild(sublabel);
      u1.appendChild(g);
    });
    rootEl.appendChild(u1);

    // Toolhead button row (HTML, below the U1 SVG)
    const toolBtns = htmlEl("div", {
      className: "htw-actions htw-tool-actions", id: "htw-tool-actions",
    });
    for (let i = 0; i < 4; i++) {
      const cell = htmlEl("div", { className: "htw-cell" });
      const loadBtn = htmlEl("button", {
        className: "primary", textContent: `Load T${i + 1}`,
        dataset: { cmd: `ACEC__Load_T${i}`, htw: "tool-load", tool: String(i) },
      });
      const unloadBtn = htmlEl("button", {
        className: "danger htw-hidden", textContent: `Unload T${i + 1}`,
        dataset: {
          cmd: `ACEC__Unload_T${i}`, confirm: `Unload T${i + 1}?`,
          htw: "tool-unload", tool: String(i),
        },
      });
      cell.appendChild(loadBtn);
      cell.appendChild(unloadBtn);
      toolBtns.appendChild(cell);
    }
    rootEl.appendChild(toolBtns);
  }

  function render(state, printState, workflow) {
    // Implemented incrementally across Tasks 4-9.
  }

  return { mount, render };
})();
```

- [ ] **Step 2: Add the CSS variables and base classes**

Append at the end of `multiace_web/src/multiace_web/static/style.css`:

```css
/* =========================================================
   hardware-twin — Hardware tab styles. Every selector is htw-*.
   ========================================================= */
:root {
  --htw-stroke:#334155;
  --htw-stroke-empty:#94a3b8;
  --htw-tube-grey:#d1d5db;
  --htw-flash-glow:rgba(245, 158, 11, 0.85);
  --htw-extrude-glow:rgba(34, 197, 94, 0.55);
  --htw-banner-bg:#fde68a;
  --htw-banner-fg:#7c2d12;
  --htw-text-muted:#64748b;
}

.htw-root {
  display: flex; flex-direction: column; gap: 0; align-items: stretch;
  padding: 8px;
}
.htw-banner {
  background: var(--htw-banner-bg);
  color: var(--htw-banner-fg);
  font-weight: 600; font-size: 13px;
  padding: 8px 12px; border-radius: 6px;
  margin: 0 8px 10px;
  display: flex; justify-content: space-between; align-items: center;
}
.htw-banner.hidden { display: none; }
.htw-banner-dismiss {
  background: transparent; border: 0; color: inherit;
  font-size: 18px; line-height: 1; cursor: pointer; padding: 0 4px;
}

.htw-empty {
  text-align: center; color: var(--htw-text-muted);
  padding: 64px 16px; font-size: 14px;
}
.htw-empty.hidden { display: none; }

.htw-ace-stack { display: flex; flex-direction: column-reverse; gap: 6px; }

.htw-ace-svg { width: 70%; height: auto; display: block; margin: 0 auto; }
.htw-coupler-svg { width: 100%; height: 80px; display: block; }
.htw-u1-svg { width: 100%; height: auto; display: block; }

.htw-actions {
  display: grid; gap: 6px;
  margin: 6px auto 14px;
}
.htw-slot-actions { width: 70%; grid-template-columns: repeat(4, 1fr); }
.htw-tool-actions { width: 100%; grid-template-columns: repeat(2, 1fr); gap: 8px; padding: 0 8px; }
.htw-actions .htw-cell { display: flex; flex-direction: column; gap: 3px; }
.htw-actions button {
  font-size: 12px; padding: 6px 8px; border: 0; border-radius: 4px;
  cursor: pointer; font-weight: 600;
}
.htw-actions button.primary { background: #1f2937; color: #fff; }
.htw-actions button.danger  { background: #b91c1c; color: #fff; }
.htw-actions button:disabled { opacity: 0.4; cursor: not-allowed; }
.htw-actions .htw-hidden { display: none; }

.htw-disconnected .htw-u1-svg,
.htw-disconnected .htw-coupler-svg,
.htw-disconnected .htw-ace-stack { opacity: 0.5; pointer-events: none; }

@media (max-width: 480px) {
  .htw-tool-source { display: none; }
}
```

- [ ] **Step 3: Wire mount() to the tab activation in app.js**

Find `function setView(name)` at `app.js:1367` and insert a one-liner after the existing two `for` loops. The function should look like:

```javascript
function setView(name) {
  for (const tab of document.querySelectorAll(".tab")) {
    tab.classList.toggle("active", tab.dataset.view === name);
  }
  for (const view of document.querySelectorAll(".view")) {
    view.classList.toggle("active", view.dataset.view === name);
  }
  if (name === "hardware" && window.HardwareTwin) {
    window.HardwareTwin.mount(document.getElementById("htw-root"));
    window.HardwareTwin.render(state, printState, workflow);
  }
}
```

- [ ] **Step 4: Validate**

Reload the dev server. Click the **Hardware** tab.

Expected: U1 SVG renders centered, full-width, with 4 dashed-empty toolheads in a 2×2 grid (T1 top-left, T2 top-right, T3 bottom-left, T4 bottom-right), header band reading "Snapmaker U1", and a 2×2 button grid below labelled "Load T1", "Load T2", etc. The "Waiting for ACE…" empty-state text is visible above the U1 (we'll hide it in Task 4 once render runs). DevTools Console: no errors.

Click any "Load Tn" button — the existing `[data-cmd]` listener should send the macro through Moonraker. (If running against the real printer, this will move the toolhead — only test on a printer that's idle.)

- [ ] **Step 5: Commit**

```bash
git add multiace_web/src/multiace_web/static/hardware-twin.js \
        multiace_web/src/multiace_web/static/style.css \
        multiace_web/src/multiace_web/static/app.js
git commit -m "feat(web): mount static U1 + couplers + toolhead buttons"
```

---

## Task 4: render() core — empty state, ACE block factory, integration with renderAll

Make the dynamic ACE-block creation work. After this task, switching to the Hardware tab should show one ACE block per discovered device, sized correctly, but slot/tube content is still placeholder.

**Files:**
- Modify: `multiace_web/src/multiace_web/static/hardware-twin.js`
- Modify: `multiace_web/src/multiace_web/static/app.js`

- [ ] **Step 1: Add an ACE block factory and the resize loop in `hardware-twin.js`**

Replace the placeholder `render` function with:

```javascript
  // ---- ACE block factory ----
  function _buildAceBlock(deviceIdx) {
    const wrap = htmlEl("div", {
      className: "htw-ace-block",
      id: `htw-ace-${deviceIdx}`,
      dataset: { device: String(deviceIdx) },
    });
    // ACE SVG (viewBox 280×160; ACE is 70% width via CSS class)
    const svg = svgEl("svg", { viewBox: "0 0 280 160", class: "htw-ace-svg" });
    // Header band
    svg.appendChild(svgEl("rect", { x: 4, y: 0, width: 272, height: 14, rx: 6, fill: "#1e293b" }));
    const hdrText = svgEl("text", {
      x: 12, y: 11, "font-size": 9, fill: "#cbd5e1",
      class: "htw-ace-header-text", id: `htw-ace-${deviceIdx}-header`,
    });
    hdrText.textContent = `ACE ${String.fromCharCode(65 + deviceIdx)}`;
    svg.appendChild(hdrText);
    // Chassis
    svg.appendChild(svgEl("rect", {
      x: 4, y: 14, width: 272, height: 138, rx: 10,
      fill: "#fff", stroke: "var(--htw-stroke)", "stroke-width": 2,
      class: "htw-ace-chassis", id: `htw-ace-${deviceIdx}-chassis`,
    }));
    // 4 slot rectangles
    for (let i = 0; i < 4; i++) {
      const x = 12 + i * 66;
      const g = svgEl("g", {
        id: `htw-ace-${deviceIdx}-slot-${i}`,
        class: "htw-slot",
        dataset: { device: String(deviceIdx), slot: String(i) },
      });
      g.appendChild(svgEl("rect", {
        x: x, y: 22, width: 58, height: 120, rx: 6,
        fill: "transparent",
        stroke: "var(--htw-stroke-empty)",
        "stroke-dasharray": "4 3",
        class: "htw-slot-body",
      }));
      const label = svgEl("text", {
        x: x + 29, y: 87,
        "text-anchor": "middle", "font-size": 14, "font-weight": 700,
        fill: "var(--htw-stroke-empty)", class: "htw-slot-label",
      });
      label.textContent = String(i + 1);
      g.appendChild(label);
      svg.appendChild(g);
    }
    wrap.appendChild(svg);

    // Slot button row
    const slotBtns = htmlEl("div", {
      className: "htw-actions htw-slot-actions",
      id: `htw-ace-${deviceIdx}-actions`,
    });
    for (let i = 0; i < 4; i++) {
      const cell = htmlEl("div", { className: "htw-cell" });
      const loadBtn = htmlEl("button", {
        className: "primary htw-hidden", textContent: "Load",
        dataset: { cmd: `ACEC__Load_T${i}`, htw: "slot-load",
                   device: String(deviceIdx), slot: String(i) },
      });
      const unloadBtn = htmlEl("button", {
        className: "danger htw-hidden", textContent: "Unload",
        dataset: { htw: "slot-unload", device: String(deviceIdx), slot: String(i) },
      });
      cell.appendChild(loadBtn);
      cell.appendChild(unloadBtn);
      slotBtns.appendChild(cell);
    }
    wrap.appendChild(slotBtns);
    return wrap;
  }

  function _resizeAceStack(deviceCount) {
    const stack = document.getElementById("htw-ace-stack");
    if (!stack) return;
    const existing = stack.querySelectorAll(".htw-ace-block");
    if (existing.length === deviceCount) return;
    if (existing.length < deviceCount) {
      for (let d = existing.length; d < deviceCount; d++) {
        // Newest ACE on top → CSS `flex-direction: column-reverse` on the
        // stack means appending here visually places the newest at the top.
        stack.appendChild(_buildAceBlock(d));
      }
    } else {
      for (let d = existing.length - 1; d >= deviceCount; d--) {
        stack.removeChild(existing[d]);
      }
    }
  }

  function render(state, printState, workflow) {
    if (!document.getElementById("htw-root")) return;
    const empty = document.getElementById("htw-empty");
    const dc = state.device_count || 0;
    if (empty) empty.classList.toggle("hidden", dc > 0);
    _resizeAceStack(dc);
    // Per-element state mutations follow in Tasks 5-9.
  }
```

- [ ] **Step 2: Wire render() into renderAll() and fetchPrint() in app.js**

Find `function renderAll()` at `app.js:864`. Append a call inside, after the existing renderers:

```javascript
function renderAll() {
  renderTopbar();
  renderSlots();
  renderToolheads();
  renderActivity();
  renderActivityPreview();
  renderActionBar();
  renderDryer();
  renderConfig();
  renderDiag();
  renderStatusBanner();
  renderWorkflow();
  if (window.HardwareTwin) {
    window.HardwareTwin.render(state, printState, workflow);
  }
}
```

Find `async function fetchPrint()` at `app.js:271`. Append a call just before the closing brace:

```javascript
  renderPrintPanel();
  renderToolheads();
  renderStatusBanner();
  renderDryerStatus();
  renderEnvStrip();
  if (window.HardwareTwin) {
    window.HardwareTwin.render(state, printState, workflow);
  }
}
```

- [ ] **Step 3: Validate**

Reload the page. Click **Hardware**.

Expected behavior depends on the synthetic-log fixture: if it advertises `device_count: 1` (typical), you should see one ACE block above the U1 with a header reading "ACE A", chassis outline, and 4 dashed-empty slot rectangles. The "Waiting for ACE…" text disappears. Slot buttons (Load/Unload) are present but hidden (visibility logic comes in Task 7). Switch to Dashboard and back — the ACE block should not duplicate.

If `device_count` is ≥ 2 in your fixture, you see two stacked blocks — the top labelled "ACE B" (newest), bottom labelled "ACE A".

Console: no errors.

- [ ] **Step 4: Commit**

```bash
git add multiace_web/src/multiace_web/static/hardware-twin.js \
        multiace_web/src/multiace_web/static/app.js
git commit -m "feat(web): dynamic ACE block creation in HardwareTwin.render"
```

---

## Task 5: render() — U1 toolhead state mapping

Apply `state.sensors`, `state.head_source`, `state.print_task_config`, and `state.last_error` to the toolhead rectangles + source labels.

**Files:**
- Modify: `multiace_web/src/multiace_web/static/hardware-twin.js`

- [ ] **Step 1: Add a `_renderToolheads` helper and call it from `render`**

Inside the IIFE in `hardware-twin.js`, add:

```javascript
  function _renderToolheads(state, printState) {
    const cfg = state.print_task_config || {};
    for (let i = 0; i < 4; i++) {
      const g = document.getElementById(`htw-tool-${i}`);
      if (!g) continue;
      const body = g.querySelector(".htw-tool-body");
      const label = g.querySelector(".htw-tool-label");
      const source = document.getElementById(`htw-tool-source-${i}`);
      const src = state.head_source ? state.head_source[i] : null;
      const sensor = state.sensors ? !!state.sensors[i] : false;
      const c = (cfg[i] && window.MultiACEUtil.rgbFromUint(cfg[i].color)) || null;
      const err = state.last_error && state.last_error.head === i;
      const extruding =
        printState && printState.state === "printing" &&
        printState.current_extruder === i;

      // Body fill / stroke
      if (sensor && c) {
        body.setAttribute("fill", c);
        body.setAttribute("stroke", "#1f2937");
        body.removeAttribute("stroke-dasharray");
      } else if (sensor) {
        // Loaded but no color (RFID didn't read) — neutral grey
        body.setAttribute("fill", "#cbd5e1");
        body.setAttribute("stroke", "#1f2937");
        body.removeAttribute("stroke-dasharray");
      } else {
        body.setAttribute("fill", "transparent");
        body.setAttribute("stroke", "var(--htw-stroke-empty)");
        body.setAttribute("stroke-dasharray", "4 3");
      }
      label.setAttribute("fill", sensor ? "#fff" : "var(--htw-stroke-empty)");

      // Error tint
      g.classList.toggle("htw-error", !!err);
      if (err) {
        g.setAttribute("title",
          state.last_error.error || state.last_error.reason || "error");
      } else {
        g.removeAttribute("title");
      }

      // Extruding emphasis (mild glow, not flash)
      g.classList.toggle("htw-extruding", !!extruding);

      // Source sublabel
      if (src) {
        const aceLetter = String.fromCharCode(65 + (src.ace || src.ace_index || 0));
        const slotN = (src.slot != null ? src.slot : 0) + 1;
        source.textContent = `ACE ${aceLetter} · Slot ${slotN}`;
        source.setAttribute("fill-opacity", "1");
      } else {
        source.textContent = "";
        source.setAttribute("fill-opacity", "0");
      }

      // Post-coupler tube color mirrors loaded source
      const post = document.getElementById(`htw-postcoupler-${i}`);
      if (post) {
        post.setAttribute("stroke",
          (sensor && c) ? c : "var(--htw-tube-grey)");
      }
    }
  }

  // Update render():
  function render(state, printState, workflow) {
    if (!document.getElementById("htw-root")) return;
    const empty = document.getElementById("htw-empty");
    const dc = state.device_count || 0;
    if (empty) empty.classList.toggle("hidden", dc > 0);
    _resizeAceStack(dc);
    _renderToolheads(state, printState);
    // Slots, tubes, buttons, banner, animations come in Tasks 6-9.
  }
```

- [ ] **Step 2: Add the error and extruding CSS**

Append to the hardware-twin block in `style.css`:

```css
.htw-tool.htw-error .htw-tool-body {
  stroke: #ef4444 !important;
  stroke-width: 3 !important;
}
@media (prefers-reduced-motion: no-preference) {
  @keyframes htwExtrudeGlow {
    0%, 100% { filter: none; }
    50%      { filter: drop-shadow(0 0 4px var(--htw-extrude-glow)); }
  }
  .htw-tool.htw-extruding .htw-tool-body {
    animation: htwExtrudeGlow 2.5s ease-in-out infinite;
  }
}
@media (prefers-reduced-motion: reduce) {
  .htw-tool.htw-extruding .htw-tool-body {
    outline: 2px solid var(--htw-extrude-glow);
  }
}
```

- [ ] **Step 3: Validate**

Reload. Open **Hardware**. Compare the toolhead colors to the existing Dashboard's toolhead cards: every toolhead with filament should be the same color in both views; empty toolheads should be dashed in both.

If your fixture has a `last_error` for some head, that head should show a red border on the Hardware tab. If a print is running, the current extruder should pulse with a faint green glow.

DevTools Console: no errors.

- [ ] **Step 4: Commit**

```bash
git add multiace_web/src/multiace_web/static/hardware-twin.js \
        multiace_web/src/multiace_web/static/style.css
git commit -m "feat(web): toolhead state mapping (color, sensor, source, error, extrude)"
```

---

## Task 6: render() — slot fills and tube parked vs active states

Map state to per-slot fill colors and to the colored fill segment over each backing tube rail. After this, a print-state snapshot tells the whole story visually (minus animations).

**Files:**
- Modify: `multiace_web/src/multiace_web/static/hardware-twin.js`
- Modify: `multiace_web/src/multiace_web/static/style.css`

- [ ] **Step 1: Replace the slot factory's tubes with full backing rails + fill paths**

In `_buildAceBlock`, after the loop that creates the 4 slot rectangles, add the per-slot tube backing rails AND the colored fill paths. These live in a SECOND SVG below the ACE SVG, sized so its viewBox aligns with the ACE viewBox + stretches down to a virtual "coupler row".

Replace the body of `_buildAceBlock` after `wrap.appendChild(svg)` and before `// Slot button row` with:

```javascript
    // Per-slot tubes (one SVG per ACE block; viewBox 360×TBD).
    // Tube length depends on how far this ACE sits from the U1's coupler row.
    // For the visual we use a fixed per-block tube SVG that runs from the
    // bottom of this ACE (y=0) to a virtual coupler row at the SVG's bottom.
    // The actual coupler→U1 stroke lives in the global #htw-coupler-svg.
    const tubeSvg = svgEl("svg", {
      viewBox: "0 0 360 80",
      preserveAspectRatio: "none",
      class: "htw-tubes-svg",
      "data-device": String(deviceIdx),
    });
    const tubeGroup = svgEl("g", { fill: "none", "stroke-linecap": "round" });
    tubeSvg.appendChild(tubeGroup);
    for (let i = 0; i < 4; i++) {
      const x = 110 + i * 60;
      // Backing rail (dashed grey, always visible)
      tubeGroup.appendChild(svgEl("line", {
        x1: x, y1: 0, x2: x, y2: 80,
        stroke: "var(--htw-tube-grey)",
        "stroke-width": 3,
        "stroke-dasharray": "2 3",
        class: "htw-tube-rail",
      }));
      // Colored fill on top (shown only when slot has filament)
      tubeGroup.appendChild(svgEl("line", {
        x1: x, y1: 0, x2: x, y2: 80,
        stroke: "transparent",
        "stroke-width": 6,
        id: `htw-tube-${deviceIdx}-${i}`,
        class: "htw-tube-fill",
        pathLength: "100",
        "stroke-dasharray": "100",
        "stroke-dashoffset": "100",
        "data-state": "empty",
      }));
    }
    wrap.appendChild(tubeSvg);
```

(Add this between the ACE SVG append and the Slot-button-row append.)

- [ ] **Step 2: Add `_renderSlotsAndTubes` and call it from render()**

Inside the IIFE, add:

```javascript
  function _slotIsActiveSource(state, deviceIdx, slotIdx) {
    if (!state.head_source) return null;
    for (const [headStr, src] of Object.entries(state.head_source)) {
      if (!src) continue;
      const aceI = src.ace != null ? src.ace : src.ace_index;
      if (aceI === deviceIdx && src.slot === slotIdx) return Number(headStr);
    }
    return null;
  }

  function _slotIsParked(state, deviceIdx, slotIdx) {
    // "Parked" = filament is in the slot but the slot is NOT the active
    // source for its mapped toolhead. Default mapping: slot N → toolhead N.
    if (deviceIdx === state.active_device && state.gate_status &&
        state.gate_status[slotIdx] === 1) {
      // Active ACE: slot is filled per gate_status. Parked iff this slot
      // is not the source of head_source[slotIdx].
      return _slotIsActiveSource(state, deviceIdx, slotIdx) === null;
    }
    // Non-active ACE: only known-filled if some head_source references it.
    return false;  // unknown → leave as empty visually
  }

  function _renderSlotsAndTubes(state) {
    const cfg = state.print_task_config || {};
    const blocks = document.querySelectorAll(".htw-ace-block");
    blocks.forEach(block => {
      const d = Number(block.dataset.device);
      block.classList.toggle("htw-ace-active", d === state.active_device);
      const hdr = document.getElementById(`htw-ace-${d}-header`);
      if (hdr) {
        hdr.textContent = `ACE ${String.fromCharCode(65 + d)} · ${
          d === state.active_device ? "active" : "idle"}`;
      }
      for (let i = 0; i < 4; i++) {
        const slot = document.getElementById(`htw-ace-${d}-slot-${i}`);
        const slotBody = slot.querySelector(".htw-slot-body");
        const slotLabel = slot.querySelector(".htw-slot-label");
        const tube = document.getElementById(`htw-tube-${d}-${i}`);

        const sourcedHead = _slotIsActiveSource(state, d, i);
        const parked = _slotIsParked(state, d, i);
        const active = sourcedHead != null;
        const filledOnActiveAce =
          d === state.active_device &&
          state.gate_status && state.gate_status[i] === 1;

        // Color comes from the toolhead this slot feeds (active),
        // or — if parked on the active ACE — from the slot's gate_status
        // alone we have no color (gate_status is 0/1). For parked on
        // non-active ACE we'd need head_source, which doesn't apply here.
        let color = null;
        if (active) {
          color = window.MultiACEUtil.rgbFromUint(
            cfg[sourcedHead] && cfg[sourcedHead].color);
        } else if (parked) {
          // Parked on active ACE: color is unknown without RFID; show neutral.
          color = "#cbd5e1";
        }

        // Slot rectangle
        if (active || parked || filledOnActiveAce) {
          slotBody.setAttribute("fill", color || "#cbd5e1");
          slotBody.setAttribute("stroke", "#1f2937");
          slotBody.removeAttribute("stroke-dasharray");
          slotLabel.setAttribute("fill", "#fff");
        } else {
          slotBody.setAttribute("fill", "transparent");
          slotBody.setAttribute("stroke", "var(--htw-stroke-empty)");
          slotBody.setAttribute("stroke-dasharray", "4 3");
          slotLabel.setAttribute("fill", "var(--htw-stroke-empty)");
        }

        // Tube fill: active = full, parked = stops 15% short, empty = 0
        if (active) {
          tube.setAttribute("stroke", color || "#cbd5e1");
          tube.setAttribute("stroke-dashoffset", "0");
          tube.dataset.state = "active";
        } else if (parked) {
          tube.setAttribute("stroke", color || "#cbd5e1");
          tube.setAttribute("stroke-dashoffset", "15");
          tube.dataset.state = "parked";
        } else {
          tube.setAttribute("stroke", "transparent");
          tube.setAttribute("stroke-dashoffset", "100");
          tube.dataset.state = "empty";
        }
      }
    });
  }

  function render(state, printState, workflow) {
    if (!document.getElementById("htw-root")) return;
    const empty = document.getElementById("htw-empty");
    const dc = state.device_count || 0;
    if (empty) empty.classList.toggle("hidden", dc > 0);
    _resizeAceStack(dc);
    _renderToolheads(state, printState);
    _renderSlotsAndTubes(state);
    // Buttons, banner, animations in Tasks 7-9.
  }
```

- [ ] **Step 3: Add active-ACE accent style**

Append to the hardware-twin block in `style.css`:

```css
.htw-ace-active .htw-ace-chassis { stroke: #0ea5e9 !important; stroke-width: 2.5 !important; }
.htw-tubes-svg { width: 70%; height: 80px; display: block; margin: -8px auto 0; }
```

- [ ] **Step 4: Validate**

Reload. **Hardware** tab.

Active ACE block should have a blue-tinted chassis outline. Slots: filled slots show colored fills; empty slots stay dashed. Tubes below each filled slot show colored vertical lines (full length when that slot is sourcing a toolhead; stops short when parked); empty slot tubes show only the dashed grey backing rail. The U1 toolheads still reflect their state from Task 5.

Compare against Dashboard's slots/toolheads — colors should match.

- [ ] **Step 5: Commit**

```bash
git add multiace_web/src/multiace_web/static/hardware-twin.js \
        multiace_web/src/multiace_web/static/style.css
git commit -m "feat(web): slot + tube state mapping (active vs parked vs empty)"
```

---

## Task 7: render() — slot button rows

Show context-aware Load/Unload buttons under each ACE block, mirroring the existing Dashboard's slot-card buttons.

**Files:**
- Modify: `multiace_web/src/multiace_web/static/hardware-twin.js`

- [ ] **Step 1: Add `_renderSlotButtons` and call it from render()**

Inside the IIFE:

```javascript
  function _renderSlotButtons(state) {
    const swap = !!state.swap_in_progress;
    const blocks = document.querySelectorAll(".htw-ace-block");
    blocks.forEach(block => {
      const d = Number(block.dataset.device);
      const row = document.getElementById(`htw-ace-${d}-actions`);
      if (!row) return;
      for (let i = 0; i < 4; i++) {
        const cell = row.children[i];
        const loadBtn = cell.querySelector('button[data-htw="slot-load"]');
        const unloadBtn = cell.querySelector('button[data-htw="slot-unload"]');

        const sourcedHead = _slotIsActiveSource(state, d, i);
        const filled =
          d === state.active_device &&
          state.gate_status && state.gate_status[i] === 1;

        if (sourcedHead != null) {
          // Slot is active source for sourcedHead — show Unload of that head
          loadBtn.classList.add("htw-hidden");
          unloadBtn.classList.remove("htw-hidden");
          unloadBtn.dataset.cmd = `ACEC__Unload_T${sourcedHead}`;
          unloadBtn.dataset.confirm = `Unload T${sourcedHead + 1}?`;
          unloadBtn.textContent = `Unload T${sourcedHead + 1}`;
          unloadBtn.disabled = swap;
          loadBtn.disabled = true;
        } else if (filled) {
          // Filled but not currently sourcing — offer Load → T(slot index)
          loadBtn.classList.remove("htw-hidden");
          unloadBtn.classList.add("htw-hidden");
          loadBtn.textContent = `Load → T${i + 1}`;
          loadBtn.dataset.cmd = `ACEC__Load_T${i}`;
          loadBtn.disabled = swap;
        } else {
          // Empty / non-active ACE / unknown
          loadBtn.classList.add("htw-hidden");
          unloadBtn.classList.add("htw-hidden");
        }
      }
    });
  }

  // Call from render():
  function render(state, printState, workflow) {
    if (!document.getElementById("htw-root")) return;
    const empty = document.getElementById("htw-empty");
    const dc = state.device_count || 0;
    if (empty) empty.classList.toggle("hidden", dc > 0);
    _resizeAceStack(dc);
    _renderToolheads(state, printState);
    _renderSlotsAndTubes(state);
    _renderSlotButtons(state);
    // Banner + animations in Tasks 8-9.
  }
```

- [ ] **Step 2: Update toolhead button visibility from `_renderToolheads`**

In the existing `_renderToolheads` function, after the per-toolhead loop's existing body, append visibility/disabled rules:

```javascript
      // Toolhead button row
      const cell = document.getElementById("htw-tool-actions").children[i];
      const loadBtn = cell.querySelector('button[data-htw="tool-load"]');
      const unloadBtn = cell.querySelector('button[data-htw="tool-unload"]');
      const swap = !!state.swap_in_progress;
      if (src) {
        loadBtn.classList.add("htw-hidden");
        unloadBtn.classList.remove("htw-hidden");
        unloadBtn.disabled = swap;
      } else {
        loadBtn.classList.remove("htw-hidden");
        unloadBtn.classList.add("htw-hidden");
        loadBtn.disabled = swap;
      }
```

- [ ] **Step 3: Validate**

Reload. **Hardware** tab.

Each filled slot in the active ACE now shows either "Load → T_N" (if not sourcing yet) or "Unload T_N" (if it is sourcing). Empty slots show no buttons. U1 toolhead row: each cell shows Load OR Unload depending on whether `head_source[i]` is set. While `swap_in_progress` is true, all visible Load/Unload buttons are disabled.

Click a button — the existing global `[data-cmd]` listener fires the macro. (Test only against an idle printer.)

- [ ] **Step 4: Commit**

```bash
git add multiace_web/src/multiace_web/static/hardware-twin.js
git commit -m "feat(web): context-aware Load/Unload buttons on Hardware tab"
```

---

## Task 8: render() — status banner

Reuse the same precedence as the existing `renderStatusBanner` in `app.js` so both banners read the same content.

**Files:**
- Modify: `multiace_web/src/multiace_web/static/hardware-twin.js`

- [ ] **Step 1: Read what the existing `renderStatusBanner` does**

Reference: `app.js:817-862` (the function `renderStatusBanner`). The new `_renderBanner` mirrors the same precedence for the standalone Hardware banner element. We do not move the logic; we duplicate just enough to render an independent `#htw-banner`.

- [ ] **Step 2: Add `_renderBanner` and call it from render()**

Inside the IIFE:

```javascript
  function _renderBanner(state, workflow) {
    const banner = document.getElementById("htw-banner");
    if (!banner) return;
    let text = null;
    let kind = "info";
    if (workflow && workflow.active && workflow.label) {
      const running = workflow.steps.find(s => s.status === "running");
      const detail = running ? ` — T${running.head + 1}` : "";
      text = `▸ ${workflow.label}${detail}`;
      kind = "info";
    } else if (state.last_error && state.last_error.error) {
      text = `⚠ ${state.last_error.action || "error"}: ${state.last_error.error}`;
      kind = "error";
    } else if (state.connected === false) {
      text = "ACE disconnected";
      kind = "error";
    }
    if (text) {
      banner.textContent = "";
      const span = document.createElement("span");
      span.textContent = text;
      banner.appendChild(span);
      const x = document.createElement("button");
      x.className = "htw-banner-dismiss";
      x.textContent = "×";
      x.onclick = () => banner.classList.add("hidden");
      banner.appendChild(x);
      banner.dataset.kind = kind;
      banner.classList.remove("hidden");
    } else {
      banner.classList.add("hidden");
    }
  }

  // Update render():
  function render(state, printState, workflow) {
    if (!document.getElementById("htw-root")) return;
    const empty = document.getElementById("htw-empty");
    const dc = state.device_count || 0;
    if (empty) empty.classList.toggle("hidden", dc > 0);
    _resizeAceStack(dc);
    _renderToolheads(state, printState);
    _renderSlotsAndTubes(state);
    _renderSlotButtons(state);
    _renderBanner(state, workflow);
    // Animations in Task 9.
  }
```

- [ ] **Step 3: Add disconnected styling**

Append to the hardware-twin CSS block:

```css
.htw-banner[data-kind="error"] { background: #fecaca; color: #7f1d1d; }
.htw-banner[data-kind="info"]  { background: var(--htw-banner-bg); color: var(--htw-banner-fg); }
```

- [ ] **Step 4: Validate**

Reload. **Hardware** tab.

If your fixture has a workflow event in progress: banner reads "▸ <label> — T_N", amber.
If `last_error` is set: banner reads "⚠ <action>: <error>", red. Click ✕: banner hides.
Disconnect (e.g. unplug USB or use a fixture with `connected: false`): banner reads "ACE disconnected", red.
Otherwise: banner is hidden.

- [ ] **Step 5: Commit**

```bash
git add multiace_web/src/multiace_web/static/hardware-twin.js \
        multiace_web/src/multiace_web/static/style.css
git commit -m "feat(web): Hardware-tab status banner (workflow / error / disconnected)"
```

---

## Task 9: Animation lifecycle — flash + tube load/unload via workflow diff

Detect transitions in `workflow.steps[].status` and trigger:
- `htw-flashing` on the source ACE slot AND destination toolhead while running
- `htw-tube-loading` (animate dashoffset 100→0) on the source slot's tube during a load
- `htw-tube-unloading` (animate dashoffset 0→100) during an unload
- finalize on transition to done/failed

**Files:**
- Modify: `multiace_web/src/multiace_web/static/hardware-twin.js`
- Modify: `multiace_web/src/multiace_web/static/style.css`

- [ ] **Step 1: Add the keyframes and flash CSS**

Append to the hardware-twin CSS block:

```css
@media (prefers-reduced-motion: no-preference) {
  @keyframes htwFlashGlow {
    0%, 100% { filter: brightness(1) drop-shadow(0 0 0 rgba(245,158,11,0)); }
    50%      { filter: brightness(1.25) drop-shadow(0 0 6px var(--htw-flash-glow)); }
  }
  @keyframes htwTubeLoad   { from { stroke-dashoffset: 100; } to { stroke-dashoffset: 0; } }
  @keyframes htwTubeUnload { from { stroke-dashoffset: 0; }   to { stroke-dashoffset: 100; } }

  .htw-slot.htw-flashing .htw-slot-body,
  .htw-tool.htw-flashing .htw-tool-body {
    animation: htwFlashGlow 1.1s ease-in-out infinite;
  }
  .htw-tube-fill.htw-tube-loading   { animation: htwTubeLoad   2.2s linear forwards; }
  .htw-tube-fill.htw-tube-unloading { animation: htwTubeUnload 2.2s linear forwards; }
}
@media (prefers-reduced-motion: reduce) {
  .htw-slot.htw-flashing .htw-slot-body,
  .htw-tool.htw-flashing .htw-tool-body {
    outline: 3px solid var(--htw-flash-glow);
  }
  .htw-tube-fill.htw-tube-loading,
  .htw-tube-fill.htw-tube-unloading {
    transition: stroke-dashoffset 0.4s linear;
  }
}
```

- [ ] **Step 2: Add the workflow-diff lifecycle in the module**

Inside the IIFE, **above** the public-API return, add:

```javascript
  // ---- Animation lifecycle ----
  let _lastWorkflow = { active: false, steps: [], kind: null };

  function _resolveSourceForHead(state, head) {
    const src = state.head_source && state.head_source[head];
    if (src) {
      const aceI = src.ace != null ? src.ace : src.ace_index;
      return { device: aceI, slot: src.slot };
    }
    // Pre-load: default mapping is slot N of active ACE → head N
    if (state.active_device != null) {
      return { device: state.active_device, slot: head };
    }
    return null;
  }

  function _startTubeAnim(deviceIdx, slotIdx, kind) {
    const tube = document.getElementById(`htw-tube-${deviceIdx}-${slotIdx}`);
    if (!tube) return;
    tube.classList.remove("htw-tube-loading", "htw-tube-unloading");
    // Trigger reflow so the animation restarts even if class was just removed
    void tube.offsetWidth;
    tube.classList.add(kind === "load" ? "htw-tube-loading" : "htw-tube-unloading");
  }

  function _stopTubeAnim(deviceIdx, slotIdx) {
    const tube = document.getElementById(`htw-tube-${deviceIdx}-${slotIdx}`);
    if (!tube) return;
    tube.classList.remove("htw-tube-loading", "htw-tube-unloading");
  }

  function _setFlash(deviceIdx, slotIdx, head, on) {
    if (deviceIdx != null && slotIdx != null) {
      const slot = document.getElementById(`htw-ace-${deviceIdx}-slot-${slotIdx}`);
      if (slot) slot.classList.toggle("htw-flashing", on);
    }
    if (head != null) {
      const tool = document.getElementById(`htw-tool-${head}`);
      if (tool) tool.classList.toggle("htw-flashing", on);
    }
  }

  function _processWorkflowDiff(state, workflow) {
    const prevSteps = (_lastWorkflow.steps || []);
    const prevByHead = Object.fromEntries(prevSteps.map(s => [s.head, s.status]));
    const cur = workflow && workflow.active ? workflow.steps : [];
    const direction = workflow && workflow.kind && workflow.kind.startsWith("unload")
      ? "unload" : "load";

    cur.forEach(step => {
      const wasRunning = prevByHead[step.head] === "running";
      const isRunning = step.status === "running";
      const src = _resolveSourceForHead(state, step.head);

      if (!wasRunning && isRunning) {
        // Step just started
        if (src) {
          _setFlash(src.device, src.slot, step.head, true);
          _startTubeAnim(src.device, src.slot, direction);
        } else {
          _setFlash(null, null, step.head, true);
        }
      } else if (wasRunning && (step.status === "done" || step.status === "failed")) {
        // Step just finished — drop flash; tube state will be set by
        // _renderSlotsAndTubes in the next render anyway, so just stop the
        // animation class.
        if (src) {
          _setFlash(src.device, src.slot, step.head, false);
          _stopTubeAnim(src.device, src.slot);
        } else {
          _setFlash(null, null, step.head, false);
        }
      }
    });

    // If workflow just dropped active=false, clear all flashes/anims
    if (_lastWorkflow.active && (!workflow || !workflow.active)) {
      document.querySelectorAll(".htw-flashing").forEach(el =>
        el.classList.remove("htw-flashing"));
      document.querySelectorAll(".htw-tube-loading, .htw-tube-unloading").forEach(el =>
        el.classList.remove("htw-tube-loading", "htw-tube-unloading"));
    }

    // Snapshot for next diff
    _lastWorkflow = workflow ? {
      active: workflow.active,
      kind: workflow.kind,
      steps: workflow.steps.map(s => ({ head: s.head, status: s.status })),
    } : { active: false, steps: [], kind: null };
  }

  function render(state, printState, workflow) {
    if (!document.getElementById("htw-root")) return;
    const empty = document.getElementById("htw-empty");
    const dc = state.device_count || 0;
    if (empty) empty.classList.toggle("hidden", dc > 0);
    _resizeAceStack(dc);
    _renderToolheads(state, printState);
    _renderSlotsAndTubes(state);
    _renderSlotButtons(state);
    _renderBanner(state, workflow);
    _processWorkflowDiff(state, workflow);
  }
```

- [ ] **Step 3: Validate against a real load/unload**

This needs a real or mocked workflow event. Easiest path: against the live printer (idle, no active print), click an Unload on a loaded toolhead from the Hardware tab. Watch:

1. Source ACE slot rectangle pulses amber.
2. Destination toolhead rectangle pulses amber.
3. Source slot's tube fill animates from full → empty (dashoffset 0 → 100), receding toward the slot.
4. When UNLOAD_HEAD event fires: pulses stop; tube goes to empty state; toolhead becomes dashed.

Repeat with Load from the same Hardware tab: tube fills from slot toward coupler/U1 (offset 100 → 0); pulses stop on LOAD_HEAD.

If `prefers-reduced-motion` is set in OS settings: pulses become static outlines and the tube uses a 0.4s transition instead of a 2.2s animation.

- [ ] **Step 4: Commit**

```bash
git add multiace_web/src/multiace_web/static/hardware-twin.js \
        multiace_web/src/multiace_web/static/style.css
git commit -m "feat(web): Hardware tab load/unload animation lifecycle"
```

---

## Task 10: Edge cases — disconnected, no-color, accessibility refinement

The pieces that don't fit cleanly into the per-section tasks above.

**Files:**
- Modify: `multiace_web/src/multiace_web/static/hardware-twin.js`
- Modify: `multiace_web/src/multiace_web/static/style.css`

- [ ] **Step 1: Apply `htw-disconnected` to the root**

In `render`, after the empty-state toggle:

```javascript
    document.getElementById("htw-root").classList.toggle(
      "htw-disconnected", state.connected === false);
```

Disable all buttons inside `#htw-root` when disconnected:

```javascript
    if (state.connected === false) {
      document.querySelectorAll("#htw-root button").forEach(b => b.disabled = true);
    }
```

(Place this after `_renderSlotButtons` call, since that function sets disabled state too.)

- [ ] **Step 2: Add `aria-label`s on the buttons**

In `_buildAceBlock`, when creating slot Load/Unload buttons, add an `aria-label`. Update the slot button row creation:

```javascript
      const loadBtn = htmlEl("button", {
        className: "primary htw-hidden", textContent: "Load",
        "aria-label": `Load slot ${i + 1}`,
        dataset: { cmd: `ACEC__Load_T${i}`, htw: "slot-load",
                   device: String(deviceIdx), slot: String(i) },
      });
      const unloadBtn = htmlEl("button", {
        className: "danger htw-hidden", textContent: "Unload",
        "aria-label": `Unload slot ${i + 1}`,
        dataset: { htw: "slot-unload", device: String(deviceIdx), slot: String(i) },
      });
```

In `mount`, the toolhead buttons get the same treatment:

```javascript
      const loadBtn = htmlEl("button", {
        className: "primary", textContent: `Load T${i + 1}`,
        "aria-label": `Load toolhead T${i + 1}`,
        dataset: { cmd: `ACEC__Load_T${i}`, htw: "tool-load", tool: String(i) },
      });
      const unloadBtn = htmlEl("button", {
        className: "danger htw-hidden", textContent: `Unload T${i + 1}`,
        "aria-label": `Unload toolhead T${i + 1}`,
        dataset: {
          cmd: `ACEC__Unload_T${i}`, confirm: `Unload T${i + 1}?`,
          htw: "tool-unload", tool: String(i),
        },
      });
```

- [ ] **Step 3: Validate**

Reload. Switch to **Hardware**.

With the printer/fixture connected: everything renders normally.
Force `connected: false` (e.g. modify the synthetic log fixture to have a `connected: false` state event, or unplug the ACE USB cable on the live printer): U1 + ACE blocks fade to 50% opacity, all buttons are disabled, banner reads "ACE disconnected".

Tab through the Hardware tab with the keyboard: focus moves through buttons; each announces its `aria-label` via screen reader.

- [ ] **Step 4: Commit**

```bash
git add multiace_web/src/multiace_web/static/hardware-twin.js \
        multiace_web/src/multiace_web/static/style.css
git commit -m "feat(web): Hardware tab edge cases — disconnected mode, aria-labels"
```

---

## Task 11: Visual regression, version bump, README, e2e validation

The plumbing that makes the change visible to ops and CI-ish processes.

**Files:**
- Modify: `multiace_web/tools/visual_regression.py`
- Modify: `multiace_web/src/multiace_web/__init__.py`
- Modify: `multiace_web/README.md`

- [ ] **Step 1: Add `"hardware"` to the visual regression tab list**

Edit `multiace_web/tools/visual_regression.py`:

```python
# Was:
READ_ONLY_TABS = ["dashboard", "activity", "dryer", "config", "diag"]
# To:
READ_ONLY_TABS = ["dashboard", "activity", "dryer", "config", "diag", "hardware"]
```

Update the docstring at line 3 from:

```
Captures Dashboard / Activity / Dryer / Config / Diag at desktop (1280x900)
```

to:

```
Captures Dashboard / Activity / Dryer / Config / Diag / Hardware at desktop (1280x900)
```

- [ ] **Step 2: Run the visual regression sweep**

```bash
cd multiace_web
. .venv/bin/activate
pip install playwright && playwright install chromium  # if not installed
python tools/visual_regression.py http://<printer-ip>/multiace/
```

Expected: 12 screenshots written to the script's output directory (6 tabs × 2 viewports). Confirm:
- Dashboard screenshots are visually unchanged from previous runs (compare to a pre-feature snapshot if you have one).
- New `*-hardware.png` screenshots show the Hardware tab content.

- [ ] **Step 3: Bump the package version**

Edit `multiace_web/src/multiace_web/__init__.py`. Find the `__version__` line and change to:

```python
__version__ = "0.6.0"
```

- [ ] **Step 4: Update the README**

In `multiace_web/README.md`, append a paragraph to the Features section describing the Hardware tab. Suggested text:

```
- **Hardware tab.** A schematic SVG twin of the ACE Pro stack and the Snapmaker U1.
  Each ACE slot has a bowden tube; tubes meet at per-toolhead couplers; one tube
  continues from each coupler to the U1. Source slots and destination toolheads
  pulse during load/unload, and the source tube animates as filament moves.
  Per-block Load/Unload buttons mirror the existing Dashboard buttons. The
  existing Dashboard tab is unchanged.
```

If the README has a CHANGELOG section, append:

```
## 0.6.0 — 2026-05-02

- New Hardware tab with animated SVG twin of the ACE-U1 system.
```

- [ ] **Step 5: Manual end-to-end validation against a real printer**

Per the project's e2e rule, do a Playwright-driven walkthrough against a real printer that's idle (no print in progress):

```bash
# Start the dev server pointed at the printer's Moonraker
MOONRAKER_URL=http://<printer-ip>:7125 \
  MULTIACE_LOG_DIR=/path/to/local/log/copy \
  uvicorn multiace_web.server:app --port 7126 --reload
```

Open `http://localhost:7126/` and:
1. Switch to **Hardware**. Confirm one ACE block per device renders.
2. Click **Load → T1** under a filled slot. Confirm the slot pulses, the tube animates ACE→U1, the toolhead pulses, and the toolhead becomes solid green (or whichever color) when LOAD_HEAD fires.
3. Click **Unload T1**. Confirm the tube animates U1→ACE and the toolhead returns to dashed-empty.
4. Switch back to **Dashboard**. Confirm slots and toolhead cards reflect the same state changes.
5. Reload the page. Confirm Hardware tab returns to the same state.

- [ ] **Step 6: Commit**

```bash
git add multiace_web/tools/visual_regression.py \
        multiace_web/src/multiace_web/__init__.py \
        multiace_web/README.md
git commit -m "release(web): bump to 0.6.0 with Hardware tab"
```

- [ ] **Step 7: Push the branch**

```bash
git push ryvin feat/multiace-web-console
```

(Per the project's "work only in ryvin fork" preference.)

---

## Self-review notes

- **Spec coverage:** Section 1 goal & 2 scope → Tasks 1-11; Section 3 decisions → reflected in CSS class names & layout in Tasks 3-9; Section 4 topology → Tasks 3, 5, 6 (couplers + slot tubes + post-coupler); Section 5 architecture → Tasks 1, 2, 3 (file edits as specified); Section 6 state mapping → Tasks 5, 6, 7, 8; Section 7 render lifecycle → Task 4 (resize) + Task 9 (animation diff); Section 8 edge cases → Task 10 + parts of Task 5/Task 8; Section 9 testing → Task 11; Section 10 versioning → Task 11; Section 11 known limitations → documented in spec, no implementation needed; Section 12 resolved details → reflected in CSS media queries + non-rendering of source sublabel below 480px.
- **No placeholders:** every code block contains complete code; no "// implement later" beyond the staged-in-future stubs that get filled in subsequent tasks (Task 1's empty mount/render are filled in Task 3 / 5-9 explicitly).
- **Type/name consistency:** `htw-tube-{device}-{slot}` ID pattern used throughout; `htw-ace-{device}-slot-{i}` used throughout; `htw-flashing`, `htw-tube-loading`, `htw-tube-unloading` class names used consistently in both CSS and JS.
