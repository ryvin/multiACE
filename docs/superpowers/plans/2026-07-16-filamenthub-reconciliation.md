# FilamentHub Desired-vs-Actual Reconciliation — Implementation Plan (M1 Phase 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The FilamentHub plugin owns a durable desired-state store and reconciles it against live decay71 occupancy, so a pulled label for a not-yet-loaded slot renders as `EXPECTED_NOT_LOADED` instead of being deleted by decay71's empty-slot eject-debounce.

**Architecture:** Two layers — desired (plugin-persisted JSON) and observed (decay71 `/api/plugin-api/state`) — joined by a pure `reconcile_slots()` in the plugin backend and rendered by the plugin frontend. No firmware, no decay71, no `multiace_web` changes.

**Tech Stack:** Python 3.11, FastAPI, httpx, pytest + respx; vanilla JS frontend.

## Global Constraints

- Scope is the `filamenthub` plugin only (`multiace_plugins/filamenthub/`). GPL-3.0 header on every new `.py`.
- Label/display only — **zero filament motion**.
- All existing 54 plugin tests must stay green; existing `POST /pull` response fields (`applied/cleared/stale/disputed/errors`) are augmented, never removed.
- Reconciliation states (exact strings): `VERIFIED`, `ASSERTED`, `CONFLICT`, `UNKNOWN_LOADED`, `EXPECTED_NOT_LOADED`, `EMPTY`.
- Desired store keys are `"<ace>_<slot>"` (decay71 override-key convention).
- Run tests from `multiace_plugins/filamenthub/` with the venv active.

---

### Task 1: Config — desired-state path

**Files:**
- Modify: `multiace_plugins/filamenthub/src/filamenthub_plugin/config.py`
- Test: `multiace_plugins/filamenthub/tests/test_config.py`

**Produces:** `Config.desired_state_path: str` (env `FILAMENTHUB_DESIRED_PATH`, default `.filamenthub_desired.json`).

- [ ] **Step 1: Failing test** — append to `test_config.py`:
```python
def test_desired_state_path_default(monkeypatch):
    monkeypatch.setenv("FILAMENTHUB_URL", "http://fh.test")
    monkeypatch.setenv("MULTIACE_PRINTER_ID", "u1-1")
    monkeypatch.delenv("FILAMENTHUB_DESIRED_PATH", raising=False)
    from filamenthub_plugin.config import load_config
    assert load_config().desired_state_path == ".filamenthub_desired.json"

def test_desired_state_path_override(monkeypatch):
    monkeypatch.setenv("FILAMENTHUB_URL", "http://fh.test")
    monkeypatch.setenv("MULTIACE_PRINTER_ID", "u1-1")
    monkeypatch.setenv("FILAMENTHUB_DESIRED_PATH", "/tmp/d.json")
    from filamenthub_plugin.config import load_config
    assert load_config().desired_state_path == "/tmp/d.json"
```
- [ ] **Step 2: Run** `pytest tests/test_config.py -q` → FAIL (`Config` has no `desired_state_path`).
- [ ] **Step 3: Implement** — add the field to the frozen dataclass and to `load_config()`:
```python
    ace_state_url: str
    desired_state_path: str
```
```python
        ace_state_url=os.environ.get(
            "FILAMENTHUB_ACE_STATE_URL",
            f"{filamenthub_url.rstrip('/')}/fleet/api/ace-state",
        ),
        desired_state_path=os.environ.get(
            "FILAMENTHUB_DESIRED_PATH", ".filamenthub_desired.json"),
```
- [ ] **Step 4: Run** `pytest tests/test_config.py -q` → PASS.
- [ ] **Step 5: Commit** `git add -A && git commit -m "feat(filamenthub): desired_state_path config"`

---

### Task 2: Desired-state store

**Files:**
- Create: `multiace_plugins/filamenthub/src/filamenthub_plugin/desired_store.py`
- Test: `multiace_plugins/filamenthub/tests/test_desired_store.py`

