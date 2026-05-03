// multiACE Web Console — Hardware tab
// Vanilla JS, no framework, no build step.
//
// Public API on window.HardwareTwin:
//   mount(rootEl)                          — once, builds static skeleton
//   render(state, printState, workflow)    — every state push; mutates only

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
