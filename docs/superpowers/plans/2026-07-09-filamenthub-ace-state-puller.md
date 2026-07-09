# FilamentHub → multiACE ace-state puller (Phase 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a label-only puller to the `filamenthub` plugin that mirrors FilamentHub's authoritative ACE slot→spool state into multiACE's slot-override labels.

**Architecture:** A new `POST /pull` endpoint in the existing FilamentHub plugin fetches FilamentHub's already-shipped `GET /fleet/api/ace-state?printer=<id>` seam (one priority-resolved winner per `(ace,slot)` + `disputed` losers), reads multiACE's current overrides, and reconciles: writes `/api/slot-override` for each winner (brand enriched from the spool list) and clears vacated slots — but only on ACEs ace-state covers. No filament motion.

**Tech Stack:** Python 3.11+, FastAPI, httpx (async), pydantic v2; pytest + pytest-asyncio + respx for tests. Vanilla JS/HTML/CSS frontend (no build step).

## Global Constraints

- **License header:** every `.py` file starts with `# License: GPL-3.0` as line 1.
- **Label-only, zero motion.** Never call any load/unload/ACE motion macro. Only `/api/slot-override` POST/GET/DELETE.
- **Consume the seam, not raw Spoolman.** The desired state comes from `GET /fleet/api/ace-state` (priority-resolved). Do NOT re-derive winners from `/api/v1/spool` / `SpoolmanClient.list_all_bindings()`.
- **Reconcile is scoped:** clears are limited to `(ace, slot)` whose `ace` appears in ace-state's winner set. Never clear labels on an ACE ace-state doesn't mention.
- **Partial failures are collected, never aborted mid-loop.** One slot's write failure must not skip the rest.
- **Disputes are shown, never written.**
- **ace-state row shape (from FilamentHub `build_ace_state`):** `{ace:int, slot:int|None, spool_id:int|None, material:str|None, color:str|None, name:str|None, asserted_by:str|None, asserted_at:str|None}`. Envelope adds `{schema:int, printer:str, ts}`.
- **decay71 slot-override API (target of `MULTIACE_URL`):** `GET /api/slot-override` → `{"overrides": {"<ace>_<slot>": {...}}}`; `POST /api/slot-override` body `{ace,slot,material,brand,subtype,color}`; `DELETE /api/slot-override/{ace}/{slot}`.
- **Commits use the ryvin identity:** `git -c user.name=ryvin -c user.email=18613731+ryvin@users.noreply.github.com commit ...`.
- **Run tests from `multiace_plugins/filamenthub/` with the plugin venv active** (`. .venv/bin/activate`). All new tests live under `multiace_plugins/filamenthub/tests/`.
- **FilamentHub is a different repo.** If a FilamentHub-side change is needed (e.g. `503` provider unwired, or adding `vendor` to the row), open an issue on `ryvin/FilamentHub` — do not edit it from here.

---

### Task 1: Config — add `ace_state_url`

**Files:**
- Modify: `multiace_plugins/filamenthub/src/filamenthub_plugin/config.py`
- Modify: `multiace_plugins/filamenthub/tests/conftest.py`
- Test: `multiace_plugins/filamenthub/tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Config.ace_state_url: str` (frozen dataclass field); `load_config()` derives it from `FILAMENTHUB_ACE_STATE_URL`, defaulting to `f"{filamenthub_url.rstrip('/')}/fleet/api/ace-state"`.

- [ ] **Step 1: Write the failing test**

Add to `multiace_plugins/filamenthub/tests/test_config.py`:

```python
def test_ace_state_url_defaults_from_filamenthub_url(monkeypatch):
    monkeypatch.setenv("FILAMENTHUB_URL", "https://fh.example.com/")
    monkeypatch.setenv("MULTIACE_PRINTER_ID", "u1-1")
    monkeypatch.delenv("FILAMENTHUB_ACE_STATE_URL", raising=False)
    from filamenthub_plugin.config import load_config
    cfg = load_config()
    assert cfg.ace_state_url == "https://fh.example.com/fleet/api/ace-state"


def test_ace_state_url_explicit_override(monkeypatch):
    monkeypatch.setenv("FILAMENTHUB_URL", "https://fh.example.com")
    monkeypatch.setenv("MULTIACE_PRINTER_ID", "u1-1")
    monkeypatch.setenv("FILAMENTHUB_ACE_STATE_URL", "http://127.0.0.1:7127/api/ace-state")
    from filamenthub_plugin.config import load_config
    cfg = load_config()
    assert cfg.ace_state_url == "http://127.0.0.1:7127/api/ace-state"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -k ace_state_url -v`
Expected: FAIL — `TypeError: __init__() missing ... 'ace_state_url'` or `AttributeError`.

- [ ] **Step 3: Add the field + derivation**

In `config.py`, add the field to the dataclass (after `port`; keep frozen) and derive in `load_config`:

```python
@dataclass(frozen=True)
class Config:
    filamenthub_url: str
    printer_id: str
    multiace_url: str
    port: int
    ace_state_url: str


def load_config() -> Config:
    filamenthub_url = os.environ["FILAMENTHUB_URL"]
    return Config(
        filamenthub_url=filamenthub_url,
        printer_id=os.environ["MULTIACE_PRINTER_ID"],
        multiace_url=os.environ.get("MULTIACE_URL", "http://127.0.0.1:7126"),
        port=int(os.environ.get("FILAMENTHUB_PLUGIN_PORT", "8089")),
        ace_state_url=os.environ.get(
            "FILAMENTHUB_ACE_STATE_URL",
            f"{filamenthub_url.rstrip('/')}/fleet/api/ace-state",
        ),
    )
```

- [ ] **Step 4: Update the shared test fixture**

In `tests/conftest.py`, add the field so every existing test still constructs a valid `Config`:

```python
    return Config(
        filamenthub_url="http://fh.test",
        printer_id="davinci-u1",
        multiace_url="http://ma.test",
        port=8089,
        ace_state_url="http://fh.test/fleet/api/ace-state",
    )
```

- [ ] **Step 5: Run the full suite to verify nothing broke**

Run: `pytest -q`
Expected: all tests PASS (existing suite + the 2 new config tests).

- [ ] **Step 6: Commit**

```bash
git add multiace_plugins/filamenthub/src/filamenthub_plugin/config.py \
        multiace_plugins/filamenthub/tests/conftest.py \
        multiace_plugins/filamenthub/tests/test_config.py
git -c user.name=ryvin -c user.email=18613731+ryvin@users.noreply.github.com \
  commit -m "feat(filamenthub): add ace_state_url config (Phase-4 puller)"
```

---

### Task 2: ace-state client

**Files:**
- Create: `multiace_plugins/filamenthub/src/filamenthub_plugin/ace_state.py`
- Test: `multiace_plugins/filamenthub/tests/test_ace_state.py`

**Interfaces:**
- Consumes: `Config.ace_state_url` (passed as a URL string).
- Produces:
  - `EXPECTED_ACE_STATE_SCHEMA = 1`
  - exceptions `AceStateError` (base), `AceStateSeamDisabled`, `AceStateProviderError`, `AceStateBadRequest`, `AceStateUnreachable` — each `str(e)` is an operator-facing message.
  - `class AceStateClient(base_url: str, timeout_s: float = 3.0)` with
    `async get_ace_state(printer_id: str) -> dict` returning
    `{"schema", "printer", "ts", "slots": list[dict], "disputed": list[dict]}`.

- [ ] **Step 1: Write the failing tests**

Create `multiace_plugins/filamenthub/tests/test_ace_state.py`:

```python
# License: GPL-3.0
"""Unit tests for the FilamentHub ace-state read-seam client."""
import httpx
import pytest
import respx

from filamenthub_plugin.ace_state import (
    AceStateClient,
    AceStateSeamDisabled,
    AceStateProviderError,
    AceStateBadRequest,
    AceStateUnreachable,
)

URL = "http://fh.test/fleet/api/ace-state"


@respx.mock
@pytest.mark.asyncio
async def test_get_ace_state_returns_envelope():
    body = {
        "schema": 1, "printer": "davinci-u1", "ts": 123,
        "slots": [{"ace": 0, "slot": 0, "spool_id": 42, "material": "PLA",
                   "color": "#00ff00", "name": "PolyTerra Green",
                   "asserted_by": "user:assign", "asserted_at": "2026-07-09T00:00:00Z"}],
        "disputed": [{"ace": 0, "slot": 1, "spool_id": 7, "material": "PETG",
                      "color": "#ff0000", "name": "X", "asserted_by": "watcher:rfid",
                      "asserted_at": "2026-07-08T00:00:00Z", "winner_spool_id": 99}],
    }
    respx.get(url__startswith=URL).mock(return_value=httpx.Response(200, json=body))
    out = await AceStateClient(URL).get_ace_state("davinci-u1")
    assert out["slots"][0]["spool_id"] == 42
    assert out["disputed"][0]["winner_spool_id"] == 99


@respx.mock
@pytest.mark.asyncio
async def test_503_raises_seam_disabled():
    respx.get(url__startswith=URL).mock(return_value=httpx.Response(503))
    with pytest.raises(AceStateSeamDisabled):
        await AceStateClient(URL).get_ace_state("davinci-u1")


@respx.mock
@pytest.mark.asyncio
async def test_502_raises_provider_error():
    respx.get(url__startswith=URL).mock(return_value=httpx.Response(502))
    with pytest.raises(AceStateProviderError):
        await AceStateClient(URL).get_ace_state("davinci-u1")


@respx.mock
@pytest.mark.asyncio
async def test_400_raises_bad_request():
    respx.get(url__startswith=URL).mock(return_value=httpx.Response(400))
    with pytest.raises(AceStateBadRequest):
        await AceStateClient(URL).get_ace_state("davinci-u1")


@respx.mock
@pytest.mark.asyncio
async def test_network_error_raises_unreachable():
    respx.get(url__startswith=URL).mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(AceStateUnreachable):
        await AceStateClient(URL).get_ace_state("davinci-u1")


@respx.mock
@pytest.mark.asyncio
async def test_schema_mismatch_still_returns_but_warns(caplog):
    body = {"schema": 999, "printer": "davinci-u1", "ts": 1, "slots": [], "disputed": []}
    respx.get(url__startswith=URL).mock(return_value=httpx.Response(200, json=body))
    out = await AceStateClient(URL).get_ace_state("davinci-u1")
    assert out["slots"] == []
    assert any("schema" in r.message.lower() for r in caplog.records)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ace_state.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'filamenthub_plugin.ace_state'`.