**Produces:** `load_desired(path) -> dict[str, dict]`, `save_desired(path, printer, slots) -> None`.

- [ ] **Step 1: Failing test** (`test_desired_store.py`):
```python
# License: GPL-3.0
import json
from filamenthub_plugin.desired_store import load_desired, save_desired


def test_missing_file_returns_empty(tmp_path):
    assert load_desired(str(tmp_path / "nope.json")) == {}


def test_roundtrip(tmp_path):
    p = str(tmp_path / "d.json")
    slots = {"0_2": {"ace": 0, "slot": 2, "spool_id": 110, "material": "PLA",
                     "brand": "Snapmaker", "subtype": "SnapSpeed Red", "color": "#FF0000"}}
    save_desired(p, "u1-1", slots)
    assert load_desired(p) == slots
    with open(p) as f:
        assert json.load(f)["printer"] == "u1-1"


def test_corrupt_file_returns_empty(tmp_path):
    p = tmp_path / "d.json"
    p.write_text("{not json")
    assert load_desired(str(p)) == {}
```
- [ ] **Step 2: Run** `pytest tests/test_desired_store.py -q` → FAIL (module missing).
- [ ] **Step 3: Implement** `desired_store.py`:
```python
# License: GPL-3.0
"""Plugin-local durable store of FilamentHub's desired per-slot loadout.

Owned by the plugin (not decay71's override store) so a desired label for a
physically-empty slot survives decay71's eject-debounce garbage collection.
"""
from __future__ import annotations
import json
import logging
import os
import tempfile

log = logging.getLogger("filamenthub.plugin")


def load_desired(path: str) -> dict[str, dict]:
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as e:
        log.warning("desired store unreadable (%s): %s", path, e)
        return {}
    slots = data.get("slots") if isinstance(data, dict) else None
    return slots if isinstance(slots, dict) else {}


def save_desired(path: str, printer: str, slots: dict[str, dict]) -> None:
    data = {"schema": 1, "printer": printer, "slots": slots}
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except OSError:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
```
- [ ] **Step 4: Run** `pytest tests/test_desired_store.py -q` → PASS.
- [ ] **Step 5: Commit** `git add -A && git commit -m "feat(filamenthub): durable desired-state store"`

---

### Task 3: Pure reconciler `reconcile_slots`

**Files:**
- Modify: `multiace_plugins/filamenthub/src/filamenthub_plugin/reconcile.py`
- Test: `multiace_plugins/filamenthub/tests/test_reconcile.py`

**Consumes:** `normalize_color` from `mapping.py`.
**Produces:** `reconcile_slots(desired: dict[str, dict], observed_aces: list[dict]) -> list[dict]` returning rows `{ace, slot, recon_state, display_name, display_material, display_color, desired, observed}`.

