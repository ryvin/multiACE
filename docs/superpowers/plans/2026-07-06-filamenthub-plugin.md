# FilamentHub → multiACE Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a standalone, reloadable decay71 plugin that adds a "FilamentHub" tab to the multiACE GUI where the operator picks a FilamentHub spool for an ACE slot — labeling the slot in multiACE and recording the location back in FilamentHub.

**Architecture:** A FastAPI sidecar on `127.0.0.1:8089` exposing `GET /integration-manifest` (so decay71 auto-discovers it as an iframe tab), serving a vanilla-JS tab UI, and orchestrating two writes on assign: `POST /api/slot-override` to the local multiACE web (colors the slot) and `assign_spool()` to FilamentHub (sets `extra.filamenthub.location`). The FilamentHub client is ryvin's tested `spoolman.py`, copied in so the plugin is self-contained.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, httpx, pydantic v2; pytest + respx for tests; vanilla HTML/JS/CSS (no build step); BusyBox sysvinit for deploy.

## Global Constraints

- Python **3.11+** (matches multiace_web floor).
- Plugin backend binds **127.0.0.1 only**; port default **8089**, must sit inside decay71's `MULTIACE_PLUGIN_PORTS` (default `8089-8098`).
- Manifest `name` must match `^[A-Za-z0-9_.-]+$` — use **`filamenthub`**.
- Plugin is **fully standalone** under `multiace_plugins/filamenthub/` — no imports from `multiace/` or `multiace_web/` at runtime; decay71 upgrades must never touch it.
- multiACE slot-override payload is exactly `{ace:int, slot:int, material:str, brand:str, subtype:str, color:str}`; color normalized to `#RRGGBB`.
- FilamentHub `extra.filamenthub` is a **double-JSON-encoded** text field — only touch it via the ported `spoolman.py` helpers.
- Never key printer serials or raw filament profiles into shared logs.
- License header GPL-3.0 on new Python files (match repo convention).

---

## File Structure

```
multiace_plugins/filamenthub/
├── pyproject.toml                # package + deps + pytest config
├── README.md                     # what it is, env vars, install
├── src/filamenthub_plugin/
│   ├── __init__.py
│   ├── config.py                 # env → Config dataclass
│   ├── spoolman.py               # ported from ryvin (FilamentHub client)
│   ├── multiace_client.py        # httpx client → local multiACE slot-override
│   ├── mapping.py                # spool dict → slot-override dict
│   ├── app.py                    # create_app(cfg): manifest, static, /spools, /assign, /unassign
│   ├── __main__.py               # load_config() → create_app → uvicorn.run
│   └── static/
│       ├── index.html
│       ├── app.js
│       └── style.css
├── tests/
│   ├── conftest.py               # Config fixture + TestClient
│   ├── test_config.py
│   ├── test_spoolman.py          # copied from ryvin test_filamenthub.py
│   ├── test_multiace_client.py
│   ├── test_mapping.py
│   └── test_app.py               # manifest, /spools, /assign, /unassign (respx)
└── install/
    ├── S66filamenthub-plugin     # BusyBox init script
    └── install_plugin.sh         # deploy to /userdata + register + nginx verify
```

---

### Task 1: Scaffold, config, and manifest endpoint

**Files:**
- Create: `multiace_plugins/filamenthub/pyproject.toml`
- Create: `multiace_plugins/filamenthub/src/filamenthub_plugin/__init__.py`
- Create: `multiace_plugins/filamenthub/src/filamenthub_plugin/config.py`
- Create: `multiace_plugins/filamenthub/src/filamenthub_plugin/app.py`
- Create: `multiace_plugins/filamenthub/tests/conftest.py`
- Create: `multiace_plugins/filamenthub/tests/test_config.py`
- Create: `multiace_plugins/filamenthub/tests/test_app.py`

**Interfaces:**
- Produces: `Config` frozen dataclass `{filamenthub_url:str, printer_id:str, multiace_url:str, port:int}`; `load_config() -> Config`; `create_app(cfg: Config) -> FastAPI` with route `GET /integration-manifest`.

- [ ] **Step 1: Write pyproject.toml**

```toml
[project]
name = "filamenthub-plugin"
version = "0.1.0"
description = "FilamentHub -> multiACE decay71 plugin (slot filament picker)"
requires-python = ">=3.11"
dependencies = ["fastapi", "uvicorn[standard]", "httpx", "pydantic>=2"]

[project.optional-dependencies]
dev = ["pytest", "pytest-asyncio", "respx", "httpx"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
asyncio_mode = "auto"
```

- [ ] **Step 2: Write the failing manifest + config tests**

`tests/conftest.py`:
```python
import pytest
from fastapi.testclient import TestClient
from filamenthub_plugin.config import Config
from filamenthub_plugin.app import create_app

@pytest.fixture
def cfg():
    return Config(
        filamenthub_url="http://fh.test",
        printer_id="davinci-u1",
        multiace_url="http://ma.test",
        port=8089,
    )

@pytest.fixture
def client(cfg):
    return TestClient(create_app(cfg))
```

