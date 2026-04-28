// multiACE Web Console - frontend
// Vanilla JS, no framework, no build step.

const state = {
  active_device: null,
  device_count: 0,
  connected: false,
  swap_in_progress: false,
  auto_feed: false,
  feed_assist: -1,
  mode: "multi",
  gate_status: [0, 0, 0, 0],
  head_source: { 0: null, 1: null, 2: null, 3: null },
  sensors: { 0: false, 1: false, 2: false, 3: false },
  print_task_config: {},
  last_error: null,
};
const events = []; // last 200 activity entries
const ws = { sock: null, retry: 0, alive: false };

const TOKEN = localStorage.getItem("multiace_token") || null;
const authHeader = () => (TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {});

// All API paths are resolved relative to the document. When mounted at
// /multiace/ behind nginx, "api/state" → /multiace/api/state, which nginx
// proxies to 127.0.0.1:7126/api/state. When mounted at /, it just works.
const api = (path) => new URL(path, document.baseURI).toString();

function setConnState(label, dotState) {
  document.getElementById("conn-label").textContent = label;
  document.getElementById("conn-dot").dataset.state = dotState;
}

function toast(msg, kind = "info") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = msg;
  document.getElementById("toast-container").appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

async function fetchState() {
  try {
    const resp = await fetch(api("api/state"), { headers: authHeader() });
    if (!resp.ok) throw new Error(`status ${resp.status}`);
    const body = await resp.json();
    Object.assign(state, body);
    renderAll();
  } catch (e) {
    console.error("fetchState", e);
    toast(`Failed to fetch state: ${e.message}`, "error");
  }
}

async function fetchEvents() {
  try {
    const resp = await fetch(api("api/events?limit=200"), { headers: authHeader() });
    if (!resp.ok) throw new Error(`status ${resp.status}`);
    const body = await resp.json();
    events.length = 0;
    events.push(...body.events);
    renderActivity();
    renderActivityPreview();
  } catch (e) {
    console.error("fetchEvents", e);
  }
}

function connectWS() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const tokenQuery = TOKEN ? `?token=${encodeURIComponent(TOKEN)}` : "";
  const url = `${proto}//${location.host}${location.pathname.replace(/\/$/, "")}/ws${tokenQuery}`;
  setConnState("Connecting…", "reconnecting");
  ws.sock = new WebSocket(url);
  let pingTimer = null;

  ws.sock.onopen = async () => {
    ws.alive = true;
    ws.retry = 0;
    setConnState("Connected", "connected");
    await fetchState();
    await fetchEvents();
    pingTimer = setInterval(() => {
      try { ws.sock.send("ping"); } catch (_) {}
    }, 30000);
  };

  ws.sock.onmessage = (ev) => {
    if (ev.data === "pong") return;
    let msg;
    try { msg = JSON.parse(ev.data); } catch (_) { return; }
    if (msg.type === "state") {
      Object.assign(state, msg.payload);
      renderAll();
    } else if (msg.type === "event") {
      events.unshift({ id: msg.id, ts: msg.ts, ...msg.payload });
      if (events.length > 200) events.length = 200;
      renderActivity();
      renderActivityPreview();
    }
  };

  ws.sock.onclose = () => {
    if (pingTimer) clearInterval(pingTimer);
    ws.alive = false;
    setConnState("Reconnecting…", "reconnecting");
    const delay = Math.min(30000, 1000 * 2 ** ws.retry);
    ws.retry += 1;
    setTimeout(connectWS, delay);
  };

  ws.sock.onerror = () => {
    setConnState("Disconnected", "disconnected");
  };
}

async function sendCommand(macro) {
  try {
    const resp = await fetch(api("api/command"), {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeader() },
      body: JSON.stringify({ macro }),
    });
    const body = await resp.json();
    if (!resp.ok) {
      toast(`${macro} failed: ${body.detail || resp.statusText}`, "error");
      return false;
    }
    toast(`${macro} sent`, "success");
    return true;
  } catch (e) {
    toast(`${macro} failed: ${e.message}`, "error");
    return false;
  }
}

function confirmDialog(text) {
  return new Promise((resolve) => {
    document.getElementById("confirm-text").textContent = text;
    const modal = document.getElementById("confirm-modal");
    modal.classList.remove("hidden");
    const ok = () => { cleanup(); resolve(true); };
    const cancel = () => { cleanup(); resolve(false); };
    function cleanup() {
      modal.classList.add("hidden");
      document.getElementById("confirm-ok").removeEventListener("click", ok);
      document.getElementById("confirm-cancel").removeEventListener("click", cancel);
    }
    document.getElementById("confirm-ok").addEventListener("click", ok);
    document.getElementById("confirm-cancel").addEventListener("click", cancel);
  });
}