- [ ] **Step 1: Failing test** — append to `test_reconcile.py`:
```python
from filamenthub_plugin.reconcile import reconcile_slots

def _obs(idx, slots):
    return [{"idx": idx, "slots": slots}]

def _slot(i, state, material="", color=None, rfid=0):
    return {"idx": i, "state": state, "material": material, "color": color, "rfid": rfid}

_RED = {"ace": 0, "slot": 2, "spool_id": 110, "material": "PLA",
        "brand": "Snapmaker", "subtype": "SnapSpeed Red", "color": "#FF0000"}
_WHITE = {"ace": 0, "slot": 3, "spool_id": 91, "material": "PLA",
          "brand": "Snapmaker", "subtype": "SnapSpeed Pearl White", "color": "#F8F8FF"}

def test_expected_not_loaded_when_desired_but_empty():
    rows = reconcile_slots({"0_2": _RED}, _obs(0, [_slot(2, "empty")]))
    r = next(x for x in rows if (x["ace"], x["slot"]) == (0, 2))
    assert r["recon_state"] == "EXPECTED_NOT_LOADED"
    assert r["display_name"] == "SnapSpeed Red"

def test_unknown_loaded_when_occupied_no_desired():
    rows = reconcile_slots({}, _obs(0, [_slot(0, "ready", rfid=1)]))
    r = rows[0]
    assert r["recon_state"] == "UNKNOWN_LOADED"

def test_verified_when_rfid_matches_desired():
    rows = reconcile_slots({"0_3": _WHITE},
                           _obs(0, [_slot(3, "ready", material="PLA", color="#F8F8FF", rfid=1)]))
    assert rows[0]["recon_state"] == "VERIFIED"

def test_asserted_when_occupied_desired_no_rfid_identity():
    rows = reconcile_slots({"0_3": _WHITE},
                           _obs(0, [_slot(3, "ready", material="", color=None, rfid=1)]))
    assert rows[0]["recon_state"] == "ASSERTED"

def test_conflict_when_rfid_disagrees():
    rows = reconcile_slots({"0_3": _WHITE},
                           _obs(0, [_slot(3, "ready", material="PETG", color="#000000", rfid=1)]))
    assert rows[0]["recon_state"] == "CONFLICT"

def test_empty_when_neither():
    rows = reconcile_slots({}, _obs(0, [_slot(1, "empty")]))
    assert rows[0]["recon_state"] == "EMPTY"
```
- [ ] **Step 2: Run** `pytest tests/test_reconcile.py -q` → FAIL (`reconcile_slots` undefined).
- [ ] **Step 3: Implement** — append to `reconcile.py` (add `normalize_color` to the existing `from .mapping import ...`):
```python
def reconcile_slots(desired: dict[str, dict],
                    observed_aces: list[dict]) -> list[dict]:
    observed: dict[tuple[int, int], dict] = {}
    for ace in observed_aces or []:
        ai = int(ace.get("idx"))
        for s in ace.get("slots") or []:
            observed[(ai, int(s.get("idx")))] = s
    keys = set(observed.keys())
    for k in desired:
        parsed = _parse_key(k)
        if parsed is not None:
            keys.add(parsed)
    rows: list[dict] = []
    for ace, slot in sorted(keys):
        d = desired.get(f"{ace}_{slot}")
        o = observed.get((ace, slot))
        occupied = bool(o) and o.get("state") != "empty"
        rfid_identity = bool(o) and o.get("rfid") == 1 and bool(
            (o.get("material") or "") or (o.get("color") or ""))
        if occupied and d:
            if rfid_identity:
                mat_ok = (o.get("material") or "") == (d.get("material") or "")
                col_ok = normalize_color(o.get("color")) == normalize_color(d.get("color"))
                state = "VERIFIED" if (mat_ok and col_ok) else "CONFLICT"
            else:
                state = "ASSERTED"
        elif occupied:
            state = "UNKNOWN_LOADED"
        elif d:
            state = "EXPECTED_NOT_LOADED"
        else:
            state = "EMPTY"
        if d and state in ("VERIFIED", "ASSERTED", "CONFLICT", "EXPECTED_NOT_LOADED"):
            name = d.get("subtype") or ""
            material = d.get("material") or ""
            color = normalize_color(d.get("color"))
        elif state == "UNKNOWN_LOADED":
            name = ""
            material = (o.get("material") or "") if o else ""
            color = normalize_color(o.get("color")) if o else ""
        else:
            name = material = color = ""
        rows.append({
            "ace": ace, "slot": slot, "recon_state": state,
            "display_name": name, "display_material": material, "display_color": color,
            "desired": d,
            "observed": ({"state": o.get("state"), "material": o.get("material"),
                          "color": o.get("color"), "rfid": o.get("rfid")} if o else None),
        })
    return rows
```
(Note: `_parse_key` already exists in `reconcile.py`.)
- [ ] **Step 4: Run** `pytest tests/test_reconcile.py -q` → PASS (all 6 + existing).
- [ ] **Step 5: Commit** `git add -A && git commit -m "feat(filamenthub): pure reconcile_slots (6-state desired-vs-observed)"`

