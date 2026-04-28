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
  for (let i = 0; i < 4; i++) {
    const src = state.head_source[i];
    const sensor = state.sensors[i];
    const err = state.last_error && state.last_error.head === i ? state.last_error : null;
    const cfg = state.print_task_config[i] || {};
    const color = rgbFromUint(cfg.color);

    const card = setEl(grid, "div");
    card.className = "card" + (color ? "" : " no-color") + (err ? " error" : "");
    if (color) card.style.setProperty("--card-color", color);
    setEl(card, "div", { className: "color-band" });

    // Head: id + swatch + status pill
    const head = setEl(card, "div"); head.className = "card-head";
    setEl(head, "span", { className: "card-id", textContent: `T${i}` });
    setEl(head, "span", { className: "card-swatch" });
    if (err) pill(head, "Error", "bad");
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
function renderActivity() {
  const list = document.getElementById("activity-list");
  list.innerHTML = "";
  const recent = events.slice(0, 50);
  if (recent.length === 0) {
    const empty = setEl(list, "li");
    empty.className = "activity-empty";
    empty.textContent = "No activity yet. Trigger a Load/Unload to see events.";
    return;
  }
  for (const ev of recent) {
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
  connectWS();
});
