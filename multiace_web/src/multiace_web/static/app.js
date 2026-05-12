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
  spool_cache: {},
  last_error: null,
  // --- Operations (smart-swap) ---
  swapParkAvailable: false,   // cached: ACEC__Park_T0 macro present (firmware supports LENGTH= on ACE_UNLOAD_HEAD)
  smartSwapPending: null,     // {head, leg, startedAt} | null — cross-leg UI lock
};
const events = []; // last 200 activity entries
const ws = { sock: null, retry: 0, alive: false };

let _pendingSwapConfirm = null;  // { cancel() } handle from showSwapConfirm, or null

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
    // Map snake_case capability flag to camelCase JS field.
    if (typeof body.swap_park_available === "boolean") {
      state.swapParkAvailable = body.swap_park_available;
    }
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
      // Map snake_case capability flag to camelCase JS field.
      if (typeof msg.payload.swap_park_available === "boolean") {
        state.swapParkAvailable = msg.payload.swap_park_available;
      }
      renderAll();
    } else if (msg.type === "event") {
      const ev = { id: msg.id, ts: msg.ts, ...msg.payload };
      events.unshift(ev);
      if (events.length > 200) events.length = 200;
      renderActivity();
      renderActivityPreview();
      applyEventToWorkflow(ev);
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

// ---- FilamentHub / web-config globals ----
window.MULTIACE_FH_URL = "";
window.MULTIACE_FH_PRINTER_ID = "u1-1";

async function loadWebConfig() {
  try {
    const r = await fetch(api("api/web-config"));
    if (r.ok) {
      const cfg = await r.json();
      window.MULTIACE_FH_URL = cfg.filamenthub_url || "";
      window.MULTIACE_FH_PRINTER_ID = cfg.filamenthub_printer_id || "u1-1";
    }
  } catch (e) {
    // Non-fatal — picker buttons render disabled
  }
}

// ---- Per-ACE autodry (Dryer-tab Auto-maintenance subsection) ----
// Fetches /api/autodry?ace=N for the current state and POSTs partial
// updates back. Decoupled from the legacy single-FSM autodry-panel which
// uses the action-based POST shape.
async function refreshDryerAutoSection(ace) {
  try {
    const r = await fetch(api(`api/autodry?ace=${ace}`), { headers: authHeader() });
    if (!r.ok) return;
    const cfg = await r.json();
    const enabledCb = document.getElementById(`auto-enabled-${ace}`);
    const keepCb = document.getElementById(`auto-keepready-${ace}`);
    const stateLbl = document.getElementById(`auto-state-${ace}`);
    if (enabledCb) enabledCb.checked = !!cfg.enabled;
    if (keepCb) keepCb.checked = !!cfg.keep_ready;
    if (stateLbl) {
      const tag = cfg.unreachable ? "unreachable"
                : cfg.locked      ? "locked (print)"
                : cfg.state || "?";
      stateLbl.textContent = `· state: ${tag}`;
    }
  } catch (_) { /* non-fatal */ }
}

async function postAutodryAce(ace, body) {
  try {
    const r = await fetch(api(`api/autodry?ace=${ace}`), {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeader() },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const detail = await r.text();
      toast(`Auto-dry ACE ${String.fromCharCode(65 + ace)}: ${detail || r.statusText}`, "error");
    } else {
      toast(`Auto-dry ACE ${String.fromCharCode(65 + ace)} updated`, "success");
    }
  } catch (e) {
    toast(`Auto-dry ACE ${String.fromCharCode(65 + ace)} failed: ${e.message}`, "error");
  }
  // Always refresh so the UI reflects whatever the server actually has.
  refreshDryerAutoSection(ace);
}

async function sendScript(script) {
  try {
    const resp = await fetch(api("api/command"), {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeader() },
      body: JSON.stringify({ script }),
    });
    const body = await resp.json();
    if (!resp.ok) {
      toast(`${script} failed: ${body.error || body.detail || resp.statusText}`, "error");
      return false;
    }
    toast(`${script.split(/\s+/)[0]} sent`, "success");
    return true;
  } catch (e) {
    toast(`${script} failed: ${e.message}`, "error");
    return false;
  }
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

function showSwapConfirm({ text, onConfirm, onCancel }) {
  const el = document.createElement("div");
  el.className = "toast swap-confirm-toast";
  const msgSpan = document.createElement("span");
  msgSpan.className = "swap-confirm-msg";
  el.appendChild(msgSpan);
  const cancelBtn = document.createElement("button");
  cancelBtn.className = "swap-confirm-cancel";
  cancelBtn.textContent = "Cancel";
  el.appendChild(cancelBtn);
  document.getElementById("toast-container").appendChild(el);

  let remaining = 3;
  let done = false;
  let intervalId = null;
  let hiddenTimer = null;

  function updateLabel() {
    msgSpan.textContent = `${text} — Cancel (${remaining}…)`;
  }
  updateLabel();

  function teardown() {
    if (intervalId) { clearInterval(intervalId); intervalId = null; }
    if (hiddenTimer) { clearTimeout(hiddenTimer); hiddenTimer = null; }
    document.removeEventListener("visibilitychange", onVis);
    window.removeEventListener("beforeunload", onUnload);
    el.remove();
  }

  function doCancel() {
    if (done) return;
    done = true;
    teardown();
    onCancel();
  }

  function doConfirm() {
    if (done) return;
    done = true;
    teardown();
    toast(`${text} — swap in progress`, "info");
    onConfirm();
  }

  cancelBtn.addEventListener("click", doCancel);

  const onUnload = () => doCancel();
  window.addEventListener("beforeunload", onUnload, { once: true });

  const onVis = () => {
    if (document.hidden) {
      hiddenTimer = setTimeout(doCancel, 2000);
    } else {
      if (hiddenTimer) { clearTimeout(hiddenTimer); hiddenTimer = null; }
    }
  };
  document.addEventListener("visibilitychange", onVis);

  intervalId = setInterval(() => {
    remaining -= 1;
    if (remaining <= 0) {
      doConfirm();
    } else {
      updateLabel();
    }
  }, 1000);

  return { cancel: doCancel };
}

/**
 * Show a failure toast for a smart-swap leg.
 *
 * Presents two actions to the user:
 *   Retry   — removes the toast and re-invokes retryFn.
 *   Dismiss — clears state.smartSwapPending; on the second+ consecutive
 *             failure also opens the Help modal so the user sees hints.
 *
 * @param {number} head             — 0-based head index
 * @param {number} leg              — swap leg number (1 or 2)
 * @param {Function} retryFn        — zero-arg function to retry the operation
 * @param {number} consecutiveFails — how many failures in a row (default 1)
 */
function showSwapFailure(head, leg, retryFn, consecutiveFails = 1) {
  const el = document.createElement("div");
  el.className = "toast error swap-failure-toast";

  const msg = document.createElement("span");
  msg.className = "swap-failure-msg";
  msg.textContent = `Swap ${tName(head)} leg ${leg} failed.`;
  el.appendChild(msg);

  const retryBtn = document.createElement("button");
  retryBtn.className = "swap-confirm-cancel";
  retryBtn.textContent = "Retry";
  el.appendChild(retryBtn);

  const dismissBtn = document.createElement("button");
  dismissBtn.className = "swap-confirm-cancel";
  dismissBtn.textContent = consecutiveFails >= 2 ? "Dismiss + hints" : "Dismiss";
  el.appendChild(dismissBtn);

  document.getElementById("toast-container").appendChild(el);

  retryBtn.addEventListener("click", () => {
    el.remove();
    retryFn();
  });

  dismissBtn.addEventListener("click", () => {
    el.remove();
    state.smartSwapPending = null;
    renderStatusBanner();
    if (consecutiveFails >= 2) {
      openHelp();
    }
  });
}

// ---- Help content & modal ----
const HELP_SECTIONS = [
  {
    title: "Top bar",
    items: [
      ["Connection dot", "Green = web console is talking to the multiACE service. Gray/red = disconnected — refresh the page or check the service."],
      ["Active ACE", "Which ACE Pro device commands are routed to right now. Shown only when more than one ACE is connected."],
      ["Auto-feed", "When ON, multiACE automatically pre-loads spools when you insert them and auto-swaps to a matching spool if one runs out mid-print. Auto-toggles ON when a print starts and OFF when it ends — manual override is fine but the next print cycle will reset it."],
      ["Mode", "Multi (multiACE active, multiple toolheads) vs Normal (stock single-spool firmware). A printer reboot is required for the change to take effect."],
    ],
  },
  {
    title: "Dashboard",
    items: [
      ["Status banner", "Top of the page when something needs attention: a Klipper exception, an in-progress workflow (e.g. Unload All), or the most recent failure."],
      ["Environment strip", "Cavity temperature and humidity (only if a humidity sensor is configured via MULTIACE_HUMIDITY_URL)."],
      ["Workflow panel", "Live progress through multi-step actions. Glyphs: ✓ done, ⟳ running, ○ queued, ✗ failed."],
      ["Print panel", "Current print state, filename, progress, layer, ETA. The 'Extruding T<n>' pill only shows during an active print."],
      ["Dryer status card", "Visible only while drying is active — target temp and remaining time."],
      ["Toolheads", "Four cards (T1..T4), one per U1 toolhead. Laid out 2x2 to match the printer: T1+T2 on the left, T3+T4 on the right. Shows material, vendor, source slot, and per-head Load/Unload buttons. (Internally Klipper still calls them T0..T3 — the GUI just shifts the display.)"],
      ["ACE slots", "Slots inside the active ACE Pro. Same 1,2-left / 3,4-right layout as the toolheads, mirroring the physical ACE. Each card shows material/color and a 'Load → T<n>' button when filament is at the gate."],
      ["Recent activity", "Last 5 events. Click 'View all →' to jump to the Activity tab."],
    ],
  },
  {
    title: "Activity",
    items: [
      ["Event feed", "Last 200 multiACE state events, newest first. Includes LOAD_HEAD, UNLOAD_HEAD, SWITCH_*, SERIAL_WRITE_FAILED, etc."],
      ["Color cues", "Green row = success (LOAD_HEAD, UNLOAD_HEAD, UNLOAD_ALL, ACE_SWITCH). Red row = a *_FAILED event."],
      ["When to use", "Debugging — if a load or unload didn't behave as expected, scroll here and look for the FAILED line and its surrounding context."],
    ],
  },
  {
    title: "Dryer",
    items: [
      ["Profiles", "Pre-set temperature + duration for common materials. Stored in this browser (localStorage), not on the server — different browsers can have different profiles."],
      ["Override", "Pick a profile then edit temp/duration before starting if you want a one-off run."],
      ["Per-ACE", "Each ACE Pro has its own dryer; on a multi-ACE setup pick the right one in the panel."],
      ["Limits", "ACE Pro hardware caps temp at ~70°C. Materials needing 80°C+ (ABS, Nylon, PC) get longer time at 70°C as a workaround."],
    ],
  },
  {
    title: "Config",
    items: [
      ["What's editable", "Live values from ace.cfg — speeds, lengths, temps, etc. The form mirrors the file."],
      ["Save & Restart", "Saving writes ace.cfg and triggers a Klipper RESTART. The printer must be in standby — RESTART aborts any active print."],
      ["Validation", "Keys and values are checked server-side; bad input is rejected before the file is written."],
    ],
  },
  {
    title: "Diagnostics",
    items: [
      ["ACE_HEAD_STATUS", "Re-queries the ACE for current head/slot state without changing anything. Use after a manual filament change or if the dashboard looks stale."],
      ["ACE_LIST", "Lists all detected ACE Pro devices with their USB paths and firmware versions."],
      ["ACE_CLEAR_HEADS", "Wipes multiACE's head_source mapping. Useful when you've manually pulled filament and the dashboard still thinks heads are loaded. Confirms first."],
      ["State JSON", "Raw current dashboard state — handy for filing bug reports."],
      ["klippy.log tail", "Last 200 lines of klippy.log — first place to look when something is wrong at the Klipper level."],
    ],
  },
  {
    title: "Auto-dry",
    items: [
      ["What it does", "Auto-dry watches chamber humidity (via the Govee BLE sensor) and runs ACE_DRY cycles automatically when humidity climbs past the target threshold. Temperature and duration are picked per-filament: PLA dries cooler/shorter, PETG/ABS hotter/longer. Cycles never start during a print or while a swap is in progress."],
      ["Modes", "Off disables it. Log only logs every decision the FSM makes but never actually starts a dryer — useful for verifying behavior before going hands-off. Active runs cycles for real. Default after install is off; mode is sticky across multiace-web restarts. Set this in the Dryer tab → Auto-dry panel."],
      ["Where to look", "Dashboard humidity tile shows the current FSM state in a colored band (\"watching\", \"drying 60°C 4h00\", \"cooldown 12m\", \"fault — click to clear\"). Dryer tab has the full panel: mode toggle, humidity target slider, last-run summary, and an \"Evaluate now\" button that bypasses the debounce buffer. Activity tab shows AUTODRY_* events with friendly labels. Diagnostics tab shows the raw FSM/inputs/persisted state for troubleshooting."],
    ],
  },
  {
    title: "Action bar",
    items: [
      ["Unload All", "Unloads filament from every loaded toolhead back to its ACE slot, one at a time. Disabled during an active swap. Asks to confirm because it's a multi-minute operation."],
    ],
  },
  {
    title: "Tips",
    items: [
      ["Stuck in 'busy'", "If the ACE shows busy with gates [-1,-1,-1,-1] for more than a minute, the USB serial probably hung. Power-cycle the ACE Pro, then restart Klipper (Diag → look for guidance, or use the printer's recovery flow)."],
      ["Filament left in the bowden after Unload All", "Means retract_length is too short for your bowden run. Edit ace.cfg → retract_length and Save & Restart."],
      ["Load fails with 'move_extrude logic error'", "Usually a malformed filament tip. Unload that head and reload — the unload retract reshapes the tip on the way back."],
    ],
  },
];

function openHelp() {
  const body = document.getElementById("help-body");
  if (!body) return;
  body.innerHTML = "";
  HELP_SECTIONS.forEach((sec, i) => {
    const det = setEl(body, "details", { className: "help-section" });
    if (i === 0) det.open = true;
    setEl(det, "summary", { textContent: sec.title });
    const ul = setEl(det, "ul", { className: "help-list" });
    for (const [name, desc] of sec.items) {
      const li = setEl(ul, "li");
      setEl(li, "b", { textContent: name + " " });
      li.appendChild(document.createTextNode(desc));
    }
  });
  document.getElementById("help-modal").classList.remove("hidden");
}
function closeHelp() {
  document.getElementById("help-modal").classList.add("hidden");
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
  aces: [],   // /api/print returns aces:[{index, dryer, humidity, last_seen_ts, is_active}]
  cavity_temp_c: null,
  humidity: { configured: false },
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
  renderEnvStrip();
  if (window.HardwareTwin) {
    window.HardwareTwin.render(state, printState, workflow);
  }
}

// ---------------------------------------------------------------------
// Auto-dry state — polled from /api/autodry every 5s. The FSM ticks every
// 60s server-side, so 5s is plenty fresh for the UI without spamming.
// ---------------------------------------------------------------------
let autodryState = { mode: "off", target_pct: 15, hysteresis_pp: 5,
                     fsm: { state: "IDLE", fault: null, last_run: null } };

// Track consecutive /api/autodry fetch failures so a persistently-broken
// endpoint surfaces in the footer instead of silently freezing the last
// good state. Threshold is 3 polls (~15s at 5s cadence).
let _autodryFetchFailCount = 0;
const _AUTODRY_FETCH_FAIL_THRESHOLD = 3;

async function fetchAutodry() {
  try {
    const r = await fetch(api("api/autodry"), { headers: authHeader() });
    if (!r.ok) {
      _autodryHandleFailure();
      return;
    }
    if (_autodryFetchFailCount >= _AUTODRY_FETCH_FAIL_THRESHOLD) {
      console.log("autodry: fetch recovered, footer back online");
    }
    _autodryFetchFailCount = 0;
    autodryState = await r.json();
    renderEnvStripFooter();
    renderAutodryPanel();  // safe to call even when panel doesn't exist
    renderDiag();          // keeps Diag tab's autodry block live (cheap; bails if elements absent)
  } catch (_) {
    _autodryHandleFailure();
  }
}

function _autodryHandleFailure() {
  _autodryFetchFailCount += 1;
  if (_autodryFetchFailCount === _AUTODRY_FETCH_FAIL_THRESHOLD) {
    console.warn("autodry: fetch failed " + _AUTODRY_FETCH_FAIL_THRESHOLD +
                 " times in a row, marking footer unavailable");
    autodryState = null;
    renderEnvStripFooter();
  }
}

// =====================================================================
// Workflow visual feedback (Unload All, single Load/Unload, Mode switch).
//
// Multi-step ACE operations are otherwise silent — the user clicks Unload All
// and waits 2-6 minutes with no status. We pre-seed a workflow model on the
// click, then update it as state events flow in over the WebSocket.
//
// "Steps" track per-toolhead phases: queued → running → done (or failed).
// The renderer shows a compact panel above the print panel with one row per
// step plus an overall progress bar. The status banner and individual
// toolhead cards mirror the same status so the operator gets layered feedback.
// =====================================================================
const workflow = {
  active: false,
  kind: null,            // "unload_all" | "unload_single" | "load_single" | "mode_switch"
  label: null,           // "Unload All", "Load → T2", etc.
  steps: [],             // [{head, status, started_at, ended_at, error}]
  current_idx: null,
  started_at: null,
  ended_at: null,
};

const _WORKFLOW_AUTODISMISS_MS = 5000;
let _workflowAutoDismissTimer = null;

function _now() { return Date.now(); }

function _loadedHeads() {
  const heads = [];
  for (let h = 0; h < 4; h++) {
    if (state.head_source && state.head_source[h]) heads.push(h);
  }
  return heads;
}

function seedUnloadAllWorkflow() {
  const loaded = _loadedHeads();
  if (loaded.length === 0) return false;
  if (_workflowAutoDismissTimer) clearTimeout(_workflowAutoDismissTimer);
  workflow.active = true;
  workflow.kind = "unload_all";
  workflow.label = "Unload All";
  workflow.steps = loaded.map((h, i) => ({
    head: h,
    status: i === 0 ? "running" : "queued",
    started_at: i === 0 ? _now() : null,
    ended_at: null,
    error: null,
  }));
  workflow.current_idx = 0;
  workflow.started_at = _now();
  workflow.ended_at = null;
  renderWorkflow();
  renderToolheads();
  renderStatusBanner();
  return true;
}

function seedSingleHeadWorkflow(kind, head, label) {
  if (_workflowAutoDismissTimer) clearTimeout(_workflowAutoDismissTimer);
  workflow.active = true;
  workflow.kind = kind;
  workflow.label = label;
  workflow.steps = [{
    head, status: "running", started_at: _now(), ended_at: null, error: null,
  }];
  workflow.current_idx = 0;
  workflow.started_at = _now();
  workflow.ended_at = null;
  renderWorkflow();
  renderToolheads();
  renderStatusBanner();
}

function _findStep(head) {
  return workflow.steps.find(s => s.head === head);
}

function _advanceCurrentIdx() {
  // Move current_idx to the next non-terminal step (queued/running).
  for (let i = 0; i < workflow.steps.length; i++) {
    const s = workflow.steps[i];
    if (s.status === "queued" || s.status === "running") {
      workflow.current_idx = i;
      if (s.status === "queued") {
        s.status = "running";
        s.started_at = _now();
      }
      return;
    }
  }
  workflow.current_idx = null;
}

function _allStepsTerminal() {
  return workflow.steps.every(s => s.status === "done" || s.status === "failed");
}

function _finishWorkflow() {
  workflow.ended_at = _now();
  workflow.current_idx = null;
  renderWorkflow();
  renderToolheads();
  renderStatusBanner();
  // Keep the panel up briefly so completion registers, then hide.
  if (_workflowAutoDismissTimer) clearTimeout(_workflowAutoDismissTimer);
  _workflowAutoDismissTimer = setTimeout(() => {
    workflow.active = false;
    workflow.steps = [];
    renderWorkflow();
    renderToolheads();
    renderStatusBanner();
  }, _WORKFLOW_AUTODISMISS_MS);
}

function applyEventToWorkflow(event) {
  if (!workflow.active) return;
  const action = event.action || "";
  const params = event.params || {};
  const head = params.head;

  if (action === "UNLOAD_HEAD" && head != null) {
    const step = _findStep(head);
    if (step && step.status !== "done" && step.status !== "failed") {
      step.status = "done";
      step.ended_at = _now();
      _advanceCurrentIdx();
    }
  } else if (action === "UNLOAD_HEAD_FAILED" && head != null) {
    const step = _findStep(head);
    if (step) {
      step.status = "failed";
      step.ended_at = _now();
      step.error = params.error || params.reason || "unknown error";
      _advanceCurrentIdx();
    }
  } else if (action === "LOAD_HEAD" && head != null) {
    const step = _findStep(head);
    if (step && step.status !== "done" && step.status !== "failed") {
      step.status = "done";
      step.ended_at = _now();
      _advanceCurrentIdx();
    }
  } else if (action === "LOAD_HEAD_FAILED" && head != null) {
    const step = _findStep(head);
    if (step) {
      step.status = "failed";
      step.ended_at = _now();
      step.error = params.error || params.reason || "unknown error";
      _advanceCurrentIdx();
    }
  } else if (action === "UNLOAD_ALL") {
    // Mark any not-yet-seen steps as done if their head_source is now null.
    for (const s of workflow.steps) {
      if (s.status === "queued" || s.status === "running") {
        if (!state.head_source[s.head]) {
          s.status = "done";
          s.ended_at = _now();
        }
      }
    }
    _advanceCurrentIdx();
  }

  if (_allStepsTerminal()) {
    _finishWorkflow();
  } else {
    renderWorkflow();
    renderToolheads();
    renderStatusBanner();
  }
}

function _stepStatusGlyph(status) {
  if (status === "done") return "✓";
  if (status === "failed") return "✗";
  if (status === "running") return "⟳";
  return "○";
}

function _stepStatusKind(status) {
  if (status === "done") return "ok";
  if (status === "failed") return "bad";
  if (status === "running") return "running";
  return "queued";
}

function _stepDescription(s) {
  if (s.status === "done") {
    const dur = (s.ended_at - s.started_at) / 1000;
    return `Unloaded · ${dur.toFixed(0)}s`;
  }
  if (s.status === "failed") return `Failed: ${s.error || "see Activity"}`;
  if (s.status === "running") {
    return workflow.kind && workflow.kind.startsWith("load")
      ? "Loading filament…"
      : "Retracting filament…";
  }
  return "Queued";
}

function _formatElapsed(ms) {
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}m ${s}s`;
}

function renderWorkflow() {
  const panel = document.getElementById("workflow-panel");
  if (!panel) return;
  panel.innerHTML = "";
  if (!workflow.active) {
    panel.classList.add("hidden");
    return;
  }
  panel.classList.remove("hidden");

  const completed = workflow.steps.filter(s => s.status === "done" || s.status === "failed").length;
  const failed = workflow.steps.filter(s => s.status === "failed").length;
  const total = workflow.steps.length;
  const pct = total === 0 ? 0 : (completed / total) * 100;
  const elapsed = (workflow.ended_at || _now()) - workflow.started_at;
  const finished = _allStepsTerminal();

  // Header row
  const header = setEl(panel, "div"); header.className = "workflow-header";
  setEl(header, "span", { className: "workflow-title", textContent: workflow.label });
  const counterText = finished
    ? (failed > 0 ? `${completed - failed} done · ${failed} failed`
                  : `${completed} done`)
    : `${completed} of ${total}`;
  setEl(header, "span", { className: "workflow-counter", textContent: counterText });
  setEl(header, "span", { className: "workflow-elapsed muted",
                          textContent: _formatElapsed(elapsed) + " elapsed" });

  // Progress bar
  const pwrap = setEl(panel, "div"); pwrap.className = "progress workflow-progress";
  const pfill = setEl(pwrap, "div");
  pfill.className = "progress-fill" + (failed > 0 ? " progress-bad" : finished ? " progress-good" : "");
  pfill.style.width = pct.toFixed(1) + "%";

  // Step list
  const list = setEl(panel, "ul"); list.className = "workflow-steps";
  for (const s of workflow.steps) {
    const li = setEl(list, "li"); li.className = "workflow-step workflow-" + _stepStatusKind(s.status);
    const cfg = (state.print_task_config && state.print_task_config[s.head]) || {};
    const color = rgbFromUint(cfg.color);
    const swatch = setEl(li, "span", { className: "workflow-swatch" });
    if (color) swatch.style.background = color;
    else swatch.classList.add("no-color");
    setEl(li, "span", { className: "workflow-glyph", textContent: _stepStatusGlyph(s.status) });
    setEl(li, "span", { className: "workflow-head-id", textContent: tName(s.head) });
    const meta = setEl(li, "span"); meta.className = "workflow-step-meta";
    const matLine = cfg.vendor || cfg.type
      ? `${cfg.vendor || ""}${cfg.vendor && cfg.type ? " " : ""}${cfg.type || ""}`.trim()
      : "";
    if (matLine) setEl(meta, "span", { className: "workflow-step-mat", textContent: matLine });
    setEl(meta, "span", { className: "workflow-step-status", textContent: _stepDescription(s) });
  }
}

function renderAutodryPanel() {
  const panel = document.getElementById("autodry-panel");
  if (!panel) return;
  const s = autodryState;
  if (!s) return;

  // Skip rebuild while the user is interacting with a control inside the panel
  // (otherwise a 5s poll mid-drag would clobber the slider). The next poll
  // (or postAutodry response) will refresh once interaction ends.
  const active = document.activeElement;
  if (active && panel.contains(active)) return;

  const fsm = s.fsm || {};

  panel.innerHTML = "";  // Full rebuild — guarded above so we don't run mid-interaction
  const card = document.createElement("div");
  card.className = "autodry-card";

  // Header
  const header = document.createElement("div");
  header.className = "autodry-header";
  header.innerHTML = `<h3>Auto-dry</h3>`;
  card.appendChild(header);

  // Mode selector
  const modeRow = document.createElement("div");
  modeRow.className = "autodry-row";
  modeRow.innerHTML = `<label>Mode</label>`;
  const modeBtns = document.createElement("div");
  modeBtns.className = "autodry-mode-btns";
  for (const m of ["off", "log", "active"]) {
    const b = document.createElement("button");
    b.textContent = m === "off" ? "Off" : m === "log" ? "Log only" : "Active";
    b.className = "autodry-mode-btn" + (s.mode === m ? " active" : "");
    b.onclick = () => postAutodry({ action: "set_mode", value: m });
    modeBtns.appendChild(b);
  }
  modeRow.appendChild(modeBtns);
  card.appendChild(modeRow);

  // Target slider
  const targetRow = document.createElement("div");
  targetRow.className = "autodry-row";
  targetRow.innerHTML = `<label>Target humidity</label>`;
  const slider = document.createElement("input");
  slider.type = "range";
  slider.min = "5";
  slider.max = "60";
  slider.value = String(s.target_pct);
  const targetLabel = document.createElement("span");
  targetLabel.className = "autodry-value";
  targetLabel.textContent = `${s.target_pct}%`;
  slider.oninput = () => {
    targetLabel.textContent = `${slider.value}%`;
  };
  slider.onchange = () => {
    postAutodry({ action: "set_target", value: parseInt(slider.value, 10) });
  };
  targetRow.appendChild(slider);
  targetRow.appendChild(targetLabel);
  card.appendChild(targetRow);

  // Sensor-floor warning if applicable
  if (s.target_pct < 13) {
    const warn = document.createElement("div");
    warn.className = "autodry-warn";
    warn.textContent =
      "Near H5104 sensor floor — auto-dry may not stop reliably below 12%.";
    card.appendChild(warn);
  }

  // Default filament type — fallback for non-RFID spools where multiACE has
  // no `type` metadata. None / blank = strict (FSM stays IDLE if type unknown).
  const filRow = document.createElement("div");
  filRow.className = "autodry-row";
  filRow.innerHTML = `<label title="Used when filament is loaded from this ACE but the slot has no type tag (non-RFID spool, no slicer-set metadata).">Default filament</label>`;
  const filSelect = document.createElement("select");
  filSelect.className = "autodry-select";
  for (const opt of [
    ["", "(none — strict)"],
    ["PLA", "PLA"],
    ["PETG", "PETG"],
    ["TPU", "TPU"],
    ["ABS", "ABS"],
    ["ASA", "ASA"],
    ["PA", "PA / Nylon"],
    ["PC", "PC"],
    ["PVA", "PVA"],
  ]) {
    const o = document.createElement("option");
    o.value = opt[0];
    o.textContent = opt[1];
    if ((s.default_filament_type || "") === opt[0]) o.selected = true;
    filSelect.appendChild(o);
  }
  filSelect.onchange = () => {
    const v = filSelect.value || null;
    postAutodry({ action: "set_default_filament_type", value: v });
  };
  filRow.appendChild(filSelect);
  card.appendChild(filRow);

  // FSM state line
  const stateRow = document.createElement("div");
  stateRow.className = "autodry-state";
  let stateText = `${fsm.state}`;
  if (fsm.state === "FAULTED" && fsm.fault) {
    stateText += ` — ${fsm.fault.code}: ${fsm.fault.msg}`;
    const reset = document.createElement("button");
    reset.textContent = "Reset fault";
    reset.className = "autodry-reset";
    reset.onclick = () => postAutodry({ action: "reset_fault" });
    stateRow.appendChild(document.createTextNode(stateText + " "));
    stateRow.appendChild(reset);
  } else {
    stateRow.textContent = stateText;
  }
  card.appendChild(stateRow);

  // Last run summary
  if (fsm.last_run) {
    const lr = document.createElement("div");
    lr.className = "autodry-last-run muted small";
    const lrFmt = `${fsm.last_run.kind} ${fsm.last_run.outcome}: ` +
                  `${fsm.last_run.trigger_rh.toFixed(1)} → ` +
                  `${fsm.last_run.end_rh.toFixed(1)}%, ` +
                  `${fsm.last_run.ran_min}m`;
    lr.textContent = `Last run: ${lrFmt}`;
    card.appendChild(lr);
  }

  // Force-evaluate button
  const tools = document.createElement("div");
  tools.className = "autodry-tools";
  const fe = document.createElement("button");
  fe.textContent = "Evaluate now";
  fe.onclick = () => postAutodry({ action: "force_evaluate" });
  tools.appendChild(fe);
  card.appendChild(tools);

  panel.appendChild(card);
}

async function postAutodry(body) {
  try {
    const r = await fetch(api("api/autodry"), {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeader() },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const detail = await r.text();
      toast(`Auto-dry: ${detail || r.statusText}`, "error");
      return;
    }
    autodryState = await r.json();
    // Blur active control so the activeElement-guard in renderAutodryPanel
    // doesn't skip the re-render after a successful action.
    if (document.activeElement && document.activeElement !== document.body) {
      document.activeElement.blur();
    }
    renderAutodryPanel();
    renderEnvStripFooter();
  } catch (e) {
    toast(`Auto-dry: ${e.message}`, "error");
  }
}

function renderEnvStripFooter() {
  const tile = document.querySelector(".env-strip .env-tile.env-humidity, .env-strip .env-tile:nth-child(2)");
  if (!tile) return;
  let footer = tile.querySelector(".env-autodry-footer");
  const fsm = (autodryState && autodryState.fsm) || {};
  const text = autodryFooterText(autodryState, fsm);
  if (!text) {
    if (footer) footer.remove();
    return;
  }
  if (!footer) {
    footer = document.createElement("div");
    footer.className = "env-autodry-footer muted";
    tile.appendChild(footer);
  }
  footer.textContent = text;
  if (autodryState === null) {
    footer.classList.add("unavailable");
    delete footer.dataset.fsmState;
  } else {
    footer.classList.remove("unavailable");
    footer.dataset.fsmState = fsm.state || "IDLE";
  }
}

// TODO(task-9): wire FAULTED footer to fault-reset action — the cursor
// affordance was removed from style.css for now to avoid implying a click
// target that does nothing.
function autodryFooterText(s, fsm) {
  if (s === null) return "Auto-dry: status unavailable";
  const mode = s.mode || "off";
  if (mode === "off") return null;
  const state = fsm.state || "IDLE";
  if (state === "WATCHING") return `Auto-dry: armed · target ${s.target_pct}%`;
  if (state === "DRYING")
    return mode === "log"
      ? `Would dry [log-only]`
      : `Drying… target ${s.target_pct}%`;
  if (state === "OBSERVED_DRYING") return `Manual dry running`;
  if (state === "COOLDOWN") return `Cooldown · resumes soon`;
  if (state === "FAULTED")
    return `Auto-dry paused · ${fsm.fault ? fsm.fault.code : "fault"}`;
  if (state === "IDLE") return null;
  return null;
}

function renderEnvStrip() {
  const strip = document.getElementById("env-strip");
  if (!strip) return;
  strip.innerHTML = "";

  const tiles = [];

  if (printState.cavity_temp_c != null && Number.isFinite(printState.cavity_temp_c)) {
    tiles.push({
      label: "Cavity",
      value: printState.cavity_temp_c.toFixed(1) + "°C",
      kind: printState.cavity_temp_c > 50 ? "warn" : "",
      sub: "U1 enclosure",
    });
  }

  const h = printState.humidity || {};
  if (h.configured) {
    if (h.ok && h.humidity_pct != null) {
      let kind = "";
      if (h.humidity_pct >= 60) kind = "bad";
      else if (h.humidity_pct >= 45) kind = "warn";
      else if (h.humidity_pct < 25) kind = "ok";
      tiles.push({
        label: h.label || "Humidity",
        value: Math.round(h.humidity_pct) + "%",
        kind,
        sub: h.temp_c != null ? `${h.temp_c.toFixed(1)}°C ambient` : "humidity",
      });
    } else {
      tiles.push({
        label: h.label || "Humidity",
        value: "—",
        kind: "warn",
        sub: "sensor offline",
      });
    }
  }

  if (tiles.length === 0) {
    strip.classList.add("hidden");
    return;
  }
  strip.classList.remove("hidden");
  for (const t of tiles) {
    const tile = setEl(strip, "div");
    tile.className = "env-tile" + (t.kind ? " env-" + t.kind : "");
    setEl(tile, "div", { className: "env-label", textContent: t.label });
    setEl(tile, "div", { className: "env-val", textContent: t.value });
    setEl(tile, "div", { className: "env-sub muted", textContent: t.sub });
  }
  // Re-attach autodry footer after the strip is rebuilt; otherwise the next
  // 4s fetchPrint() would wipe it until the next 5s autodry poll.
  renderEnvStripFooter();
}

function renderDryerStatus() {
  const card = document.getElementById("dryer-status-card");
  if (!card) return;
  // /api/print now returns aces:[{index, dryer, humidity, last_seen_ts, is_active}]
  // for each ACE. Fall back to single-ACE printState.dryer for older backend.
  const aces = (printState.aces && printState.aces.length)
    ? printState.aces
    : [{ index: 0, dryer: printState.dryer || null, humidity: null, last_seen_ts: null, is_active: true }];
  const anyDrying = aces.some(a => a.dryer && a.dryer.status && a.dryer.status !== "stop");
  card.classList.toggle("hidden", !anyDrying);
  card.innerHTML = "";
  if (!anyDrying) return;

  card.classList.add("card");
  setEl(card, "div", { className: "color-band" });
  card.style.setProperty("--card-color", "var(--warn)");

  const head = setEl(card, "div"); head.className = "card-head";
  setEl(head, "span", { className: "card-id", textContent: "ACE Dryers" });
  pill(head, "DRYING", "warn");

  for (const a of aces) {
    card.appendChild(renderDryerRow(a));
  }
}

function renderDryerRow(aceBlock) {
  const ace = aceBlock.index;
  const d = aceBlock.dryer || {};
  const isDrying = d.status && d.status !== "stop";
  const stale = !aceBlock.is_active;

  const row = document.createElement("div");
  row.className = "dryer-row";
  if (!isDrying) row.classList.add("dryer-row-idle");

  const label = setEl(row, "span"); label.className = "dryer-ace";
  label.textContent = `ACE ${String.fromCharCode(65 + ace)}`;

  const dot = setEl(row, "span"); dot.className = "dot" + (isDrying ? " dot-on" : "");
  dot.textContent = isDrying ? "●" : "○";

  const stateSpan = setEl(row, "span"); stateSpan.className = "dryer-state";
  stateSpan.textContent = isDrying ? "drying" : "idle";

  if (isDrying) {
    const tempSpan = setEl(row, "span"); tempSpan.className = "muted";
    tempSpan.textContent = `${d.target_temp || 0}°C`;
    const remSpan = setEl(row, "span"); remSpan.className = "muted";
    remSpan.textContent = formatMinutes(d.remain_min || 0);
  }

  if (stale) {
    const staleBadge = setEl(row, "span"); staleBadge.className = "badge badge-stale";
    staleBadge.textContent = "stale";
  }

  if (isDrying) {
    const stopBtn = setEl(row, "button", { textContent: "Stop" });
    stopBtn.className = "danger";
    stopBtn.title = `Stop drying on ACE ${String.fromCharCode(65 + ace)}`;
    stopBtn.addEventListener("click", async () => {
      if (!await confirmDialog(`Stop dryer on ACE ${String.fromCharCode(65 + ace)}?`)) return;
      try {
        const r = await fetch(api("api/dry/stop"), {
          method: "POST",
          headers: { "Content-Type": "application/json", ...authHeader() },
          body: JSON.stringify({ ace }),
        });
        if (!r.ok) {
          const body = await r.json().catch(() => ({}));
          toast(`Stop failed: ${body.error || r.statusText}`, "error");
        } else {
          toast(`Stop ACE ${String.fromCharCode(65 + ace)} sent`, "success");
        }
      } catch (e) {
        toast(`Stop failed: ${e.message}`, "error");
      }
    });
  }

  return row;
}

let _printPollTimer = null;
function startPrintPolling() {
  if (_printPollTimer) return;
  fetchPrint();
  _printPollTimer = setInterval(fetchPrint, 4000);
}

let _autodryPollTimer = null;
function startAutodryPolling() {
  if (_autodryPollTimer) return;
  fetchAutodry();
  _autodryPollTimer = setInterval(fetchAutodry, 5000);  // 5s — FSM ticks every 60s
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
  // Klipper keeps toolhead.extruder set after a print ends, so only show the
  // "Extruding T<n>" pill while a print is actively running.
  if (head != null && stateName === "printing") {
    const tagP = pill(headRow, `Extruding ${tName(head)}`, "ok");
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
  if (state.swap_in_progress || state.smartSwapPending || (workflow.active && !workflow.ended_at)) {
    banner.classList.remove("hidden"); banner.classList.add("warn");
    if (state.smartSwapPending) {
      const p = state.smartSwapPending;
      setEl(banner, "strong", { textContent: `Smart-swap ${tName(p.head)} in progress` });
      setEl(banner, "span", { textContent: ` — leg ${p.leg} of 2. All chevron menus locked.` });
    } else if (workflow.active) {
      const cur = workflow.current_idx != null ? workflow.steps[workflow.current_idx] : null;
      const total = workflow.steps.length;
      const done = workflow.steps.filter(s => s.status === "done").length;
      const remaining = total - done - workflow.steps.filter(s => s.status === "failed").length;
      const headLabel = cur ? tName(cur.head) : "";
      setEl(banner, "strong", { textContent: `${workflow.label} in progress` });
      setEl(banner, "span", {
        textContent: cur
          ? ` — working on ${headLabel}, ${remaining} remaining`
          : " — finishing up"
      });
    } else {
      setEl(banner, "strong", { textContent: "Tool change in progress…" });
      setEl(banner, "span", { textContent: " Hold actions until this completes." });
    }
    return;
  }
  if (state.last_error) {
    const err = state.last_error;
    banner.classList.remove("hidden"); banner.classList.add("bad");
    const headTag = Number.isInteger(err.head) ? `${tName(err.head)} ` : "";

    // Recognize the "load_feeding ... timeout" pattern — the firmware emits
    // this when LOAD's ACE-side feed runs to the configured length but the
    // head's runout_sensor never trips. Most common cause is the filament
    // tip having drifted past the ACE drive wheel (cumulative cross-slot
    // coupling, or a too-long LENGTH= retract). The wheel spins but doesn't
    // bite. Recovery is a manual reseat of the slot — there's no firmware-
    // side fix (the wheel encoder measures wheel rotation, not filament
    // motion, so the firmware can't tell past-grip from successful feed
    // until the full configured load_length has elapsed without tripping
    // the head's runout sensor). Surface the recovery hint explicitly.
    const isLoadFeedTimeout =
      err.action === "LOAD_HEAD_FAILED" &&
      err.reason === "feed_auto_error" &&
      typeof err.error === "string" &&
      /load_feeding/i.test(err.error) &&
      /timeout/i.test(err.error);

    if (isLoadFeedTimeout && Number.isInteger(err.ace) && Number.isInteger(err.slot)) {
      const aceLetter = String.fromCharCode(65 + err.ace);
      setEl(banner, "strong", { textContent: `${headTag}Load timed out` });
      const msg = setEl(banner, "span");
      msg.textContent =
        ` — ${aceLetter}${err.slot} wheel not gripping filament. ` +
        `Likely past-grip drift. Open ACE ${aceLetter}, pull slot ${err.slot}'s ` +
        `filament out, cut a fresh 45° tip, re-insert through the intake until ` +
        `the wheel tugs it inward. See troubleshooting.md for details.`;
    } else {
      setEl(banner, "strong", { textContent: `${headTag}${err.action}` });
      const msg = setEl(banner, "span");
      msg.textContent = " " + (err.error || err.reason || "see Activity for details");
    }
  }
}