---

### Task 4: Observed reader `MultiAceClient.get_state`

**Files:**
- Modify: `multiace_plugins/filamenthub/src/filamenthub_plugin/multiace_client.py`
- Test: `multiace_plugins/filamenthub/tests/test_multiace_client.py`

**Produces:** `async MultiAceClient.get_state() -> dict`.

- [ ] **Step 1: Failing test** — append:
```python
import httpx, respx, pytest
from filamenthub_plugin.multiace_client import MultiAceClient

@respx.mock
@pytest.mark.asyncio
async def test_get_state_returns_body():
    respx.get("http://ma.test/api/plugin-api/state").mock(
        return_value=httpx.Response(200, json={"aces": [{"idx": 0, "slots": []}]}))
    body = await MultiAceClient("http://ma.test").get_state()
    assert body["aces"][0]["idx"] == 0
```
- [ ] **Step 2: Run** `pytest tests/test_multiace_client.py -q` → FAIL.
- [ ] **Step 3: Implement** — add method to `MultiAceClient`:
```python
    async def get_state(self) -> dict:
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.get(f"{self._base}/api/plugin-api/state")
            r.raise_for_status()
            return r.json()
```
- [ ] **Step 4: Run** `pytest tests/test_multiace_client.py -q` → PASS.
- [ ] **Step 5: Commit** `git add -A && git commit -m "feat(filamenthub): MultiAceClient.get_state (observed occupancy)"`

---

### Task 5: `GET /slots` endpoint + Pull persists desired + reconciliation summary

**Files:**
- Modify: `multiace_plugins/filamenthub/src/filamenthub_plugin/app.py`
- Test: `multiace_plugins/filamenthub/tests/test_app.py`

**Consumes:** Tasks 1-4.
**Produces:** `GET /slots -> {slots, observed_ok}`; `POST /pull` also persists desired and returns `reconciliation` + optional `warning`.

- [ ] **Step 1: Failing tests** — append to `test_app.py` (helpers `_ace_state_body`, `_spools_body` already exist):
```python
def _plugin_state_body(slots):
    return {"aces": [{"idx": 0, "slots": slots}]}

@respx.mock
def test_slots_reconciles_desired_vs_observed(client, tmp_path, monkeypatch):
    monkeypatch.setenv("FILAMENTHUB_DESIRED_PATH", str(tmp_path / "d.json"))
    from filamenthub_plugin import desired_store
    desired_store.save_desired(str(tmp_path / "d.json"), "davinci-u1",
        {"0_2": {"ace": 0, "slot": 2, "spool_id": 110, "material": "PLA",
                 "brand": "Snapmaker", "subtype": "SnapSpeed Red", "color": "#FF0000"}})
    respx.get("http://ma.test/api/plugin-api/state").mock(return_value=httpx.Response(
        200, json=_plugin_state_body([{"idx": 2, "state": "empty", "rfid": 0,
                                       "material": "", "color": None}])))
    r = client.get("/slots")
    assert r.status_code == 200
    body = r.json()
    assert body["observed_ok"] is True
    row = next(x for x in body["slots"] if (x["ace"], x["slot"]) == (0, 2))
    assert row["recon_state"] == "EXPECTED_NOT_LOADED"
    assert row["display_name"] == "SnapSpeed Red"

@respx.mock
def test_slots_degrades_when_state_unreachable(client, tmp_path, monkeypatch):
    monkeypatch.setenv("FILAMENTHUB_DESIRED_PATH", str(tmp_path / "d.json"))
    respx.get("http://ma.test/api/plugin-api/state").mock(return_value=httpx.Response(502))
    r = client.get("/slots")
    assert r.status_code == 200
    assert r.json()["observed_ok"] is False

@respx.mock
def test_pull_persists_desired(client, tmp_path, monkeypatch):
    p = str(tmp_path / "d.json")
    monkeypatch.setenv("FILAMENTHUB_DESIRED_PATH", p)
    respx.get("http://fh.test/fleet/api/ace-state").mock(
        return_value=httpx.Response(200, json=_ace_state_body()))
    respx.get("http://fh.test/api/v1/spool").mock(
        return_value=httpx.Response(200, json=_spools_body()))
    respx.get("http://ma.test/api/slot-override").mock(
        return_value=httpx.Response(200, json={"overrides": {}}))
    respx.post("http://ma.test/api/slot-override").mock(
        return_value=httpx.Response(200, json={"ok": True}))
    respx.get("http://ma.test/api/plugin-api/state").mock(
        return_value=httpx.Response(200, json=_plugin_state_body([])))
    r = client.post("/pull")
    assert r.status_code == 200
    assert "reconciliation" in r.json()
    from filamenthub_plugin.desired_store import load_desired
    assert "0_0" in load_desired(p)   # winner (ace0 slot0) persisted
```

