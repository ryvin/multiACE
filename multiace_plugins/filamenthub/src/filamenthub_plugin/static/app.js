const MULTIACE_STATE = "/multiace/api/plugin-api/state";
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

function slotCard(ace, slot, occupied, label, color) {
  const el = document.createElement("button");
  el.className = "slot" + (occupied ? "" : " empty");

  const swatch = document.createElement("span");
  swatch.className = "swatch";
  swatch.style.backgroundColor = color || "transparent";
  el.appendChild(swatch);

  const name = document.createElement("span");
  name.className = "name";
  name.textContent = label || "empty";
  el.appendChild(name);

  const meta = document.createElement("span");
  meta.className = "meta";
  meta.textContent = `ACE ${ace + 1} · slot ${slot + 1}`;
  el.appendChild(meta);

  el.addEventListener("click", () => openPicker(ace, slot));
  return el;
}

async function render() {
  setStatus("Loading…");
  try {
    const [state, inv] = await Promise.all([
      jget(MULTIACE_STATE), jget(`${PLUGIN}spools`)]);
    spools = inv.spools || [];
    const grid = $("#grid"); grid.innerHTML = "";
    (state.aces || []).forEach((ace) => {
      const h = document.createElement("div");
      h.className = "ace-group"; h.textContent = `ACE ${ace.idx + 1}`;
      grid.appendChild(h);
      (ace.slots || []).forEach((s) => {
        const occupied = s.state !== "empty";
        grid.appendChild(slotCard(ace.idx, s.idx,
          occupied, s.material ? `${s.material}` : (occupied ? "loaded" : ""),
          s.color));
      });
    });
    setStatus(`${spools.length} spools in FilamentHub`);
  } catch (e) { setStatus(`Error: ${e.message}`); }
}

function openPicker(ace, slot) {
  target = { ace, slot };
  $("#picker-title").textContent = `ACE ${ace + 1} · slot ${slot + 1}`;
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

$("#refresh").addEventListener("click", render);
$("#filter").addEventListener("input", (e) => renderSpoolList(e.target.value));
$("#clear-slot").addEventListener("click", clearSlot);
$("#cancel").addEventListener("click", () => $("#picker").close());
render();