// ---- Print state (separate from multiACE state; pulled from Moonraker via /api/print) ----
const printState = {
  state: "standby",
  filename: null,
  progress: 0,
  print_duration: 0,
  total_duration: 0,
  eta_sec: null,
  layer: null,
  total_layer: null,
  current_extruder: null,
  exception: null,
  message: null,
  dryer: { status: "stop", target_temp: 0, duration_min: 0, remain_min: 0 },
  _last_fetch_ok: false,
};

async function fetchPrint() {
  try {
    const resp = await fetch(api("api/print"), { headers: authHeader() });
    if (!resp.ok) throw new Error(`status ${resp.status}`);
    const body = await resp.json();
    Object.assign(printState, body);
    printState._last_fetch_ok = true;
  } catch (e) {
    printState._last_fetch_ok = false;
  }
  renderPrintPanel();
  // Toolhead "Extruding" emphasis depends on printState.current_extruder
  // and printState.state, so refresh those cards too.
  renderToolheads();
  renderStatusBanner();
  renderDryerStatus();
}

function renderDryerStatus() {
  const card = document.getElementById("dryer-status-card");
  if (!card) return;
  const d = printState.dryer || {};
  const isActive = d.status && d.status !== "stop";
  card.classList.toggle("hidden", !isActive);
  card.innerHTML = "";
  if (!isActive) return;

  card.classList.add("card");
  setEl(card, "div", { className: "color-band" });
  card.style.setProperty("--card-color", "var(--warn)");

  const head = setEl(card, "div"); head.className = "card-head";
  setEl(head, "span", { className: "card-id", textContent: "ACE Dryer" });
  pill(head, "DRYING", "warn");

  // Stats row
  const main = setEl(card, "div"); main.className = "print-main";
  const stats = setEl(main, "div"); stats.className = "print-stats";
  const tStat = setEl(stats, "div"); tStat.className = "stat";
  setEl(tStat, "span", { className: "stat-label", textContent: "Target" });
  setEl(tStat, "span", { className: "stat-val", textContent: `${d.target_temp || 0}°C` });
  const dStat = setEl(stats, "div"); dStat.className = "stat";
  setEl(dStat, "span", { className: "stat-label", textContent: "Duration" });
  setEl(dStat, "span", { className: "stat-val", textContent: formatMinutes(d.duration_min || 0) });
  const rStat = setEl(stats, "div"); rStat.className = "stat";
  setEl(rStat, "span", { className: "stat-label", textContent: "Remaining" });
  setEl(rStat, "span", { className: "stat-val", textContent: formatMinutes(d.remain_min || 0) });
  const eStat = setEl(stats, "div"); eStat.className = "stat";
  setEl(eStat, "span", { className: "stat-label", textContent: "Done at" });
  const doneAt = new Date(Date.now() + (d.remain_min || 0) * 60 * 1000);
  const hh = doneAt.getHours().toString().padStart(2, "0");
  const mm = doneAt.getMinutes().toString().padStart(2, "0");
  setEl(eStat, "span", { className: "stat-val", textContent: `${hh}:${mm}` });

  // Progress (elapsed = duration - remaining)
  const total = d.duration_min || 0;
  const elapsed = Math.max(0, total - (d.remain_min || 0));
  const pct = total > 0 ? Math.max(0, Math.min(100, (elapsed / total) * 100)) : 0;
  const pwrap = setEl(main, "div"); pwrap.className = "progress";
  const pfill = setEl(pwrap, "div"); pfill.className = "progress-fill progress-warn";
  pfill.style.width = pct.toFixed(1) + "%";
  setEl(pwrap, "span", { className: "progress-label", textContent: pct.toFixed(0) + "%" });

  const actions = setEl(card, "div"); actions.className = "actions";
  const stop = setEl(actions, "button", { textContent: "Stop drying" });
  stop.dataset.cmd = "ACED__Dry_Stop";
  stop.dataset.confirm = "Stop dryer?";
  stop.classList.add("danger");
}

let _printPollTimer = null;
function startPrintPolling() {
  if (_printPollTimer) return;
  fetchPrint();
  _printPollTimer = setInterval(fetchPrint, 4000);
}