> **Implementer note:** `desired_state_path` is read at `load_config()` time. Inspect `conftest.py`'s `client` fixture; if `cfg` is cached at import, these three tests must build a fresh app after setting the env (`from filamenthub_plugin.app import create_app; from filamenthub_plugin.config import load_config; TestClient(create_app(load_config()))`), or add a `conftest.py` env default for `FILAMENTHUB_DESIRED_PATH` pointing at a tmp path. Follow the existing fixture pattern. Confirm `_ace_state_body()`'s winner slot key and adjust the `"0_0"` assertion to match it.

- [ ] **Step 2: Run** `pytest tests/test_app.py -q` → FAIL (`/slots` 404; no `reconciliation`).
- [ ] **Step 3: Implement** in `app.py`:
  - Add imports: `from .desired_store import load_desired, save_desired` and extend the reconcile import to `from .reconcile import plan_reconcile, reconcile_slots`.
  - Add endpoint:
```python
    @app.get("/slots")
    async def slots():
        desired = load_desired(cfg.desired_state_path)
        ma = MultiAceClient(cfg.multiace_url)
        try:
            state = await ma.get_state()
            observed_ok = True
        except httpx.HTTPError as e:
            log.warning("plugin-api/state fetch failed: %s", e)
            state, observed_ok = {}, False
        return {"slots": reconcile_slots(desired, state.get("aces", [])),
                "observed_ok": observed_ok}
```
  - In `pull()`, after the apply/clear loops and before the return, persist the merged desired map and compute the reconciliation summary:
```python
        # Persist desired (durable; survives decay71 eject-debounce GC).
        winner_desired = {}
        for row in winners:
            if row.get("slot") is None:
                continue
            a, s = int(row["ace"]), int(row["slot"])
            payload = ace_state_row_to_override(
                row, brand_by_spool_id.get(row.get("spool_id"), ""))
            payload["spool_id"] = row.get("spool_id")
            winner_desired[f"{a}_{s}"] = payload
        covered = set(state.get("aces_covered") or [])
        if not covered:
            covered = {int(r["ace"]) for r in winners if r.get("slot") is not None}
        merged = dict(load_desired(cfg.desired_state_path))
        merged.update(winner_desired)
        for k in list(merged.keys()):
            a = int(k.split("_")[0])
            if a in covered and k not in winner_desired:
                del merged[k]
        save_desired(cfg.desired_state_path, cfg.printer_id, merged)

        warning = None
        if "aces_covered" in state and not state.get("aces_covered"):
            warning = "FilamentHub reported no coverage; clears skipped"

        recon = {"verified": 0, "asserted": 0, "expected_not_loaded": 0,
                 "unknown_loaded": 0, "conflict": 0}
        try:
            st = await ma.get_state()
            for rr in reconcile_slots(merged, st.get("aces", [])):
                key = rr["recon_state"].lower()
                if key in recon:
                    recon[key] += 1
        except httpx.HTTPError:
            recon = None
```
  - Extend the return dict with `"reconciliation": recon, "warning": warning`.
  - `ace_state_row_to_override` is already imported.