// ---- Print queue tab ----

let _pqData = null;       // last fetched print queue response
let _pqPollTimer = null;

function openFixLoadoutWizard(item) {
  const modal = document.getElementById("fix-loadout-modal");
  const body = document.getElementById("fix-loadout-body");
  if (!modal || !body) return;

  const tools = item.tools || {};
  const unresolved = Object.entries(tools).filter(([, t]) =>
    t.match_quality !== "exact" || !t.resolved
  );

  if (unresolved.length === 0) {
    toast("All tools already resolved — nothing to fix.", "info");
    return;
  }

  body.innerHTML = unresolved.map(([idx, t]) => {
    const hasSmartSwap = typeof window.initiateSmartSwap === "function";
    const fhUrl = window.MULTIACE_FH_URL || "";
    const fhPrinter = window.MULTIACE_FH_PRINTER_ID || "u1-1";
    const pickerHref = fhUrl
      ? `${fhUrl}?picker=ace&printer=${encodeURIComponent(fhPrinter)}&ace=&slot=`
      : null;

    return `<div class="fix-row" data-tool="${idx}">
      <div class="fix-row-header">
        <strong>Tool ${idx}</strong>
        ${_colorSwatch(t.color)} ${t.type}
        ${_matchIcon(t.match_quality)}
        ${t.match_quality === "ambiguous"
          ? `<span class="fix-hint">${t.candidates.length} candidates — pick one below</span>`
          : `<span class="fix-hint">No matching spool found</span>`}
      </div>
      ${t.match_quality === "ambiguous" && t.candidates.length > 0
        ? `<div class="fix-candidates">
            ${t.candidates.map(c =>
              `<button class="fix-candidate-btn ghost-btn"
                 data-tool="${idx}" data-ace="${c.ace}" data-slot="${c.slot}"
                 data-spool="${c.spool_id}">
                 ACE ${String.fromCharCode(65 + c.ace)} / Slot ${c.slot}
                 ${c.spool_name ? "— " + c.spool_name : ""}
               </button>`
            ).join("")}
          </div>`
        : ""}
      <div class="fix-actions">
        ${hasSmartSwap
          ? `<button class="fix-smartswap-btn ghost-btn" data-tool="${idx}"
               title="Use smart-swap to load a matching spool">
               Load matching spool…
             </button>`
          : `<span class="fix-hint">Smart-swap not available — load via
               ${pickerHref
                 ? `<a href="${pickerHref}" target="_blank">FilamentHub picker</a>`
                 : "FilamentHub picker (FILAMENTHUB_URL not configured)"}
             </span>`}
        ${pickerHref
          ? `<a class="ghost-btn" href="${pickerHref}" target="_blank">
               Bind a new spool…
             </a>`
          : ""}
      </div>
    </div>`;
  }).join("<hr>");

  modal.classList.remove("hidden");

  body.querySelectorAll(".fix-candidate-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      const filename = item.filename;
      toast(`Accepting ACE ${String.fromCharCode(65 + parseInt(btn.dataset.ace))} / Slot ${parseInt(btn.dataset.slot)} for tool ${btn.dataset.tool}`, "info");
      await fetch(api("api/print_queue/" + encodeURIComponent(filename) + "/revalidate"),
        { method: "POST", headers: authHeader() });
      await fetchPrintQueue();
      closeFixLoadoutWizard();
    });
  });

  body.querySelectorAll(".fix-smartswap-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const toolIdx = parseInt(btn.dataset.tool, 10);
      const toolMeta = tools[String(toolIdx)] || {};
      if (typeof window.initiateSmartSwap === "function") {
        const head = toolMeta.physical_head ?? toolIdx;
        window.initiateSmartSwap(head, null, null);
        closeFixLoadoutWizard();
      } else {
        toast("Smart-swap not available. Load the spool manually via FilamentHub picker.", "error");
      }
    });
  });
}