- [ ] **Step 3: Implement the client**

Create `src/filamenthub_plugin/ace_state.py`:

```python
# License: GPL-3.0
"""Client for FilamentHub's ACE-state read seam (Phase 3 → Phase 4 puller).

Reads GET {base_url}?printer=<id> — FilamentHub's authoritative desired ACE
slot→spool state, one priority-resolved winner per (ace, slot) plus losing
claims under ``disputed``. See ``FilamentHub/scripts/sentinel/ace_state.py``
for the server side (do not import it — different repo/process).
"""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger("filamenthub.ace_state")

# Must match FilamentHub scripts/sentinel/ace_state.py:ACE_STATE_SCHEMA.
EXPECTED_ACE_STATE_SCHEMA = 1


class AceStateError(Exception):
    """Base for ace-state fetch failures. ``str(e)`` is operator-facing."""


class AceStateSeamDisabled(AceStateError):
    """503 — the FilamentHub watcher didn't wire the ace-state provider."""


class AceStateProviderError(AceStateError):
    """502 — the provider raised while building state."""


class AceStateBadRequest(AceStateError):
    """400 — bad/missing printer parameter."""


class AceStateUnreachable(AceStateError):
    """Network/timeout/transport failure reaching FilamentHub."""


class AceStateClient:
    def __init__(self, base_url: str, timeout_s: float = 3.0) -> None:
        self._url = base_url
        self._timeout = timeout_s

    async def get_ace_state(self, printer_id: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                resp = await c.get(self._url, params={"printer": printer_id})
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            raise AceStateUnreachable(f"FilamentHub ace-state unreachable: {e}") from e

        if resp.status_code == 503:
            raise AceStateSeamDisabled(
                "FilamentHub ace-state not enabled (watcher provider unwired)")
        if resp.status_code == 502:
            raise AceStateProviderError("FilamentHub ace-state provider error")
        if resp.status_code == 400:
            raise AceStateBadRequest("FilamentHub rejected the ace-state request (400)")
        if resp.status_code >= 400:
            raise AceStateError(f"FilamentHub ace-state returned {resp.status_code}")

        try:
            body = resp.json()
        except ValueError as e:
            raise AceStateError("FilamentHub ace-state returned non-JSON") from e

        schema = body.get("schema")
        if schema != EXPECTED_ACE_STATE_SCHEMA:
            log.warning("ace-state schema %r != expected %d; parsing best-effort",
                        schema, EXPECTED_ACE_STATE_SCHEMA)
        body.setdefault("slots", [])
        body.setdefault("disputed", [])
        return body
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ace_state.py -v`
Expected: all 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add multiace_plugins/filamenthub/src/filamenthub_plugin/ace_state.py \
        multiace_plugins/filamenthub/tests/test_ace_state.py
git -c user.name=ryvin -c user.email=18613731+ryvin@users.noreply.github.com \
  commit -m "feat(filamenthub): ace-state read-seam client with typed errors"
```

---

### Task 3: Row → slot-override mapping helper

**Files:**
- Modify: `multiace_plugins/filamenthub/src/filamenthub_plugin/mapping.py`
- Test: `multiace_plugins/filamenthub/tests/test_mapping.py`

**Interfaces:**
- Consumes: an ace-state slot row dict + a `brand` string.
- Produces: `ace_state_row_to_override(row: dict, brand: str) -> dict` returning
  `{"ace", "slot", "material", "brand", "subtype", "color"}` — the exact kwargs
  `MultiAceClient.set_override` accepts. Reuses `normalize_color`.

- [ ] **Step 1: Write the failing tests**

Add to `multiace_plugins/filamenthub/tests/test_mapping.py`:

```python
from filamenthub_plugin.mapping import ace_state_row_to_override


def test_ace_state_row_to_override_maps_fields_and_brand():
    row = {"ace": 1, "slot": 2, "spool_id": 42, "material": "PLA",
           "color": "00ff00", "name": "PolyTerra Green", "asserted_by": "user:assign"}
    out = ace_state_row_to_override(row, brand="PolyTerra")
    assert out == {"ace": 1, "slot": 2, "material": "PLA", "brand": "PolyTerra",
                   "subtype": "PolyTerra Green", "color": "#00ff00"}


