const $ = (s) => document.querySelector(s);
const setStatus = (m) => { $("#status").textContent = m || ""; };

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

function fieldRow(labelText, input) {
  const row = document.createElement("label");
  row.className = "field";
  const span = document.createElement("span");
  span.textContent = labelText;
  row.appendChild(span);
  row.appendChild(input);
  return row;
}

function aceCard(entry) {
  const el = document.createElement("div");
  el.className = "card" + (entry.fault ? " faulted" : "");

  const head = document.createElement("div");
  head.className = "card-head";
  const title = document.createElement("strong");
  title.textContent = `ACE ${entry.ace + 1}`;
  head.appendChild(title);
  const state = document.createElement("span");
  state.className = `state state-${entry.state.toLowerCase()}`;
  state.textContent = entry.state;
  head.appendChild(state);
  el.appendChild(head);

  const hum = document.createElement("p");
  hum.className = "humidity";
  hum.textContent = entry.humidity_pct == null
    ? "Humidity: unknown"
    : `Humidity: ${entry.humidity_pct.toFixed(1)}%`;
  el.appendChild(hum);

  if (entry.remaining_min != null) {
    const rem = document.createElement("p");
    rem.textContent = `Remaining: ~${entry.remaining_min} min`;
    el.appendChild(rem);
  }

  if (entry.fault) {
    const f = document.createElement("p");
    f.className = "fault-msg";
    f.textContent = `Fault (${entry.fault.code}): ${entry.fault.msg}`;
    el.appendChild(f);
    const clearBtn = document.createElement("button");
    clearBtn.className = "btn btn-ghost";
    clearBtn.textContent = "Clear fault";
    clearBtn.addEventListener("click", () => resetFault(entry.ace));
    el.appendChild(clearBtn);
  }

  const enabledInput = document.createElement("input");
  enabledInput.type = "checkbox";
  enabledInput.checked = !!entry.enabled;

  const targetInput = document.createElement("input");
  targetInput.type = "number"; targetInput.min = "0"; targetInput.max = "100";
  targetInput.value = entry.target_pct;

  const tempInput = document.createElement("input");
  tempInput.type = "number"; tempInput.min = "0"; tempInput.max = "90";
  tempInput.value = entry.temp_c;

  const durationInput = document.createElement("input");
  durationInput.type = "number"; durationInput.min = "1";
  durationInput.value = entry.duration_min;

  const form = document.createElement("div");
  form.className = "fields";
  form.appendChild(fieldRow("Enabled", enabledInput));
  form.appendChild(fieldRow("Target %", targetInput));
  form.appendChild(fieldRow("Temp °C", tempInput));
  form.appendChild(fieldRow("Duration min", durationInput));
  el.appendChild(form);

  const actions = document.createElement("div");
  actions.className = "actions";
  const saveBtn = document.createElement("button");
  saveBtn.className = "btn";
  saveBtn.textContent = "Save";
  saveBtn.addEventListener("click", () => saveConfig(entry.ace, {
    enabled: enabledInput.checked,
    target_pct: Number(targetInput.value),
    temp: Number(tempInput.value),
    duration_min: Number(durationInput.value),
  }));
  actions.appendChild(saveBtn);

  const dryBtn = document.createElement("button");
  dryBtn.className = "btn btn-ghost";
  dryBtn.textContent = "Dry now";
  dryBtn.disabled = entry.state === "DRYING";
  dryBtn.addEventListener("click", () => dryNow(entry.ace));
  actions.appendChild(dryBtn);

  el.appendChild(actions);
  return el;
}

async function render() {
  setStatus("Loading…");
  try {
    const data = await jget("status");
    const cards = $("#cards"); cards.innerHTML = "";
    (data.aces || []).forEach((entry) => cards.appendChild(aceCard(entry)));
    setStatus(`${(data.aces || []).length} ACE(s)`);
  } catch (e) { setStatus(`Error: ${e.message}`); }
}

async function saveConfig(ace, cfg) {
  setStatus("Saving…");
  try {
    await jpost("config", { ace, ...cfg });
    await render();
  } catch (e) { setStatus(`Save failed: ${e.message}`); }
}

async function dryNow(ace) {
  setStatus("Starting dry…");
  try {
    await jpost("dry", { ace });
    await render();
  } catch (e) { setStatus(`Dry failed: ${e.message}`); }
}

async function resetFault(ace) {
  setStatus("Clearing fault…");
  try {
    await jpost("reset-fault", { ace });
    await render();
  } catch (e) { setStatus(`Clear failed: ${e.message}`); }
}

$("#refresh").addEventListener("click", render);
render();
