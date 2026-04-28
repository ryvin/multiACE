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
    const resp = await fetch("/api/state", { headers: authHeader() });
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
    const resp = await fetch("/api/events?limit=200", { headers: authHeader() });
    if (!resp.ok) throw new Error(`status ${resp.status}`);
    const body = await resp.json();
    events.length = 0;
    events.push(...body.events);
    renderActivity();
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
    const resp = await fetch("/api/command", {
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

// Render functions are declared in subsequent tasks; placeholder for now
function renderAll() {
  renderTopbar();
  renderSlots();
  renderToolheads();
  renderActivity();
  renderActionBar();
  renderDryer();
  renderConfig();
  renderDiag();
}
function renderTopbar() {
  const label = document.getElementById("active-ace-label");
  if (state.device_count <= 1) {
    label.textContent = state.active_device !== null ? `ACE ${state.active_device}` : "—";
  } else {
    // Multi-ACE: render a button cluster
    label.innerHTML = "";
    for (let i = 0; i < state.device_count; i++) {
      const b = document.createElement("button");
      b.textContent = `ACE ${i}`;
      b.dataset.cmd = `ACEA__Switch_${i}`;
      if (i === state.active_device) b.classList.add("primary");
      label.appendChild(b);
    }
  }
  document.getElementById("slots-active-ace").textContent =
    state.active_device !== null ? `(ACE ${state.active_device})` : "(none)";
}
function slotIcon(filled) {
  return filled ? "●" : "○";
}

function rgbFromUint(packed) {
  // ACE color is uint32 0xAARRGGBB or similar; treat low 24 bits as RGB
  const r = (packed >> 16) & 0xff;
  const g = (packed >> 8) & 0xff;
  const b = packed & 0xff;
  return `rgb(${r},${g},${b})`;
}

function renderSlots() {
  const grid = document.getElementById("slots-grid");
  grid.innerHTML = "";
  const ace = state.active_device;
  for (let i = 0; i < 4; i++) {
    const filled = state.gate_status[i] === 1;
    const loadedTo = Object.entries(state.head_source).find(
      ([, src]) => src && src.ace === ace && src.slot === i
    );
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `
      <h3>Slot ${i} ${slotIcon(filled)}</h3>
      <div class="row"><span>Gate:</span><span>${filled ? "filled" : "empty"}</span></div>
      <div class="row"><span>Loaded to:</span><span>${loadedTo ? `T${loadedTo[0]}` : "—"}</span></div>
      <div class="actions">
        <button data-cmd="ACEC__Load_T${i}" ${!filled || state.swap_in_progress ? "disabled" : ""}>Load → T${i}</button>
        <button data-cmd="ACEC__Unload_T${i}" data-confirm="Unload T${i}?" ${!loadedTo || state.swap_in_progress ? "disabled" : ""}>Unload T${i}</button>
      </div>
    `;
    grid.appendChild(card);
  }
}

function renderToolheads() {
  const grid = document.getElementById("toolheads-grid");
  grid.innerHTML = "";
  for (let i = 0; i < 4; i++) {
    const src = state.head_source[i];
    const sensor = state.sensors[i];
    const err = state.last_error && state.last_error.head === i ? state.last_error : null;
    const cfg = state.print_task_config[i] || {};
    const card = document.createElement("div");
    card.className = `card ${err ? "error" : ""}`;
    const colorSwatch = cfg.color && cfg.color !== 4294967295
      ? `<span style="display:inline-block;width:14px;height:14px;border-radius:50%;background:${rgbFromUint(cfg.color)};vertical-align:middle"></span>`
      : "";
    card.innerHTML = `
      <h3>T${i} ${colorSwatch}</h3>
      <div class="row"><span>Loaded:</span><span>${src ? `ACE ${src.ace} slot ${src.slot}` : "—"}</span></div>
      <div class="row"><span>Sensor:</span><span>${sensor ? "filament present" : "empty"}</span></div>
      <div class="row"><span>Vendor:</span><span class="muted">${cfg.vendor || "—"}</span></div>
      ${err ? `<div class="err-msg">⚠ ${err.action}: ${err.error || err.reason || ""}</div>` : ""}
      <div class="actions">
        <button data-cmd="ACEC__Load_T${i}" ${state.swap_in_progress ? "disabled" : ""}>Load</button>
        <button data-cmd="ACEC__Unload_T${i}" data-confirm="Unload T${i}?" ${!src || state.swap_in_progress ? "disabled" : ""}>Unload</button>
      </div>
    `;
    grid.appendChild(card);
  }
}
function renderActivity() {
  const list = document.getElementById("activity-list");
  list.innerHTML = "";
  const recent = events.slice(0, 50);
  for (const ev of recent) {
    const li = document.createElement("li");
    const isFail = (ev.action || "").endsWith("_FAILED");
    const isOk = !isFail && ["LOAD_HEAD", "UNLOAD_HEAD", "UNLOAD_ALL", "ACE_SWITCH"]
      .some((a) => (ev.action || "").startsWith(a));
    if (isFail) li.classList.add("fail");
    else if (isOk) li.classList.add("ok");
    const params = ev.params ? JSON.stringify(ev.params) : "";
    li.textContent = `${ev.ts || ""} ${ev.action || "?"} ${params}`;
    list.appendChild(li);
  }
}
function renderActionBar() {
  const af = document.getElementById("autofeed-toggle");
  af.textContent = `Auto-feed: ${state.auto_feed ? "ON" : "OFF"}`;
  af.dataset.cmd = state.auto_feed ? "ACEE__Autofeed_Off" : "ACEE__Autofeed_On";
  af.removeAttribute("data-confirm");
  const mt = document.getElementById("mode-toggle");
  mt.textContent = `Mode: ${state.mode === "normal" ? "Normal" : "Multi"}`;
  mt.dataset.cmd = state.mode === "normal" ? "ACEF__Mode_Multi" : "ACEF__Mode_Normal";
  mt.dataset.confirm = "Switch mode? Reboot required to take effect.";
  // Disable the static action-bar buttons during a swap. Per-card Load/Unload
  // buttons (slots, toolheads) own their own disabled state — don't touch those.
  const disabled = state.swap_in_progress;
  for (const btn of document.querySelectorAll(".actionbar button")) {
    btn.disabled = disabled;
  }
}
function renderDryer() {
  const panel = document.getElementById("dryer-panel");
  panel.innerHTML = "";
  const count = Math.max(state.device_count, 1);
  for (let i = 0; i < count; i++) {
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `
      <h3>ACE ${i}</h3>
      <div class="actions">
        <button data-cmd="ACED__Dry_Start_${i}">Start dry</button>
        <button data-cmd="ACED__Dry_Stop" data-confirm="Stop dryer?">Stop dry</button>
      </div>
      <p class="muted">Custom temp/duration via Config tab (per-ACE overrides).</p>
    `;
    panel.appendChild(card);
  }
}

let configValues = {}; // last fetched config

async function renderConfig() {
  const fields = document.getElementById("config-fields");
  if (Object.keys(configValues).length === 0) {
    try {
      const resp = await fetch("/api/config", { headers: authHeader() });
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
    lbl.innerHTML = `<span>${k}</span><input type="text" name="${k}" value="${v}" />`;
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
    const resp = await fetch("/api/config", {
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
    const resp = await fetch("/api/logs/klippy?lines=200", { headers: authHeader() });
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
  connectWS();
});