def test_ace_state_row_to_override_blank_fields_default_to_empty_string():
    row = {"ace": 0, "slot": 0, "spool_id": 1, "material": None,
           "color": None, "name": None}
    out = ace_state_row_to_override(row, brand="")
    assert out == {"ace": 0, "slot": 0, "material": "", "brand": "",
                   "subtype": "", "color": ""}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mapping.py -k ace_state_row -v`
Expected: FAIL — `ImportError: cannot import name 'ace_state_row_to_override'`.

- [ ] **Step 3: Implement the helper**

Append to `mapping.py`:

```python
def ace_state_row_to_override(row: dict, brand: str) -> dict:
    """Map one FilamentHub ace-state slot row to a multiACE slot-override payload.

    ``brand`` is passed in (the ace-state row has no vendor); callers enrich it
    from the spool list. ``name`` is the most informative subtype we have.
    """
    return {
        "ace": int(row["ace"]),
        "slot": int(row["slot"]),
        "material": row.get("material") or "",
        "brand": brand or "",
        "subtype": row.get("name") or "",
        "color": normalize_color(row.get("color")),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_mapping.py -v`
Expected: all PASS (existing mapping tests + 2 new).

- [ ] **Step 5: Commit**

```bash
git add multiace_plugins/filamenthub/src/filamenthub_plugin/mapping.py \
        multiace_plugins/filamenthub/tests/test_mapping.py
git -c user.name=ryvin -c user.email=18613731+ryvin@users.noreply.github.com \
  commit -m "feat(filamenthub): ace-state row -> slot-override mapping helper"
```

---

### Task 4: `MultiAceClient.list_overrides()`

**Files:**
- Modify: `multiace_plugins/filamenthub/src/filamenthub_plugin/multiace_client.py`
- Test: `multiace_plugins/filamenthub/tests/test_multiace_client.py`

**Interfaces:**
- Consumes: multiACE `GET /api/slot-override` → `{"overrides": {"<ace>_<slot>": {...}}}`.
- Produces: `async list_overrides() -> dict[str, dict]` returning the `overrides`
  map (empty dict if the key is absent). Raises `httpx.HTTPStatusError` on non-2xx.

- [ ] **Step 1: Write the failing test**

Add to `multiace_plugins/filamenthub/tests/test_multiace_client.py`:

```python
@respx.mock
@pytest.mark.asyncio
async def test_list_overrides_returns_map():
    respx.get("http://ma.test/api/slot-override").mock(
        return_value=httpx.Response(200, json={"overrides": {
            "0_0": {"material": "PLA"}, "1_2": {"material": "PETG"}}}))
    ma = MultiAceClient("http://ma.test")
    out = await ma.list_overrides()
    assert set(out.keys()) == {"0_0", "1_2"}


@respx.mock
@pytest.mark.asyncio
async def test_list_overrides_missing_key_is_empty():
    respx.get("http://ma.test/api/slot-override").mock(
        return_value=httpx.Response(200, json={}))
    ma = MultiAceClient("http://ma.test")
    assert await ma.list_overrides() == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_multiace_client.py -k list_overrides -v`
Expected: FAIL — `AttributeError: 'MultiAceClient' object has no attribute 'list_overrides'`.

- [ ] **Step 3: Implement the method**

Add to `MultiAceClient` in `multiace_client.py`:

```python
    async def list_overrides(self) -> dict[str, dict]:
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.get(f"{self._base}/api/slot-override")
            r.raise_for_status()
            return r.json().get("overrides", {})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_multiace_client.py -v`
Expected: all PASS (existing + 2 new).

- [ ] **Step 5: Commit**

```bash
git add multiace_plugins/filamenthub/src/filamenthub_plugin/multiace_client.py \
        multiace_plugins/filamenthub/tests/test_multiace_client.py
git -c user.name=ryvin -c user.email=18613731+ryvin@users.noreply.github.com \
  commit -m "feat(filamenthub): MultiAceClient.list_overrides for reconcile"
```

---

### Task 5: Reconcile planner (pure function)

**Files:**
- Create: `multiace_plugins/filamenthub/src/filamenthub_plugin/reconcile.py`
- Test: `multiace_plugins/filamenthub/tests/test_reconcile.py`

**Interfaces:**
- Consumes: `ace_state_row_to_override` (Task 3); ace-state `slots` (winners);
  the current-override key set from `list_overrides()` (Task 4); a
  `{spool_id: brand}` map.
- Produces:
  ```python
  def plan_reconcile(
      winners: list[dict],
      current_override_keys: Iterable[str],
      brand_by_spool_id: dict[int, str],
  ) -> tuple[list[dict], list[tuple[int, int]]]:
      # returns (to_apply, to_clear)
  ```
  `to_apply` is a list of `set_override` kwargs dicts (one per winner with a
  non-None slot); `to_clear` is a list of `(ace, slot)` to `clear_override`.
  A slot is cleared only if its `ace` is in the winners' ace set AND
  `(ace, slot)` is not itself a winner.

- [ ] **Step 1: Write the failing tests**

Create `multiace_plugins/filamenthub/tests/test_reconcile.py`:

```python
# License: GPL-3.0
"""Unit tests for the pure reconcile planner."""
from filamenthub_plugin.reconcile import plan_reconcile


def _row(ace, slot, spool_id, material="PLA", color="#ffffff", name="n"):
    # 6-digit hex: normalize_color rejects 3-digit shorthand (returns "").
    return {"ace": ace, "slot": slot, "spool_id": spool_id,
            "material": material, "color": color, "name": name}


def test_apply_maps_winners_with_brand():
    winners = [_row(0, 0, 42)]
    to_apply, to_clear = plan_reconcile(winners, [], {42: "PolyTerra"})
    assert to_apply == [{"ace": 0, "slot": 0, "material": "PLA",
                         "brand": "PolyTerra", "subtype": "n", "color": "#ffffff"}]
    assert to_clear == []


def test_clears_vacated_slot_on_known_ace():
    # ACE 0 has a winner at slot 0; slot 1 is currently labeled but no longer a
    # winner -> cleared (same ACE = FilamentHub-known).
    winners = [_row(0, 0, 42)]
    to_apply, to_clear = plan_reconcile(winners, ["0_0", "0_1"], {})
    assert to_clear == [(0, 1)]


def test_does_not_clear_unknown_ace():
    # ACE 1 is not in the winner set -> its labels are left untouched.
    winners = [_row(0, 0, 42)]
    _, to_clear = plan_reconcile(winners, ["0_0", "1_3"], {})
    assert to_clear == []


def test_does_not_clear_a_slot_that_is_still_a_winner():
    winners = [_row(0, 0, 42), _row(0, 1, 43)]
    _, to_clear = plan_reconcile(winners, ["0_0", "0_1"], {})
    assert to_clear == []


def test_skips_winner_with_none_slot():
    winners = [{"ace": 0, "slot": None, "spool_id": 1,
                "material": "PLA", "color": "#fff", "name": "n"}]
    to_apply, to_clear = plan_reconcile(winners, [], {})
    assert to_apply == []


def test_ignores_malformed_override_keys():
    winners = [_row(0, 0, 42)]
    _, to_clear = plan_reconcile(winners, ["0_0", "bogus", "0_x"], {})
    assert to_clear == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_reconcile.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'filamenthub_plugin.reconcile'`.

- [ ] **Step 3: Implement the planner**

Create `src/filamenthub_plugin/reconcile.py`:

```python
# License: GPL-3.0
"""Pure planner: turn FilamentHub's desired ace-state into apply/clear actions.

No I/O — takes plain data, returns the two action lists the endpoint executes.
Kept pure so the scoping rule (only clear on FilamentHub-known ACEs) is unit-
testable without mocking any HTTP.
"""
from __future__ import annotations

from typing import Iterable

from .mapping import ace_state_row_to_override


def _parse_key(key: str) -> tuple[int, int] | None:
    """Parse a decay71 override key ``"<ace>_<slot>"`` -> (ace, slot), or None."""
    parts = key.split("_")
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def plan_reconcile(
    winners: list[dict],
    current_override_keys: Iterable[str],
    brand_by_spool_id: dict[int, str],
) -> tuple[list[dict], list[tuple[int, int]]]:
    desired: set[tuple[int, int]] = set()
    to_apply: list[dict] = []
    for row in winners:
        if row.get("slot") is None:
            continue
        brand = brand_by_spool_id.get(row.get("spool_id"), "")
        payload = ace_state_row_to_override(row, brand)
        desired.add((payload["ace"], payload["slot"]))
        to_apply.append(payload)

    known_aces = {ace for ace, _ in desired}
    to_clear: list[tuple[int, int]] = []
    for key in current_override_keys:
        parsed = _parse_key(key)
        if parsed is None:
            continue
        ace, slot = parsed
        if ace in known_aces and (ace, slot) not in desired:
            to_clear.append((ace, slot))
    to_clear.sort()
    return to_apply, to_clear
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_reconcile.py -v`
Expected: all 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add multiace_plugins/filamenthub/src/filamenthub_plugin/reconcile.py \
        multiace_plugins/filamenthub/tests/test_reconcile.py
git -c user.name=ryvin -c user.email=18613731+ryvin@users.noreply.github.com \
  commit -m "feat(filamenthub): pure reconcile planner (scoped clears)"
```

---

### Task 6: `/pull` and `/ace-state` endpoints

**Files:**
- Modify: `multiace_plugins/filamenthub/src/filamenthub_plugin/app.py`
- Test: `multiace_plugins/filamenthub/tests/test_app.py`

**Interfaces:**
- Consumes: `AceStateClient` (Task 2), `plan_reconcile` (Task 5),
  `MultiAceClient.set_override/clear_override/list_overrides` (Task 4),
  `SpoolmanClient.list_spools` (existing, for brand enrichment).
- Produces:
  - `GET /ace-state` → the raw ace-state envelope (for the UI grid); maps
    `AceStateError` subclasses to HTTP `502` with the error message as `detail`.
  - `POST /pull` → `{"applied": [...], "cleared": [...], "disputed": [...], "errors": [...]}`.
    `applied` entries are `{"ace","slot","material","brand","subtype","color"}`;
    `cleared` entries are `{"ace","slot"}`; `errors` are `{"action","ace","slot","error"}`.

- [ ] **Step 1: Write the failing tests**

Add to `multiace_plugins/filamenthub/tests/test_app.py`:

```python
import httpx
import respx


def _ace_state_body():
    return {"schema": 1, "printer": "davinci-u1", "ts": 1,
            "slots": [{"ace": 0, "slot": 0, "spool_id": 42, "material": "PLA",
                       "color": "#00ff00", "name": "PolyTerra Green",
                       "asserted_by": "user:assign", "asserted_at": "z"}],
            "disputed": [{"ace": 0, "slot": 1, "spool_id": 7, "material": "PETG",
                          "color": "#ff0000", "name": "X",
                          "asserted_by": "watcher:rfid", "asserted_at": "y",
                          "winner_spool_id": 42}]}


def _spools_body():
    return [{"id": 42, "archived": False,
             "filament": {"name": "PolyTerra Green", "material": "PLA",
                          "color_hex": "00ff00", "vendor": {"name": "PolyTerra"}},
             "remaining_weight": 800,
             "extra": {"filamenthub": '"{\\"location\\": {\\"printer\\": \\"davinci-u1\\", \\"ace\\": 0, \\"slot\\": 0}}"'}}]


@respx.mock
def test_pull_applies_winner_and_clears_vacated(client):
    respx.get("http://fh.test/fleet/api/ace-state").mock(
        return_value=httpx.Response(200, json=_ace_state_body()))
    respx.get("http://fh.test/api/v1/spool").mock(
        return_value=httpx.Response(200, json=_spools_body()))
    # multiACE currently has an extra label at (0,1) that is no longer a winner.
    respx.get("http://ma.test/api/slot-override").mock(
        return_value=httpx.Response(200, json={"overrides": {"0_0": {}, "0_1": {}}}))
    post = respx.post("http://ma.test/api/slot-override").mock(
        return_value=httpx.Response(200, json={"ok": True, "key": "0_0"}))
    delete = respx.delete(url__regex=r"http://ma\.test/api/slot-override/\d+/\d+").mock(
        return_value=httpx.Response(200, json={"ok": True}))

    r = client.post("/pull")
    assert r.status_code == 200
    data = r.json()
    assert data["applied"] == [{"ace": 0, "slot": 0, "material": "PLA",
                                "brand": "PolyTerra", "subtype": "PolyTerra Green",
                                "color": "#00ff00"}]
    assert data["cleared"] == [{"ace": 0, "slot": 1}]
    assert data["disputed"][0]["winner_spool_id"] == 42
    assert data["errors"] == []
    assert post.called and delete.called


@respx.mock
def test_pull_collects_partial_errors(client):
    respx.get("http://fh.test/fleet/api/ace-state").mock(
        return_value=httpx.Response(200, json=_ace_state_body()))
    respx.get("http://fh.test/api/v1/spool").mock(
        return_value=httpx.Response(200, json=_spools_body()))
    respx.get("http://ma.test/api/slot-override").mock(
        return_value=httpx.Response(200, json={"overrides": {}}))
    respx.post("http://ma.test/api/slot-override").mock(
        return_value=httpx.Response(502))

    r = client.post("/pull")
    assert r.status_code == 200
    data = r.json()
    assert data["applied"] == []
    assert len(data["errors"]) == 1
    assert data["errors"][0]["action"] == "apply"
    assert data["errors"][0]["ace"] == 0 and data["errors"][0]["slot"] == 0


@respx.mock
def test_pull_maps_seam_disabled_to_502(client):
    respx.get("http://fh.test/fleet/api/ace-state").mock(
        return_value=httpx.Response(503))
    r = client.post("/pull")
    assert r.status_code == 502
    assert "not enabled" in r.json()["detail"]


@respx.mock
def test_get_ace_state_passthrough(client):
    respx.get("http://fh.test/fleet/api/ace-state").mock(
        return_value=httpx.Response(200, json=_ace_state_body()))
    r = client.get("/ace-state")
    assert r.status_code == 200
    assert r.json()["slots"][0]["spool_id"] == 42
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_app.py -k "pull or ace_state" -v`
Expected: FAIL — `/pull` and `/ace-state` return 404.

- [ ] **Step 3: Implement the endpoints**

In `app.py`, add imports near the top (alongside the existing `from .mapping import ...`):

```python
from .ace_state import AceStateClient, AceStateError
from .reconcile import plan_reconcile
```

Inside `create_app`, before the `static_dir` mount (the `StaticFiles` mount at
`/` must remain the LAST route registered), add:

```python
    @app.get("/ace-state")
    async def ace_state():
        client = AceStateClient(cfg.ace_state_url)
        try:
            return await client.get_ace_state(cfg.printer_id)
        except AceStateError as e:
            raise HTTPException(status_code=502, detail=str(e))

    @app.post("/pull")
    async def pull():
        # 1. Desired state from FilamentHub's priority-resolved seam.
        try:
            state = await AceStateClient(cfg.ace_state_url).get_ace_state(cfg.printer_id)
        except AceStateError as e:
            raise HTTPException(status_code=502, detail=str(e))
        winners = state.get("slots", [])
        disputed = state.get("disputed", [])

        # 2. Brand enrichment from the spool inventory (ace-state has no vendor).
        sm = SpoolmanClient(cfg.filamenthub_url, cfg.printer_id)
        try:
            spools = await sm.list_spools(raise_on_error=True)
            brand_by_spool_id = {s["spool_id"]: (s.get("vendor") or "") for s in spools}
        except httpx.HTTPError as e:
            log.warning("brand enrichment failed, proceeding blank: %s", e)
            brand_by_spool_id = {}

        # 3. Current multiACE overrides -> scoped reconcile plan.
        ma = MultiAceClient(cfg.multiace_url)
        try:
            current = await ma.list_overrides()
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"multiACE unreachable: {e}")
        to_apply, to_clear = plan_reconcile(winners, current.keys(), brand_by_spool_id)

        # 4. Execute — collect failures, never abort mid-loop.
        applied, cleared, errors = [], [], []
        for payload in to_apply:
            try:
                await ma.set_override(**payload)
                applied.append(payload)
            except httpx.HTTPError as e:
                errors.append({"action": "apply", "ace": payload["ace"],
                               "slot": payload["slot"], "error": str(e)})
        for ace_idx, slot_idx in to_clear:
            try:
                await ma.clear_override(ace_idx, slot_idx)
                cleared.append({"ace": ace_idx, "slot": slot_idx})
            except httpx.HTTPError as e:
                errors.append({"action": "clear", "ace": ace_idx,
                               "slot": slot_idx, "error": str(e)})

        return {"applied": applied, "cleared": cleared,
                "disputed": disputed, "errors": errors}
```

Note: `SpoolmanClient` is already imported inside `create_app` in the existing
code (`from .spoolman import SpoolmanClient`); keep that import reachable for the
`/pull` handler (move it to module top if the linter flags scope).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_app.py -v`
Expected: all PASS (existing app tests + 4 new).

- [ ] **Step 5: Run the whole suite**

Run: `pytest -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add multiace_plugins/filamenthub/src/filamenthub_plugin/app.py \
        multiace_plugins/filamenthub/tests/test_app.py
git -c user.name=ryvin -c user.email=18613731+ryvin@users.noreply.github.com \
  commit -m "feat(filamenthub): POST /pull + GET /ace-state (label-only mirror)"
```

---

### Task 7: Frontend — "Pull from FilamentHub" button + on-open auto-pull

**Files:**
- Modify: `multiace_plugins/filamenthub/src/filamenthub_plugin/static/index.html`
- Modify: `multiace_plugins/filamenthub/src/filamenthub_plugin/static/app.js`
- Modify: `multiace_plugins/filamenthub/src/filamenthub_plugin/static/style.css`

**Interfaces:**
- Consumes: `POST /pull` (Task 6) → `{applied, cleared, disputed, errors}`.
- Produces: no new backend interface; a button + status/disputes rendering.

There is no unit-test harness for the vanilla frontend; verification is the
Playwright read-only smoke in Step 4 (the button must never trigger motion — it
only calls `/pull`, which is label-only).

- [ ] **Step 1: Add the button and a disputes container**

In `index.html`, add a Pull button next to Refresh and a disputes region above the grid:

```html
  <header class="fh-head">
    <h1>FilamentHub → ACE slots</h1>
    <button id="pull" class="btn">Pull from FilamentHub</button>
    <button id="refresh" class="btn">Refresh</button>
  </header>
  <p id="status" class="status" role="status"></p>
  <section id="disputes" class="disputes" aria-label="Disputed slots" hidden></section>
  <section id="grid" class="grid" aria-label="ACE slots"></section>
```

- [ ] **Step 2: Wire the pull handler + auto-pull on open**

In `app.js`, add (reusing the existing `jpost`, `setStatus`, `$`, and `render` helpers):

```javascript
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
    li.textContent = `ACE ${d.ace + 1} · slot ${d.slot + 1}: spool ${d.spool_id} ` +
                     `also claims this (winner: ${d.winner_spool_id})`;
    ul.appendChild(li);
  });
  box.appendChild(ul);
}