`tests/test_config.py`:
```python
import os
from filamenthub_plugin.config import load_config

def test_load_config_reads_env(monkeypatch):
    monkeypatch.setenv("FILAMENTHUB_URL", "http://fh.local")
    monkeypatch.setenv("MULTIACE_PRINTER_ID", "davinci-u1")
    monkeypatch.delenv("MULTIACE_URL", raising=False)
    monkeypatch.delenv("FILAMENTHUB_PLUGIN_PORT", raising=False)
    cfg = load_config()
    assert cfg.filamenthub_url == "http://fh.local"
    assert cfg.printer_id == "davinci-u1"
    assert cfg.multiace_url == "http://127.0.0.1:7126"   # default
    assert cfg.port == 8089                               # default
```

`tests/test_app.py`:
```python
def test_manifest_shape(client):
    r = client.get("/integration-manifest")
    assert r.status_code == 200
    m = r.json()
    assert m["name"] == "filamenthub"
    assert m["label"] == "FilamentHub"
    assert m["ui_url"] == "/"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd multiace_plugins/filamenthub && pip install -e ".[dev]" && pytest tests/test_config.py tests/test_app.py -v`
Expected: FAIL — `ModuleNotFoundError: filamenthub_plugin.config` / `app`.

- [ ] **Step 4: Write config.py**

```python
"""Environment configuration for the FilamentHub plugin."""
from __future__ import annotations
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    filamenthub_url: str
    printer_id: str
    multiace_url: str
    port: int


def load_config() -> Config:
    return Config(
        filamenthub_url=os.environ["FILAMENTHUB_URL"],
        printer_id=os.environ["MULTIACE_PRINTER_ID"],
        multiace_url=os.environ.get("MULTIACE_URL", "http://127.0.0.1:7126"),
        port=int(os.environ.get("FILAMENTHUB_PLUGIN_PORT", "8089")),
    )
```

- [ ] **Step 5: Write app.py (manifest only for now)**

```python
"""FilamentHub plugin FastAPI app: manifest + (later) picker endpoints."""
from __future__ import annotations
from fastapi import FastAPI
from .config import Config

MANIFEST = {"name": "filamenthub", "label": "FilamentHub",
            "version": "0.1.0", "ui_url": "/"}


def create_app(cfg: Config) -> FastAPI:
    app = FastAPI(title="FilamentHub Plugin")
    app.state.cfg = cfg

    @app.get("/integration-manifest")
    def integration_manifest():
        return MANIFEST

    return app
```

- [ ] **Step 6: Write __init__.py (empty package marker)**

```python
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_config.py tests/test_app.py -v`
Expected: PASS (3 tests).

- [ ] **Step 8: Commit**

```bash
git add multiace_plugins/filamenthub/pyproject.toml multiace_plugins/filamenthub/src multiace_plugins/filamenthub/tests
git commit -m "feat(fh-plugin): scaffold, config, and integration-manifest endpoint"
```

---

### Task 2: Port the FilamentHub client (spoolman.py)

**Files:**
- Create: `multiace_plugins/filamenthub/src/filamenthub_plugin/spoolman.py` (copy of `multiace_web/src/multiace_web/spoolman.py`)
- Create: `multiace_plugins/filamenthub/tests/test_spoolman.py` (copy of `multiace_web/tests/test_filamenthub.py`, imports adjusted)

**Interfaces:**
- Produces: `SpoolmanClient(base_url, printer_id)` with async `list_spools() -> list[dict]`, `assign_spool(spool_id, ace, slot) -> dict`, `unassign_slot(ace, slot) -> int|None`. Each spool dict: `{spool_id, name, material, color, vendor, weight_remaining_g, location}` where `location` is `{ace, slot}|None`.

- [ ] **Step 1: Copy the client verbatim**

Copy `multiace_web/src/multiace_web/spoolman.py` to `multiace_plugins/filamenthub/src/filamenthub_plugin/spoolman.py` unchanged (it has no `multiace_web` imports — only stdlib + httpx). Verify the top of the file matches the source (module docstring, `_decode_fh`, `_encode_fh`, `SpoolBinding`, `SpoolmanClient`).

- [ ] **Step 2: Copy the tests, fix the import path**

Copy `multiace_web/tests/test_filamenthub.py` to `multiace_plugins/filamenthub/tests/test_spoolman.py`. Change every `from multiace_web.spoolman import ...` to `from filamenthub_plugin.spoolman import ...`. Change nothing else.

- [ ] **Step 3: Run the copied tests**

Run: `pytest tests/test_spoolman.py -v`
Expected: PASS — same count as the source suite (they exercise `list_spools`, `assign_spool`, `unassign_slot`, and the double-encode round-trip via respx).

- [ ] **Step 4: Commit**

```bash
git add multiace_plugins/filamenthub/src/filamenthub_plugin/spoolman.py multiace_plugins/filamenthub/tests/test_spoolman.py
git commit -m "feat(fh-plugin): vendor ryvin spoolman.py FilamentHub client + tests"
```

---

### Task 3: Local multiACE slot-override client

**Files:**
- Create: `multiace_plugins/filamenthub/src/filamenthub_plugin/multiace_client.py`
- Create: `multiace_plugins/filamenthub/tests/test_multiace_client.py`

**Interfaces:**
- Consumes: nothing from prior tasks.
- Produces: `MultiAceClient(base_url, timeout_s=3.0)` with async `set_override(ace, slot, material, brand, subtype, color) -> dict` (POST `/api/slot-override`) and `clear_override(ace, slot) -> dict` (DELETE `/api/slot-override/{ace}/{slot}`). Both `raise_for_status()`.

