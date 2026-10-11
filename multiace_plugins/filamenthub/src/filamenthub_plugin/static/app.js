// License: GPL-3.0
// FilamentHub plugin UI — vanilla JS, no build step.
//
// Runs as an iframe at /plugin/filamenthub/, so every plugin fetch is a
// RELATIVE path (e.g. "filaments") that resolves under that prefix. Remote and
// user text is only ever written via textContent; innerHTML is used solely to
// clear containers.
"use strict";

const API = {
  slots: "slots",
  spools: "spools",
  filaments: "filaments",
  assign: "assign",
  assignProfile: "assign-profile",
  unassign: "unassign",
  pull: "pull",
};
const SLOTS_PER_ACE = 4;
const TOAST_MS = 4000;

const $ = (s) => document.querySelector(s);
const setStatus = (m) => { $("#status").textContent = m || ""; };

let spools = [];          // cached spool inventory
let filaments = null;     // cached profile list (lazy-loaded on first open)
let target = null;        // {ace, slot} being edited (0-based)
let opener = null;        // element to refocus when the picker closes
let busy = false;         // blocks double-submits while a write is in flight

// ---- helpers --------------------------------------------------------------

async function jget(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} → ${r.status}`);
  return r.json();
}

function errorDetail(body, fallback) {
  const d = body && body.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((x) => (x && x.msg) || String(x)).join("; ");
  return fallback;
}

async function jpost(url, body) {
  const r = await fetch(url, { method: "POST",
    headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(errorDetail(err, `${url} → ${r.status}`));
  }
  return r.json();
}

// API is 0-based; everything the user sees is 1-based.
const aceLabel = (ace) => `ACE ${ace + 1}`;
const slotLabel = (ace, slot) => `${aceLabel(ace)} · Slot ${slot + 1}`;

// Only accept plain hex colours from remote data; anything else → no swatch.
function cssColor(c) {
  if (typeof c !== "string") return null;
  const m = c.trim().match(/^#?([0-9a-f]{6}|[0-9a-f]{8}|[0-9a-f]{3})$/i);
  return m ? `#${m[1].slice(0, 6)}` : null;
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = String(text);
  return e;
}

function swatch(color, extraCls) {
  const s = el("span", `swatch${extraCls ? " " + extraCls : ""}`);
  const c = cssColor(color);
  if (c) s.style.backgroundColor = c; else s.classList.add("swatch-none");
  s.setAttribute("aria-hidden", "true");
  return s;
}

let toastTimer = null;
function toast(msg, kind = "ok") {
  const t = $("#toast");
  t.textContent = msg;
  t.className = `toast toast-${kind}`;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, TOAST_MS);
}

// ---- slot grid ------------------------------------------------------------

const STATES = {
  VERIFIED:            { badge: "Verified",       cls: "st-verified",
                         help: "RFID tag matches the assigned filament" },
  ASSERTED:            { badge: "Assigned",       cls: "st-asserted",
                         help: "Loaded and assigned; no RFID to verify" },
  EXPECTED_NOT_LOADED: { badge: "Not loaded",     cls: "st-expected",
                         help: "Assigned in FilamentHub but the slot is empty" },
  UNKNOWN_LOADED:      { badge: "Unknown",        cls: "st-unknown",
                         help: "Filament present but not assigned — tap to identify" },
  CONFLICT:            { badge: "Conflict",       cls: "st-conflict",
                         help: "RFID tag disagrees with the assigned filament" },
  EMPTY:               { badge: "Empty",          cls: "st-empty",
                         help: "Nothing assigned or loaded" },
  NO_DATA:             { badge: "No printer data", cls: "st-nodata",
                         help: "Printer state unavailable" },
};

function observedText(o) {
  if (!o) return "Printer: no data";
  if (o.state === "empty") return "Printer: empty";
  const bits = [o.material || "unknown material"];
  if (cssColor(o.color)) bits.push(cssColor(o.color));
  if (o.rfid === 1) bits.push("RFID");
  return `Printer: ${bits.join(" · ")}`;
}

function desiredText(d) {
  if (!d) return "Hub: not assigned";
  const name = [d.brand, d.subtype].filter(Boolean).join(" ");
  return `Hub: ${[d.material, name].filter(Boolean).join(" · ") || "assigned"}`;
}

