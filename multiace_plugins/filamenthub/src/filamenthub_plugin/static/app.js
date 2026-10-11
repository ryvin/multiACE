const PLUGIN = "";               // same dir: /plugin/filamenthub/
const $ = (s) => document.querySelector(s);
const setStatus = (m) => { $("#status").textContent = m || ""; };

let spools = [];                 // cached inventory
let target = null;               // {ace, slot} being edited

async function jget(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} → ${r.status}`);
  return r.json();
}
async function jpost(url, body) {
  const r = await fetch(url, { method: "POST",
    headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${url} → ${r.status}`);
  return r.json();
}

// 0-based lane label matching the dashboard: A0…A3 / B0…B3.
function laneLabel(ace, slot) {
  return `${String.fromCharCode(65 + ace)}${slot}`;
}

function slotCard(row) {
  const { ace, slot, recon_state, display_name, display_color, observed } = row;
  const el = document.createElement("button");
  el.className = "slot";

  const swatch = document.createElement("span");
  swatch.className = "swatch";
  swatch.style.backgroundColor = display_color || (observed && observed.color) || "transparent";
  el.appendChild(swatch);

  const name = document.createElement("span");
  name.className = "name";

  switch (recon_state) {
    case "VERIFIED":
      name.textContent = `${display_name} ✓`;
      break;
    case "ASSERTED": {
      name.textContent = display_name;
      const dot = document.createElement("span");
      dot.className = "dot-unverified";
      dot.title = "unverified";
      name.appendChild(dot);
      break;
    }
    case "EXPECTED_NOT_LOADED":
      el.classList.add("ghost");
      swatch.classList.add("desaturated");
      name.textContent = `Expected: ${display_name} — not loaded`;
      break;
    case "UNKNOWN_LOADED":
      el.classList.add("unknown");
      name.textContent = "Filament present — unknown · tap to identify";
      break;
    case "CONFLICT":
      el.classList.add("conflict");
      name.textContent = `Tag: ${(observed && observed.material) || "?"} · Hub: ${display_name}`;
      break;
    default: // EMPTY
      el.classList.add("empty");
      name.textContent = "Empty";
  }
  el.appendChild(name);

  const meta = document.createElement("span");
  meta.className = "meta";
  meta.textContent = laneLabel(ace, slot);
  el.appendChild(meta);

  el.addEventListener("click", () => openPicker(ace, slot));
  return el;
}

async function render() {
  setStatus("Loading…");
  try {
    const [slotsRes, inv] = await Promise.all([
      jget(`${PLUGIN}slots`), jget(`${PLUGIN}spools`)]);
    spools = inv.spools || [];
    const grid = $("#grid"); grid.innerHTML = "";
    let currentAce = null;
    (slotsRes.slots || []).forEach((row) => {
      if (row.ace !== currentAce) {
        currentAce = row.ace;
        const h = document.createElement("div");
        h.className = "ace-group";
        h.textContent = `ACE ${String.fromCharCode(65 + row.ace)}`;
        grid.appendChild(h);
      }
      grid.appendChild(slotCard(row));
    });
    let statusMsg = `${spools.length} spools in FilamentHub`;
    if (slotsRes.observed_ok === false) {
      statusMsg = `printer state unavailable — showing expected loadout · ${statusMsg}`;
    }
    setStatus(statusMsg);
  } catch (e) { setStatus(`Error: ${e.message}`); }
}

function openPicker(ace, slot) {
  target = { ace, slot };
  $("#picker-title").textContent = laneLabel(ace, slot);
  $("#filter").value = "";
  renderSpoolList("");
  $("#picker").showModal();
}

function renderSpoolList(q) {
  const ul = $("#spool-list"); ul.innerHTML = "";
  const needle = q.toLowerCase();
  spools.filter((s) => !needle ||
      `${s.name} ${s.material} ${s.vendor}`.toLowerCase().includes(needle))
    .forEach((s) => {
      const color = s.color ? (s.color.startsWith("#") ? s.color : `#${s.color}`) : "transparent";
      const li = document.createElement("li");

      const swatch = document.createElement("span");
      swatch.className = "swatch";
      swatch.style.backgroundColor = color;
      li.appendChild(swatch);

      const info = document.createElement("span");
      const strong = document.createElement("strong");
      strong.textContent = s.name || "?";
      info.appendChild(strong);
      info.appendChild(document.createTextNode(
        ` — ${s.material || ""} ${s.vendor ? "· " + s.vendor : ""}`));
      li.appendChild(info);

      li.addEventListener("click", () => assign(s.spool_id));
      ul.appendChild(li);
    });
}

async function assign(spoolId) {
  setStatus("Assigning…");
  $("#picker").close();
  try {
    await jpost(`${PLUGIN}assign`, { spool_id: spoolId, ace: target.ace, slot: target.slot });
    await render();
  } catch (e) { setStatus(`Assign failed: ${e.message}`); }
}

async function clearSlot() {
  setStatus("Clearing…");
  $("#picker").close();
  try {
    await jpost(`${PLUGIN}unassign`, { ace: target.ace, slot: target.slot });
    await render();
  } catch (e) { setStatus(`Clear failed: ${e.message}`); }
}

function renderDisputes(disputed) {
  const box = $("#disputes");
  box.innerHTML = "";
  if (!disputed || !disputed.length) { box.hidden = true; return; }
  box.hidden = false;
  const h = document.createElement("strong");
  h.textContent = `${disputed.length} disputed slot(s) — resolve in FilamentHub:`;
  box.appendChild(h);
  const ul = document.createElement("ul");
  disputed.forEach((d) => {
    const li = document.createElement("li");
    li.textContent = `${laneLabel(d.ace, d.slot)}: spool ${d.spool_id} ` +
                     `also claims this (winner: ${d.winner_spool_id})`;
    ul.appendChild(li);
  });
  box.appendChild(ul);
}

// prune=true → full reconcile incl. destructive clears (explicit button).
// prune=false → additive-only, used by auto-pull-on-open so a transient seam
// drop can't churn or delete valid labels.
async function pull(prune = true) {
  setStatus(prune ? "Pulling from FilamentHub…" : "Syncing labels from FilamentHub…");
  try {
    const res = await jpost(`${PLUGIN}pull`, { prune });
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
    setStatus(msg);
    await render();   // refresh the grid to show the new labels
  } catch (e) {
    setStatus(`Pull failed: ${e.message}`);
  }
}

$("#refresh").addEventListener("click", render);
$("#filter").addEventListener("input", (e) => renderSpoolList(e.target.value));
$("#clear-slot").addEventListener("click", clearSlot);
$("#cancel").addEventListener("click", () => $("#picker").close());
$("#pull").addEventListener("click", () => pull(true));
render().then(() => pull(false));