- [ ] **Step 1: Write the failing tests**

`tests/test_multiace_client.py`:
```python
import httpx
import pytest
import respx
from filamenthub_plugin.multiace_client import MultiAceClient


@respx.mock
@pytest.mark.asyncio
async def test_set_override_posts_payload():
    route = respx.post("http://ma.test/api/slot-override").mock(
        return_value=httpx.Response(200, json={"ok": True, "key": "0_1"}))
    ma = MultiAceClient("http://ma.test")
    out = await ma.set_override(ace=0, slot=1, material="PLA",
                                brand="Generic", subtype="Blue", color="#0000ff")
    assert out == {"ok": True, "key": "0_1"}
    sent = route.calls.last.request
    import json
    body = json.loads(sent.content)
    assert body == {"ace": 0, "slot": 1, "material": "PLA",
                    "brand": "Generic", "subtype": "Blue", "color": "#0000ff"}


@respx.mock
@pytest.mark.asyncio
async def test_clear_override_deletes():
    respx.delete("http://ma.test/api/slot-override/0/1").mock(
        return_value=httpx.Response(200, json={"ok": True}))
    ma = MultiAceClient("http://ma.test")
    out = await ma.clear_override(0, 1)
    assert out == {"ok": True}


@respx.mock
@pytest.mark.asyncio
async def test_set_override_raises_on_5xx():
    respx.post("http://ma.test/api/slot-override").mock(
        return_value=httpx.Response(502))
    ma = MultiAceClient("http://ma.test")
    with pytest.raises(httpx.HTTPStatusError):
        await ma.set_override(ace=0, slot=0, material="", brand="",
                              subtype="", color="")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_multiace_client.py -v`
Expected: FAIL — `ModuleNotFoundError: filamenthub_plugin.multiace_client`.

- [ ] **Step 3: Write multiace_client.py**

```python
"""Async client for the local multiACE web slot-override endpoint."""
from __future__ import annotations
import httpx


class MultiAceClient:
    def __init__(self, base_url: str, timeout_s: float = 3.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout_s

    async def set_override(self, ace: int, slot: int, material: str,
                           brand: str, subtype: str, color: str) -> dict:
        payload = {"ace": ace, "slot": slot, "material": material,
                   "brand": brand, "subtype": subtype, "color": color}
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.post(f"{self._base}/api/slot-override", json=payload)
            r.raise_for_status()
            return r.json()

    async def clear_override(self, ace: int, slot: int) -> dict:
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.delete(f"{self._base}/api/slot-override/{ace}/{slot}")
            r.raise_for_status()
            return r.json()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_multiace_client.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add multiace_plugins/filamenthub/src/filamenthub_plugin/multiace_client.py multiace_plugins/filamenthub/tests/test_multiace_client.py
git commit -m "feat(fh-plugin): multiACE slot-override client"
```

---

### Task 4: Spool→override mapping and /spools endpoint

**Files:**
- Create: `multiace_plugins/filamenthub/src/filamenthub_plugin/mapping.py`
- Modify: `multiace_plugins/filamenthub/src/filamenthub_plugin/app.py`
- Create: `multiace_plugins/filamenthub/tests/test_mapping.py`
- Modify: `multiace_plugins/filamenthub/tests/test_app.py`

**Interfaces:**
- Consumes: `SpoolmanClient.list_spools()` (Task 2); spool dict shape.
- Produces: `normalize_color(color: str|None) -> str`; `spool_to_override(spool: dict, ace: int, slot: int) -> dict` returning `{ace, slot, material, brand, subtype, color}`. Route `GET /spools -> {"spools": [...]}`.

- [ ] **Step 1: Write the failing mapping tests**

`tests/test_mapping.py`:
```python
from filamenthub_plugin.mapping import normalize_color, spool_to_override


def test_normalize_color_adds_hash():
    assert normalize_color("0000ff") == "#0000ff"

def test_normalize_color_keeps_hash():
    assert normalize_color("#00ff00") == "#00ff00"

def test_normalize_color_blank_on_none():
    assert normalize_color(None) == ""
    assert normalize_color("") == ""

def test_spool_to_override_maps_fields():
    spool = {"spool_id": 7, "name": "Galaxy Blue", "material": "PLA",
             "color": "0000ff", "vendor": "Generic",
             "weight_remaining_g": 812.0, "location": None}
    ov = spool_to_override(spool, ace=1, slot=2)
    assert ov == {"ace": 1, "slot": 2, "material": "PLA",
                  "brand": "Generic", "subtype": "Galaxy Blue",
                  "color": "#0000ff"}

def test_spool_to_override_handles_missing_fields():
    ov = spool_to_override({"spool_id": 1}, ace=0, slot=0)
    assert ov == {"ace": 0, "slot": 0, "material": "",
                  "brand": "", "subtype": "", "color": ""}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mapping.py -v`
Expected: FAIL — `ModuleNotFoundError: filamenthub_plugin.mapping`.

- [ ] **Step 3: Write mapping.py**