function slotCard(row) {
  const { ace, slot, recon_state, display_name, display_material,
          display_color, desired, observed } = row;
  const st = STATES[recon_state] || STATES.EMPTY;
  const card = el("button", `slot ${st.cls}`);
  card.type = "button";
  card.dataset.ace = ace;
  card.dataset.slot = slot;

  const top = el("span", "slot-top");
  top.appendChild(el("span", "slot-num", `Slot ${slot + 1}`));
  const badge = el("span", "badge", st.badge);
  badge.title = st.help;
  top.appendChild(badge);
  card.appendChild(top);

  const color = display_color || (observed && observed.color);
  card.appendChild(swatch(color, recon_state === "EXPECTED_NOT_LOADED" ? "desaturated" : ""));

  const title = display_material || display_name
    ? [display_material, display_name].filter(Boolean).join(" · ")
    : st.badge;
  card.appendChild(el("span", "slot-title", title));
  card.appendChild(el("span", "slot-line", desiredText(desired)));
  card.appendChild(el("span", "slot-line", observedText(observed)));

  card.setAttribute("aria-label",
    `${slotLabel(ace, slot)}: ${st.badge}. ${title}. ${st.help}. Open picker`);
  card.addEventListener("click", () => openPicker(row, card));
  return card;
}

// Every ACE shows all its slots even when /slots only knows some of them.
function fillRows(rows, observedOk) {
  const byAce = new Map();
  rows.forEach((r) => {
    if (!byAce.has(r.ace)) byAce.set(r.ace, new Map());
    byAce.get(r.ace).set(r.slot, r);
  });
  const aces = [...byAce.keys()].sort((a, b) => a - b);
  return aces.map((ace) => {
    const known = byAce.get(ace);
    const n = Math.max(SLOTS_PER_ACE, ...[...known.keys()].map((s) => s + 1));
    const slots = [];
    for (let s = 0; s < n; s++) {
      slots.push(known.get(s) || { ace, slot: s,
        recon_state: observedOk ? "EMPTY" : "NO_DATA",
        display_name: "", display_material: "", display_color: "",
        desired: null, observed: null });
    }
    return { ace, slots };
  });
}

function renderGrid(slotsRes) {
  const grid = $("#grid");
  grid.innerHTML = "";
  const groups = fillRows(slotsRes.slots || [], slotsRes.observed_ok !== false);
  if (!groups.length) {
    grid.appendChild(el("p", "empty-note", "No ACE slots reported yet."));
    return;
  }
  groups.forEach(({ ace, slots }) => {
    const sec = el("section", "ace");
    const h = el("h2", "ace-title", aceLabel(ace));
    h.id = `ace-${ace}`;
    sec.setAttribute("aria-labelledby", h.id);
    sec.appendChild(h);
    const list = el("div", "slots");
    slots.forEach((r) => list.appendChild(slotCard(r)));
    sec.appendChild(list);
    grid.appendChild(sec);
  });
}

async function render() {
  setStatus("Loading…");
  try {
    const [slotsRes, inv] = await Promise.all([jget(API.slots), jget(API.spools)]);
    spools = inv.spools || [];
    renderGrid(slotsRes);
    let msg = `${spools.length} spools in FilamentHub`;
    if (slotsRes.observed_ok === false) {
      msg = `Printer state unavailable — showing expected loadout · ${msg}`;
    }
    setStatus(msg);
  } catch (e) { setStatus(`Error: ${e.message}`); }
}

// ---- picker ---------------------------------------------------------------

function selectTab(name) {
  ["spools", "profiles"].forEach((n) => {
    const on = n === name;
    const tab = $(`#tab-${n}`);
    tab.setAttribute("aria-selected", String(on));
    tab.tabIndex = on ? 0 : -1;
    $(`#panel-${n}`).hidden = !on;
  });
  if (name === "profiles") loadProfiles();
  $(name === "spools" ? "#filter" : "#profile-filter").focus();
}

function onTabKey(e) {
  if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
  e.preventDefault();
  const next = $("#tab-spools").getAttribute("aria-selected") === "true"
    ? "profiles" : "spools";
  selectTab(next);
  $(`#tab-${next}`).focus();
}