function closeFixLoadoutWizard() {
  document.getElementById("fix-loadout-modal")?.classList.add("hidden");
}

async function fetchPrintQueue() {
  try {
    const resp = await fetch(api("api/print_queue"), { headers: authHeader() });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    _pqData = await resp.json();
    renderPrintQueue();
  } catch (e) {
    console.error("fetchPrintQueue", e);
  }
}

function _statusChip(status) {
  const map = {
    ready: { label: "Ready", cls: "chip-green" },
    pending: { label: "Pending", cls: "chip-yellow" },
    needs_loadout: { label: "Needs loadout", cls: "chip-orange" },
    error: { label: "Error", cls: "chip-red" },
  };
  const info = map[status] || { label: status, cls: "chip-gray" };
  return `<span class="status-chip ${info.cls}">${info.label}</span>`;
}

function _matchIcon(quality) {
  if (quality === "exact") return '<span class="match-ok" title="Matched">&#10003;</span>';
  if (quality === "ambiguous") return '<span class="match-warn" title="Ambiguous">?</span>';
  return '<span class="match-err" title="No match">!</span>';
}

function _colorSwatch(hex) {
  const clean = (hex || "").replace("#", "");
  return `<span class="color-swatch" style="background:#${clean}" title="#${clean}"></span>`;
}