```python
"""Map a FilamentHub spool record to a multiACE slot-override payload.

`subtype` is mapped from the spool `name` (the most informative variant we
have); change here if FilamentHub later exposes a dedicated SKU field.
"""
from __future__ import annotations


def normalize_color(color: str | None) -> str:
    if not color:
        return ""
    color = color.strip()
    return color if color.startswith("#") else f"#{color}"


def spool_to_override(spool: dict, ace: int, slot: int) -> dict:
    return {
        "ace": ace,
        "slot": slot,
        "material": spool.get("material") or "",
        "brand": spool.get("vendor") or "",
        "subtype": spool.get("name") or "",
        "color": normalize_color(spool.get("color")),
    }
```

- [ ] **Step 4: Add the /spools endpoint to app.py**

Add inside `create_app`, after the manifest route:
```python
    from .spoolman import SpoolmanClient

    @app.get("/spools")
    async def spools():
        sm = SpoolmanClient(cfg.filamenthub_url, cfg.printer_id)
        return {"spools": await sm.list_spools()}
```

- [ ] **Step 5: Write the failing /spools app test**

Append to `tests/test_app.py`:
```python
import httpx
import respx


@respx.mock
def test_spools_endpoint_lists_inventory(client):
    respx.get("http://fh.test/api/v1/spool").mock(return_value=httpx.Response(
        200, json=[{"id": 7, "archived": False,
                    "remaining_weight": 800.0, "extra": {},
                    "filament": {"name": "Galaxy Blue", "material": "PLA",
                                 "color_hex": "0000ff",
                                 "vendor": {"name": "Generic"}}}]))
    r = client.get("/spools")
    assert r.status_code == 200
    spools = r.json()["spools"]
    assert spools[0]["spool_id"] == 7
    assert spools[0]["material"] == "PLA"
    assert spools[0]["vendor"] == "Generic"
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_mapping.py tests/test_app.py -v`
Expected: PASS (manifest + 5 mapping + 1 spools).

- [ ] **Step 7: Commit**

```bash
git add multiace_plugins/filamenthub/src/filamenthub_plugin/mapping.py multiace_plugins/filamenthub/src/filamenthub_plugin/app.py multiace_plugins/filamenthub/tests/test_mapping.py multiace_plugins/filamenthub/tests/test_app.py
git commit -m "feat(fh-plugin): spool->override mapping and /spools endpoint"
```

---

### Task 5: /assign orchestration (FilamentHub write-back + multiACE label)

**Files:**
- Modify: `multiace_plugins/filamenthub/src/filamenthub_plugin/app.py`
- Modify: `multiace_plugins/filamenthub/tests/test_app.py`

**Interfaces:**
- Consumes: `SpoolmanClient.list_spools/assign_spool` (Task 2), `MultiAceClient.set_override` (Task 3), `spool_to_override` (Task 4).
- Produces: Route `POST /assign` with body `{spool_id:int, ace:int, slot:int}` → `{ok:true, location:{...}, override:{...}}`. Order: (1) FilamentHub `assign_spool`, then (2) multiACE `set_override`. 404 if spool_id not in inventory; 502 with stage detail on either write failure.

- [ ] **Step 1: Write the failing tests (success + both failure modes)**

Append to `tests/test_app.py`:
```python
def _spool_route():
    return respx.get("http://fh.test/api/v1/spool").mock(return_value=httpx.Response(
        200, json=[{"id": 7, "archived": False, "remaining_weight": 800.0,
                    "extra": {},
                    "filament": {"name": "Galaxy Blue", "material": "PLA",
                                 "color_hex": "0000ff",
                                 "vendor": {"name": "Generic"}}}]))


@respx.mock
def test_assign_writes_both_sides(client):
    _spool_route()
    respx.get("http://fh.test/api/v1/spool/7").mock(return_value=httpx.Response(
        200, json={"id": 7, "extra": {}}))
    respx.patch("http://fh.test/api/v1/spool/7").mock(return_value=httpx.Response(200))
    ov_route = respx.post("http://ma.test/api/slot-override").mock(
        return_value=httpx.Response(200, json={"ok": True, "key": "1_2"}))
    r = client.post("/assign", json={"spool_id": 7, "ace": 1, "slot": 2})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["location"] == {"printer": "davinci-u1", "ace": 1, "slot": 2}
    import json
    sent = json.loads(ov_route.calls.last.request.content)
    assert sent["color"] == "#0000ff" and sent["brand"] == "Generic"


@respx.mock
def test_assign_unknown_spool_404(client):
    _spool_route()
    r = client.post("/assign", json={"spool_id": 999, "ace": 0, "slot": 0})
    assert r.status_code == 404


@respx.mock
def test_assign_multiace_failure_502(client):
    _spool_route()
    respx.get("http://fh.test/api/v1/spool/7").mock(return_value=httpx.Response(
        200, json={"id": 7, "extra": {}}))
    respx.patch("http://fh.test/api/v1/spool/7").mock(return_value=httpx.Response(200))
    respx.post("http://ma.test/api/slot-override").mock(
        return_value=httpx.Response(502))
    r = client.post("/assign", json={"spool_id": 7, "ace": 1, "slot": 2})
    assert r.status_code == 502
    assert "multiace" in r.json()["detail"].lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_app.py -k assign -v`
Expected: FAIL — no `/assign` route (404 for all).

- [ ] **Step 3: Implement /assign in app.py**