// Pause/Resume/Cancel hit Moonraker directly (same origin via fluidd nginx).
async function moonrakerPrintAction(verb) {
  // verb in {"pause","resume","cancel"}
  try {
    const resp = await fetch(`/printer/print/${verb}`, { method: "POST" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    toast(`Print ${verb}d`, "success");
    fetchPrint();
  } catch (e) {
    toast(`Print ${verb} failed: ${e.message}`, "error");
  }
}

function fmtDuration(sec) {
  if (sec == null || !Number.isFinite(sec)) return "—";
  sec = Math.max(0, Math.round(sec));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function fmtETA(eta_sec) {
  if (eta_sec == null) return "—";
  const date = new Date(Date.now() + eta_sec * 1000);
  const hh = date.getHours().toString().padStart(2, "0");
  const mm = date.getMinutes().toString().padStart(2, "0");
  return `${hh}:${mm}`;
}

function basenameWithoutHash(filename) {
  if (!filename) return "";
  const last = filename.split("/").pop();
  // Slicer-uploaded files often have a hash prefix like "1774430959773-af7b68e2_plate_1.gcode"
  const m = last.match(/^\d{8,}-[a-f0-9]+[_-](.+)$/);
  return m ? m[1] : last;
}

const PRINT_STATE_KIND = {
  printing: "ok",
  paused:   "warn",
  complete: "ok",
  cancelled:"",
  standby:  "",
  error:    "bad",
};

function renderPrintPanel() {
  const panel = document.getElementById("print-panel");
  if (!panel) return;
  panel.innerHTML = "";
  panel.classList.add("card");
  panel.classList.add("no-color");
  panel.style.removeProperty("--card-color");
  // Top color band: tinted by current extruder filament if printing
  const head = printState.current_extruder;
  let band;
  if (head != null) {
    const cfg = (state.print_task_config && state.print_task_config[head]) || {};
    const color = rgbFromUint(cfg.color);
    if (color) {
      band = setEl(panel, "div", { className: "color-band" });
      panel.style.setProperty("--card-color", color);
      panel.classList.remove("no-color");
    }
  }
  if (!band) setEl(panel, "div", { className: "color-band" });

  const headRow = setEl(panel, "div"); headRow.className = "card-head";
  setEl(headRow, "span", { className: "card-id", textContent: "Print" });
  const stateName = printState.state || "standby";
  pill(headRow, stateName.toUpperCase(), PRINT_STATE_KIND[stateName] || "");
  if (head != null) {
    const tagP = pill(headRow, `Extruding T${head}`, "ok");
    tagP.classList.add("now-extruding");
  }

  const isActive = stateName === "printing" || stateName === "paused";

  // Main content
  const main = setEl(panel, "div"); main.className = "print-main";
  const filename = setEl(main, "div"); filename.className = "print-filename";
  filename.textContent = isActive
    ? basenameWithoutHash(printState.filename) || "(unnamed)"
    : (stateName === "complete" ? "Print complete"
       : stateName === "cancelled" ? "Print cancelled"
       : stateName === "error" ? "Print errored"
       : "No active print");

  if (isActive) {
    // Progress bar
    const pwrap = setEl(main, "div"); pwrap.className = "progress";
    const pfill = setEl(pwrap, "div"); pfill.className = "progress-fill";
    const pct = Math.max(0, Math.min(100, (printState.progress || 0) * 100));
    pfill.style.width = pct.toFixed(1) + "%";
    setEl(pwrap, "span", { className: "progress-label", textContent: pct.toFixed(1) + "%" });

    // Stat row
    const stats = setEl(main, "div"); stats.className = "print-stats";
    const layerStat = setEl(stats, "div"); layerStat.className = "stat";
    setEl(layerStat, "span", { className: "stat-label", textContent: "Layer" });
    setEl(layerStat, "span", { className: "stat-val",
      textContent: printState.layer != null && printState.total_layer != null
        ? `${printState.layer} / ${printState.total_layer}`
        : "—" });

    const elapStat = setEl(stats, "div"); elapStat.className = "stat";
    setEl(elapStat, "span", { className: "stat-label", textContent: "Elapsed" });
    setEl(elapStat, "span", { className: "stat-val", textContent: fmtDuration(printState.print_duration) });

    const remStat = setEl(stats, "div"); remStat.className = "stat";
    setEl(remStat, "span", { className: "stat-label", textContent: "Remaining" });
    setEl(remStat, "span", { className: "stat-val", textContent: fmtDuration(printState.eta_sec) });

    const etaStat = setEl(stats, "div"); etaStat.className = "stat";
    setEl(etaStat, "span", { className: "stat-label", textContent: "ETA" });
    setEl(etaStat, "span", { className: "stat-val", textContent: fmtETA(printState.eta_sec) });
  }

  // Actions
  const actions = setEl(panel, "div"); actions.className = "actions";
  if (stateName === "printing") {
    const pauseBtn = setEl(actions, "button", { textContent: "Pause" });
    pauseBtn.classList.add("primary");
    pauseBtn.addEventListener("click", () => moonrakerPrintAction("pause"));
  }
  if (stateName === "paused") {
    const resumeBtn = setEl(actions, "button", { textContent: "Resume" });
    resumeBtn.classList.add("primary");
    resumeBtn.addEventListener("click", async () => {
      if (await confirmDialog("Resume print?")) moonrakerPrintAction("resume");
    });
  }
  if (isActive) {
    const cancelBtn = setEl(actions, "button", { textContent: "Cancel" });
    cancelBtn.classList.add("danger");
    cancelBtn.addEventListener("click", async () => {
      if (await confirmDialog("Cancel print? Progress will be lost.")) {
        moonrakerPrintAction("cancel");
      }
    });
  }
  if (!printState._last_fetch_ok) {
    const note = setEl(panel, "div", { className: "muted small" });
    note.textContent = "(print state unavailable)";
  }
}

function renderStatusBanner() {
  const banner = document.getElementById("status-banner");
  if (!banner) return;
  banner.innerHTML = "";
  banner.classList.add("hidden");
  banner.classList.remove("bad", "warn");

  // Priority order: Klipper exception > swap_in_progress > last_error
  const exc = printState.exception;
  if (exc && (exc.message || exc.code)) {
    banner.classList.remove("hidden"); banner.classList.add("bad");
    setEl(banner, "strong", { textContent: "Print paused" });
    const msg = setEl(banner, "span");
    msg.textContent = " " + (exc.message || `code ${exc.code}`);
    return;
  }
  if (state.swap_in_progress) {
    banner.classList.remove("hidden"); banner.classList.add("warn");
    setEl(banner, "strong", { textContent: "Tool change in progress…" });
    setEl(banner, "span", { textContent: " Hold actions until this completes." });
    return;
  }
  if (state.last_error) {
    const err = state.last_error;
    banner.classList.remove("hidden"); banner.classList.add("bad");
    setEl(banner, "strong", { textContent: `T${err.head} ${err.action}` });
    const msg = setEl(banner, "span");
    msg.textContent = " " + (err.error || err.reason || "see Activity for details");
  }
}

// Render functions are declared in subsequent tasks; placeholder for now
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
}
function renderTopbar() {
  const label = document.getElementById("active-ace-label");
  label.innerHTML = "";
  if (state.device_count <= 1) {
    const pill = document.createElement("span");
    pill.className = "ace-pill";
    const k = document.createElement("span"); k.className = "muted"; k.textContent = "Active:";
    const v = document.createElement("strong");
    v.textContent = state.active_device !== null ? `ACE ${state.active_device}` : "—";
    pill.append(k, v);
    label.appendChild(pill);
  } else {
    for (let i = 0; i < state.device_count; i++) {
      const b = document.createElement("button");
      b.textContent = `ACE ${i}`;
      b.dataset.cmd = `ACEA__Switch_${i}`;
      if (i === state.active_device) b.classList.add("primary");
      label.appendChild(b);
    }
  }
  const slotsBadge = document.getElementById("slots-active-ace");
  slotsBadge.textContent = state.active_device !== null ? `ACE ${state.active_device}` : "no ACE";
}

// ACE color is 0xAARRGGBB (or 0xFFRRGGBB on real data); low 24 bits = RGB.
// 4294967295 (0xFFFFFFFF) is multiACE's "no color" sentinel.
function rgbFromUint(packed) {
  if (packed == null || packed === 4294967295) return null;
  const r = (packed >>> 16) & 0xff;
  const g = (packed >>> 8) & 0xff;
  const b = packed & 0xff;
  return `rgb(${r},${g},${b})`;
}

function setEl(parent, tag, props) {
  const el = document.createElement(tag);
  if (props) Object.assign(el, props);
  parent.appendChild(el);
  return el;
}

function metaRow(parent, key, val, valClass) {
  const row = setEl(parent, "div"); row.className = "meta-row";
  const k = setEl(row, "span", { className: "meta-key", textContent: key });
  const v = setEl(row, "span", { className: "meta-val" + (valClass ? " " + valClass : ""), textContent: val });
  return { row, k, v };
}

function pill(parent, text, kind) {
  const el = setEl(parent, "span", { className: "pill" + (kind ? " " + kind : ""), textContent: text });
  return el;
}

function renderSlots() {
  const grid = document.getElementById("slots-grid");
  grid.innerHTML = "";
  const ace = state.active_device;
  for (let i = 0; i < 4; i++) {
    const filled = state.gate_status[i] === 1;
    const loadedToEntry = Object.entries(state.head_source).find(
      ([, src]) => src && src.ace === ace && src.slot === i
    );
    const loadedToHead = loadedToEntry ? loadedToEntry[0] : null;
    // If this slot is feeding head H, derive the filament color from H's task cfg
    const hcfg = loadedToHead != null ? (state.print_task_config[loadedToHead] || {}) : {};
    const color = rgbFromUint(hcfg.color);

    const card = setEl(grid, "div");
    card.className = "card" + (color ? "" : " no-color");
    if (color) card.style.setProperty("--card-color", color);
    setEl(card, "div", { className: "color-band" });

    const head = setEl(card, "div"); head.className = "card-head";
    setEl(head, "span", { className: "card-id", textContent: `Slot ${i}` });
    setEl(head, "span", { className: "card-swatch" });
    if (filled) pill(head, "Filled", "ok"); else pill(head, "Empty");

    const meta = setEl(card, "div"); meta.className = "card-meta";
    metaRow(meta, "Feeding", loadedToHead != null ? `T${loadedToHead}` : "—");
    metaRow(meta, "Material", hcfg.type || (loadedToHead != null ? "—" : "—"));
    metaRow(meta, "Vendor", hcfg.vendor || "—");

    const actions = setEl(card, "div"); actions.className = "actions";
    const loadBtn = setEl(actions, "button", { textContent: `Load → T${i}` });
    loadBtn.dataset.cmd = `ACEC__Load_T${i}`;
    loadBtn.classList.add("primary");
    loadBtn.disabled = !filled || state.swap_in_progress;
    const unloadBtn = setEl(actions, "button", { textContent: `Unload T${i}` });
    unloadBtn.dataset.cmd = `ACEC__Unload_T${i}`;
    unloadBtn.dataset.confirm = `Unload T${i}?`;
    unloadBtn.classList.add("danger");
    unloadBtn.disabled = !loadedToEntry || state.swap_in_progress;
  }
}

function renderToolheads() {
  const grid = document.getElementById("toolheads-grid");
  grid.innerHTML = "";
  const activeHead = (printState.state === "printing") ? printState.current_extruder : null;
  for (let i = 0; i < 4; i++) {
    const src = state.head_source[i];
    const sensor = state.sensors[i];
    const err = state.last_error && state.last_error.head === i ? state.last_error : null;
    const cfg = state.print_task_config[i] || {};
    const color = rgbFromUint(cfg.color);

    const card = setEl(grid, "div");
    card.className = "card" + (color ? "" : " no-color") + (err ? " error" : "")
      + (activeHead === i ? " extruding" : "");
    if (color) card.style.setProperty("--card-color", color);
    setEl(card, "div", { className: "color-band" });

    // Head: id + swatch + status pill
    const head = setEl(card, "div"); head.className = "card-head";
    setEl(head, "span", { className: "card-id", textContent: `T${i}` });
    setEl(head, "span", { className: "card-swatch" });
    if (activeHead === i) pill(head, "Extruding", "ok");
    else if (err) pill(head, "Error", "bad");
    else if (src && sensor) pill(head, "Loaded", "ok");
    else if (src && !sensor) pill(head, "No Filament", "warn");
    else pill(head, "Idle");

    // Meta
    const meta = setEl(card, "div"); meta.className = "card-meta";
    metaRow(meta, "Material", cfg.type || "—");
    metaRow(meta, "Vendor", cfg.vendor || "—");
    metaRow(meta, "Source", src ? `ACE ${src.ace} · Slot ${src.slot}` : "—");
    metaRow(meta, "Sensor", sensor ? "Filament present" : "Empty",
            sensor ? "" : "muted");

    if (err) {
      const e = setEl(card, "div", { className: "err-msg" });
      e.textContent = `⚠ ${err.action}: ${err.error || err.reason || "unknown error"}`;
    }

    // Actions
    const actions = setEl(card, "div"); actions.className = "actions";
    const loadBtn = setEl(actions, "button", { textContent: "Load" });
    loadBtn.dataset.cmd = `ACEC__Load_T${i}`;
    loadBtn.classList.add("primary");
    loadBtn.disabled = state.swap_in_progress;
    const unloadBtn = setEl(actions, "button", { textContent: "Unload" });
    unloadBtn.dataset.cmd = `ACEC__Unload_T${i}`;
    unloadBtn.dataset.confirm = `Unload T${i}?`;
    unloadBtn.classList.add("danger");
    unloadBtn.disabled = !src || state.swap_in_progress;
  }
}
function renderActivityPreview() {
  const list = document.getElementById("activity-preview");
  if (!list) return;
  fillActivityList(list, events.slice(0, 5),
    "No multiACE events yet — load or unload a toolhead to see activity here.");
}

function fillActivityList(list, items, emptyText) {
  list.innerHTML = "";
  if (items.length === 0) {
    setEl(list, "li", { className: "activity-empty", textContent: emptyText });
    return;
  }
  for (const ev of items) {
    const li = setEl(list, "li");
    const action = ev.action || "?";
    const isFail = action.endsWith("_FAILED");
    const isOk = !isFail && ["LOAD_HEAD", "UNLOAD_HEAD", "UNLOAD_ALL", "ACE_SWITCH"]
      .some((a) => action.startsWith(a));
    if (isFail) li.classList.add("fail");
    else if (isOk) li.classList.add("ok");
    setEl(li, "span", { className: "ts", textContent: ev.ts || "" });
    const right = setEl(li, "span");
    setEl(right, "span", { className: "action", textContent: action + " " });
    if (ev.params) {
      setEl(right, "span", { className: "params", textContent: JSON.stringify(ev.params) });
    }
  }
}

function renderActivity() {
  const list = document.getElementById("activity-list");
  if (!list) return;
  fillActivityList(list, events.slice(0, 50),
    "No activity yet. Trigger a Load/Unload to see events.");
}
function renderActionBar() {
  // Topbar toggles (always visible)
  const af = document.getElementById("autofeed-toggle");
  af.querySelector(".ghost-btn-value").textContent = state.auto_feed ? "ON" : "OFF";
  af.dataset.cmd = state.auto_feed ? "ACEE__Autofeed_Off" : "ACEE__Autofeed_On";
  af.removeAttribute("data-confirm");
  af.setAttribute("aria-pressed", state.auto_feed ? "true" : "false");

  const mt = document.getElementById("mode-toggle");
  mt.querySelector(".ghost-btn-value").textContent = state.mode === "normal" ? "Normal" : "Multi";
  mt.dataset.cmd = state.mode === "normal" ? "ACEF__Mode_Multi" : "ACEF__Mode_Normal";
  mt.dataset.confirm = "Switch mode? Reboot required to take effect.";
  mt.setAttribute("aria-pressed", state.mode !== "normal" ? "true" : "false");

  // Disable the static action-bar buttons during a swap. Per-card Load/Unload
  // buttons (slots, toolheads) own their own disabled state — don't touch those.
  const disabled = state.swap_in_progress;
  for (const btn of document.querySelectorAll(".actionbar button")) {
    btn.disabled = disabled;
  }
}
// ---- Dryer profiles ----
// Defaults sized for ACE Pro's typical max ~70°C. ABS/Nylon/PC are usually
// dried at 80°C+ on a dedicated dryer; on ACE Pro they get longer time at
// the highest the unit can run.
const DEFAULT_DRYER_PROFILES = [
  { id: "pla",   name: "PLA",          temp: 50, duration: 240,  note: "4h @ 50°C" },
  { id: "petg",  name: "PETG",         temp: 65, duration: 360,  note: "6h @ 65°C" },
  { id: "tpu",   name: "TPU / TPE",    temp: 50, duration: 480,  note: "8h @ 50°C" },
  { id: "abs",   name: "ABS / ASA",    temp: 70, duration: 480,  note: "8h @ 70°C (ACE max)" },
  { id: "pa",    name: "Nylon (PA)",   temp: 70, duration: 720,  note: "12h @ 70°C — longer to compensate for cap" },
  { id: "pc",    name: "PC",           temp: 70, duration: 480,  note: "8h @ 70°C" },
  { id: "pva",   name: "PVA / BVOH",   temp: 45, duration: 360,  note: "6h @ 45°C — water-soluble, low temp" },
  { id: "quick", name: "Quick freshen",temp: 50, duration: 60,   note: "1h @ 50°C — a short top-up" },
];

function loadDryProfiles() {
  try {
    const raw = localStorage.getItem("multiace_dryer_profiles");
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed) && parsed.length) return parsed;
    }
  } catch (_) {}
  return DEFAULT_DRYER_PROFILES;
}