async function pull() {
  setStatus("Pulling from FilamentHub…");
  try {
    const res = await jpost(`${PLUGIN}pull`, {});
    renderDisputes(res.disputed);
    const parts = [`applied ${res.applied.length}`, `cleared ${res.cleared.length}`];
    if (res.errors.length) parts.push(`errors ${res.errors.length}`);
    setStatus(`Pull complete: ${parts.join(", ")}`);
    await render();   // refresh the grid to show the new labels
  } catch (e) {
    setStatus(`Pull failed: ${e.message}`);
  }
}

$("#pull").addEventListener("click", pull);
```

Then find where the app first calls `render()` on load and add an auto-pull right
after it (so opening the tab mirrors FilamentHub once):

```javascript
render().then(pull);
```

(If the existing load line is `render();`, replace it with `render().then(pull);`.)

- [ ] **Step 3: Style the disputes region**

Append to `style.css`:

```css
.disputes {
  margin: 0.5rem 0;
  padding: 0.5rem 0.75rem;
  border-left: 3px solid #d97706;
  background: color-mix(in oklab, #d97706 12%, transparent);
  border-radius: 4px;
  font-size: 0.9rem;
}
.disputes ul { margin: 0.25rem 0 0; padding-left: 1.2rem; }
#pull { background: #2563eb; color: #fff; }
#pull:hover { background: #1d4ed8; }
#pull:focus-visible { outline: 2px solid #93c5fd; outline-offset: 2px; }
```

- [ ] **Step 4: Verify against the live printer (read-only Playwright smoke)**

Preconditions: printer idle (check `print_stats.state` is not `printing`/`paused`),
`DAVINCI_U1_HOST` exported, the plugin serving.

Manually, in a Playwright session against `http://$DAVINCI_U1_HOST/plugin/filamenthub/`:
1. Confirm the "Pull from FilamentHub" button renders.
2. Click it; confirm the status line shows `Pull complete: applied N, cleared M`.
3. Confirm the grid labels update and **no toolhead/ACE motion occurs** (watch the
   printer; `/pull` is label-only by construction).
4. If FilamentHub returns disputes, confirm the amber disputes banner lists them.
5. Capture a screenshot for the PR.

Expected: labels mirror FilamentHub; zero motion; disputes (if any) shown.

- [ ] **Step 5: Commit**

```bash
git add multiace_plugins/filamenthub/src/filamenthub_plugin/static/
git -c user.name=ryvin -c user.email=18613731+ryvin@users.noreply.github.com \
  commit -m "feat(filamenthub): Pull-from-FilamentHub button + disputes UI"
```

---

### Task 8: Docs — README

**Files:**
- Modify: `multiace_plugins/filamenthub/README.md`

**Interfaces:** none.

- [ ] **Step 1: Document the puller**

Add a section to `README.md` describing the new behavior and config:

```markdown
## Pull from FilamentHub (Phase 4)

The **Pull from FilamentHub** button (and auto-pull on tab open) mirrors
FilamentHub's authoritative loaded configuration into multiACE's slot labels —
**label-only, no filament motion**. It reads FilamentHub's
`GET /fleet/api/ace-state?printer=<MULTIACE_PRINTER_ID>` seam (one winner per
slot, `disputed` losers shown but never written), enriches brand from the spool
list, and reconciles multiACE's `/api/slot-override` labels — clearing vacated
slots only on ACEs FilamentHub reports.

| Var | Default | Meaning |
|---|---|---|
| `FILAMENTHUB_ACE_STATE_URL` | `${FILAMENTHUB_URL}/fleet/api/ace-state` | ace-state read seam |

If Pull reports "ace-state not enabled (watcher provider unwired)", the
FilamentHub watcher hasn't injected its `ace_state_provider` — that's a
FilamentHub-side fix (`ryvin/FilamentHub`), not a multiACE change.
```

- [ ] **Step 2: Commit**

```bash
git add multiace_plugins/filamenthub/README.md
git -c user.name=ryvin -c user.email=18613731+ryvin@users.noreply.github.com \
  commit -m "docs(filamenthub): document Phase-4 pull-from-FilamentHub"
```

---

## Self-Review

**Spec coverage:**
- ace-state client (503/502/400/network/schema) → Task 2. ✓
- `POST /pull` reconcile (apply/clear/disputed/errors) → Task 6, pure planner Task 5. ✓
- Mapping incl. brand enrichment + colour normalisation → Task 3 (mapping) + Task 6 (enrichment source). ✓
- Scoped reconcile (known-ACE only) → Task 5 `plan_reconcile` + tests. ✓
- `GET /ace-state` passthrough → Task 6. ✓
- UI button + on-open trigger + disputes banner → Task 7. ✓
- Config `FILAMENTHUB_ACE_STATE_URL` → Task 1. ✓
- Label-only / no motion → Global Constraints + Task 7 Step 4 verification. ✓
- Out-of-scope (physical load, two-way sync, background poll, FilamentHub edits) → not implemented; FilamentHub needs → issue. ✓

**Placeholder scan:** every code step contains complete code; no TBD/TODO/"add error handling" left. ✓

**Type consistency:** `ace_state_row_to_override(row, brand)` (Task 3) is consumed with the same signature in Task 5; `plan_reconcile(winners, keys, brand_by_spool_id) -> (to_apply, to_clear)` matches its use in Task 6; `list_overrides() -> dict` (Task 4) used as `current.keys()` in Task 6; `AceStateClient.get_ace_state` return keys (`slots`/`disputed`) match Task 6 usage. ✓

## Notes / risk

- The app tests mock outbound `httpx` calls with `respx`; the FastAPI
  `TestClient` drives the app, and `respx` intercepts the plugin's own async
  httpx clients (same pattern as the existing `test_app.py` assign/unassign
  tests).
- If `GET /api/slot-override` shape differs on the deployed decay71 build,
  Task 4's single method is the only place to adjust — verified against
  `v0.99.2b:multiace/web/backend/main.py:1813`.