Add imports at top of `app.py`:
```python
import httpx
from fastapi import HTTPException
from pydantic import BaseModel
from .multiace_client import MultiAceClient
from .mapping import spool_to_override
```
Add the model above `create_app`:
```python
class AssignReq(BaseModel):
    spool_id: int
    ace: int
    slot: int
```
Add inside `create_app` after `/spools`:
```python
    @app.post("/assign")
    async def assign(req: AssignReq):
        sm = SpoolmanClient(cfg.filamenthub_url, cfg.printer_id)
        spools = await sm.list_spools()
        spool = next((s for s in spools if s["spool_id"] == req.spool_id), None)
        if spool is None:
            raise HTTPException(status_code=404, detail="spool not found in FilamentHub")
        # 1. write-back to FilamentHub
        try:
            location = await sm.assign_spool(req.spool_id, req.ace, req.slot)
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"FilamentHub write failed: {e}")
        # 2. label the slot in multiACE
        ma = MultiAceClient(cfg.multiace_url)
        try:
            override = await ma.set_override(**spool_to_override(spool, req.ace, req.slot))
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502,
                detail=f"multiACE slot-override failed (FilamentHub already updated): {e}")
        return {"ok": True, "location": location, "override": override}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_app.py -k assign -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add multiace_plugins/filamenthub/src/filamenthub_plugin/app.py multiace_plugins/filamenthub/tests/test_app.py
git commit -m "feat(fh-plugin): /assign orchestrates FilamentHub write-back + multiACE label"
```

---

### Task 6: /unassign orchestration

**Files:**
- Modify: `multiace_plugins/filamenthub/src/filamenthub_plugin/app.py`
- Modify: `multiace_plugins/filamenthub/tests/test_app.py`

**Interfaces:**
- Consumes: `SpoolmanClient.unassign_slot` (Task 2), `MultiAceClient.clear_override` (Task 3).
- Produces: Route `POST /unassign` with body `{ace:int, slot:int}` → `{ok:true, cleared_spool_id:int|None}`. Clears FilamentHub location then multiACE override. multiACE clear failure → 502.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_app.py`:
```python
@respx.mock
def test_unassign_clears_both_sides(client):
    respx.get("http://fh.test/api/v1/spool").mock(return_value=httpx.Response(
        200, json=[{"id": 7, "archived": False, "remaining_weight": 800.0,
                    "extra": {"filamenthub": json.dumps(json.dumps(
                        {"schema": 1, "location":
                         {"printer": "davinci-u1", "ace": 1, "slot": 2}}))},
                    "filament": {"name": "Galaxy Blue", "material": "PLA",
                                 "color_hex": "0000ff",
                                 "vendor": {"name": "Generic"}}}]))
    respx.get("http://fh.test/api/v1/spool/7").mock(return_value=httpx.Response(
        200, json={"id": 7, "extra": {}}))
    respx.patch("http://fh.test/api/v1/spool/7").mock(return_value=httpx.Response(200))
    respx.delete("http://ma.test/api/slot-override/1/2").mock(
        return_value=httpx.Response(200, json={"ok": True}))
    r = client.post("/unassign", json={"ace": 1, "slot": 2})
    assert r.status_code == 200
    assert r.json()["cleared_spool_id"] == 7
```
(`import json` is already present from Task 5's tests.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_app.py -k unassign -v`
Expected: FAIL — no `/unassign` route.

- [ ] **Step 3: Implement /unassign in app.py**

Add model above `create_app`:
```python
class UnassignReq(BaseModel):
    ace: int
    slot: int
```
Add inside `create_app` after `/assign`:
```python
    @app.post("/unassign")
    async def unassign(req: UnassignReq):
        sm = SpoolmanClient(cfg.filamenthub_url, cfg.printer_id)
        try:
            cleared = await sm.unassign_slot(req.ace, req.slot)
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"FilamentHub clear failed: {e}")
        ma = MultiAceClient(cfg.multiace_url)
        try:
            await ma.clear_override(req.ace, req.slot)
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502,
                detail=f"multiACE clear failed (FilamentHub already cleared): {e}")
        return {"ok": True, "cleared_spool_id": cleared}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_app.py -k unassign -v`
Expected: PASS.

- [ ] **Step 5: Run the whole suite**

Run: `pytest -v`
Expected: PASS — all tasks' tests green.

- [ ] **Step 6: Commit**

```bash
git add multiace_plugins/filamenthub/src/filamenthub_plugin/app.py multiace_plugins/filamenthub/tests/test_app.py
git commit -m "feat(fh-plugin): /unassign clears FilamentHub location + multiACE override"
```

---

### Task 7: Tab UI (static frontend)

**Files:**
- Create: `multiace_plugins/filamenthub/src/filamenthub_plugin/static/index.html`
- Create: `multiace_plugins/filamenthub/src/filamenthub_plugin/static/app.js`
- Create: `multiace_plugins/filamenthub/src/filamenthub_plugin/static/style.css`
- Modify: `multiace_plugins/filamenthub/src/filamenthub_plugin/app.py` (mount static at `/`)
- Modify: `multiace_plugins/filamenthub/tests/test_app.py` (assert `/` serves HTML)