function saveDryProfiles(profiles) {
  localStorage.setItem("multiace_dryer_profiles", JSON.stringify(profiles));
}

// Per-ACE selection state (ace_index -> profile_id), client-only
const dryerSelection = {};

async function startDry(aceIdx, tempC, durationMin) {
  try {
    const resp = await fetch(api("api/dry"), {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeader() },
      body: JSON.stringify({ ace: aceIdx, temp_c: tempC, duration_min: durationMin }),
    });
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      toast(`Dry failed: ${body.detail || resp.statusText}`, "error");
      return false;
    }
    toast(`ACE ${aceIdx}: drying at ${tempC}°C for ${formatMinutes(durationMin)}`, "success");
    return true;
  } catch (e) {
    toast(`Dry failed: ${e.message}`, "error");
    return false;
  }
}

function formatMinutes(m) {
  if (m < 60) return `${m}min`;
  const h = Math.floor(m / 60);
  const r = m % 60;
  return r === 0 ? `${h}h` : `${h}h${r}min`;
}

function renderDryer() {
  const panel = document.getElementById("dryer-panel");
  panel.innerHTML = "";
  const profiles = loadDryProfiles();
  const count = Math.max(state.device_count, 1);
  for (let i = 0; i < count; i++) {
    const card = setEl(panel, "div"); card.className = "card no-color";
    setEl(card, "div", { className: "color-band" });

    const head = setEl(card, "div"); head.className = "card-head";
    setEl(head, "span", { className: "card-id", textContent: `ACE ${i}` });
    pill(head, "Profile drying");

    // Profile selector
    const profileBlock = setEl(card, "div"); profileBlock.className = "dryer-profile";
    const label = setEl(profileBlock, "label"); label.className = "dryer-label";
    setEl(label, "span", { className: "meta-key", textContent: "Filament" });
    const select = setEl(label, "select"); select.className = "dryer-select";
    for (const p of profiles) {
      const opt = setEl(select, "option");
      opt.value = p.id;
      opt.textContent = `${p.name} — ${p.note}`;
    }
    const customOpt = setEl(select, "option");
    customOpt.value = "__custom__";
    customOpt.textContent = "Custom…";
    const selectedId = dryerSelection[i] || profiles[0].id;
    select.value = selectedId;

    // Inline temp/duration override (pre-fills from the selected profile)
    const overrides = setEl(card, "div"); overrides.className = "dryer-overrides";
    const tempLabel = setEl(overrides, "label");
    setEl(tempLabel, "span", { className: "meta-key", textContent: "Temp °C" });
    const tempIn = setEl(tempLabel, "input");
    tempIn.type = "number"; tempIn.min = "30"; tempIn.max = "120"; tempIn.step = "1";
    const durLabel = setEl(overrides, "label");
    setEl(durLabel, "span", { className: "meta-key", textContent: "Duration (min)" });
    const durIn = setEl(durLabel, "input");
    durIn.type = "number"; durIn.min = "1"; durIn.max = "2880"; durIn.step = "5";

    function applySelection() {
      const profile = profiles.find((p) => p.id === select.value);
      if (profile) {
        tempIn.value = profile.temp;
        durIn.value = profile.duration;
      }
    }
    applySelection();
    select.addEventListener("change", () => {
      dryerSelection[i] = select.value;
      applySelection();
    });

    // Actions
    const actions = setEl(card, "div"); actions.className = "actions";
    const start = setEl(actions, "button", { textContent: "Start dry" });
    start.classList.add("primary");
    start.addEventListener("click", async () => {
      const t = parseInt(tempIn.value, 10);
      const d = parseInt(durIn.value, 10);
      if (!Number.isFinite(t) || !Number.isFinite(d)) {
        toast("Invalid temperature or duration", "error");
        return;
      }
      const profile = profiles.find((p) => p.id === select.value);
      const profName = profile ? profile.name : "Custom";
      const ok = await confirmDialog(
        `Start drying ACE ${i} at ${t}°C for ${formatMinutes(d)} (${profName})?`
      );
      if (!ok) return;
      start.disabled = true;
      await startDry(i, t, d);
      start.disabled = false;
    });
    const stop = setEl(actions, "button", { textContent: "Stop" });
    stop.dataset.cmd = "ACED__Dry_Stop";
    stop.dataset.confirm = "Stop dryer?";
    stop.classList.add("danger");
  }

  // "Edit profiles" link below the grid
  const tools = setEl(panel.parentElement, "div");
  // Avoid stacking duplicates across re-renders: only create once
  if (!document.getElementById("dryer-tools")) {
    tools.id = "dryer-tools";
    tools.className = "dryer-tools";
    const editBtn = setEl(tools, "button", { textContent: "Edit profiles…" });
    editBtn.className = "ghost-btn";
    editBtn.addEventListener("click", () => editDryProfiles());
    const resetBtn = setEl(tools, "button", { textContent: "Reset to defaults" });
    resetBtn.addEventListener("click", () => {
      if (confirm("Reset dryer profiles to defaults?")) {
        localStorage.removeItem("multiace_dryer_profiles");
        renderDryer();
        toast("Profiles reset", "success");
      }
    });
  }
}