- [ ] **Step 4: Run** `pytest tests/test_app.py -q` → PASS; then `pytest -q` → all green.
- [ ] **Step 5: Commit** `git add -A && git commit -m "feat(filamenthub): GET /slots reconciliation + pull persists desired"`

---

### Task 6: Frontend — render reconciliation states

**Files:**
- Modify: `multiace_plugins/filamenthub/src/filamenthub_plugin/static/app.js`
- Modify: `multiace_plugins/filamenthub/src/filamenthub_plugin/static/style.css`

**Consumes:** `GET /slots` (Task 5). No unit tests (verified live). Keep it small.

- [ ] **Step 1: Repoint `render()`** to `GET /slots` instead of `MULTIACE_STATE`. For each row build a card keyed by `recon_state`:
  - `VERIFIED` → solid card, `display_color` swatch, `display_name` + ` ✓`.
  - `ASSERTED` → solid card, `display_name`, small "unverified" dot.
  - `EXPECTED_NOT_LOADED` → `.slot.ghost` (dashed border, desaturated swatch), text `Expected: ${display_name} — not loaded`.
  - `UNKNOWN_LOADED` → `.slot.unknown` (amber), text `Filament present — unknown · tap to identify`.
  - `CONFLICT` → `.slot.conflict` (red), text `Tag: ${observed.material} · Hub: ${display_name}`.
  - `EMPTY` → `.slot.empty`, text `Empty`.
- [ ] **Step 2: Label** each card `A0…A3 / B0…B3`: `const L = String.fromCharCode(65 + ace); meta.textContent = \`${L}${slot}\`;` (0-based; removes `ace+1 / slot+1`). Apply to `openPicker`'s title and dispute rows.
- [ ] **Step 3: Pull status line** uses the `reconciliation` summary when present: `Pulled: ${r.verified+r.asserted} identified, ${r.expected_not_loaded} awaiting load, ${r.unknown_loaded} unidentified`. Auto-pull-on-open sets status `Syncing labels from FilamentHub…` before the POST.
- [ ] **Step 4: `style.css`** — add `.slot.ghost` (dashed border, `opacity:.7`), `.slot.unknown` (amber left-border), `.slot.conflict` (red left-border). Reuse existing `.slot`/`.swatch` tokens.
- [ ] **Step 5: Commit** `git add -A && git commit -m "feat(filamenthub): reconciliation grid (ghost/unknown/conflict, 0-based, names)"`

---

### Task 7: Live verification + deploy

**Files:** none (operational).

- [ ] **Step 1:** `pytest -q` in `multiace_plugins/filamenthub/` → all green (54 + new).
- [ ] **Step 2: Safety** — `curl -s http://$DAVINCI_U1_HOST:7125/printer/objects/query?print_stats` must be `complete/standby/cancelled` (never deploy during `printing`/`paused`).
- [ ] **Step 3: Deploy** the plugin folder and run `sh install/install_plugin.sh` (as `root@$DAVINCI_U1_HOST`, pw `snapmaker`; box has `wget`).
- [ ] **Step 4: Verify acceptance criteria live** — trigger a Pull, then `GET /plugin/filamenthub/slots`: slot 2 = `EXPECTED_NOT_LOADED` "SnapSpeed Red" and **persists across repeated polls**; occupied unknown slots = `UNKNOWN_LOADED`. Screenshot the plugin tab via Playwright at 1280×900.
- [ ] **Step 5:** Update project memory (`filamenthub-ace-state-puller`) with the deployed reconciliation status.