**Interfaces:**
- Consumes: `GET /multiace/api/plugin-api/state` (occupancy, same-origin), `GET /plugin/filamenthub/spools`, `POST /plugin/filamenthub/assign`, `POST /plugin/filamenthub/unassign`.
- Produces: iframe UI served at `GET /` (StaticFiles, `html=True`).

- [ ] **Step 1: Mount static files in app.py**

Add near the top imports:
```python
from pathlib import Path
from fastapi.staticfiles import StaticFiles
```
At the END of `create_app`, after all API routes (StaticFiles at `/` must be mounted last so it doesn't shadow `/spools`, `/assign`, etc.):
```python
    static_dir = Path(__file__).parent / "static"
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app
```
Remove the old `return app` that preceded this block.

- [ ] **Step 2: Write index.html**

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>FilamentHub</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <header class="fh-head">
    <h1>FilamentHub → ACE slots</h1>
    <button id="refresh" class="btn">Refresh</button>
  </header>
  <p id="status" class="status" role="status"></p>
  <section id="grid" class="grid" aria-label="ACE slots"></section>

  <dialog id="picker" class="picker">
    <h2 id="picker-title">Pick a spool</h2>
    <input id="filter" class="filter" type="search" placeholder="Filter by name / material / vendor…" autocomplete="off">
    <ul id="spool-list" class="spool-list"></ul>
    <menu class="picker-actions">
      <button id="clear-slot" class="btn btn-ghost">Clear slot</button>
      <button id="cancel" class="btn btn-ghost" value="cancel">Cancel</button>
    </menu>
  </dialog>

  <script src="app.js"></script>
</body>
</html>
```

- [ ] **Step 3: Write style.css**

```css
:root {
  --bg: #14171c; --surface: #1e232b; --line: #2c333d;
  --text: #e7ecf2; --muted: #96a0ad; --accent: #4f9dff;
  --radius: 10px; --space: 14px;
}
* { box-sizing: border-box; }
body { margin: 0; font: 15px/1.4 system-ui, sans-serif;
  background: var(--bg); color: var(--text); padding: var(--space); }
.fh-head { display: flex; align-items: center; justify-content: space-between; gap: var(--space); }
h1 { font-size: 1.15rem; margin: 0; }
.status { color: var(--muted); min-height: 1.2em; margin: 8px 0; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: var(--space); }
.ace-group { grid-column: 1 / -1; color: var(--muted); font-weight: 600; margin-top: 8px; }
.slot { background: var(--surface); border: 1px solid var(--line);
  border-radius: var(--radius); padding: 12px; cursor: pointer;
  display: flex; flex-direction: column; gap: 6px; transition: border-color .15s, transform .1s; }
.slot:hover { border-color: var(--accent); transform: translateY(-1px); }
.slot:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.slot .swatch { width: 100%; height: 26px; border-radius: 6px; border: 1px solid var(--line); }
.slot .name { font-weight: 600; }
.slot .meta { color: var(--muted); font-size: .85rem; }
.slot.empty .name { color: var(--muted); font-style: italic; }
.btn { background: var(--accent); color: #05121f; border: 0; border-radius: 8px;
  padding: 8px 14px; font-weight: 600; cursor: pointer; }
.btn-ghost { background: transparent; color: var(--text); border: 1px solid var(--line); }
.picker { background: var(--surface); color: var(--text); border: 1px solid var(--line);
  border-radius: var(--radius); width: min(520px, 92vw); }
.picker::backdrop { background: rgba(0,0,0,.55); }
.filter { width: 100%; padding: 9px; margin: 8px 0; border-radius: 8px;
  border: 1px solid var(--line); background: var(--bg); color: var(--text); }
.spool-list { list-style: none; margin: 0; padding: 0; max-height: 46vh; overflow-y: auto; }
.spool-list li { display: flex; align-items: center; gap: 10px; padding: 9px 6px;
  border-radius: 8px; cursor: pointer; }
.spool-list li:hover { background: var(--bg); }
.spool-list .swatch { width: 22px; height: 22px; border-radius: 5px; border: 1px solid var(--line); flex: none; }
.picker-actions { display: flex; justify-content: flex-end; gap: 8px; padding: 0; margin: 12px 0 0; }
```

- [ ] **Step 4: Write app.js**

```javascript
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
  el.innerHTML = `<span class="swatch" style="background:${color || "transparent"}"></span>
    <span class="name">${label || "empty"}</span>
    <span class="meta">ACE ${ace + 1} · slot ${slot + 1}</span>`;
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
      li.innerHTML = `<span class="swatch" style="background:${color}"></span>
        <span><strong>${s.name || "?"}</strong> — ${s.material || ""} ${s.vendor ? "· " + s.vendor : ""}</span>`;
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
```

- [ ] **Step 5: Add the static-serving app test**

Append to `tests/test_app.py`:
```python
def test_root_serves_ui(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "FilamentHub" in r.text
```

- [ ] **Step 6: Run tests to verify pass (and static mount didn't shadow APIs)**

Run: `pytest tests/test_app.py -v`
Expected: PASS — manifest, /spools, assign×3, unassign, and the new root-UI test all green.

- [ ] **Step 7: Commit**

```bash
git add multiace_plugins/filamenthub/src/filamenthub_plugin/static multiace_plugins/filamenthub/src/filamenthub_plugin/app.py multiace_plugins/filamenthub/tests/test_app.py
git commit -m "feat(fh-plugin): tab UI (grid + spool picker) served at /"
```

---

### Task 8: Runner + printer install (init script, installer, nginx verify)

**Files:**
- Create: `multiace_plugins/filamenthub/src/filamenthub_plugin/__main__.py`
- Create: `multiace_plugins/filamenthub/install/S66filamenthub-plugin`
- Create: `multiace_plugins/filamenthub/install/install_plugin.sh`
- Create: `multiace_plugins/filamenthub/README.md`

**Interfaces:**
- Consumes: `load_config()` (Task 1), `create_app` (Tasks 1–7).
- Produces: `python -m filamenthub_plugin` serving uvicorn on `127.0.0.1:$FILAMENTHUB_PLUGIN_PORT`; deploy under `/userdata/filamenthub-plugin`; init `S66filamenthub-plugin`.

- [ ] **Step 1: Write __main__.py**

```python
"""Run the FilamentHub plugin sidecar."""
from __future__ import annotations
import uvicorn
from .config import load_config
from .app import create_app


def main() -> None:
    cfg = load_config()
    app = create_app(cfg)
    uvicorn.run(app, host="127.0.0.1", port=cfg.port, log_level="info")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke-run locally to confirm it boots and serves the manifest**

Run:
```bash
FILAMENTHUB_URL=http://127.0.0.1:9 MULTIACE_PRINTER_ID=test FILAMENTHUB_PLUGIN_PORT=8089 \
  python -m filamenthub_plugin & sleep 2
curl -s http://127.0.0.1:8089/integration-manifest; echo
kill %1
```
Expected output line: `{"name":"filamenthub","label":"FilamentHub","version":"0.1.0","ui_url":"/"}`

- [ ] **Step 3: Write the BusyBox init script `install/S66filamenthub-plugin`**

```sh
#!/bin/sh
# FilamentHub multiACE plugin — BusyBox sysvinit
APP_DIR=/userdata/filamenthub-plugin
VENV=$APP_DIR/.venv
LOGFILE=/home/lava/printer_data/logs/filamenthub-plugin.log
PIDFILE=/var/run/filamenthub-plugin.pid
export FILAMENTHUB_URL="${FILAMENTHUB_URL:-http://127.0.0.1:7912}"
export MULTIACE_PRINTER_ID="${MULTIACE_PRINTER_ID:-davinci-u1}"
export MULTIACE_URL="${MULTIACE_URL:-http://127.0.0.1:7126}"
export FILAMENTHUB_PLUGIN_PORT="${FILAMENTHUB_PLUGIN_PORT:-8089}"
export PYTHONPATH="$APP_DIR/src"

start() {
  echo "Starting filamenthub-plugin"
  start-stop-daemon -S -b -m -p "$PIDFILE" \
    -x "$VENV/bin/python" -- -m filamenthub_plugin >>"$LOGFILE" 2>&1
}
stop() {
  echo "Stopping filamenthub-plugin"
  start-stop-daemon -K -p "$PIDFILE" 2>/dev/null
  rm -f "$PIDFILE"
}
case "$1" in
  start) start ;;
  stop) stop ;;
  restart) stop; sleep 1; start ;;
  *) echo "Usage: $0 {start|stop|restart}"; exit 1 ;;
esac
```

- [ ] **Step 4: Write `install/install_plugin.sh`**

```sh
#!/bin/sh
# Deploy the FilamentHub plugin to the printer's persistent partition.
# Usage (on printer): sh install_plugin.sh
set -e
SRC="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR=/userdata/filamenthub-plugin
NGINX_DROPIN=/etc/nginx/fluidd.d/multiace-plugin.conf

echo "== Deploying to $APP_DIR =="
mkdir -p "$APP_DIR"
cp -r "$SRC/src" "$SRC/pyproject.toml" "$APP_DIR/"

echo "== Python venv + deps =="
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --no-input fastapi "uvicorn[standard]" httpx "pydantic>=2"

echo "== Registering init script =="
cp "$SRC/install/S66filamenthub-plugin" /etc/init.d/S66filamenthub-plugin
chmod +x /etc/init.d/S66filamenthub-plugin

echo "== Verifying nginx /plugin/ route (required for the iframe) =="
if grep -rqs "location /plugin/" /etc/nginx/ ; then
  echo "  OK: an nginx 'location /plugin/' already exists (decay71 plugin routing present)."
else
  echo "  Adding $NGINX_DROPIN → proxy /plugin/ to 127.0.0.1:7126"
  cat > "$NGINX_DROPIN" <<'EOF'
location /plugin/ {
    proxy_pass http://127.0.0.1:7126;
    proxy_set_header Host $host;
    proxy_http_version 1.1;
}
EOF
  nginx -t && /etc/init.d/S50nginx reload 2>/dev/null || nginx -s reload
fi

echo "== Starting plugin =="
/etc/init.d/S66filamenthub-plugin restart
sleep 2
curl -s http://127.0.0.1:8089/integration-manifest && echo && echo "Install OK."
```

- [ ] **Step 5: Write README.md**

````markdown
# FilamentHub → multiACE plugin

A standalone decay71 plugin: adds a **FilamentHub** tab to the multiACE GUI where you
pick a spool from FilamentHub inventory for an ACE slot. It labels the slot in multiACE
(`POST /api/slot-override`) and records the spool's location back in FilamentHub.

## Environment

| Var | Default | Meaning |
|---|---|---|
| `FILAMENTHUB_URL` | — (required) | FilamentHub/Spoolman base URL |
| `MULTIACE_PRINTER_ID` | `davinci-u1` | id used in `extra.filamenthub.location.printer` |
| `MULTIACE_URL` | `http://127.0.0.1:7126` | local multiACE web |
| `FILAMENTHUB_PLUGIN_PORT` | `8089` | must be within decay71 `MULTIACE_PLUGIN_PORTS` (8089–8098) |

## Local dev

```bash
cd multiace_plugins/filamenthub && pip install -e ".[dev]" && pytest
FILAMENTHUB_URL=http://<fh> MULTIACE_PRINTER_ID=davinci-u1 python -m filamenthub_plugin
```

## Printer install

Copy this folder to the printer, then: `sh install/install_plugin.sh`
(edit `FILAMENTHUB_URL`/`MULTIACE_PRINTER_ID` in `/etc/init.d/S66filamenthub-plugin` first).
````

- [ ] **Step 6: Commit**

```bash
git add multiace_plugins/filamenthub/src/filamenthub_plugin/__main__.py multiace_plugins/filamenthub/install multiace_plugins/filamenthub/README.md
git commit -m "feat(fh-plugin): uvicorn runner, BusyBox init, installer with nginx /plugin/ verify"
```

---

### Task 9: Live E2E smoke (Playwright, per project rule)

**Files:**
- Create: `multiace_plugins/filamenthub/tools/e2e_filamenthub.py`

**Interfaces:**
- Consumes: the deployed plugin + decay71 GUI on the printer.
- Produces: a read-mostly Playwright check; the single write (assign) is gated behind an explicit `--allow-write` flag and a `print_stats.state` pre-flight.

- [ ] **Step 1: Write the E2E script**

```python
"""Live smoke for the FilamentHub tab. Read-only unless --allow-write.
Usage: python tools/e2e_filamenthub.py [--allow-write]
Env: DAVINCI_U1_HOST (default 192.168.1.136)."""
import os, sys, httpx
from playwright.sync_api import sync_playwright

HOST = os.environ.get("DAVINCI_U1_HOST", "192.168.1.136")
BASE = f"http://{HOST}"
ALLOW_WRITE = "--allow-write" in sys.argv


def preflight_safe():
    r = httpx.get(f"http://{HOST}:7125/printer/objects/query?print_stats", timeout=5)
    state = r.json()["result"]["status"]["print_stats"]["state"]
    print(f"print_stats.state = {state}")
    return state in ("standby", "complete", "cancelled", "error")


def main():
    if ALLOW_WRITE and not preflight_safe():
        sys.exit("UNSAFE: a print is active — refusing to write.")
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.goto(f"{BASE}/multiace/", wait_until="networkidle")
        pg.get_by_role("button", name="FilamentHub").click()
        frame = pg.frame_locator("iframe.plugin-iframe")
        frame.get_by_role("heading", name="FilamentHub → ACE slots").wait_for()
        pg.screenshot(path="fh-tab.png", full_page=True)
        print("FilamentHub tab rendered; screenshot fh-tab.png")
        if ALLOW_WRITE:
            frame.locator(".slot").first.click()
            frame.locator(".spool-list li").first.click()
            frame.get_by_text("spools in FilamentHub").wait_for()
            print("assign path exercised")
        b.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the read-only smoke (after Task 8 deploy)**

Run: `python multiace_plugins/filamenthub/tools/e2e_filamenthub.py`
Expected: prints `print_stats.state = …`, `FilamentHub tab rendered; screenshot fh-tab.png`, and writes `fh-tab.png`.

- [ ] **Step 3: Commit**

```bash
git add multiace_plugins/filamenthub/tools/e2e_filamenthub.py
git commit -m "test(fh-plugin): live Playwright smoke for the FilamentHub tab"
```

---

## Self-Review

**Spec coverage:**
- Manual picker UI → Task 7. ✓
- Write-back to FilamentHub → Task 5 (`assign_spool`). ✓
- Slot label via `POST /api/slot-override` → Tasks 3+5. ✓
- Standalone reloadable sidecar + manifest discovery → Tasks 1, 8. ✓
- Reuse `spoolman.py` → Task 2. ✓
- Field mapping table → Task 4. ✓
- Config env vars → Task 1. ✓
- Deployment + nginx `/plugin/` gotcha → Task 8. ✓
- Unit + E2E testing → Tasks 1–7 (pytest), Task 9 (Playwright). ✓
- Open item "subtype mapping" → resolved in Task 4 (spool `name`), documented. ✓
- Open item "nginx `/plugin/` exists?" → resolved in Task 8 (detect-or-add). ✓

**Placeholder scan:** No TBD/TODO left; every code step shows complete code.

**Type consistency:** `set_override(ace, slot, material, brand, subtype, color)` and `spool_to_override(...)` return the same six keys; `assign_spool(spool_id, ace, slot)` / `unassign_slot(ace, slot)` match ryvin's signatures used in Tasks 5–6; spool dict keys (`spool_id, name, material, color, vendor, location`) consistent between Task 2 producer and Tasks 4–7 consumers.