function editDryProfiles() {
  const current = loadDryProfiles();
  const text = prompt(
    "Dryer profiles (JSON). Each entry: { id, name, temp, duration, note }",
    JSON.stringify(current, null, 2)
  );
  if (text == null) return;
  try {
    const parsed = JSON.parse(text);
    if (!Array.isArray(parsed)) throw new Error("must be an array");
    for (const p of parsed) {
      if (!p.id || !p.name) throw new Error("each profile needs id and name");
      p.temp = parseInt(p.temp, 10);
      p.duration = parseInt(p.duration, 10);
      if (!Number.isFinite(p.temp) || !Number.isFinite(p.duration)) {
        throw new Error("temp and duration must be integers");
      }
    }
    saveDryProfiles(parsed);
    renderDryer();
    toast("Profiles saved", "success");
  } catch (e) {
    toast(`Invalid profiles: ${e.message}`, "error");
  }
}

let configValues = {}; // last fetched config

async function renderConfig() {
  const fields = document.getElementById("config-fields");
  if (Object.keys(configValues).length === 0) {
    try {
      const resp = await fetch(api("api/config"), { headers: authHeader() });
      const body = await resp.json();
      configValues = body.values || {};
    } catch (e) {
      fields.innerHTML = `<p class="muted">Failed to load config.</p>`;
      return;
    }
  }
  fields.innerHTML = "";
  for (const [k, v] of Object.entries(configValues)) {
    const lbl = document.createElement("label");
    const span = document.createElement("span");
    span.textContent = k;
    const input = document.createElement("input");
    input.type = "text";
    input.name = k;
    input.value = v;
    lbl.appendChild(span);
    lbl.appendChild(input);
    fields.appendChild(lbl);
  }
}

