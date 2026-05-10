// multiACE Web Console — Hardware tab
// Vanilla JS, no framework, no build step.
//
// Public API on window.HardwareTwin:
//   mount(rootEl)                          — once, builds static skeleton
//   render(state, printState, workflow)    — every state push; mutates only

window.HardwareTwin = (function () {
  // ---- Shared helpers ----
  const _tName = (i) => window.MultiACEUtil.tName(i);

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

    // Coordinate system used by all SVGs:
    //   U1 SVG: viewBox 360×240. Toolhead positions: T1(28,38), T2(186,38),
    //   T3(28,124), T4(186,124). Input strip at y=14 width=240.
    //   Coupler SVG (this row): viewBox 360×80. 4 couplers at x=110+i*60
    //   (i.e. 110, 170, 230, 290). Coupler bars y=12 height=16; post-coupler
    //   tubes from y=28 to y=80 (where they meet the U1 input strip).
    //   ACE SVG (per device): viewBox 280×160. Slots at x=12+i*66 width=58
    //   height=120 (y=22..142). ACE rendered at 70% width centered, so its
    //   slot centers visually align with the coupler x positions above.
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
        class: "htw-postcoupler",
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
      label.textContent = _tName(i);
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
        className: "primary", textContent: `Load ${_tName(i)}`,
        "aria-label": `Load toolhead ${_tName(i)}`,
        dataset: { cmd: `ACEC__Load_T${i}`, htw: "tool-load", tool: String(i) },
      });
      const unloadBtn = htmlEl("button", {
        className: "danger htw-hidden", textContent: `Unload ${_tName(i)}`,
        "aria-label": `Unload toolhead ${_tName(i)}`,
        dataset: {
          cmd: `ACEC__Unload_T${i}`, confirm: `Unload ${_tName(i)}?`,
          htw: "tool-unload", tool: String(i),
        },
      });
      cell.appendChild(loadBtn);
      cell.appendChild(unloadBtn);
      toolBtns.appendChild(cell);
    }
    rootEl.appendChild(toolBtns);
  }

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
        "data-device": String(deviceIdx),
        "data-slot": String(i),
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
      label.textContent = String(i);
      g.appendChild(label);
      svg.appendChild(g);
    }
    wrap.appendChild(svg);

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

    // Slot button row
    const slotBtns = htmlEl("div", {
      className: "htw-actions htw-slot-actions",
      id: `htw-ace-${deviceIdx}-actions`,
    });
    for (let i = 0; i < 4; i++) {
      const cell = htmlEl("div", { className: "htw-cell" });

      // 📖 FilamentHub picker — always visible, disabled when
      // FILAMENTHUB_URL is unset. Click opens the FilamentHub spool picker
      // in a new tab with ?picker=ace&printer=&ace=&slot= so the user can
      // pick or scan an NFC tag for what's in this physical slot.
      const pickerBtn = htmlEl("button", {
        className: "btn-icon", textContent: "📖",
        "aria-label": `Pick spool for ACE ${String.fromCharCode(65 + deviceIdx)} slot ${i}`,
        dataset: { htw: "slot-picker",
                   device: String(deviceIdx), slot: String(i) },
      });
      const fhBase = window.MULTIACE_FH_URL || "";
      if (fhBase) {
        pickerBtn.title = "Pick spool from FilamentHub";
        pickerBtn.addEventListener("click", () => {
          const pid = encodeURIComponent(window.MULTIACE_FH_PRINTER_ID || "u1-1");
          const url = `${fhBase.replace(/\/$/, "")}/?picker=ace&printer=${pid}&ace=${deviceIdx}&slot=${i}`;
          window.open(url, "_blank", "noopener,noreferrer");
        });
      } else {
        pickerBtn.disabled = true;
        pickerBtn.title = "Set FILAMENTHUB_URL to enable";
      }

      const loadBtn = htmlEl("button", {
        className: "primary htw-hidden", textContent: "Load",
        "aria-label": `Load slot ${i}`,
        dataset: { cmd: `ACEC__Load_T${i}`, htw: "slot-load",
                   device: String(deviceIdx), slot: String(i) },
      });
      const unloadBtn = htmlEl("button", {
        className: "danger htw-hidden", textContent: "Unload",
        "aria-label": `Unload slot ${i}`,
        dataset: { htw: "slot-unload", device: String(deviceIdx), slot: String(i) },
      });
      cell.appendChild(pickerBtn);
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

      // Body fill / stroke. Track effective body color so labels can pick a
      // contrasting fill (handles white/light filament where #fff is unreadable).
      let bodyBg = null;
      if (sensor && c) {
        body.setAttribute("fill", c);
        body.setAttribute("stroke", "#1f2937");
        body.removeAttribute("stroke-dasharray");
        bodyBg = c;
      } else if (sensor) {
        // Loaded but no color (RFID didn't read) — neutral grey
        body.setAttribute("fill", "#cbd5e1");
        body.setAttribute("stroke", "#1f2937");
        body.removeAttribute("stroke-dasharray");
        bodyBg = "rgb(203,213,225)";
      } else {
        body.setAttribute("fill", "transparent");
        body.setAttribute("stroke", "var(--htw-stroke-empty)");
        body.setAttribute("stroke-dasharray", "4 3");
        // Empty body is transparent — chassis #fff shows through.
        bodyBg = "rgb(255,255,255)";
      }
      label.setAttribute("fill",
        sensor
          ? (window.MultiACEUtil.textOnColor(bodyBg) || "#fff")
          : "var(--htw-stroke-empty)");

      // Error tint
      g.classList.toggle("htw-error", !!err);
      // SVG tooltips require a <title> CHILD element, not a `title` attribute.
      let titleEl = g.querySelector(":scope > title");
      if (err) {
        if (!titleEl) {
          titleEl = document.createElementNS(SVG_NS, "title");
          g.appendChild(titleEl);
        }
        titleEl.textContent = state.last_error.error || "error";
      } else if (titleEl) {
        titleEl.remove();
      }

      // Extruding emphasis (mild glow, not flash)
      g.classList.toggle("htw-extruding", !!extruding);

      // Source sublabel — sits inside the toolhead body, so contrast it
      // against whatever bodyBg ended up being (white chassis or filament color).
      if (src) {
        const aceLetter = String.fromCharCode(65 + (src.ace ?? 0));
        const slotN = (src.slot != null ? src.slot : 0);
        source.textContent = `ACE ${aceLetter} · Slot ${slotN}`;
        source.setAttribute("fill-opacity", "1");
        source.setAttribute("fill",
          window.MultiACEUtil.textOnColor(bodyBg) || "#0f172a");
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
    }
  }

  function _slotIsActiveSource(state, deviceIdx, slotIdx) {
    if (!state.head_source) return null;
    for (const [headStr, src] of Object.entries(state.head_source)) {
      if (!src) continue;
      const aceI = src.ace ?? 0;
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
        if (active || parked) {
          const slotBg = color || "rgb(203,213,225)";
          slotBody.setAttribute("fill", slotBg);
          slotBody.setAttribute("stroke", "#1f2937");
          slotBody.removeAttribute("stroke-dasharray");
          slotLabel.setAttribute("fill",
            window.MultiACEUtil.textOnColor(slotBg) || "#fff");
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
          unloadBtn.dataset.confirm = `Unload ${_tName(sourcedHead)}?`;
          unloadBtn.textContent = `Unload ${_tName(sourcedHead)}`;
          unloadBtn.disabled = swap;
          loadBtn.disabled = true;
        } else if (filled) {
          // Filled but not currently sourcing — offer Load → T(slot index)
          loadBtn.classList.remove("htw-hidden");
          unloadBtn.classList.add("htw-hidden");
          loadBtn.textContent = `Load → ${_tName(i)}`;
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

  function _renderBanner(state, workflow) {
    const banner = document.getElementById("htw-banner");
    if (!banner) return;
    let text = null;
    let kind = "info";
    if (workflow && workflow.active && workflow.label) {
      const running = (workflow.steps || []).find(s => s.status === "running");
      const detail = running ? ` — ${_tName(running.head)}` : "";
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

  // ---- Animation lifecycle ----
  let _lastWorkflow = { active: false, steps: [], kind: null };

  function _resolveSourceForHead(state, head) {
    const src = state.head_source && state.head_source[head];
    if (src) {
      const aceI = src.ace ?? 0;
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

  function _renderRoot(state) {
    const root = document.getElementById("htw-root");
    if (!root) return;
    const disconnected = state.connected === false;
    root.classList.toggle("htw-disconnected", disconnected);
    if (disconnected) {
      root.querySelectorAll("button").forEach(b => { b.disabled = true; });
    }
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
    _renderRoot(state);
    _renderBanner(state, workflow);
    _processWorkflowDiff(state, workflow);
  }

  return { mount, render };
})();