function _renderResolutionTable(tools) {
  const rows = Object.entries(tools).map(([idx, t]) => {
    const resolved = t.resolved;
    const aceLabel = resolved
      ? `ACE ${String.fromCharCode(65 + resolved.ace)} / Slot ${resolved.slot}`
      : "—";
    const spool = t.candidates && t.candidates.length === 1
      ? (t.candidates[0].spool_name || "—")
      : (resolved ? (t.candidates.find(c => c.spool_id === resolved.spool_id) || {}).spool_name || "—" : "—");
    return `<tr>
      <td>${idx}</td>
      <td>${_colorSwatch(t.color)} ${t.type}</td>
      <td>${aceLabel}</td>
      <td>${spool}</td>
      <td>${_matchIcon(t.match_quality)}</td>
    </tr>`;
  }).join("");
  return `<table class="resolution-table">
    <thead><tr><th>Tool</th><th>Filament</th><th>Slot</th><th>Spool</th><th></th></tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

function renderPrintQueue() {
  const listEl = document.getElementById("print-queue-list");
  const emptyEl = document.getElementById("print-queue-empty");
  if (!listEl) return;
  if (!_pqData || !_pqData.items || _pqData.items.length === 0) {
    listEl.innerHTML = "";
    emptyEl && emptyEl.classList.remove("hidden");
    return;
  }
  emptyEl && emptyEl.classList.add("hidden");

  listEl.innerHTML = _pqData.items.map((item, i) => {
    const ts = item.generated_at
      ? new Date(item.generated_at).toLocaleString()
      : "unknown";
    const isReady = item.status === "ready";
    return `<div class="pq-item" data-index="${i}">
      <div class="pq-item-header">
        <span class="pq-filename">${item.filename}</span>
        <span class="pq-ts">${ts}</span>
        ${_statusChip(item.status)}
      </div>
      <details class="pq-details">
        <summary>Resolution table (${Object.keys(item.tools || {}).length} tools,
          ${(item.swaps || []).length} swap(s))</summary>
        ${_renderResolutionTable(item.tools || {})}
      </details>
      <div class="pq-actions">
        <button class="primary pq-print-btn" data-filename="${item.filename}"
          ${isReady ? "" : "disabled"} title="${isReady ? "Start print" : "Resolve all tools first"}">
          Print
        </button>
        <button class="pq-fix-btn" data-index="${i}"
          ${item.status === "ready" ? "disabled" : ""}>
          Fix loadout
        </button>
        <button class="pq-revalidate-btn" data-filename="${item.filename}">
          Re&#8209;validate
        </button>
      </div>
      ${item.status === "error" ? `<div class="pq-error-banner">
        Error: ${item.reason || "unknown"}
        ${item.errors && item.errors.length ? " — " + item.errors[0] : ""}
      </div>` : ""}
    </div>`;
  }).join("");

  // Wire Print buttons
  // NOTE: /api/print_queue/<filename>/print endpoint is not yet implemented (T5 gap).
  // The fetch will 404; the toast error handler catches it gracefully.
  listEl.querySelectorAll(".pq-print-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      const filename = btn.dataset.filename;
      try {
        await fetch(api("api/print_queue/" + encodeURIComponent(filename) + "/print"),
          { method: "POST", headers: authHeader() });
        toast(`Print started: ${filename}`, "success");
      } catch (e) {
        toast(`Print failed: ${e.message}`, "error");
      }
    });
  });

  // Wire Re-validate buttons
  listEl.querySelectorAll(".pq-revalidate-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      const filename = btn.dataset.filename;
      btn.disabled = true;
      btn.textContent = "Validating…";
      try {
        const resp = await fetch(
          api("api/print_queue/" + encodeURIComponent(filename) + "/revalidate"),
          { method: "POST", headers: authHeader() }
        );
        if (!resp.ok) {
          const body = await resp.json().catch(() => ({}));
          toast(`Re-validate failed: ${body.detail || resp.statusText}`, "error");
        } else {
          toast("Re-validated — refreshing…", "success");
          await fetchPrintQueue();
        }
      } catch (e) {
        toast(`Re-validate error: ${e.message}`, "error");
      } finally {
        btn.disabled = false;
        btn.textContent = "Re‑validate";
      }
    });
  });

  // Wire Fix loadout buttons
  listEl.querySelectorAll(".pq-fix-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const idx = parseInt(btn.dataset.index, 10);
      const item = _pqData.items[idx];
      openFixLoadoutWizard(item);
    });
  });
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
  renderWorkflow();
  if (window.HardwareTwin) {
    window.HardwareTwin.render(state, printState, workflow);
  }
}
function renderTopbar() {
  const label = document.getElementById("active-ace-label");
  label.innerHTML = "";
  const deviceCount = state.device_count || 0;
  const pill = document.createElement("span");
  pill.className = "ace-pill";
  const k = document.createElement("span"); k.className = "muted";
  k.textContent = deviceCount > 1 ? "ACEs:" : "Active:";
  const v = document.createElement("strong");
  if (deviceCount > 1) {
    v.textContent = `${deviceCount} (active: ACE ${String.fromCharCode(65 + (state.active_device ?? 0))})`;
  } else {
    v.textContent = state.active_device !== null ? `ACE ${state.active_device}` : "—";
  }
  pill.append(k, v);
  label.appendChild(pill);
  // The slots-active-ace chip in the toolheads section header — keep showing
  // active for the toolheads view, since toolheads belong to a single printer.
  const slotsBadge = document.getElementById("slots-active-ace");
  if (slotsBadge) {
    slotsBadge.textContent = state.active_device !== null ? `ACE ${state.active_device}` : "no ACE";
  }
}

// ACE color is 0xAARRGGBB (or 0xFFRRGGBB on real data); low 24 bits = RGB.
// Treat null/undefined/0 as "no color set". Don't special-case 0xFFFFFFFF —
// that's the value real white-filament spools report, and treating it as a
// sentinel hides the swatch (makes T2 look blank when loaded with white PLA).
function rgbFromUint(packed) {
  if (packed == null || packed === 0) return null;
  const r = (packed >>> 16) & 0xff;
  const g = (packed >>> 8) & 0xff;
  const b = packed & 0xff;
  return `rgb(${r},${g},${b})`;
}

// UI labels are 1-based even though internal indices stay 0-based. Macros,
// audit events, and Klipper g-code (T0..T3) keep using the raw index — only
// human-visible text gets shifted.
function tName(i)    { return `T${+i}`; }
function slotName(i) { return `Slot ${+i}`; }

// Pick contrasting text color for a swatch background. Uses ITU-R BT.601
// perceived brightness so white/light filament gets dark text and dark
// filament gets white text. Returns null if input is unparseable so callers
// can fall back to their own default.
function textOnColor(rgb) {
  if (!rgb) return null;
  const m = /^rgb\((\d+),\s*(\d+),\s*(\d+)\)$/i.exec(rgb);
  if (!m) return null;
  const r = +m[1], g = +m[2], b = +m[3];
  const y = (r * 299 + g * 587 + b * 114) / 1000;
  return y > 150 ? "#0f172a" : "#fff";
}

// Expose helpers to hardware-twin.js (vanilla project, no module system).
window.MultiACEUtil = { rgbFromUint, tName, slotName, textOnColor };

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
  const container = document.getElementById("slots-grid");
  container.innerHTML = "";
  container.classList.add("slots-panel");
  const deviceCount = Math.max(state.device_count || 1, 1);
  for (let ace = 0; ace < deviceCount; ace++) {
    container.appendChild(renderAceBlock(ace));
  }
}

function renderAceBlock(ace) {
  const block = document.createElement("div");
  block.className = "ace-block";
  block.dataset.ace = String(ace);
  const isActive = (state.active_device === ace);
  if (isActive) block.classList.add("is-active");
  else block.classList.add("is-stale");

  const head = document.createElement("header");
  head.className = "ace-block-head";
  const label = document.createElement("span");
  label.className = "ace-label";
  // ACE A=0, ACE B=1, etc. — show both letter and index for clarity
  label.innerHTML = `ACE ${String.fromCharCode(65 + ace)} <span class="muted">(#${ace})</span>`;
  head.appendChild(label);
  const badge = document.createElement("span");
  badge.className = "badge";
  if (isActive) {
    badge.classList.add("badge-active");
    badge.textContent = "active";
  } else {
    badge.classList.add("badge-stale");
    badge.textContent = "stale";
  }
  head.appendChild(badge);
  block.appendChild(head);

  const slotsGrid = document.createElement("div");
  slotsGrid.className = "ace-block-slots";
  for (let i = 0; i < 4; i++) {
    slotsGrid.appendChild(renderSlotCard(ace, i));
  }
  block.appendChild(slotsGrid);
  return block;
}

function lowestFreeHead() {
  for (let h = 0; h < 4; h++) {
    if (!state.head_source[h] && !state.sensors[h]) return h;
  }
  return null;
}

/**
 * Classify a toolhead's current state relative to an optional target ACE.
 *
 * Returns one of:
 *   "empty"            — no head_source entry (head is unregistered)
 *   "parked"           — source.parked === true (filament retracted to ACE)
 *   "bookkeeping_empty" — source present but no physical filament sensor signal
 *   "loaded_cross_ace" — filament is from a different ACE than targetAce
 *   "loaded_same_ace"  — filament is from targetAce (or no targetAce specified)
 *
 * @param {number} headIdx  — 0-based head index
 * @param {number|null} targetAce — 0-based ACE index to compare against, or null
 * @returns {string} classification string
 */
function classifyHeadState(headIdx, targetAce = null) {
  const src = state.head_source[headIdx];
  const sensor = !!state.sensors[headIdx];

  if (!src) {
    return "empty";
  }
  if (src.parked === true) {
    return "parked";
  }
  if (!sensor) {
    return "bookkeeping_empty";
  }
  if (targetAce !== null && src.ace !== targetAce) {
    return "loaded_cross_ace";
  }
  return "loaded_same_ace";
}

/**
 * Return a human-readable reason why chevron actions should be disabled,
 * or null if actions are permitted.
 *
 * Priority order:
 *   1. Print in progress or paused — no operations allowed.
 *   2. A swap is already in progress (firmware is working).
 *   3. A smart-swap leg is pending in the UI state machine.
 *
 * @returns {string|null}
 */
function chevronGateReason() {
  const ps = printState.state;
  if (ps === "printing" || ps === "paused") {
    return `Print ${ps} — actions disabled`;
  }
  if (state.swap_in_progress) {
    return "Swap in progress — wait for completion";
  }
  if (state.smartSwapPending !== null) {
    const p = state.smartSwapPending;
    return `Smart-swap pending on ${tName(p.head)} leg ${p.leg} — wait for completion`;
  }
  return null;
}

function buildFilamentHubPickerUrl(ace, slot) {
  const base = window.MULTIACE_FH_URL;
  if (!base) return null;
  const pid = encodeURIComponent(window.MULTIACE_FH_PRINTER_ID || "u1-1");
  return `${base.replace(/\/$/, '')}/?picker=ace&printer=${pid}&ace=${ace}&slot=${slot}`;
}

/**
 * initiateSmartSwap — execute a load to targetHead from (targetAce, targetSlot),
 * branching per the head-state matrix.
 *
 * Head-state matrix:
 *   empty            → direct ACE_LOAD_HEAD, no toast
 *   loaded_same_ace  → toast + ACEC__Unload_T<n> → ACE_LOAD_HEAD
 *   loaded_cross_ace → if swapParkAvailable: toast + ACE_UNLOAD_HEAD LENGTH=600 → ACE_LOAD_HEAD
 *                      else: same as loaded_same_ace (fallback)
 *   parked           → conservative v1: same as loaded_same_ace branch
 *   bookkeeping_empty→ treated as loaded
 */
async function initiateSmartSwap(targetHead, targetAce, targetSlot, headState) {
  const gate = chevronGateReason();
  if (gate) { toast(gate, "error"); return; }

  // Empty head: direct load, no toast
  if (headState === "empty") {
    seedSingleHeadWorkflow("load_single", targetHead, `Load → ${tName(targetHead)}`);
    const ok = await sendScript(`ACE_LOAD_HEAD HEAD=${targetHead} ACE=${targetAce} SLOT=${targetSlot}`);
    if (!ok) {
      for (const s of workflow.steps) {
        if (s.status !== "done") { s.status = "failed"; s.error = "command rejected"; s.ended_at = _now(); }
      }
      renderWorkflow();
    }
    return;
  }

  // Build toast text for displacement swap
  const src = state.head_source[targetHead];
  const srcAceLetter = String.fromCharCode(65 + src.ace);
  const srcSlotLabel = String(src.slot);
  const dstAceLetter = String.fromCharCode(65 + targetAce);
  // Slot labels are 0-based throughout the app (chevron menus, slot cards,
   // activity feed). The +1 here was a leftover from the 1-based labeling
   // era and made the swap-confirm toast say e.g. "Swap A1 → B2" when the
   // actual swap targets B slot 1 (mate-pair of T1).
  const dstSlotLabel = String(targetSlot);
  const swapLabel = `${srcAceLetter}${srcSlotLabel} → ${dstAceLetter}${dstSlotLabel}`;

  const usePark = state.swapParkAvailable && headState === "loaded_cross_ace";
  const timeEst = usePark ? "~4 min" : "~6 min";
  const toastText = headState === "parked"
    ? `Swap (parked ${srcAceLetter}${srcSlotLabel}) → ${dstAceLetter}${dstSlotLabel} (${timeEst})`
    : `Swap ${swapLabel} (${timeEst})`;

  _pendingSwapConfirm = showSwapConfirm({
    text: toastText,
    onConfirm: () => {
      _pendingSwapConfirm = null;
      _executeSmartSwapLeg1(targetHead, targetAce, targetSlot, usePark, 0);
    },
    onCancel: () => {
      _pendingSwapConfirm = null;
      toast(`Swap ${swapLabel} cancelled`, "info");
    },
  });
}

async function _executeSmartSwapLeg1(targetHead, targetAce, targetSlot, usePark, failCount) {
  state.smartSwapPending = { head: targetHead, leg: 1, startedAt: _now() };
  renderStatusBanner();

  let leg1Ok;
  if (usePark) {
    // Park retract — route through the ACEC__Park_T<n> macro so the length
    // is owned by [ace] config (default_park_retract_length_mm) rather than
    // hardcoded here. Keeps the firmware-side config the single source of
    // truth for retract length (revised 2026-05-10 from a hardcoded 600 mm
    // to a macro after the 600 mm retract caused cumulative cross-slot
    // drift past the ACE drive wheel — see ace.cfg comment for details).
    leg1Ok = await sendCommand(`ACEC__Park_T${targetHead}`);
  } else {
    seedSingleHeadWorkflow("unload_single", targetHead, `Unload ${tName(targetHead)}`);
    leg1Ok = await sendCommand(`ACEC__Unload_T${targetHead}`);
    if (!leg1Ok) {
      for (const s of workflow.steps) {
        if (s.status !== "done") { s.status = "failed"; s.error = "command rejected"; s.ended_at = _now(); }
      }
      renderWorkflow();
    }
  }

  if (!leg1Ok) {
    const newFailCount = failCount + 1;
    showSwapFailure(targetHead, 1,
      () => _executeSmartSwapLeg1(targetHead, targetAce, targetSlot, usePark, newFailCount),
      newFailCount
    );
    return;
  }

  // Wait for leg 1's effect on head_source to propagate before dispatching
  // leg 2. Without this, the server preflight on leg 2 races against the
  // multiace_state.log tailer: leg 1's HTTP response returns the moment the
  // firmware's audit hits the log, but the tailer reads the file
  // asynchronously and may not have applied the new head_source yet when
  // the preflight runs — yielding a 409 "head busy" on a head we *just*
  // unloaded. See the T6 swap-park test on 2026-05-10.
  await _waitForSwapLeg1Propagation(targetHead);

  _executeSmartSwapLeg2(targetHead, targetAce, targetSlot, 0);
}

async function _waitForSwapLeg1Propagation(targetHead) {
  const DEADLINE_MS = 5000;
  const t0 = Date.now();
  while (Date.now() - t0 < DEADLINE_MS) {
    const src = state.head_source[targetHead];
    // Leg 1 succeeded if the source is now null (full unload cleared it)
    // OR has parked=true (LENGTH= retract path). Either way the preflight
    // will allow leg 2.
    if (!src || src.parked === true) return;
    await new Promise(r => setTimeout(r, 100));
  }
  // Defensive: proceed anyway. If leg 2 then 409s, the existing
  // showSwapFailure path surfaces it.
}

async function _executeSmartSwapLeg2(targetHead, targetAce, targetSlot, failCount) {
  state.smartSwapPending = { head: targetHead, leg: 2, startedAt: _now() };
  renderStatusBanner();
  seedSingleHeadWorkflow("load_single", targetHead, `Load → ${tName(targetHead)}`);
  const leg2Ok = await sendScript(`ACE_LOAD_HEAD HEAD=${targetHead} ACE=${targetAce} SLOT=${targetSlot}`);

  if (!leg2Ok) {
    for (const s of workflow.steps) {
      if (s.status !== "done") { s.status = "failed"; s.error = "command rejected"; s.ended_at = _now(); }
    }
    renderWorkflow();
    const newFailCount = failCount + 1;
    showSwapFailure(targetHead, 2,
      () => _executeSmartSwapLeg2(targetHead, targetAce, targetSlot, newFailCount),
      newFailCount
    );
    return;
  }

  state.smartSwapPending = null;
  renderStatusBanner();
}

function openHeadTargetMenu(anchor, ace, slotIdx) {
  document.querySelectorAll(".head-target-menu").forEach(el => el.remove());
  const menu = document.createElement("div");
  menu.className = "head-target-menu";

  const gateReason = chevronGateReason();

  // ---- Unload items: one per head that sources from this (ace, slot) ----
  for (let h = 0; h < 4; h++) {
    const src = state.head_source[h];
    if (!src || src.ace !== ace || src.slot !== slotIdx) continue;

    const hc = classifyHeadState(h);
    if (hc === "empty") continue;

    const item = document.createElement("button");
    item.className = "head-target-menu-item";

    if (gateReason) {
      item.disabled = true;
      item.title = gateReason;
      item.textContent = `↗ Unload ${tName(h)} (gated)`;
    } else {
      item.textContent = `↗ Unload ${tName(h)}`;
      if (hc === "bookkeeping_empty") {
        item.title = `⚠ Sensor disagrees with bookkeeping. Unload may fail; recovery: ACE_CLEAR_HEADS HEAD=${h} from gcode console.`;
      }
      item.addEventListener("click", async () => {
        menu.remove();
        seedSingleHeadWorkflow("unload_single", h, `Unload ${tName(h)}`);
        const ok = await sendCommand(`ACEC__Unload_T${h}`);
        if (!ok) {
          for (const s of workflow.steps) {
            if (s.status !== "done") { s.status = "failed"; s.error = "command rejected"; s.ended_at = _now(); }
          }
          renderWorkflow();
        }
      });
    }
    menu.appendChild(item);
  }

  // ---- Load item: only the mate-pair head ----
  // The U1 has 4 toolheads, each with its own splitter Y-junction connecting
  // ACE A slot N and ACE B slot N (matching index). Slot N's filament can
  // ONLY physically reach toolhead N — the bowden geometry is fixed. Offering
  // → T<other> would be a non-physical operation (firmware allows it but the
  // result is unpredictable). Render only the single valid mate-pair head.
  //
  // Suppress entirely when this exact slot already actively feeds the
  // mate-pair head (same ace+slot, not parked) — Load would be a no-op
  // refresh and the Unload row above already lets the user act on it.
  // Keep the Load row when parked=true (re-engage from park).
  {
    const h = slotIdx;
    const currentSrc = state.head_source[h];
    const alreadyActiveHere =
      currentSrc &&
      currentSrc.ace === ace &&
      currentSrc.slot === slotIdx &&
      !currentSrc.parked;

    if (!alreadyActiveHere) {
      // Separator before the Load row, but only if some Unload items were
      // already added (otherwise it would lead the menu).
      if (menu.children.length > 0) {
        const sep = document.createElement("hr");
        sep.className = "head-target-menu-sep";
        menu.appendChild(sep);
      }

      const hc = classifyHeadState(h, ace);
      const item = document.createElement("button");
      item.className = "head-target-menu-item";

      if (gateReason) {
        item.disabled = true;
        item.title = gateReason;
        item.textContent = `→ ${tName(h)} (gated)`;
      } else {
        const label = hc === "empty"
          ? `→ ${tName(h)}`
          : `→ ${tName(h)} (swap)`;
        item.textContent = label;
        if (hc !== "empty") {
          const src = state.head_source[h];
          const srcAceLetter = String.fromCharCode(65 + src.ace);
          item.title = `Will displace ${srcAceLetter}${src.slot} currently loaded in ${tName(h)}`;
        }
        item.addEventListener("click", async () => {
          menu.remove();
          await initiateSmartSwap(h, ace, slotIdx, hc);
        });
      }
      menu.appendChild(item);
    }
  }

  // Position and dismiss logic. Append first (offscreen) so we can measure
  // the menu's actual rendered size before clamping to the viewport — on
  // mobile (e.g., 390px wide) the chevron is often within 30 px of the
  // right edge, so the default "anchor.left + menu.width" placement
  // pushes the menu off-screen and the user can't read or tap the items.
  const r = anchor.getBoundingClientRect();
  menu.style.position = "fixed";
  menu.style.top = "-9999px";
  menu.style.left = "0";
  menu.style.zIndex = "1000";
  document.body.appendChild(menu);

  const VIEWPORT_MARGIN = 8;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const mr = menu.getBoundingClientRect();

  // Horizontal: prefer anchor.left, but if that would clip the right edge,
  // shift left so menu.right aligns with viewport - margin. Then clamp to
  // viewport left if still off-screen (very narrow viewport vs. wide menu).
  let left = r.left;
  if (left + mr.width + VIEWPORT_MARGIN > vw) {
    left = Math.max(VIEWPORT_MARGIN, vw - mr.width - VIEWPORT_MARGIN);
  }
  if (left < VIEWPORT_MARGIN) left = VIEWPORT_MARGIN;

  // Vertical: prefer below anchor; if that would clip the bottom, open
  // upward instead (so the menu's bottom is just above the anchor).
  let top = r.bottom + 4;
  if (top + mr.height + VIEWPORT_MARGIN > vh) {
    const upward = r.top - mr.height - 4;
    top = upward >= VIEWPORT_MARGIN ? upward : Math.max(VIEWPORT_MARGIN, vh - mr.height - VIEWPORT_MARGIN);
  }

  menu.style.left = `${left}px`;
  menu.style.top = `${top}px`;

  setTimeout(() => {
    function onDocClick(ev) {
      if (!menu.contains(ev.target)) {
        menu.remove();
        document.removeEventListener("click", onDocClick);
      }
    }
    document.addEventListener("click", onDocClick);
  }, 0);
}

function renderSlotCard(ace, slotIdx) {
  // Reuses the existing slot-card markup. For inactive ACEs, gate_status is
  // unknown — show "?" pill. For active ACE, use the existing logic.
  const isActive = state.active_device === ace;
  const filled = isActive ? (state.gate_status[slotIdx] === 1) : null;
  // head_source is global — find any head sourced from (ace, slotIdx)
  const loadedToEntry = Object.entries(state.head_source).find(
    ([, src]) => src && src.ace === ace && src.slot === slotIdx
  );
  const loadedToHead = loadedToEntry ? loadedToEntry[0] : null;
  const hcfg = loadedToHead != null ? (state.print_task_config[loadedToHead] || {}) : {};
  const color = rgbFromUint(hcfg.color);

  const card = document.createElement("div");
  card.className = "card" + (color ? "" : " no-color");
  if (color) card.style.setProperty("--card-color", color);
  setEl(card, "div", { className: "color-band" });

  const head = setEl(card, "div"); head.className = "card-head";
  setEl(head, "span", { className: "card-id", textContent: slotName(slotIdx) });
  setEl(head, "span", { className: "card-swatch" });
  if (filled === true) pill(head, "Filled", "ok");
  else if (filled === false) pill(head, "Empty");
  else pill(head, "?");  // unknown gate_status (inactive ACE)

  // Parked badge — shown when this slot's filament is parked in the bowden
  if (loadedToEntry) {
    const loadedSrc = state.head_source[loadedToHead];
    if (loadedSrc && loadedSrc.parked === true) {
      card.classList.add("parked");
      pill(head, "Parked", "parked-badge");
    }
  }

  const meta = setEl(card, "div"); meta.className = "card-meta";
  metaRow(meta, "Feeding", loadedToHead != null ? tName(loadedToHead) : "—");
  metaRow(meta, "Material", hcfg.type || "—");
  metaRow(meta, "Vendor", hcfg.vendor || "—");

  // Spool binding from spool_cache (Task 8 broadcast)
  const cacheForAce = (state.spool_cache && state.spool_cache[String(ace)]) || {};
  const spool = cacheForAce[String(slotIdx)] || null;

  // Actions: 📖 picker, Load split-button + chevron, Unload (when applicable)
  const actions = setEl(card, "div"); actions.className = "actions";

  const pickerBtn = setEl(actions, "button", { textContent: "📖" });
  pickerBtn.className = "btn-icon";
  const fhUrl = buildFilamentHubPickerUrl(ace, slotIdx);
  if (fhUrl) {
    pickerBtn.title = "Pick spool from FilamentHub";
    pickerBtn.addEventListener("click", () => {
      window.open(fhUrl, "_blank", "noopener,noreferrer");
    });
  } else {
    pickerBtn.disabled = true;
    pickerBtn.title = "Set FILAMENTHUB_URL to enable";
  }

  const split = setEl(actions, "span"); split.className = "slot-load-split";
  const loadBtn = setEl(split, "button", { textContent: "Load" });
  loadBtn.classList.add("primary");
  loadBtn.disabled = (filled === false) || state.swap_in_progress;
  loadBtn.addEventListener("click", async () => {
    const head = lowestFreeHead();
    if (head === null) {
      openHeadTargetMenu(loadBtn, ace, slotIdx);
      return;
    }
    await sendScript(`ACE_LOAD_HEAD HEAD=${head} ACE=${ace} SLOT=${slotIdx}`);
  });
  const chevron = setEl(split, "button", { textContent: "▾" });
  chevron.classList.add("primary");
  chevron.title = "Load to a specific head";
  chevron.disabled = (filled === false) || state.swap_in_progress;
  chevron.addEventListener("click", () => openHeadTargetMenu(chevron, ace, slotIdx));

  if (loadedToEntry) {
    const unloadBtn = setEl(actions, "button", { textContent: `Unload ${tName(loadedToHead)}` });
    unloadBtn.dataset.cmd = `ACEC__Unload_T${loadedToHead}`;
    unloadBtn.dataset.confirm = `Unload ${tName(loadedToHead)}?`;
    unloadBtn.classList.add("danger");
    unloadBtn.disabled = state.swap_in_progress;
  }

  if (spool) {
    const spoolRow = setEl(card, "div"); spoolRow.className = "slot-spool";
    if (spool.color) {
      const sw = setEl(spoolRow, "span"); sw.className = "spool-swatch";
      sw.style.background = `#${spool.color}`;
    }
    setEl(spoolRow, "span", { className: "spool-name", textContent: spool.name || `#${spool.spool_id}` });
    if (spool.material) {
      setEl(spoolRow, "span", { className: "muted", textContent: ` · ${spool.material}` });
    }
    if (spool.weight_remaining_g != null) {
      setEl(spoolRow, "span", { className: "muted", textContent: ` · ${Math.round(spool.weight_remaining_g)}g` });
    }
  }

  return card;
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

    // Workflow step (if any) for this toolhead — drives card emphasis + bottom strip.
    const wfStep = workflow.active ? _findStep(i) : null;
    const wfRunning = wfStep && wfStep.status === "running";
    const wfFailed = wfStep && wfStep.status === "failed";

    const card = setEl(grid, "div");
    card.className = "card" + (color ? "" : " no-color")
      + (err || wfFailed ? " error" : "")
      + (activeHead === i ? " extruding" : "")
      + (wfRunning ? " in-workflow" : "");
    if (color) card.style.setProperty("--card-color", color);
    setEl(card, "div", { className: "color-band" });

    // Head: id + swatch + status pill
    const head = setEl(card, "div"); head.className = "card-head";
    setEl(head, "span", { className: "card-id", textContent: tName(i) });
    setEl(head, "span", { className: "card-swatch" });
    if (wfRunning) pill(head, _stepDescription(wfStep).replace("…",""), "warn");
    else if (wfFailed) pill(head, "Failed", "bad");
    else if (activeHead === i) pill(head, "Extruding", "ok");
    else if (err) pill(head, "Error", "bad");
    else if (src && sensor) pill(head, "Loaded", "ok");
    else if (src && !sensor) pill(head, "No Filament", "warn");
    else pill(head, "Idle");

    // Meta
    const meta = setEl(card, "div"); meta.className = "card-meta";
    metaRow(meta, "Material", cfg.type || "—");
    metaRow(meta, "Vendor", cfg.vendor || "—");
    metaRow(meta, "Source", src ? `ACE ${src.ace} · ${slotName(src.slot)}` : "—");
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

// Friendly labels for AUTODRY_* events emitted by the auto-dry subsystem.
// Falls through to the raw action code if not present here.
const AUTODRY_ACTION_LABELS = {
  AUTODRY_TRIGGERED:      "Auto-dry triggered",
  AUTODRY_DRY_RUN:        "Auto-dry would trigger (log mode)",
  AUTODRY_FINISHED:       "Auto-dry finished",
  AUTODRY_SKIPPED_PRINT:  "Auto-dry skipped — print active",
  AUTODRY_SKIPPED_SWAP:   "Auto-dry skipped — swap in progress",
  AUTODRY_SKIPPED_DAILY:  "Auto-dry skipped — daily cap reached",
  AUTODRY_FAILED_SENSOR:  "Auto-dry FAULT — sensor unreadable",
  AUTODRY_FAILED_LIMIT:   "Auto-dry FAULT — max run reached",
  AUTODRY_FAILED_DELTA:   "Auto-dry FAULT — RH delta too small",
  AUTODRY_FAULT_CLEARED:  "Auto-dry fault cleared",
  AUTODRY_FINISHED_AFTER_RESTART: "Auto-dry finished after restart",
};

function extractEventAce(ev) {
  // Per-event ACE attribution: params.ace > params.target_ace > active_device.
  const p = ev.params || {};
  if (typeof p.ace === "number") return p.ace;
  if (typeof p.target_ace === "number") return p.target_ace;
  if (typeof ev.active_device === "number") return ev.active_device;
  return null;
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
    const label = AUTODRY_ACTION_LABELS[action] || action;
    const isFail = action.endsWith("_FAILED") || action.startsWith("AUTODRY_FAILED");
    const isOk = !isFail && ["LOAD_HEAD", "UNLOAD_HEAD", "UNLOAD_ALL", "ACE_SWITCH"]
      .some((a) => action.startsWith(a));
    if (isFail) li.classList.add("fail");
    else if (isOk) li.classList.add("ok");
    setEl(li, "span", { className: "ts", textContent: ev.ts || "" });
    // ACE tag — quick visual attribution per row
    const ace = extractEventAce(ev);
    if (ace !== null) {
      setEl(li, "span", {
        className: "activity-ace-tag",
        textContent: `ACE ${String.fromCharCode(65 + ace)}`,
      });
    }
    const right = setEl(li, "span");
    setEl(right, "span", { className: "action", textContent: label + " " });
    if (ev.params) {
      setEl(right, "span", { className: "params", textContent: JSON.stringify(ev.params) });
    }
  }
}

// Activity tab filter — null = All, integer = specific ACE
window._activityFilter = null;

function renderActivityChips() {
  const host = document.getElementById("activity-chips");
  if (!host) return;
  host.innerHTML = "";
  const deviceCount = Math.max(state.device_count || 1, 1);
  const filters = [{label: "All", val: null}];
  for (let i = 0; i < deviceCount; i++) {
    filters.push({label: `ACE ${String.fromCharCode(65 + i)}`, val: i});
  }
  for (const f of filters) {
    const btn = setEl(host, "button", { textContent: f.label });
    btn.className = "activity-ace-chip" + (window._activityFilter === f.val ? " is-on" : "");
    btn.addEventListener("click", () => {
      window._activityFilter = f.val;
      renderActivity();
    });
  }
}

function renderActivity() {
  const list = document.getElementById("activity-list");
  if (!list) return;
  renderActivityChips();
  const filter = window._activityFilter;
  let items = events.slice(0, 200);
  if (filter !== null) {
    items = items.filter(ev => extractEventAce(ev) === filter);
  }
  fillActivityList(list, items.slice(0, 50),
    filter === null
      ? "No activity yet. Trigger a Load/Unload to see events."
      : `No activity for ACE ${String.fromCharCode(65 + filter)} yet.`);
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

    // Per-ACE auto-maintenance subsection: shows enabled + keep_ready
    // toggles and the current FSM state. Fed by /api/autodry?ace=N.
    const autoSection = setEl(card, "div");
    autoSection.className = "dryer-auto-section";
    autoSection.dataset.ace = String(i);
    setEl(autoSection, "div", {
      className: "dryer-auto-head",
      innerHTML: `<strong>Auto-maintenance</strong> <span class="muted small" id="auto-state-${i}">…</span>`,
    });
    const enabledLbl = setEl(autoSection, "label"); enabledLbl.className = "dryer-auto-toggle";
    const enabledCb = setEl(enabledLbl, "input"); enabledCb.type = "checkbox";
    enabledCb.id = `auto-enabled-${i}`;
    setEl(enabledLbl, "span", {
      textContent: " Enabled — watch humidity and trigger dryer when threshold crossed",
    });
    const keepLbl = setEl(autoSection, "label"); keepLbl.className = "dryer-auto-toggle";
    const keepCb = setEl(keepLbl, "input"); keepCb.type = "checkbox";
    keepCb.id = `auto-keepready-${i}`;
    setEl(keepLbl, "span", {
      innerHTML: " Keep ready — maintain dryness even when no head is sourcing this ACE " +
                 "<span class='muted small'>(uses default-filament profile)</span>",
    });
    enabledCb.addEventListener("change", () => postAutodryAce(i, { enabled: enabledCb.checked }));
    keepCb.addEventListener("change", () => postAutodryAce(i, { keep_ready: keepCb.checked }));
    refreshDryerAutoSection(i);
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

// Hints for individual keys: tooltip/help text + input type constraints.
// Keys not listed here render as a plain text input with no help (existing
// behavior). Add hints incrementally for the calibration-critical ones —
// these are values where wrong settings have material physical consequences
// (filament past grip, blocked bowden, etc.).
const CONFIG_KEY_HINTS = {
  default_park_retract_length_mm: {
    help: "Park retract length (mm) for cross-ACE swap-park. Too short → blocks shared bowden between splitter and toolhead. Too long → filament drifts past ACE drive wheel (needs manual reseat). Davinci-U1 calibrated 700. Firmware bounds: 100-2000. Tune in ±50mm steps.",
    type: "number", min: 100, max: 2000, step: 50,
  },
  retract_length: {
    help: "Full-unload retract distance (mm). Calibrated to head→splitter+ACE-side full retract. Wrong values cause incomplete unloads.",
    type: "number", min: 500, max: 3000, step: 50,
  },
  retract_speed: {
    help: "Retract speed (mm/s).",
    type: "number", min: 5, max: 100, step: 1,
  },
  load_length: {
    help: "Full-load feed distance from ACE to head sensor (mm).",
    type: "number", min: 500, max: 3000, step: 50,
  },
  feed_speed: {
    help: "Feed speed (mm/s) for load/unload operations.",
    type: "number", min: 5, max: 100, step: 1,
  },
  tip_refresh_retract_length: {
    help: "Pre-load tip refresh: small retract distance (mm) to back off a deformed tip.",
    type: "number", min: 0, max: 200, step: 5,
  },
  tip_refresh_feed_length: {
    help: "Pre-load tip refresh: forward feed (mm) after retract to expose fresh filament.",
    type: "number", min: 0, max: 400, step: 5,
  },
  ace_device_count: {
    help: "Number of ACE Pro units. Required for multi-ACE setups so multiACE waits for all of them at startup.",
    type: "number", min: 1, max: 8, step: 1,
  },
};

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
    const hint = CONFIG_KEY_HINTS[k];
    if (hint) {
      input.type = hint.type || "text";
      if (hint.min != null) input.min = hint.min;
      if (hint.max != null) input.max = hint.max;
      if (hint.step != null) input.step = hint.step;
    } else {
      input.type = "text";
    }
    input.name = k;
    input.value = v;
    lbl.appendChild(span);
    lbl.appendChild(input);
    if (hint && hint.help) {
      const helpEl = document.createElement("small");
      helpEl.className = "config-help";
      helpEl.textContent = hint.help;
      lbl.appendChild(helpEl);
    }
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

// Diag tab — selected ACE for per-ACE blocks. null = legacy single-FSM view.
window._diagAce = 0;
// Per-ACE autodry data fetched on-demand when the dropdown changes.
let _diagAceState = null;

async function fetchDiagAceState(ace) {
  try {
    const r = await fetch(api(`api/autodry?ace=${ace}`), { headers: authHeader() });
    if (!r.ok) { _diagAceState = null; return; }
    _diagAceState = await r.json();
  } catch (_) {
    _diagAceState = null;
  }
}

function renderDiagAceDropdown() {
  const sel = document.getElementById("diag-ace");
  if (!sel) return;
  const deviceCount = Math.max(state.device_count || 1, 1);
  const current = window._diagAce ?? 0;
  // Repopulate only if device_count changed (preserves user selection)
  if (sel.options.length !== deviceCount) {
    sel.innerHTML = "";
    for (let i = 0; i < deviceCount; i++) {
      const opt = document.createElement("option");
      opt.value = String(i);
      opt.textContent = `ACE ${String.fromCharCode(65 + i)} (#${i})`;
      if (i === current) opt.selected = true;
      sel.appendChild(opt);
    }
    if (!sel.dataset.bound) {
      sel.addEventListener("change", async (ev) => {
        window._diagAce = parseInt(ev.target.value, 10);
        await fetchDiagAceState(window._diagAce);
        renderDiag();
      });
      sel.dataset.bound = "1";
    }
  }
}

function renderDiagPerAce() {
  const host = document.getElementById("diag-per-ace");
  if (!host) return;
  host.innerHTML = "";
  const ace = window._diagAce ?? 0;
  const isActive = state.active_device === ace;

  const block = setEl(host, "div"); block.className = "diag-ace-block";
  setEl(block, "h4", { textContent: `ACE ${String.fromCharCode(65 + ace)} (#${ace})` });

  // Connection status
  const status = setEl(block, "div"); status.className = "diag-row";
  setEl(status, "span", { className: "muted", textContent: "Status:" });
  setEl(status, "span", {
    textContent: isActive ? "active (currently connected)" : "inactive (last-known data)",
  });

  // Heads sourced from this ACE
  const headsRow = setEl(block, "div"); headsRow.className = "diag-row";
  setEl(headsRow, "span", { className: "muted", textContent: "Heads sourced:" });
  const headsList = Object.entries(state.head_source || {})
    .filter(([, src]) => src && src.ace === ace)
    .map(([h, src]) => `${tName(h)} ← ${slotName(src.slot)} (${src.type || "?"})`)
    .join(", ") || "none";
  setEl(headsRow, "span", { textContent: headsList });

  // Gate status (only meaningful for active ACE)
  if (isActive) {
    const gateRow = setEl(block, "div"); gateRow.className = "diag-row";
    setEl(gateRow, "span", { className: "muted", textContent: "Gate status:" });
    const gates = (state.gate_status || []).map((g, i) => `S${i+1}=${g === 1 ? "filled" : "empty"}`).join("  ");
    setEl(gateRow, "span", { textContent: gates });
  }

  // Per-ACE autodry FSM (fetched via /api/autodry?ace=N)
  setEl(block, "h5", { textContent: "Autodry FSM (per-ACE)" });
  const fsmPre = setEl(block, "pre");
  fsmPre.className = "diag-json";
  fsmPre.textContent = _diagAceState ? JSON.stringify(_diagAceState, null, 2) : "(not yet fetched — change dropdown to refresh)";
}

function renderDiag() {
  renderDiagAceDropdown();
  renderDiagPerAce();
  document.getElementById("diag-state").textContent =
    JSON.stringify(state, null, 2);

  // Auto-dry diagnostics — only renders when autodryState is loaded
  const fsmPre = document.getElementById("diag-autodry-fsm");
  const inputsPre = document.getElementById("diag-autodry-inputs");
  const persistedPre = document.getElementById("diag-autodry-persisted");
  if (fsmPre && inputsPre && persistedPre) {
    if (autodryState) {
      fsmPre.textContent = JSON.stringify(autodryState.fsm || {}, null, 2);
      // Derive a "current inputs" view from printState + state
      const lastPrint = printState || {};
      const hum = (lastPrint.humidity) || {};
      const dryer = (lastPrint.dryer) || {};
      const inputs = {
        humidity_ok: !!hum.ok,
        humidity_pct: hum.humidity_pct ?? null,
        cavity_temp_c: lastPrint.cavity_temp_c ?? null,
        klipper_print_state: lastPrint.state ?? "(unknown)",
        dryer_status: dryer.status ?? "(unknown)",
        active_device: state.active_device ?? null,
        head_source: state.head_source || {},
        swap_in_progress: !!state.swap_in_progress,
      };
      inputsPre.textContent = JSON.stringify(inputs, null, 2);
      const { fsm: _fsm, ...persisted } = autodryState;
      persistedPre.textContent = JSON.stringify(persisted, null, 2);
    } else {
      fsmPre.textContent = "(autodry status unavailable)";
      inputsPre.textContent = "(no data — open dashboard or check Moonraker reachability)";
      persistedPre.textContent = "(no data)";
    }
  }
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
  // Abort any pending swap-confirm toast when the user navigates away.
  if (_pendingSwapConfirm) {
    _pendingSwapConfirm.cancel();
    _pendingSwapConfirm = null;
  }
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

// Embed mode: when loaded inside Mainsail/Fluidd's iframe webcam panel, hide
// the global chrome (topbar + tab nav) and jump straight to the tab named in
// `?tab=`. Default embed tab is "hardware" since that's what fits best in the
// compact panel slot.
function applyEmbedMode() {
  const params = new URLSearchParams(location.search);
  if (params.get("embed") !== "1") return null;
  document.body.classList.add("multiace-embed");
  const tab = params.get("tab") || "hardware";
  return tab;
}

document.addEventListener("DOMContentLoaded", () => {
  for (const tab of document.querySelectorAll(".tab")) {
    if (!tab.dataset.view) continue;  // skip help button and other non-view tabs
    tab.addEventListener("click", () => setView(tab.dataset.view));
  }
  const embedTab = applyEmbedMode();
  if (embedTab) setView(embedTab);
  const helpBtn = document.getElementById("help-btn");
  if (helpBtn) helpBtn.addEventListener("click", openHelp);
  const helpClose = document.getElementById("help-close");
  if (helpClose) helpClose.addEventListener("click", closeHelp);
  const helpModal = document.getElementById("help-modal");
  if (helpModal) helpModal.addEventListener("click", (ev) => {
    if (ev.target === helpModal) closeHelp();  // click outside the card closes
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && !helpModal.classList.contains("hidden")) closeHelp();
  });
  // Bind any data-cmd buttons (action bar, diag panel)
  document.body.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("[data-cmd]");
    if (!btn) return;
    const macro = btn.dataset.cmd;
    const confirm = btn.dataset.confirm;
    if (confirm && !(await confirmDialog(confirm))) return;
    btn.disabled = true;
    // Pre-seed the workflow panel before the POST so the user gets instant
    // feedback. The actual state events that arrive over WS will then update
    // the seeded steps in place.
    let seeded = false;
    if (macro === "ACEC__Unload_All") {
      seeded = seedUnloadAllWorkflow();
    } else {
      const m = macro.match(/^ACEC__Unload_T(\d+)$/);
      const ml = macro.match(/^ACEC__Load_T(\d+)$/);
      if (m) seedSingleHeadWorkflow("unload_single", parseInt(m[1], 10), `Unload T${m[1]}`);
      else if (ml) seedSingleHeadWorkflow("load_single", parseInt(ml[1], 10), `Load → T${ml[1]}`);
    }
    const ok = await sendCommand(macro);
    if (!ok && seeded) {
      // Seeded panel for a command that bounced; fail the workflow visibly.
      for (const s of workflow.steps) {
        if (s.status !== "done") { s.status = "failed"; s.error = "command rejected"; s.ended_at = _now(); }
      }
      renderWorkflow();
    }
    btn.disabled = false;
  });
  // "View all →" link inside the dashboard's activity preview switches to the Activity tab.
  const viewAll = document.getElementById("activity-more");
  if (viewAll) {
    viewAll.addEventListener("click", (ev) => { ev.preventDefault(); setView("activity"); });
  }
  loadWebConfig();
  connectWS();
  startPrintPolling();
  startAutodryPolling();

  // Print queue tab — start poll when tab is active
  document.querySelectorAll(".tab").forEach(btn => {
    if (btn.dataset.view === "print-queue") {
      btn.addEventListener("click", () => {
        fetchPrintQueue();
        if (!_pqPollTimer) {
          _pqPollTimer = setInterval(fetchPrintQueue, 10000);
        }
      });
    }
  });

  document.getElementById("pq-refresh-btn")?.addEventListener("click", fetchPrintQueue);
  document.getElementById("fix-loadout-close")?.addEventListener("click", closeFixLoadoutWizard);

  // Eagerly fetch per-ACE autodry state so the Diag tab has data on first
  // open even if the user hasn't touched the dropdown yet.
  fetchDiagAceState(window._diagAce ?? 0).then(() => renderDiag());
});