document.addEventListener("submit", async (ev) => {
  if (ev.target.id !== "config-form") return;
  ev.preventDefault();
  if (!(await confirmDialog("Save config and restart Klipper?"))) return;
  const updates = {};
  for (const input of ev.target.querySelectorAll("input[name]")) {
    if (input.value !== configValues[input.name]) {
      updates[input.name] = input.value;
    }
  }
  if (Object.keys(updates).length === 0) {
    toast("No changes to save");
    return;
  }
  try {
    const resp = await fetch(api("api/config"), {
      method: "PUT",
      headers: { "Content-Type": "application/json", ...authHeader() },
      body: JSON.stringify({ values: updates }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      toast(`Save failed: ${err.detail || resp.statusText}`, "error");
      return;
    }
    toast("Saved. Klipper restarting…", "success");
    configValues = { ...configValues, ...updates };
  } catch (e) {
    toast(`Save failed: ${e.message}`, "error");
  }
});

function renderDiag() {
  document.getElementById("diag-state").textContent =
    JSON.stringify(state, null, 2);
}

// Lazy-load klippy log slice when diag view opens
document.addEventListener("click", async (ev) => {
  const tab = ev.target.closest('.tab[data-view="diag"]');
  if (!tab) return;
  const pre = document.getElementById("diag-klippy");
  pre.textContent = "Loading…";
  try {
    const resp = await fetch(api("api/logs/klippy?lines=200"), { headers: authHeader() });
    const body = await resp.json();
    pre.textContent = (body.lines || []).join("\n");
  } catch (e) {
    pre.textContent = `Failed: ${e.message}`;
  }
});

// View switching (tabs)
function setView(name) {
  for (const tab of document.querySelectorAll(".tab")) {
    tab.classList.toggle("active", tab.dataset.view === name);
  }
  for (const view of document.querySelectorAll(".view")) {
    view.classList.toggle("active", view.dataset.view === name);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  for (const tab of document.querySelectorAll(".tab")) {
    tab.addEventListener("click", () => setView(tab.dataset.view));
  }
  // Bind any data-cmd buttons (action bar, diag panel)
  document.body.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("[data-cmd]");
    if (!btn) return;
    const macro = btn.dataset.cmd;
    const confirm = btn.dataset.confirm;
    if (confirm && !(await confirmDialog(confirm))) return;
    btn.disabled = true;
    await sendCommand(macro);
    btn.disabled = false;
  });
  // "View all →" link inside the dashboard's activity preview switches to the Activity tab.
  const viewAll = document.getElementById("activity-more");
  if (viewAll) {
    viewAll.addEventListener("click", (ev) => { ev.preventDefault(); setView("activity"); });
  }
  connectWS();
  startPrintPolling();
});