function openPicker(row, card) {
  target = { ace: row.ace, slot: row.slot };
  opener = card || null;
  $("#picker-title").textContent = slotLabel(row.ace, row.slot);
  $("#picker-current").textContent =
    `${desiredText(row.desired)} — ${observedText(row.observed)}`;
  $("#filter").value = "";
  $("#profile-filter").value = "";
  $("#create-spool").checked = false;
  renderSpoolList("");
  $("#picker").showModal();
  selectTab("spools");
}

function closePicker() {
  const d = $("#picker");
  if (d.open) d.close();
}

function pickButton({ color, title, sub, aside, onPick }) {
  const b = el("button", "pick-item");
  b.type = "button";
  b.appendChild(swatch(color));
  const info = el("span", "pick-info");
  info.appendChild(el("span", "pick-title", title));
  if (sub) info.appendChild(el("span", "pick-sub", sub));
  b.appendChild(info);
  if (aside) b.appendChild(el("span", "pick-aside", aside));
  b.addEventListener("click", onPick);
  return b;
}

function matches(needle, ...fields) {
  return !needle || fields.join(" ").toLowerCase().includes(needle);
}

function renderSpoolList(q) {
  const ul = $("#spool-list");
  ul.innerHTML = "";
  const needle = q.trim().toLowerCase();
  const hits = spools.filter((s) => matches(needle, s.name, s.material, s.vendor));
  if (!hits.length) {
    ul.appendChild(el("li", "empty-note", spools.length ? "No matching spools." : "No spools in FilamentHub."));
    return;
  }
  hits.forEach((s) => {
    const li = el("li");
    const where = s.location ? ` · in ${slotLabel(s.location.ace, s.location.slot)}` : "";
    const grams = typeof s.weight_remaining_g === "number"
      ? `${Math.round(s.weight_remaining_g)} g` : "";
    li.appendChild(pickButton({
      color: s.color,
      title: s.name || `Spool #${s.spool_id}`,
      sub: `${[s.material, s.vendor].filter(Boolean).join(" · ")}${where}`,
      aside: grams,
      onPick: () => assignSpool(s.spool_id),
    }));
    ul.appendChild(li);
  });
}

async function loadProfiles() {
  if (filaments) { renderProfileList($("#profile-filter").value); return; }
  const box = $("#profile-list");
  box.innerHTML = "";
  box.appendChild(el("p", "empty-note", "Loading profiles…"));
  try {
    const res = await jget(API.filaments);
    filaments = res.filaments || [];
  } catch (e) {
    box.innerHTML = "";
    box.appendChild(el("p", "empty-note", `Could not load profiles: ${e.message}`));
    return;
  }
  renderProfileList($("#profile-filter").value);
}

function tempsText(f) {
  const t = [];
  if (f.extruder_temp != null) t.push(`Nozzle ${f.extruder_temp}°C`);
  if (f.bed_temp != null) t.push(`Bed ${f.bed_temp}°C`);
  return t.join(" · ");
}

function renderProfileList(q) {
  const box = $("#profile-list");
  box.innerHTML = "";
  if (!filaments) return;
  const needle = (q || "").trim().toLowerCase();
  const hits = filaments.filter((f) => matches(needle, f.name, f.material, f.vendor));
  if (!hits.length) {
    box.appendChild(el("p", "empty-note", filaments.length ? "No matching profiles." : "No profiles in FilamentHub."));
    return;
  }
  // Backend already sorts by vendor then name; group consecutive vendors.
  let ul = null;
  let vendor = Symbol("none");
  hits.forEach((f) => {
    const v = f.vendor || "Unknown vendor";
    if (v !== vendor) {
      vendor = v;
      box.appendChild(el("h3", "vendor", v));
      ul = el("ul", "pick-group");
      box.appendChild(ul);
    }
    const li = el("li");
    li.appendChild(pickButton({
      color: f.color,
      title: f.name || `Profile #${f.filament_id}`,
      sub: [f.material, tempsText(f)].filter(Boolean).join(" · "),
      onPick: () => assignProfile(f.filament_id),
    }));
    ul.appendChild(li);
  });
}

// ---- writes ---------------------------------------------------------------

async function runWrite(label, fn, okMsg) {
  if (busy || !target) return;
  busy = true;
  const where = slotLabel(target.ace, target.slot);
  closePicker();
  setStatus(`${label}…`);
  try {
    await fn();
    toast(`${okMsg} ${where}`, "ok");
    await render();
  } catch (e) {
    toast(`${label} failed (${where}): ${e.message}`, "error");
    setStatus(`${label} failed: ${e.message}`);
  } finally { busy = false; }
}

