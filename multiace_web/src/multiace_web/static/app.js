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
  renderDiag();
}
function renderTopbar() {
  document.getElementById("active-ace-label").textContent =
    state.active_device !== null ? `ACE ${state.active_device}` : "—";
  document.getElementById("slots-active-ace").textContent =
    state.active_device !== null ? `(ACE ${state.active_device})` : "(none)";
}
function renderSlots() { /* impl in Task 14 */ }
function renderToolheads() { /* impl in Task 14 */ }
function renderActivity() { /* impl in Task 15 */ }
function renderActionBar() { /* impl in Task 16 */ }
function renderDiag() { /* impl in Task 17 */ }

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