function assignSpool(spoolId) {
  const t = target;
  return runWrite("Assigning spool",
    () => jpost(API.assign, { spool_id: spoolId, ace: t.ace, slot: t.slot }),
    "Assigned spool to");
}

function assignProfile(filamentId) {
  const t = target;
  const createSpool = $("#create-spool").checked;
  return runWrite("Assigning profile",
    () => jpost(API.assignProfile, { filament_id: filamentId, ace: t.ace,
                                     slot: t.slot, create_spool: createSpool }),
    createSpool ? "Created spool and assigned it to" : "Assigned profile to");
}

function clearSlot() {
  const t = target;
  if (!t || !window.confirm(`Clear ${slotLabel(t.ace, t.slot)} in FilamentHub and multiACE?`)) return;
  return runWrite("Clearing slot",
    () => jpost(API.unassign, { ace: t.ace, slot: t.slot }), "Cleared");
}

// ---- disputes + pull ------------------------------------------------------

function renderDisputes(disputed) {
  const box = $("#disputes");
  box.innerHTML = "";
  if (!disputed || !disputed.length) { box.hidden = true; return; }
  box.hidden = false;
  box.appendChild(el("strong", null,
    `${disputed.length} disputed slot(s) — resolve in FilamentHub:`));
  const ul = el("ul");
  disputed.forEach((d) => {
    ul.appendChild(el("li", null,
      `${slotLabel(d.ace, d.slot)}: spool ${d.spool_id} also claims this ` +
      `(winner: ${d.winner_spool_id})`));
  });
  box.appendChild(ul);
}

// prune=true → full reconcile incl. destructive clears (explicit button).
// prune=false → additive-only, used by auto-pull-on-open so a transient seam
// drop can't churn or delete valid labels.
async function pull(prune = true) {
  setStatus(prune ? "Pulling from FilamentHub…" : "Syncing labels from FilamentHub…");
  try {
    const res = await jpost(API.pull, { prune });
    renderDisputes(res.disputed);
    let msg;
    if (res.reconciliation) {
      const r = res.reconciliation;
      msg = `Pulled: ${r.verified + r.asserted} identified, ` +
            `${r.expected_not_loaded} awaiting load, ${r.unknown_loaded} unidentified`;
    } else {
      const parts = [`applied ${res.applied.length}`];
      if (prune) parts.push(`cleared ${res.cleared.length}`);
      else if (res.stale && res.stale.length) parts.push(`${res.stale.length} stale (not cleared)`);
      if (res.errors.length) parts.push(`errors ${res.errors.length}`);
      msg = `${prune ? "Pull" : "Sync"} complete: ${parts.join(", ")}`;
    }
    if (res.warning) msg += ` — ${res.warning}`;
    if (prune) toast(msg, res.errors && res.errors.length ? "error" : "ok");
    await render();   // refresh the grid to show the new labels
    setStatus(msg);
  } catch (e) {
    setStatus(`Pull failed: ${e.message}`);
    if (prune) toast(`Pull failed: ${e.message}`, "error");
  }
}

// ---- wiring ---------------------------------------------------------------

$("#refresh").addEventListener("click", render);
$("#pull").addEventListener("click", () => pull(true));
$("#filter").addEventListener("input", (e) => renderSpoolList(e.target.value));
$("#profile-filter").addEventListener("input", (e) => renderProfileList(e.target.value));
$("#tab-spools").addEventListener("click", () => selectTab("spools"));
$("#tab-profiles").addEventListener("click", () => selectTab("profiles"));
$("#tab-spools").addEventListener("keydown", onTabKey);
$("#tab-profiles").addEventListener("keydown", onTabKey);
$("#clear-slot").addEventListener("click", clearSlot);
$("#cancel").addEventListener("click", closePicker);
$("#close-picker").addEventListener("click", closePicker);
// Native <dialog> closes on Escape; restore focus to the slot that opened it.
$("#picker").addEventListener("close", () => {
  if (opener && document.body.contains(opener)) opener.focus();
});
// Click on the backdrop (outside the dialog box) closes too.
$("#picker").addEventListener("click", (e) => {
  if (e.target === e.currentTarget) closePicker();
});

render().then(() => pull(false));
