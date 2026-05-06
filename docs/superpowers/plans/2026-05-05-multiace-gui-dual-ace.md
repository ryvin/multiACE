# multiACE Web — dual-ACE GUI + FilamentHub picker — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface and control both ACE Pros across all multiACE web tabs, with independent autodry FSMs and a deep-link to FilamentHub for per-(ACE, slot) spool selection.

**Architecture:** Backend refactors the single autodry FSM into one-per-ACE managed by `AutodryManager`; a new `MultiAcePoller` round-robins between ACEs while idle and sticks to the active ACE during prints. A new `SpoolmanClient` polls FilamentHub's nginx-proxied Spoolman every 5 s for slot bindings keyed by `(ace, slot)`. Frontend renders both ACEs side-by-side, adds a split-button + chevron for explicit `ACE_LOAD_HEAD HEAD=N ACE=M SLOT=S`, and adds a 📖 button per slot that opens FilamentHub's existing picker via `?picker=ace&printer=&ace=&slot=`.

**Tech Stack:** Python 3.11+ / FastAPI / pydantic v2 / httpx / pytest / pytest-asyncio / respx / vanilla JS+CSS (no build step) / Playwright (manual e2e)

**Spec:** `docs/superpowers/specs/2026-05-05-multiace-gui-dual-ace-design.md`

---

## File structure

| File | Status | Purpose |
|---|---|---|
| `multiace_web/src/multiace_web/spoolman.py` | **create** | Async Spoolman client. Returns `{ace: {slot: SpoolBinding}}`. |
| `multiace_web/src/multiace_web/autodryer.py` | modify | Add `AutodryManager`, `PerAceFSM`, migration; keep existing FSM types. |
| `multiace_web/src/multiace_web/poller.py` | modify | Add `MultiAcePoller` class. Existing `StatusPoller` / `PrintStatePoller` stay for now. |
| `multiace_web/src/multiace_web/state.py` | modify | Extend `AppState` with `spool_cache: dict[int, dict[int, SpoolBinding]]` and per-ACE autodry serialization. |
| `multiace_web/src/multiace_web/server.py` | modify | New endpoints: `/api/slots`, `/api/dry/stop`, `/api/autodry?ace=`. Extend `/api/print`, `/api/command`. New env vars. |
| `multiace_web/src/multiace_web/static/app.js` | modify | New renderers; remove old single-ACE switcher pills. |
| `multiace_web/src/multiace_web/static/style.css` | modify | New layout classes. |
| `multiace_web/tests/test_spoolman.py` | **create** | `SpoolmanClient` tests with `respx`. |
| `multiace_web/tests/test_autodryer.py` | modify | Add per-ACE manager tests, migration tests. |
| `multiace_web/tests/test_poller.py` | modify | Add `MultiAcePoller` round-robin tests. |
| `multiace_web/tests/test_server.py` | modify | Cover new endpoints; assert 409 pre-flight; assert per-ACE shape. |
| `multiace_web/tools/visual_regression.py` | modify | Capture new layouts. |
| `multiace_web/tools/e2e_dual_ace.py` | **create** | Manual Playwright golden-path script. |

---

## Task 1: SpoolmanClient module

**Files:**
- Create: `multiace_web/src/multiace_web/spoolman.py`
- Test: `multiace_web/tests/test_spoolman.py`

- [ ] **Step 1: Write the failing test for the data type and base URL handling**

```python
# multiace_web/tests/test_spoolman.py
import httpx
import pytest
import respx

from multiace_web.spoolman import SpoolBinding, SpoolmanClient


@pytest.mark.asyncio
async def test_list_all_bindings_groups_by_ace_then_slot() -> None:
    """Spoolman returns a flat list of spools; client groups them
    {ace: {slot: SpoolBinding}} for the configured printer only."""
    base_url = "http://fh.local"
    spools = [
        # bound to ace=1 slot=0 on this printer — included
        {"id": 142, "filament": {"name": "PLA Black", "material": "PLA", "color_hex": "000000"},
         "remaining_weight": 920.0,
         "extra": {"filamenthub": '{"schema":1,"location":{"printer":"u1-1","ace":1,"slot":0}}'}},
        # bound to ace=0 slot=2 — included
        {"id": 208, "filament": {"name": "TPU Blue", "material": "TPU", "color_hex": "1133ff"},
         "remaining_weight": 410.0,
         "extra": {"filamenthub": '{"schema":1,"location":{"printer":"u1-1","ace":0,"slot":2}}'}},
        # bound to a different printer — excluded
        {"id": 311, "filament": {"name": "ABS White", "material": "ABS", "color_hex": "ffffff"},
         "remaining_weight": 730.0,
         "extra": {"filamenthub": '{"schema":1,"location":{"printer":"kobra","ace":0,"slot":0}}'}},
        # legacy: missing ace field — defaults to ace=0
        {"id": 412, "filament": {"name": "PETG Red", "material": "PETG", "color_hex": "ff0000"},
         "remaining_weight": 600.0,
         "extra": {"filamenthub": '{"schema":1,"location":{"printer":"u1-1","slot":3}}'}},
    ]
    async with respx.mock(base_url=base_url) as mock:
        mock.get("/api/v1/spool").respond(200, json=spools)
        client = SpoolmanClient(base_url=base_url, printer_id="u1-1")
        bindings = await client.list_all_bindings()

    assert set(bindings.keys()) == {0, 1}
    assert bindings[1][0].spool_id == 142
    assert bindings[1][0].material == "PLA"
    assert bindings[0][2].spool_id == 208
    assert bindings[0][3].spool_id == 412   # legacy, defaulted
    # the kobra spool is excluded
    all_ids = {b.spool_id for slots in bindings.values() for b in slots.values()}
    assert 311 not in all_ids


@pytest.mark.asyncio
async def test_list_all_bindings_handles_timeout_returns_empty() -> None:
    """Timeout → empty dict. Caller decides cache aging."""
    async with respx.mock(base_url="http://fh.local") as mock:
        mock.get("/api/v1/spool").mock(side_effect=httpx.TimeoutException("slow"))
        client = SpoolmanClient(base_url="http://fh.local", printer_id="u1-1", timeout_s=0.1)
        bindings = await client.list_all_bindings()
    assert bindings == {}


@pytest.mark.asyncio
async def test_list_all_bindings_handles_5xx_returns_empty() -> None:
    async with respx.mock(base_url="http://fh.local") as mock:
        mock.get("/api/v1/spool").respond(503)
        client = SpoolmanClient(base_url="http://fh.local", printer_id="u1-1")
        bindings = await client.list_all_bindings()
    assert bindings == {}
```

- [ ] **Step 2: Run tests to verify they fail (module doesn't exist)**

Run: `cd multiace_web && pytest tests/test_spoolman.py -v`
Expected: `ModuleNotFoundError: No module named 'multiace_web.spoolman'`

- [ ] **Step 3: Implement the module**

```python
# multiace_web/src/multiace_web/spoolman.py
"""Async client for Spoolman (proxied through FilamentHub's nginx).

Returns spool bindings grouped by (ACE, slot) for the configured printer.
Treats spools whose `extra.filamenthub.location.ace` is missing as ace=0
so single-ACE installs and pre-migration spools render correctly.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger("multiace.spoolman")


@dataclass
class SpoolBinding:
    spool_id: int
    name: str | None
    material: str | None
    color: str | None
    weight_remaining_g: float | None


class SpoolmanClient:
    def __init__(self, base_url: str, printer_id: str, timeout_s: float = 3.0) -> None:
        self._base = base_url.rstrip("/")
        self._printer_id = printer_id
        self._timeout = timeout_s

    async def list_all_bindings(self) -> dict[int, dict[int, SpoolBinding]]:
        """Returns {ace: {slot: SpoolBinding}} for spools bound to this printer.
        Empty dict on timeout, network error, or non-2xx response."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(f"{self._base}/api/v1/spool")
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            log.warning("Spoolman unreachable: %s", e)
            return {}
        if resp.status_code >= 400:
            log.warning("Spoolman returned %d", resp.status_code)
            return {}

        try:
            spools = resp.json()
        except ValueError:
            log.warning("Spoolman returned non-JSON")
            return {}

        out: dict[int, dict[int, SpoolBinding]] = {}
        for sp in spools:
            try:
                fh_raw = (sp.get("extra") or {}).get("filamenthub")
                if not fh_raw:
                    continue
                fh = json.loads(fh_raw) if isinstance(fh_raw, str) else fh_raw
                loc = fh.get("location") or {}
                if loc.get("printer") != self._printer_id:
                    continue
                ace = int(loc.get("ace", 0))
                slot = int(loc["slot"])
            except (KeyError, ValueError, TypeError) as e:
                log.debug("Skipping malformed spool extra: %s", e)
                continue

            fil = sp.get("filament") or {}
            binding = SpoolBinding(
                spool_id=int(sp["id"]),
                name=fil.get("name"),
                material=fil.get("material"),
                color=fil.get("color_hex"),
                weight_remaining_g=sp.get("remaining_weight"),
            )
            out.setdefault(ace, {})[slot] = binding
        return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd multiace_web && pytest tests/test_spoolman.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add multiace_web/src/multiace_web/spoolman.py multiace_web/tests/test_spoolman.py
git -c user.name="Raul" -c user.email="raul@leadingbit.com" commit -m "$(cat <<'EOF'
feat(web): SpoolmanClient — fetch (ace, slot) bindings from FilamentHub

New module that polls FilamentHub's nginx-proxied Spoolman REST API and
groups spools by (ace, slot) for the configured printer. Tolerates the
legacy schema (no `ace` field defaults to 0) and degrades gracefully on
timeout / 5xx (returns empty dict; caller decides cache aging).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Per-ACE autodry types — `PerAceFSM` and `AutodryManager`

**Files:**
- Modify: `multiace_web/src/multiace_web/autodryer.py` (add new types alongside existing)
- Test: `multiace_web/tests/test_autodryer.py:end-of-file` (append new test class)

- [ ] **Step 1: Write failing tests for the new types**

```python
# Append to multiace_web/tests/test_autodryer.py

class TestAutodryManager:
    def test_default_construction_yields_one_fsm_per_device(self) -> None:
        from multiace_web.autodryer import AutodryManager, PerAceFSM
        mgr = AutodryManager.with_defaults(device_count=2)
        assert len(mgr.fsms) == 2
        assert mgr.fsms[0].ace == 0
        assert mgr.fsms[1].ace == 1
        assert all(isinstance(f, PerAceFSM) for f in mgr.fsms)
        # disabled by default — explicit opt-in per ACE
        assert all(not f.config.enabled for f in mgr.fsms)

    def test_get_returns_fsm_by_ace_index(self) -> None:
        from multiace_web.autodryer import AutodryManager
        mgr = AutodryManager.with_defaults(device_count=2)
        f = mgr.get(1)
        assert f.ace == 1

    def test_get_raises_for_out_of_range_ace(self) -> None:
        from multiace_web.autodryer import AutodryManager
        mgr = AutodryManager.with_defaults(device_count=2)
        with pytest.raises(KeyError):
            mgr.get(2)

    def test_serialize_roundtrip(self) -> None:
        from multiace_web.autodryer import AutodryManager
        mgr = AutodryManager.with_defaults(device_count=2)
        mgr.get(1).config.enabled = True
        mgr.get(1).config.target_pct = 12
        d = mgr.serialize()
        mgr2 = AutodryManager.deserialize(d, device_count=2)
        assert mgr2.get(1).config.enabled is True
        assert mgr2.get(1).config.target_pct == 12
        assert mgr2.get(0).config.enabled is False

    def test_deserialize_grows_to_device_count_when_persisted_count_is_smaller(self) -> None:
        """If hardware count grew (1 → 2 ACEs), deserialize fills missing FSMs with defaults."""
        from multiace_web.autodryer import AutodryManager
        mgr_one = AutodryManager.with_defaults(device_count=1)
        mgr_one.get(0).config.enabled = True
        d = mgr_one.serialize()
        mgr_two = AutodryManager.deserialize(d, device_count=2)
        assert mgr_two.get(0).config.enabled is True
        assert mgr_two.get(1).config.enabled is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd multiace_web && pytest tests/test_autodryer.py::TestAutodryManager -v`
Expected: ImportError on `AutodryManager` or `PerAceFSM`.

- [ ] **Step 3: Add the new types to autodryer.py**

Append to `multiace_web/src/multiace_web/autodryer.py` (do NOT remove the existing `PersistedState`, `FSMSnapshot`, `AutoDryer` — they stay for the runtime in Task 4):

```python
# Append to multiace_web/src/multiace_web/autodryer.py

@dataclass
class PerAceConfig:
    """Per-ACE autodry knobs. Each ACE's FSM has its own copy."""
    enabled: bool = False
    target_pct: int = 15
    hysteresis_pp: int = 5
    default_filament_type: str | None = None


@dataclass
class PerAceFSM:
    """One autodry FSM bound to one ACE. Holds config, persisted snapshot,
    and runtime locks (locked-during-print, USB-unreachable)."""
    ace: int
    config: PerAceConfig = field(default_factory=PerAceConfig)
    snapshot: FSMSnapshot = field(default_factory=FSMSnapshot)
    locked: bool = False        # set when a print pins another ACE
    unreachable: bool = False   # set after 2 consecutive ACE_SWITCH failures


@dataclass
class AutodryManager:
    """Owns one PerAceFSM per ACE. Top-level container that the runtime
    AutoDryer task delegates to. Persistence shape: {fsms: [{...}, ...]}.
    """
    fsms: list[PerAceFSM]

    @classmethod
    def with_defaults(cls, device_count: int) -> "AutodryManager":
        return cls(fsms=[PerAceFSM(ace=i) for i in range(device_count)])

    def get(self, ace: int) -> PerAceFSM:
        if ace < 0 or ace >= len(self.fsms):
            raise KeyError(f"ace index {ace} out of range (have {len(self.fsms)})")
        return self.fsms[ace]

    def serialize(self) -> dict[str, Any]:
        return {
            "schema": 2,
            "fsms": [
                {
                    "ace": f.ace,
                    "config": dataclasses.asdict(f.config),
                    "snapshot": dataclasses.asdict(f.snapshot),
                }
                for f in self.fsms
            ],
        }

    @classmethod
    def deserialize(cls, d: dict[str, Any], device_count: int) -> "AutodryManager":
        raw_fsms = {int(f["ace"]): f for f in (d.get("fsms") or []) if "ace" in f}
        fsms: list[PerAceFSM] = []
        for i in range(device_count):
            raw = raw_fsms.get(i)
            if raw is None:
                fsms.append(PerAceFSM(ace=i))
                continue
            cfg_d = raw.get("config") or {}
            snap_d = raw.get("snapshot") or {}
            fsms.append(PerAceFSM(
                ace=i,
                config=PerAceConfig(**{k: v for k, v in cfg_d.items() if k in PerAceConfig.__dataclass_fields__}),
                snapshot=_snapshot_from_dict(snap_d),
            ))
        return cls(fsms=fsms)


def _snapshot_from_dict(d: dict[str, Any]) -> FSMSnapshot:
    """Reconstruct an FSMSnapshot from its serialized form, guarding against
    schema drift (extra keys ignored, missing keys → defaults)."""
    state = FSMState(d.get("state", FSMState.IDLE.value))
    fault_d = d.get("fault")
    fault = Fault(**fault_d) if fault_d else None
    last_run_d = d.get("last_run")
    last_run = LastRun(**last_run_d) if last_run_d else None
    return FSMSnapshot(
        state=state,
        since_ts=float(d.get("since_ts", 0.0)),
        fault=fault,
        last_run=last_run,
        trigger_announcement_id=d.get("trigger_announcement_id"),
        daily_duty=list(d.get("daily_duty") or []),
        cooldown_until_ts=float(d.get("cooldown_until_ts", 0.0)),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd multiace_web && pytest tests/test_autodryer.py::TestAutodryManager -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add multiace_web/src/multiace_web/autodryer.py multiace_web/tests/test_autodryer.py
git -c user.name="Raul" -c user.email="raul@leadingbit.com" commit -m "$(cat <<'EOF'
feat(web): autodryer — PerAceFSM + AutodryManager dataclasses

Adds per-ACE FSM container alongside the existing single-FSM model. Pure
data layer, no runtime wiring yet — that's Task 4. Serialize/deserialize
round-trip works and tolerates device_count growth.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Migration from legacy single-FSM persistence

**Files:**
- Modify: `multiace_web/src/multiace_web/autodryer.py` (add `migrate_from_legacy`)
- Test: `multiace_web/tests/test_autodryer.py` (extend `TestAutodryManager`)

- [ ] **Step 1: Write the failing migration test**

```python
# Append to TestAutodryManager class

    def test_migrate_from_legacy_single_fsm(self) -> None:
        """Legacy schema (single FSM with target_ace) migrates to new
        per-ACE list, preserving config on the targeted ACE only."""
        from multiace_web.autodryer import AutodryManager, FSMState
        legacy = {
            "mode": "active",
            "target_ace": 1,
            "target_pct": 12,
            "hysteresis_pp": 4,
            "default_filament_type": "PETG",
            "fsm": {
                "state": "WATCHING",
                "since_ts": 1234.0,
                "cooldown_until_ts": 0.0,
            },
        }
        mgr = AutodryManager.migrate_from_legacy(legacy, device_count=2)
        assert mgr.get(0).config.enabled is False
        assert mgr.get(1).config.enabled is True
        assert mgr.get(1).config.target_pct == 12
        assert mgr.get(1).config.hysteresis_pp == 4
        assert mgr.get(1).config.default_filament_type == "PETG"
        assert mgr.get(1).snapshot.state == FSMState.WATCHING
        assert mgr.get(0).snapshot.state == FSMState.IDLE  # untargeted FSM is fresh

    def test_migrate_from_legacy_off_mode_disables_all(self) -> None:
        from multiace_web.autodryer import AutodryManager
        legacy = {"mode": "off", "target_ace": 0, "target_pct": 15, "hysteresis_pp": 5}
        mgr = AutodryManager.migrate_from_legacy(legacy, device_count=2)
        assert all(not f.config.enabled for f in mgr.fsms)

    def test_deserialize_routes_legacy_shape_through_migration(self) -> None:
        """Loading a v1 (legacy) blob via deserialize() should yield the
        migrated v2 shape, so existing on-disk files Just Work."""
        from multiace_web.autodryer import AutodryManager
        legacy = {"mode": "active", "target_ace": 0, "target_pct": 15, "hysteresis_pp": 5}
        mgr = AutodryManager.deserialize(legacy, device_count=2)
        assert mgr.get(0).config.enabled is True
        assert len(mgr.fsms) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd multiace_web && pytest tests/test_autodryer.py::TestAutodryManager -v -k migrate`
Expected: AttributeError on `AutodryManager.migrate_from_legacy`.

- [ ] **Step 3: Implement migration**

Add to `AutodryManager` in `multiace_web/src/multiace_web/autodryer.py`:

```python
    @classmethod
    def migrate_from_legacy(cls, legacy: dict[str, Any], device_count: int) -> "AutodryManager":
        """Convert a legacy single-FSM persisted blob (v1) to the new v2
        per-ACE shape. Only the FSM at `target_ace` keeps the legacy config;
        all others are constructed with defaults and `enabled = False`."""
        target_ace = int(legacy.get("target_ace", 0))
        enabled = legacy.get("mode") == "active"
        cfg = PerAceConfig(
            enabled=enabled,
            target_pct=int(legacy.get("target_pct", 15)),
            hysteresis_pp=int(legacy.get("hysteresis_pp", 5)),
            default_filament_type=legacy.get("default_filament_type"),
        )
        fsm_d = legacy.get("fsm") or {}
        snap = _snapshot_from_dict(fsm_d) if fsm_d else FSMSnapshot()

        fsms: list[PerAceFSM] = []
        for i in range(device_count):
            if i == target_ace and 0 <= i < device_count:
                fsms.append(PerAceFSM(ace=i, config=cfg, snapshot=snap))
            else:
                fsms.append(PerAceFSM(ace=i))
        return cls(fsms=fsms)
```

Then update `deserialize` to detect the legacy shape and route through migration:

```python
    @classmethod
    def deserialize(cls, d: dict[str, Any], device_count: int) -> "AutodryManager":
        # Schema v1 (legacy) had top-level "mode" + "target_ace"; v2 has "fsms"
        if "fsms" not in d and "target_ace" in d:
            return cls.migrate_from_legacy(d, device_count)
        raw_fsms = {int(f["ace"]): f for f in (d.get("fsms") or []) if "ace" in f}
        # ... rest unchanged
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd multiace_web && pytest tests/test_autodryer.py::TestAutodryManager -v`
Expected: 8 passed (3 new + 5 from Task 2)

- [ ] **Step 5: Commit**

```bash
git add multiace_web/src/multiace_web/autodryer.py multiace_web/tests/test_autodryer.py
git -c user.name="Raul" -c user.email="raul@leadingbit.com" commit -m "$(cat <<'EOF'
feat(web): autodryer — migrate legacy single-FSM persistence to per-ACE

deserialize() now detects v1-shaped JSON (top-level target_ace/mode) and
routes through migrate_from_legacy(), preserving the targeted ACE's config
and giving every other ACE a disabled-default FSM.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Wire `AutodryManager` into the runtime `AutoDryer` task

**Files:**
- Modify: `multiace_web/src/multiace_web/autodryer.py` (refactor `AutoDryer` class)
- Modify: `multiace_web/src/multiace_web/server.py:240-280` (init), `:580-600` (action handlers)
- Modify: `multiace_web/tests/test_autodryer.py` (extend existing runtime tests for per-ACE)

This is the largest task. The existing `AutoDryer` class wraps a single FSM. We refactor it so each tick can target any specific ACE; the `MultiAcePoller` (Task 5) decides which ACE to tick this round.

- [ ] **Step 1: Write the failing test for per-ACE tick**

```python
# Append to test_autodryer.py near other AutoDryer runtime tests

class TestAutoDryerPerAce:
    @pytest.mark.asyncio
    async def test_tick_routes_to_correct_fsm_by_ace(self, tmp_path) -> None:
        """tick(ace=N, sample) advances FSM N's state, leaves others alone."""
        from multiace_web.autodryer import AutoDryer, AutodryManager, FSMState
        mgr = AutodryManager.with_defaults(device_count=2)
        mgr.get(0).config.enabled = True
        mgr.get(0).config.target_pct = 15
        mgr.get(0).config.hysteresis_pp = 5
        # FSM 1 left disabled

        ad = AutoDryer(
            manager=mgr,
            persist_path=tmp_path / "ad.json",
            send_gcode=lambda s: None,    # no-op
            announce=lambda *a, **k: None,
        )
        # humidity above threshold should kick FSM 0 out of IDLE
        await ad.tick(ace=0, humidity_pct=22.0, dryer_status="stop", filament_types_in_use=["PLA"])
        assert mgr.get(0).snapshot.state in (FSMState.WATCHING, FSMState.DRYING)
        # FSM 1 untouched
        assert mgr.get(1).snapshot.state == FSMState.IDLE

    @pytest.mark.asyncio
    async def test_tick_skipped_when_locked(self, tmp_path) -> None:
        """A locked FSM (other ACE printing) does not advance."""
        from multiace_web.autodryer import AutoDryer, AutodryManager, FSMState
        mgr = AutodryManager.with_defaults(device_count=2)
        mgr.get(0).config.enabled = True
        mgr.get(0).locked = True
        ad = AutoDryer(manager=mgr, persist_path=tmp_path / "ad.json",
                       send_gcode=lambda s: None, announce=lambda *a, **k: None)
        await ad.tick(ace=0, humidity_pct=22.0, dryer_status="stop", filament_types_in_use=["PLA"])
        assert mgr.get(0).snapshot.state == FSMState.IDLE   # didn't advance

    @pytest.mark.asyncio
    async def test_serialized_state_round_trips_to_disk(self, tmp_path) -> None:
        """Save → load → state preserved per-ACE."""
        from multiace_web.autodryer import AutoDryer, AutodryManager
        mgr = AutodryManager.with_defaults(device_count=2)
        mgr.get(1).config.enabled = True
        mgr.get(1).config.target_pct = 12
        path = tmp_path / "ad.json"
        ad = AutoDryer(manager=mgr, persist_path=path,
                       send_gcode=lambda s: None, announce=lambda *a, **k: None)
        await ad.save()
        ad2 = AutoDryer.load(path=path, device_count=2,
                             send_gcode=lambda s: None, announce=lambda *a, **k: None)
        assert ad2._manager.get(1).config.enabled is True
        assert ad2._manager.get(1).config.target_pct == 12
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd multiace_web && pytest tests/test_autodryer.py::TestAutoDryerPerAce -v`
Expected: TypeError or AttributeError — `AutoDryer` ctor and `tick` signatures don't match yet.

- [ ] **Step 3: Refactor `AutoDryer` class**

In `multiace_web/src/multiace_web/autodryer.py`, replace the existing `AutoDryer` class. The new shape (existing FSM transition logic stays — only the wrapper changes):

```python
class AutoDryer:
    """Runtime wrapper for the per-ACE autodry FSMs.

    Each `tick(ace=...)` call runs FSM transitions for that ACE only,
    using its own config/snapshot. The poller calls tick() for whichever
    ACE is currently being scanned (round-robin while idle, stuck on
    active ACE during a print)."""

    def __init__(
        self,
        manager: AutodryManager,
        persist_path: Path | str,
        send_gcode: Callable[[str], Awaitable[None] | None],
        announce: Callable[..., Awaitable[None] | None],
    ) -> None:
        self._manager = manager
        self._path = Path(persist_path)
        self._send_gcode = send_gcode
        self._announce = announce
        self._lock = asyncio.Lock()

    @property
    def manager(self) -> AutodryManager:
        return self._manager

    @classmethod
    def load(
        cls,
        path: Path | str,
        device_count: int,
        send_gcode: Callable[[str], Awaitable[None] | None],
        announce: Callable[..., Awaitable[None] | None],
    ) -> "AutoDryer":
        p = Path(path)
        if p.exists():
            try:
                d = json.loads(p.read_text())
                mgr = AutodryManager.deserialize(d, device_count=device_count)
            except (json.JSONDecodeError, OSError, ValueError) as e:
                log.warning("autodry persist load failed (%s); starting fresh", e)
                mgr = AutodryManager.with_defaults(device_count=device_count)
        else:
            mgr = AutodryManager.with_defaults(device_count=device_count)
        return cls(manager=mgr, persist_path=p, send_gcode=send_gcode, announce=announce)

    async def save(self) -> None:
        """Atomic write."""
        d = self._manager.serialize()
        async with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w", dir=self._path.parent, delete=False, suffix=".tmp"
            ) as tf:
                json.dump(d, tf)
                tmp_name = tf.name
            os.replace(tmp_name, self._path)

    async def tick(
        self,
        *,
        ace: int,
        humidity_pct: float | None,
        dryer_status: str | None,
        filament_types_in_use: list[str],
    ) -> None:
        """Advance FSM[ace] by one tick. No-op if locked, unreachable, or disabled."""
        fsm = self._manager.get(ace)
        if fsm.locked or fsm.unreachable or not fsm.config.enabled:
            return
        # Reuse existing transition function with the per-ACE config + snapshot.
        # The existing function _advance_fsm() already takes target_pct,
        # hysteresis_pp, default_filament_type as args — pass them from cfg.
        new_snapshot = _advance_fsm(
            snapshot=fsm.snapshot,
            target_pct=fsm.config.target_pct,
            hysteresis_pp=fsm.config.hysteresis_pp,
            default_filament_type=fsm.config.default_filament_type,
            humidity_pct=humidity_pct,
            dryer_status=dryer_status,
            filament_types_in_use=filament_types_in_use,
            now_ts=time.time(),
            send_gcode=self._send_gcode,
            announce=self._announce,
            ace=ace,
        )
        fsm.snapshot = new_snapshot
        await self.save()
```

`_advance_fsm` is the existing pure transition function (currently takes `target_pct`, `hysteresis_pp`, etc. as args; check the existing implementation and add `ace` as a kwarg — used only for log lines and gcode generation like `ACE_DRY ACE={ace}`).

- [ ] **Step 4: Update `server.py` autodry init**

Replace the existing `_autodry_init` block (`server.py:~240-280`) with the new shape:

```python
    # in server.py startup
    autodry_path = os.environ.get(
        "MULTIACE_AUTODRY_STATE_PATH",
        "/userdata/multiace-web/app/.autodry_state.json",
    )
    device_count = state.last_state.device_count if state.last_state else 1
    app.state.autodry = AutoDryer.load(
        path=autodry_path,
        device_count=device_count,
        send_gcode=moonraker.run_gcode,
        announce=announcer.publish,
    )
```

Update the `set_target_ace` handler in `server.py:~590` — it's no longer a single global, so this action becomes a no-op or is removed (we'll add `/api/autodry?ace=` in Task 6 to replace it).

- [ ] **Step 5: Run all autodry tests**

Run: `cd multiace_web && pytest tests/test_autodryer.py -v`
Expected: all existing tests pass + 3 new TestAutoDryerPerAce tests pass.

If existing tests fail because the `AutoDryer` ctor changed, rewrite their setup — they should construct an `AutodryManager` with `device_count=1`, set `mgr.get(0).config.enabled = True` plus the legacy `target_pct` / `hysteresis_pp`, and pass `mgr` to `AutoDryer(manager=mgr, ...)`. Use a small helper `_legacy_compat_setup` if it reduces churn.

- [ ] **Step 6: Commit**

```bash
git add multiace_web/src/multiace_web/autodryer.py multiace_web/src/multiace_web/server.py multiace_web/tests/test_autodryer.py
git -c user.name="Raul" -c user.email="raul@leadingbit.com" commit -m "$(cat <<'EOF'
refactor(web): AutoDryer delegates to AutodryManager (per-ACE)

The runtime class is now stateless w.r.t. which ACE it targets — the caller
(MultiAcePoller, next task) decides which ACE to tick each cycle. Each FSM
keeps its own enabled/target_pct/hysteresis config and snapshot.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: `MultiAcePoller` round-robin

**Files:**
- Modify: `multiace_web/src/multiace_web/poller.py` (add `MultiAcePoller`)
- Modify: `multiace_web/src/multiace_web/server.py` (replace existing PrintStatePoller boot with MultiAcePoller)
- Test: `multiace_web/tests/test_poller.py`

- [ ] **Step 1: Write failing tests**

```python
# Append to multiace_web/tests/test_poller.py

import pytest
from unittest.mock import AsyncMock, MagicMock

from multiace_web.poller import MultiAcePoller


class FakeMoonraker:
    def __init__(self) -> None:
        self.gcodes_run: list[str] = []
        self.print_state = "standby"
        self.active_ace = 0
        self.ace_status = {0: {"status": "ready", "active_device": 1}}
    async def run_gcode(self, script: str) -> None:
        self.gcodes_run.append(script)
        if script.startswith("ACE_SWITCH"):
            target = int(script.split("TARGET=")[1])
            self.active_ace = target
    async def query_objects(self, *names: str) -> dict:
        if "print_stats" in names:
            return {"print_stats": {"state": self.print_state}}
        if "ace" in names:
            return {"ace": {"active_device": self.active_ace + 1, "status": "ready",
                            "humidity": 22.0, "dryer_status": {"status": "stop"}}}
        return {}


@pytest.mark.asyncio
async def test_idle_alternates_between_aces() -> None:
    """While idle, target index toggles 0 → 1 → 0."""
    fake_mr = FakeMoonraker()
    autodry = MagicMock()
    autodry.tick = AsyncMock()
    autodry.manager.get.side_effect = lambda i: MagicMock(unreachable=False, locked=False)

    poller = MultiAcePoller(moonraker=fake_mr, autodry=autodry, device_count=2, period_s=0.0)
    await poller.tick()
    assert fake_mr.active_ace == 1   # switched from 0 → 1
    await poller.tick()
    assert fake_mr.active_ace == 0   # back to 0
    assert autodry.tick.await_count == 2


@pytest.mark.asyncio
async def test_printing_sticks_to_active_ace_and_locks_others() -> None:
    fake_mr = FakeMoonraker()
    fake_mr.print_state = "printing"
    fake_mr.active_ace = 1
    autodry = MagicMock()
    autodry.tick = AsyncMock()
    fsms = {i: MagicMock(unreachable=False, locked=False) for i in range(2)}
    autodry.manager.get.side_effect = lambda i: fsms[i]

    poller = MultiAcePoller(moonraker=fake_mr, autodry=autodry, device_count=2, period_s=0.0)
    await poller.tick()
    assert fake_mr.active_ace == 1   # no switch
    assert "ACE_SWITCH" not in " ".join(fake_mr.gcodes_run)
    assert fsms[0].locked is True
    assert fsms[1].locked is False
    autodry.tick.assert_awaited_with(ace=1, humidity_pct=22.0, dryer_status="stop", filament_types_in_use=[])


@pytest.mark.asyncio
async def test_two_consecutive_switch_failures_mark_unreachable() -> None:
    """If ACE_SWITCH fails twice in a row for the same target, that FSM
    is marked unreachable until a future switch succeeds."""
    fake_mr = FakeMoonraker()
    autodry = MagicMock()
    autodry.tick = AsyncMock()
    fsms = {i: MagicMock(unreachable=False, locked=False) for i in range(2)}
    autodry.manager.get.side_effect = lambda i: fsms[i]

    # Make ACE_SWITCH fail
    async def fail_switch(script: str) -> None:
        if script.startswith("ACE_SWITCH"):
            raise RuntimeError("usb gone")
        fake_mr.gcodes_run.append(script)
    fake_mr.run_gcode = fail_switch  # type: ignore[assignment]

    poller = MultiAcePoller(moonraker=fake_mr, autodry=autodry, device_count=2, period_s=0.0)
    await poller.tick()
    assert fsms[1].unreachable is False  # one failure, not yet
    await poller.tick()
    assert fsms[1].unreachable is True   # second failure → marked
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd multiace_web && pytest tests/test_poller.py -v`
Expected: ImportError on `MultiAcePoller`.

- [ ] **Step 3: Implement `MultiAcePoller`**

Append to `multiace_web/src/multiace_web/poller.py`:

```python
class MultiAcePoller:
    """Round-robin between ACEs while idle; stick to the active ACE during a print.

    Behavior:
    - idle: switch to next ACE if needed, query [ace] state, tick that FSM.
    - printing: skip switch, query active ACE only, lock other FSMs.
    - 2 consecutive switch failures → mark target FSM unreachable.
    """

    def __init__(
        self,
        moonraker,                    # MoonrakerClient duck-typed
        autodry,                      # AutoDryer instance
        device_count: int,
        period_s: float = 5.0,
    ) -> None:
        self._mr = moonraker
        self._autodry = autodry
        self._n = max(device_count, 1)
        self._period = period_s
        self._stop = asyncio.Event()
        self._last_polled = -1
        self._consecutive_switch_failures: dict[int, int] = {}

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception:
                log.exception("MultiAcePoller tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._period)
                return
            except asyncio.TimeoutError:
                pass

    async def tick(self) -> None:
        ps = await self._mr.query_objects("print_stats")
        state = (ps.get("print_stats") or {}).get("state", "standby")

        if state == "printing":
            await self._tick_printing()
        else:
            await self._tick_idle()

    async def _tick_printing(self) -> None:
        ace_status = (await self._mr.query_objects("ace")).get("ace") or {}
        active_idx = max(0, int(ace_status.get("active_device", 1)) - 1)
        for i in range(self._n):
            self._autodry.manager.get(i).locked = (i != active_idx)
        await self._tick_fsm(active_idx, ace_status)

    async def _tick_idle(self) -> None:
        target = (self._last_polled + 1) % self._n
        for i in range(self._n):
            self._autodry.manager.get(i).locked = False

        ace_status = (await self._mr.query_objects("ace")).get("ace") or {}
        active_idx = max(0, int(ace_status.get("active_device", 1)) - 1)

        if active_idx != target:
            try:
                await self._mr.run_gcode(f"ACE_SWITCH TARGET={target}")
                self._consecutive_switch_failures[target] = 0
                self._autodry.manager.get(target).unreachable = False
                ace_status = (await self._mr.query_objects("ace")).get("ace") or {}
            except Exception as e:
                self._consecutive_switch_failures[target] = (
                    self._consecutive_switch_failures.get(target, 0) + 1
                )
                if self._consecutive_switch_failures[target] >= 2:
                    self._autodry.manager.get(target).unreachable = True
                log.warning("ACE_SWITCH TARGET=%d failed: %s", target, e)
                self._last_polled = target
                return

        await self._tick_fsm(target, ace_status)
        self._last_polled = target

    async def _tick_fsm(self, ace_idx: int, ace_status: dict) -> None:
        humidity = ace_status.get("humidity")
        dryer = (ace_status.get("dryer_status") or {}).get("status")
        types_in_use: list[str] = []  # populated from head_source elsewhere; empty here is OK
        await self._autodry.tick(
            ace=ace_idx,
            humidity_pct=humidity,
            dryer_status=dryer,
            filament_types_in_use=types_in_use,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd multiace_web && pytest tests/test_poller.py -v`
Expected: all pass (existing + 3 new).

- [ ] **Step 5: Wire `MultiAcePoller` into server startup**

In `multiace_web/src/multiace_web/server.py`, replace the existing single-ACE `PrintStatePoller` instantiation with `MultiAcePoller`. Keep the existing class for now if other code still uses it; we'll prune in a follow-up.

- [ ] **Step 6: Commit**

```bash
git add multiace_web/src/multiace_web/poller.py multiace_web/src/multiace_web/server.py multiace_web/tests/test_poller.py
git -c user.name="Raul" -c user.email="raul@leadingbit.com" commit -m "$(cat <<'EOF'
feat(web): MultiAcePoller round-robin scanner

Idle: alternates target ACE 0/1, switches via ACE_SWITCH, ticks that FSM.
Printing: pins to the active ACE, locks others. Two consecutive switch
failures → marks the target FSM unreachable; recovery is automatic on
next successful switch.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Server endpoints — `/api/slots`, `/api/dry/stop`, `/api/autodry?ace=`

**Files:**
- Modify: `multiace_web/src/multiace_web/server.py`
- Modify: `multiace_web/src/multiace_web/state.py` (add `spool_cache` field)
- Test: `multiace_web/tests/test_server.py`

- [ ] **Step 1: Write failing endpoint tests**

```python
# Append near other endpoint tests in test_server.py

class TestSlotsEndpoint:
    def test_returns_one_block_per_ace_with_slots_and_spools(self, client) -> None:
        # Pre-populate state.spool_cache with one binding
        from multiace_web.spoolman import SpoolBinding
        client.app.state.app_state.spool_cache = {
            1: {0: SpoolBinding(spool_id=142, name="PLA Black", material="PLA",
                                 color="000000", weight_remaining_g=920.0)}
        }
        client.app.state.app_state.last_state = _two_ace_state_event()
        r = client.get("/api/slots")
        assert r.status_code == 200
        body = r.json()
        assert len(body["aces"]) == 2
        ace1 = next(a for a in body["aces"] if a["index"] == 1)
        s0 = next(s for s in ace1["slots"] if s["slot"] == 0)
        assert s0["spool"]["spool_id"] == 142
        assert s0["spool"]["material"] == "PLA"


class TestDryStopEndpoint:
    @pytest.mark.asyncio
    async def test_post_dry_stop_with_ace_param_switches_then_stops(self, client) -> None:
        sent = []
        async def fake_gcode(s: str) -> None:
            sent.append(s)
        client.app.state.moonraker.run_gcode = fake_gcode
        r = client.post("/api/dry/stop", json={"ace": 1})
        assert r.status_code == 200
        assert sent == ["ACE_SWITCH TARGET=1", "ACE_STOP_DRYING"]

    def test_returns_502_when_switch_fails(self, client) -> None:
        async def fail_gcode(s: str) -> None:
            if "ACE_SWITCH" in s:
                raise RuntimeError("usb gone")
        client.app.state.moonraker.run_gcode = fail_gcode
        r = client.post("/api/dry/stop", json={"ace": 1})
        assert r.status_code == 502
        assert "switch" in r.json()["error"]


class TestAutodryPerAceEndpoint:
    def test_get_returns_one_fsm_state(self, client) -> None:
        r = client.get("/api/autodry?ace=0")
        assert r.status_code == 200
        body = r.json()
        assert body["ace"] == 0
        assert "enabled" in body
        assert "target_pct" in body

    def test_post_updates_one_fsm_config(self, client) -> None:
        r = client.post("/api/autodry?ace=0", json={
            "enabled": True, "target_pct": 12, "hysteresis_pp": 4,
            "default_filament_type": "PETG"
        })
        assert r.status_code == 200
        mgr = client.app.state.autodry.manager
        assert mgr.get(0).config.enabled is True
        assert mgr.get(0).config.target_pct == 12

    def test_post_returns_404_for_out_of_range_ace(self, client) -> None:
        r = client.post("/api/autodry?ace=9", json={"enabled": True})
        assert r.status_code == 404
```

`_two_ace_state_event()` is a helper that returns a state-log event with `device_count=2`. Add to conftest.py:

```python
@pytest.fixture
def _two_ace_state_event():
    def _make():
        return {
            "action": "STATUS",
            "device_count": 2,
            "active_device": 0,
            "gate_status": [1, 1, 1, 1],
            "head_source": {"0": None, "1": None, "2": None, "3": None},
            "sensors": {"0": False, "1": False, "2": False, "3": False},
        }
    return _make
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd multiace_web && pytest tests/test_server.py::TestSlotsEndpoint tests/test_server.py::TestDryStopEndpoint tests/test_server.py::TestAutodryPerAceEndpoint -v`
Expected: 404 / endpoint not found / state attribute missing.

- [ ] **Step 3: Add `spool_cache` to AppState**

In `multiace_web/src/multiace_web/state.py`, find the AppState class (or equivalent dataclass) and add:

```python
    # Per-(ACE, slot) Spoolman bindings, populated by the SpoolmanClient
    # background task. Empty until first poll completes.
    spool_cache: dict[int, dict[int, "SpoolBinding"]] = field(default_factory=dict)
```

Import `SpoolBinding` lazily under `TYPE_CHECKING` to avoid circular import.

- [ ] **Step 4: Implement the three endpoints in server.py**

```python
# Add to server.py

@app.get("/api/slots")
async def get_slots() -> dict:
    s = app.state.app_state.last_state or {}
    device_count = int(s.get("device_count", 1))
    cache = app.state.app_state.spool_cache or {}
    head_source = s.get("head_source") or {}
    gate_status = s.get("gate_status") or [0, 0, 0, 0]

    aces = []
    for ace_idx in range(device_count):
        slots = []
        for slot in range(4):
            binding = cache.get(ace_idx, {}).get(slot)
            slots.append({
                "slot": slot,
                "gate_status": gate_status[slot] if ace_idx == s.get("active_device", 0) else None,
                "spool": (
                    {"spool_id": binding.spool_id, "name": binding.name,
                     "material": binding.material, "color": binding.color,
                     "weight_remaining_g": binding.weight_remaining_g}
                    if binding else None
                ),
            })
        aces.append({
            "index": ace_idx,
            "slots": slots,
            "is_active": ace_idx == s.get("active_device", 0),
        })
    return {"aces": aces}


class DryStopRequest(BaseModel):
    ace: int = Field(ge=0, le=3)


@app.post("/api/dry/stop")
async def post_dry_stop(req: DryStopRequest) -> dict:
    mr = app.state.moonraker
    try:
        await mr.run_gcode(f"ACE_SWITCH TARGET={req.ace}")
    except Exception as e:
        return JSONResponse(status_code=502,
                            content={"error": f"could not switch to ACE {req.ace}: {e}"})
    try:
        await mr.run_gcode("ACE_STOP_DRYING")
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": str(e)})
    return {"ok": True}


class AutodryConfigUpdate(BaseModel):
    enabled: bool | None = None
    target_pct: int | None = Field(default=None, ge=5, le=80)
    hysteresis_pp: int | None = Field(default=None, ge=1, le=20)
    default_filament_type: str | None = None


@app.get("/api/autodry")
async def get_autodry(ace: int) -> dict:
    try:
        fsm = app.state.autodry.manager.get(ace)
    except KeyError:
        return JSONResponse(status_code=404, content={"error": f"no FSM for ace={ace}"})
    return {
        "ace": ace,
        "enabled": fsm.config.enabled,
        "target_pct": fsm.config.target_pct,
        "hysteresis_pp": fsm.config.hysteresis_pp,
        "default_filament_type": fsm.config.default_filament_type,
        "state": fsm.snapshot.state.value,
        "locked": fsm.locked,
        "unreachable": fsm.unreachable,
    }


@app.post("/api/autodry")
async def post_autodry(ace: int, body: AutodryConfigUpdate) -> dict:
    try:
        fsm = app.state.autodry.manager.get(ace)
    except KeyError:
        return JSONResponse(status_code=404, content={"error": f"no FSM for ace={ace}"})
    if body.enabled is not None: fsm.config.enabled = body.enabled
    if body.target_pct is not None: fsm.config.target_pct = body.target_pct
    if body.hysteresis_pp is not None: fsm.config.hysteresis_pp = body.hysteresis_pp
    if body.default_filament_type is not None:
        fsm.config.default_filament_type = body.default_filament_type or None
    await app.state.autodry.save()
    return {"ok": True, "ace": ace}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd multiace_web && pytest tests/test_server.py -v -k "Slots or DryStop or Autodry"`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add multiace_web/src/multiace_web/server.py multiace_web/src/multiace_web/state.py multiace_web/tests/test_server.py multiace_web/tests/conftest.py
git -c user.name="Raul" -c user.email="raul@leadingbit.com" commit -m "$(cat <<'EOF'
feat(web): /api/slots, /api/dry/stop, /api/autodry per-ACE endpoints

- /api/slots returns one block per ACE with slot bindings + active flag
- /api/dry/stop {ace} switches active ACE then runs ACE_STOP_DRYING
- /api/autodry?ace= GET/POST per-FSM config

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Extend `/api/print`; pre-flight 409 on `/api/command` for busy-head loads

**Files:**
- Modify: `multiace_web/src/multiace_web/server.py`
- Test: `multiace_web/tests/test_server.py`

- [ ] **Step 1: Write failing tests**

```python
# Append to test_server.py

class TestApiPrintMultiAce:
    def test_includes_per_ace_dryer_status(self, client) -> None:
        # /api/print should include aces: [{index, dryer, last_seen_ts}]
        r = client.get("/api/print")
        body = r.json()
        assert "aces" in body
        assert isinstance(body["aces"], list)


class TestCommandPreflight409:
    def test_load_into_busy_head_returns_409(self, client) -> None:
        client.app.state.app_state.last_state = {
            **_state_with(),
            "head_source": {"0": {"ace": 0, "slot": 0, "type": "PLA", "color": "000000"},
                             "1": None, "2": None, "3": None},
        }
        r = client.post("/api/command", json={
            "script": "ACE_LOAD_HEAD HEAD=0 ACE=1 SLOT=0"
        })
        assert r.status_code == 409
        assert "busy" in r.json()["error"].lower()

    def test_load_from_empty_slot_returns_409(self, client) -> None:
        client.app.state.app_state.last_state = {
            **_state_with(),
            "active_device": 0,
            "gate_status": [0, 1, 1, 1],   # slot 0 empty (0 = unavailable)
        }
        r = client.post("/api/command", json={
            "script": "ACE_LOAD_HEAD HEAD=0 ACE=0 SLOT=0"
        })
        assert r.status_code == 409
        assert "empty" in r.json()["error"].lower()

    def test_unparseable_script_passes_through(self, client) -> None:
        """Don't break existing callers — only fail-fast on the specific ACE_LOAD_HEAD shape."""
        r = client.post("/api/command", json={"script": "G28"})
        assert r.status_code in (200, 502)  # depends on Moonraker mock, but NOT 409
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `cd multiace_web && pytest tests/test_server.py -v -k "ApiPrintMultiAce or CommandPreflight"`
Expected: 4 failures (KeyError on aces / no 409 returned).

- [ ] **Step 3: Implement `/api/print` extension**

In `server.py` find the existing `/api/print` handler, and after the existing dryer fetch, add:

```python
    # NEW: per-ACE dryer block
    aces_block = []
    s = app.state.app_state.last_state or {}
    device_count = int(s.get("device_count", 1))
    active_idx = int(s.get("active_device", 0))
    for ace_idx in range(device_count):
        if ace_idx == active_idx:
            # currently-connected ACE: live data from [ace] object we just fetched
            aces_block.append({
                "index": ace_idx,
                "dryer": ace_obj.get("dryer_status"),
                "humidity": ace_obj.get("humidity"),
                "last_seen_ts": time.time(),
                "is_active": True,
            })
        else:
            cached = app.state.app_state.last_ace_data.get(ace_idx)  # set by MultiAcePoller
            aces_block.append({
                "index": ace_idx,
                "dryer": (cached or {}).get("dryer_status"),
                "humidity": (cached or {}).get("humidity"),
                "last_seen_ts": (cached or {}).get("last_seen_ts"),
                "is_active": False,
            })
    body["aces"] = aces_block
```

In `state.py` add `last_ace_data: dict[int, dict[str, Any]] = field(default_factory=dict)`. In `MultiAcePoller._tick_fsm`, snapshot the ace data into `state.last_ace_data[ace_idx] = {**ace_status, "last_seen_ts": time.time()}` before calling autodry.tick.

- [ ] **Step 4: Implement `/api/command` 409 pre-flight**

Add to the existing `/api/command` handler (top of body, before forwarding to Moonraker):

```python
import re

_LOAD_HEAD_RE = re.compile(
    r"^\s*ACE_LOAD_HEAD\s+HEAD=(\d+)(?:\s+ACE=(\d+))?(?:\s+SLOT=(\d+))?\s*$",
    re.IGNORECASE,
)

def _preflight_load_head(state: dict, script: str) -> str | None:
    """Return error string if the load is provably going to fail, else None."""
    m = _LOAD_HEAD_RE.match(script)
    if not m:
        return None
    head = int(m.group(1))
    ace = int(m.group(2)) if m.group(2) is not None else int(state.get("active_device", 0))
    slot = int(m.group(3)) if m.group(3) is not None else head

    head_source = state.get("head_source") or {}
    if head_source.get(str(head)):
        return f"head T{head} is busy — unload first"
    # Slot-empty check is only meaningful when the requested ACE is the active one
    # (gate_status reflects active ACE only).
    if ace == int(state.get("active_device", 0)):
        gate = state.get("gate_status") or []
        if slot < len(gate) and gate[slot] == 0:
            return f"ACE {ace} slot {slot} is empty"
    return None

# Inside the /api/command POST handler, before forwarding to Moonraker:
err = _preflight_load_head(app.state.app_state.last_state or {}, body.script)
if err:
    return JSONResponse(status_code=409, content={"error": err})
```

- [ ] **Step 5: Run tests, verify they pass**

Run: `cd multiace_web && pytest tests/test_server.py -v -k "ApiPrintMultiAce or CommandPreflight"`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add multiace_web/src/multiace_web/server.py multiace_web/src/multiace_web/state.py multiace_web/tests/test_server.py
git -c user.name="Raul" -c user.email="raul@leadingbit.com" commit -m "$(cat <<'EOF'
feat(web): /api/print per-ACE dryer block + ACE_LOAD_HEAD preflight 409

- /api/print now returns aces:[{index, dryer, humidity, last_seen_ts,
  is_active}] for each ACE (live for active, last-known for others).
- /api/command rejects ACE_LOAD_HEAD with busy head or empty slot at the
  server before round-tripping to Moonraker (cheap UX guardrail; the
  firmware already checks too).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Wire SpoolmanClient into server boot + WebSocket payload

**Files:**
- Modify: `multiace_web/src/multiace_web/server.py`
- Test: `multiace_web/tests/test_server.py`

- [ ] **Step 1: Write failing test for the WS payload shape**

```python
# Append to test_server.py

class TestSpoolCacheInWebSocket:
    @pytest.mark.asyncio
    async def test_spool_cache_in_state_broadcast(self, client) -> None:
        from multiace_web.spoolman import SpoolBinding
        client.app.state.app_state.spool_cache = {
            0: {1: SpoolBinding(spool_id=42, name="PLA", material="PLA",
                                color="ffaa00", weight_remaining_g=300.0)}
        }
        with client.websocket_connect("/ws") as ws:
            msg = ws.receive_json(timeout=2.0)
            # The state push should include spool_cache under a stable key
            assert "spool_cache" in msg or "state" in msg
            cache = msg.get("spool_cache") or msg.get("state", {}).get("spool_cache") or {}
            # accept either flat or nested-by-state shape
            ace0 = cache.get("0") or cache.get(0) or {}
            slot1 = ace0.get("1") or ace0.get(1) or {}
            assert slot1.get("spool_id") == 42
```

- [ ] **Step 2: Run test, verify it fails**

Run: `cd multiace_web && pytest tests/test_server.py::TestSpoolCacheInWebSocket -v`
Expected: KeyError or None comparisons fail.

- [ ] **Step 3: Add SpoolmanClient init + background fetch task**

In `server.py` startup, add:

```python
    fh_url = os.environ.get("FILAMENTHUB_URL", "").strip()
    fh_printer = os.environ.get("FILAMENTHUB_PRINTER_ID", "").strip() or "u1-1"

    if fh_url:
        app.state.spoolman = SpoolmanClient(base_url=fh_url, printer_id=fh_printer)

        async def _spool_poll_loop():
            while True:
                try:
                    bindings = await app.state.spoolman.list_all_bindings()
                    if bindings:
                        app.state.app_state.spool_cache = bindings
                        app.state.app_state.spool_cache_last_seen_ts = time.time()
                    elif time.time() - app.state.app_state.spool_cache_last_seen_ts > 300:
                        # 5 min stale → clear
                        app.state.app_state.spool_cache = {}
                except Exception:
                    log.exception("spool poll failed")
                await asyncio.sleep(5.0)

        app.state.spool_task = asyncio.create_task(_spool_poll_loop())
    else:
        app.state.spoolman = None
        log.info("FILAMENTHUB_URL not set — spool cache disabled")
```

In `state.py` add `spool_cache_last_seen_ts: float = 0.0`.

- [ ] **Step 4: Include `spool_cache` in WebSocket pushes**

Find the WS broadcast helper in `server.py` (likely a function that builds the `state` dict pushed to clients). Add a serialized form of `spool_cache`:

```python
def _serialize_spool_cache(cache: dict[int, dict[int, "SpoolBinding"]]) -> dict:
    return {
        str(ace): {
            str(slot): {"spool_id": b.spool_id, "name": b.name, "material": b.material,
                        "color": b.color, "weight_remaining_g": b.weight_remaining_g}
            for slot, b in slots.items()
        }
        for ace, slots in cache.items()
    }

# in the broadcast builder:
broadcast["spool_cache"] = _serialize_spool_cache(app_state.spool_cache)
```

- [ ] **Step 5: Run tests, verify pass**

Run: `cd multiace_web && pytest tests/test_server.py::TestSpoolCacheInWebSocket -v`
Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add multiace_web/src/multiace_web/server.py multiace_web/src/multiace_web/state.py multiace_web/tests/test_server.py
git -c user.name="Raul" -c user.email="raul@leadingbit.com" commit -m "$(cat <<'EOF'
feat(web): wire SpoolmanClient + push spool_cache over WebSocket

Background task polls FilamentHub-proxied Spoolman every 5s when
FILAMENTHUB_URL is set, ages cache to empty after 5 min of failures, and
broadcasts the (ace, slot) → spool map in every state frame.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Frontend — Dashboard slots panel side-by-side

**Files:**
- Modify: `multiace_web/src/multiace_web/static/app.js`
- Modify: `multiace_web/src/multiace_web/static/style.css`

- [ ] **Step 1: Read the current slots renderer and the dashboard root**

Run: `grep -n -E "(renderSlots|slots-panel|ace-switcher)" multiace_web/src/multiace_web/static/app.js | head`

Identify the function that today renders one ACE worth of slots (spec audit said `app.js:1235` checks `state.active_device`). Note the function name and call site.

- [ ] **Step 2: Replace the renderer**

Replace the existing slots renderer (the one that reads `state.active_device`) with `renderSlotsPanelMultiAce()`. New function:

```js
function renderSlotsPanelMultiAce(container, state) {
  const deviceCount = state?.device_count ?? 1;
  const activeIdx   = state?.active_device ?? 0;
  const blocks = [];
  for (let ace = 0; ace < deviceCount; ace++) {
    const isActive = (ace === activeIdx);
    const block = document.createElement('div');
    block.className = `ace-block${isActive ? ' is-active' : ' is-stale'}`;
    block.dataset.ace = String(ace);
    block.innerHTML = `
      <header class="ace-block-head">
        <span class="ace-label">ACE ${String.fromCharCode(65 + ace)} <span class="muted">(#${ace})</span></span>
        ${isActive ? '<span class="badge badge-active">active</span>' : '<span class="badge badge-stale">stale</span>'}
      </header>
      <div class="slot-rows" data-ace="${ace}"></div>
    `;
    blocks.push(block);
    const slotRows = block.querySelector('.slot-rows');
    for (let s = 0; s < 4; s++) {
      slotRows.appendChild(renderSlotRow(state, ace, s));
    }
  }
  container.replaceChildren(...blocks);
}
```

`renderSlotRow` is implemented in Task 10 — for now stub it:

```js
function renderSlotRow(state, ace, slot) {
  const row = document.createElement('div');
  row.className = 'slot-row';
  row.dataset.ace = String(ace);
  row.dataset.slot = String(slot);
  row.textContent = `S${slot} (placeholder)`;
  return row;
}
```

- [ ] **Step 3: Remove the existing ACE switcher pills**

Find and remove the `device_count > 1` block that renders ACE 0/1 switcher pills (was at `app.js:1161`). The new layout shows both ACEs unconditionally, so the switcher is dead code.

- [ ] **Step 4: Add CSS for side-by-side layout**

Append to `multiace_web/src/multiace_web/static/style.css`:

```css
.slots-panel {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(380px, 1fr));
  gap: 1rem;
}
.ace-block {
  background: var(--surface);
  border-radius: var(--radius);
  padding: 1rem;
  border: 1px solid var(--border, rgba(255,255,255,.08));
}
.ace-block.is-stale { opacity: .85; }
.ace-block.is-unreachable { opacity: .45; filter: grayscale(.6); }
.ace-block-head {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: .5rem;
  font-weight: 600;
}
.badge { font-size: .75rem; padding: .15rem .5rem; border-radius: 999px; }
.badge-active { background: var(--accent); color: var(--on-accent, #fff); }
.badge-stale  { background: rgba(255,255,255,.08); color: var(--muted, #888); }
.slot-rows { display: flex; flex-direction: column; gap: .35rem; }
```

- [ ] **Step 5: Manual smoke against the live printer**

Restart `multiace-web` (or run dev server) and load Dashboard. Both ACE blocks should appear side-by-side on a wide screen and stack on a narrow viewport.

```bash
MOONRAKER_URL=http://192.168.1.171:7125 \
  uvicorn multiace_web.server:app --port 7126 --reload
```

Then `xdg-open http://localhost:7126/` (or open in browser manually).

- [ ] **Step 6: Commit**

```bash
git add multiace_web/src/multiace_web/static/app.js multiace_web/src/multiace_web/static/style.css
git -c user.name="Raul" -c user.email="raul@leadingbit.com" commit -m "$(cat <<'EOF'
feat(web): Dashboard renders both ACEs side-by-side

Removes the active-only switcher pills; renderSlotsPanelMultiAce() emits
one ace-block per device_count. CSS auto-fit grid makes them side-by-side
on wide screens, stacked on narrow. Slot rows are still placeholders —
real interactivity in next task.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Frontend — slot row controls (split-button, chevron, 📖 deep-link)

**Files:**
- Modify: `multiace_web/src/multiace_web/static/app.js`
- Modify: `multiace_web/src/multiace_web/static/style.css`

- [ ] **Step 1: Implement `renderSlotRow` properly**

Replace the placeholder `renderSlotRow` in `app.js`:

```js
function renderSlotRow(state, ace, slot) {
  const row = document.createElement('div');
  row.className = 'slot-row';
  row.dataset.ace = String(ace);
  row.dataset.slot = String(slot);

  const isActive = (state?.active_device ?? 0) === ace;
  const gate = isActive ? (state?.gate_status?.[slot] ?? 0) : null;
  const cache = state?.spool_cache?.[String(ace)]?.[String(slot)];
  const headSource = state?.head_source ?? {};

  // Spool summary cell
  const spoolHtml = cache
    ? `<span class="spool-swatch" style="background:#${cache.color || '666'}"></span>
       <span class="spool-name">${escapeHtml(cache.name || `#${cache.spool_id}`)}</span>
       <span class="muted">${cache.material || ''}${cache.weight_remaining_g != null ? ' · ' + Math.round(cache.weight_remaining_g) + 'g' : ''}</span>`
    : `<span class="muted">${gate === 0 ? '(empty)' : gate === null ? '(unknown)' : '(unbound)'}</span>`;

  // FilamentHub deep-link
  const fhUrl = buildFilamentHubPickerUrl(window.MULTIACE_FH_URL, window.MULTIACE_FH_PRINTER_ID, ace, slot);
  const pickerBtn = fhUrl
    ? `<button class="btn-icon" data-action="picker" title="Pick spool from FilamentHub">📖</button>`
    : `<button class="btn-icon" disabled title="Set FILAMENTHUB_URL to enable">📖</button>`;

  // Load split-button (disabled if slot empty per active gate)
  const loadDisabled = (isActive && gate === 0);
  const loadBtn = `
    <span class="slot-load-split">
      <button class="btn btn-primary" data-action="load" ${loadDisabled ? 'disabled' : ''}>Load</button>
      <button class="btn btn-primary" data-action="load-menu" aria-label="Load to specific head">▾</button>
    </span>`;

  // Unload button (only meaningful if some head sources from this slot)
  const fromThisSlot = Object.entries(headSource).find(([h, src]) =>
    src && src.ace === ace && src.slot === slot);
  const unloadBtn = fromThisSlot
    ? `<button class="btn btn-secondary" data-action="unload" data-head="${fromThisSlot[0]}">Unload T${fromThisSlot[0]}</button>`
    : '';

  row.innerHTML = `
    <span class="slot-label">S${slot}</span>
    <span class="slot-spool">${spoolHtml}</span>
    <span class="slot-actions">${pickerBtn} ${loadBtn} ${unloadBtn}</span>
  `;

  // Wire handlers
  row.querySelectorAll('button[data-action]').forEach(btn => {
    btn.addEventListener('click', (e) => onSlotAction(e, state, ace, slot));
  });
  return row;
}

function buildFilamentHubPickerUrl(fhBase, printerId, ace, slot) {
  if (!fhBase) return null;
  const base = fhBase.replace(/\/$/, '');
  return `${base}/?picker=ace&printer=${encodeURIComponent(printerId || 'u1-1')}&ace=${ace}&slot=${slot}`;
}

function lowestFreeHead(state) {
  const headSource = state?.head_source ?? {};
  const sensors = state?.sensors ?? {};
  for (let h = 0; h < 4; h++) {
    if (!headSource[String(h)] && !sensors[String(h)]) return h;
  }
  return null;
}

async function onSlotAction(e, state, ace, slot) {
  const action = e.currentTarget.dataset.action;
  if (action === 'picker') {
    const url = buildFilamentHubPickerUrl(window.MULTIACE_FH_URL, window.MULTIACE_FH_PRINTER_ID, ace, slot);
    if (url) window.open(url, '_blank', 'noopener,noreferrer');
  } else if (action === 'load') {
    const head = lowestFreeHead(state);
    if (head === null) {
      // open menu instead
      openHeadMenu(e.currentTarget, state, ace, slot);
      return;
    }
    await sendLoad(ace, slot, head);
  } else if (action === 'load-menu') {
    openHeadMenu(e.currentTarget, state, ace, slot);
  } else if (action === 'unload') {
    await sendCommand(`ACEC__Unload_T${e.currentTarget.dataset.head}`);
  }
}

function openHeadMenu(anchor, state, ace, slot) {
  const headSource = state?.head_source ?? {};
  const menu = document.createElement('div');
  menu.className = 'head-target-menu';
  for (let h = 0; h < 4; h++) {
    const busy = !!headSource[String(h)];
    const item = document.createElement('button');
    item.className = 'head-target-menu-item';
    item.disabled = busy;
    item.textContent = busy ? `→ T${h} (busy)` : `→ T${h}`;
    item.addEventListener('click', async () => {
      menu.remove();
      if (!busy) await sendLoad(ace, slot, h);
    });
    menu.appendChild(item);
  }
  // Anchor menu near the chevron
  const r = anchor.getBoundingClientRect();
  menu.style.position = 'fixed';
  menu.style.top = `${r.bottom + 4}px`;
  menu.style.left = `${r.left}px`;
  document.body.appendChild(menu);
  // Dismiss on outside click
  setTimeout(() => {
    document.addEventListener('click', function onDocClick(ev) {
      if (!menu.contains(ev.target)) {
        menu.remove();
        document.removeEventListener('click', onDocClick);
      }
    });
  }, 0);
}

async function sendLoad(ace, slot, head) {
  await sendCommand(`ACE_LOAD_HEAD HEAD=${head} ACE=${ace} SLOT=${slot}`);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}
```

`sendCommand(script)` is the existing helper that POSTs `/api/command`. If it doesn't exist yet, define it once near the top of the relevant block.

- [ ] **Step 2: Pass FilamentHub config to the frontend**

In the existing `index.html` template (or wherever the page is served), inject the env vars:

```html
<script>
  window.MULTIACE_FH_URL = "{{ fh_url }}";
  window.MULTIACE_FH_PRINTER_ID = "{{ fh_printer }}";
</script>
```

If the page is served as static (no template), expose them via an existing `/api/config` or new `/api/web-config` endpoint that the frontend hits at boot:

```python
@app.get("/api/web-config")
async def get_web_config() -> dict:
    return {
        "filamenthub_url": os.environ.get("FILAMENTHUB_URL", ""),
        "filamenthub_printer_id": os.environ.get("FILAMENTHUB_PRINTER_ID", "u1-1"),
    }
```

Frontend boot calls this once and stores the result on `window.MULTIACE_FH_*`.

- [ ] **Step 3: Add CSS for split-button + menu**

```css
.slot-row {
  display: flex; align-items: center; gap: .5rem;
  padding: .35rem .5rem;
  border-radius: 6px;
  background: rgba(255,255,255,.02);
}
.slot-label { width: 2rem; font-weight: 600; }
.slot-spool { flex: 1; display: flex; align-items: center; gap: .35rem; }
.spool-swatch { display: inline-block; width: 14px; height: 14px; border-radius: 3px; border: 1px solid rgba(0,0,0,.25); }
.slot-actions { display: flex; gap: .35rem; align-items: center; }
.btn-icon { background: transparent; border: 1px solid var(--border, rgba(255,255,255,.1));
            border-radius: 6px; padding: .15rem .35rem; cursor: pointer; }
.slot-load-split { display: inline-flex; }
.slot-load-split > button:first-child { border-radius: 6px 0 0 6px; padding: .25rem .65rem; }
.slot-load-split > button:last-child  { border-radius: 0 6px 6px 0; padding: .25rem .35rem; border-left: 1px solid rgba(0,0,0,.2); }
.head-target-menu {
  background: var(--surface); border: 1px solid var(--border, rgba(255,255,255,.1));
  border-radius: 6px; padding: .25rem; display: flex; flex-direction: column;
  min-width: 160px; box-shadow: 0 4px 12px rgba(0,0,0,.4); z-index: 1000;
}
.head-target-menu-item {
  background: transparent; border: 0; padding: .35rem .5rem; text-align: left;
  border-radius: 4px; cursor: pointer; color: var(--fg);
}
.head-target-menu-item:hover:not([disabled]) { background: rgba(255,255,255,.06); }
.head-target-menu-item[disabled] { opacity: .4; cursor: not-allowed; }
```

- [ ] **Step 4: Manual smoke**

Refresh the dashboard against the live printer. Verify:
- 📖 button opens FilamentHub URL in a new tab.
- Load (default) issues a `ACE_LOAD_HEAD HEAD=<auto> ACE=N SLOT=S` to `/api/command`.
- Chevron opens menu with busy heads disabled.
- After unload, the row's Unload button disappears.

- [ ] **Step 5: Commit**

```bash
git add multiace_web/src/multiace_web/static/app.js multiace_web/src/multiace_web/static/style.css multiace_web/src/multiace_web/server.py
git -c user.name="Raul" -c user.email="raul@leadingbit.com" commit -m "$(cat <<'EOF'
feat(web): slot row split-button + chevron menu + FilamentHub deep-link

Each Dashboard slot row now has:
- 📖 button → opens FilamentHub picker in a new tab with printer/ace/slot
  query params (disabled when FILAMENTHUB_URL is unset).
- Load (default) → ACE_LOAD_HEAD to lowest free head (head_source null AND
  e<h>_filament not detected); falls back to opening the chevron menu.
- ▾ chevron → menu of T0/T1/T2/T3 with busy heads disabled.

New /api/web-config endpoint exposes FILAMENTHUB_URL / _PRINTER_ID to the
frontend at boot.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Frontend — Dryer card stacked rows

**Files:**
- Modify: `multiace_web/src/multiace_web/static/app.js`
- Modify: `multiace_web/src/multiace_web/static/style.css`

- [ ] **Step 1: Replace the dryer card renderer**

Find the existing single-dryer card renderer (audit said `app.js:877`, fed by `printState.dryer`). Replace with:

```js
function renderDryerCardMultiAce(container, printState) {
  const aces = printState?.aces ?? [];
  if (aces.length === 0) {
    container.innerHTML = `<div class="muted">All idle</div>`;
    return;
  }
  const rows = aces.map(a => renderDryerRow(a)).join('');
  container.innerHTML = rows;
  container.querySelectorAll('button[data-action="dry-stop"]').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      const ace = parseInt(e.currentTarget.dataset.ace, 10);
      await fetch('/api/dry/stop', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ace}),
      });
    });
  });
}

function renderDryerRow(aceBlock) {
  const status = aceBlock?.dryer?.status || 'stop';
  const isDrying = status !== 'stop';
  const icon = isDrying ? '<span class="dot dot-on">●</span>' : '<span class="dot">○</span>';
  const tempStr  = isDrying ? `${aceBlock.dryer.target_temp}°C` : '';
  const remainStr = isDrying ? `${formatRemaining(aceBlock.dryer.remain_time)}` : '';
  const stale = aceBlock.is_active ? '' : '<span class="badge badge-stale">stale</span>';
  return `
    <div class="dryer-row">
      <span class="dryer-ace">ACE ${String.fromCharCode(65 + aceBlock.index)}</span>
      ${icon}
      <span class="dryer-state">${isDrying ? 'drying' : 'idle'}</span>
      <span class="muted">${tempStr}</span>
      <span class="muted">${remainStr}</span>
      ${stale}
      ${isDrying ? `<button class="btn btn-secondary" data-action="dry-stop" data-ace="${aceBlock.index}">Stop</button>` : ''}
    </div>
  `;
}

function formatRemaining(s) {
  if (!s) return '';
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h ? `${h}h${String(m).padStart(2,'0')}m` : `${m}m`;
}
```

- [ ] **Step 2: Add CSS**

```css
.dryer-row {
  display: flex; align-items: center; gap: .5rem;
  padding: .35rem .5rem;
  border-radius: 6px;
  background: rgba(255,255,255,.02);
}
.dryer-ace { font-weight: 600; min-width: 4.5rem; }
.dryer-state { min-width: 4rem; }
.dot { font-size: 1rem; }
.dot-on { color: var(--accent); }
```

- [ ] **Step 3: Manual smoke**

Click Dashboard. Confirm two dryer rows appear when `device_count=2`. Start drying on ACE 1 via the Dryer tab; the Dashboard row updates within ~5 s. Click Stop and verify the gcode reaches Moonraker (check the audit log).

- [ ] **Step 4: Commit**

```bash
git add multiace_web/src/multiace_web/static/app.js multiace_web/src/multiace_web/static/style.css
git -c user.name="Raul" -c user.email="raul@leadingbit.com" commit -m "$(cat <<'EOF'
feat(web): Dashboard dryer card shows one row per ACE with Stop button

Replaces the single-dryer card with stacked rows fed by /api/print's
new aces:[{index, dryer, is_active}] block. Stop button on each row
calls /api/dry/stop {ace}. Inactive ACE rows show a "stale" badge.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: Frontend — Activity ACE column + filter chips

**Files:**
- Modify: `multiace_web/src/multiace_web/static/app.js`
- Modify: `multiace_web/src/multiace_web/static/style.css`

- [ ] **Step 1: Update the activity renderer**

Find the existing `renderActivity()` (audit said `app.js:1362`). Replace with:

```js
function renderActivity(container, events, deviceCount) {
  const filter = window._activityFilter ?? null;   // null=all, integer=specific ACE
  const chipsHtml = renderActivityChips(deviceCount, filter);
  const visible = events.filter(e => filter === null || extractAce(e) === filter);
  const rowsHtml = visible.map(renderActivityRow).join('');
  container.innerHTML = `
    <div class="activity-chips">${chipsHtml}</div>
    <div class="activity-list">${rowsHtml}</div>
  `;
  container.querySelectorAll('.activity-ace-chip').forEach(c => {
    c.addEventListener('click', () => {
      const f = c.dataset.filter;
      window._activityFilter = f === 'all' ? null : parseInt(f, 10);
      renderActivity(container, events, deviceCount);
    });
  });
}

function renderActivityChips(deviceCount, current) {
  const chips = [`<button class="activity-ace-chip ${current === null ? 'is-on' : ''}" data-filter="all">All</button>`];
  for (let i = 0; i < deviceCount; i++) {
    chips.push(`<button class="activity-ace-chip ${current === i ? 'is-on' : ''}" data-filter="${i}">ACE ${String.fromCharCode(65 + i)}</button>`);
  }
  return chips.join('');
}

function extractAce(event) {
  // Events have params:{ace} for explicit ACE-scoped actions; for SWITCH the
  // target_ace is in active_device or params.target_ace; fall back to
  // event.active_device.
  const p = event.params || {};
  if (p.ace != null) return p.ace;
  if (p.target_ace != null) return p.target_ace;
  return event.active_device ?? 0;
}

function renderActivityRow(event) {
  const ts = (event.ts_iso || event.ts || '').slice(11, 19);
  const ace = extractAce(event);
  const action = event.action || '';
  const params = JSON.stringify(event.params || {});
  return `
    <div class="activity-row">
      <span class="activity-ts muted">${ts}</span>
      <span class="activity-ace-tag">ACE ${String.fromCharCode(65 + ace)}</span>
      <span class="activity-action">${action}</span>
      <span class="activity-params muted">${escapeHtml(params)}</span>
    </div>
  `;
}
```

- [ ] **Step 2: Add CSS**

```css
.activity-chips { display: flex; gap: .35rem; margin-bottom: .5rem; }
.activity-ace-chip {
  display: inline-block; padding: 2px 10px; border-radius: 999px;
  background: rgba(255,255,255,.05); border: 1px solid var(--border, rgba(255,255,255,.1));
  cursor: pointer; font-size: .85rem; color: var(--fg);
}
.activity-ace-chip.is-on { background: var(--accent); color: var(--on-accent, #fff); }
.activity-row { display: grid; grid-template-columns: 4.5rem 4.5rem 12rem 1fr; gap: .5rem; padding: .15rem 0; }
.activity-ace-tag { font-weight: 600; }
```

- [ ] **Step 3: Manual smoke**

Open Activity tab. Confirm chips appear, default filter is "All", clicking ACE A or ACE B narrows the visible rows.

- [ ] **Step 4: Commit**

```bash
git add multiace_web/src/multiace_web/static/app.js multiace_web/src/multiace_web/static/style.css
git -c user.name="Raul" -c user.email="raul@leadingbit.com" commit -m "$(cat <<'EOF'
feat(web): Activity tab adds ACE column and All/ACE A/ACE B filter chips

Filter is client-side; events without an explicit ACE in params fall
back to active_device. Tag column makes per-ACE flow easier to scan.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: Frontend — Diag ACE dropdown

**Files:**
- Modify: `multiace_web/src/multiace_web/static/app.js`
- Modify: `multiace_web/src/multiace_web/static/style.css`

- [ ] **Step 1: Add a dropdown that scopes per-ACE diag panels**

Find the existing diag renderer (audit said `app.js:1664`). Wrap it:

```js
function renderDiag(container, state) {
  const deviceCount = state?.device_count ?? 1;
  const current = window._diagAce ?? 0;
  const opts = [];
  for (let i = 0; i < deviceCount; i++) {
    opts.push(`<option value="${i}" ${i === current ? 'selected' : ''}>ACE ${String.fromCharCode(65 + i)} (#${i})</option>`);
  }
  container.innerHTML = `
    <header class="diag-toolbar">
      <label>Show ACE: <select id="diagAce">${opts.join('')}</select></label>
      <span class="muted">Per-ACE blocks below scope to this selection. Globals (klippy log, ACE_LIST) stay full-printer.</span>
    </header>
    <div id="diagPerAce"></div>
    <hr/>
    <div id="diagGlobals"></div>
  `;
  document.getElementById('diagAce').addEventListener('change', (e) => {
    window._diagAce = parseInt(e.target.value, 10);
    renderDiag(container, state);
  });
  renderDiagPerAce(document.getElementById('diagPerAce'), state, current);
  renderDiagGlobals(document.getElementById('diagGlobals'), state);
}

function renderDiagPerAce(host, state, ace) {
  // USB path, slots, sensors, head_source slice, autodry FSM for this ACE
  const headSourceForAce = Object.entries(state?.head_source ?? {})
    .filter(([h, src]) => src && src.ace === ace)
    .map(([h, src]) => `T${h} ← ACE ${String.fromCharCode(65+ace)} / S${src.slot} (${src.type || '?'})`).join('<br/>') || '<span class="muted">no toolheads loaded from this ACE</span>';
  host.innerHTML = `
    <h4>ACE ${String.fromCharCode(65 + ace)} (#${ace})</h4>
    <div><b>Active:</b> ${state?.active_device === ace ? 'yes (currently connected)' : 'no (last-known data)'}</div>
    <div><b>Heads sourced:</b><br/>${headSourceForAce}</div>
    <pre class="diag-json">${escapeHtml(JSON.stringify({
      active_device: state?.active_device,
      gate_status: state?.active_device === ace ? state?.gate_status : null,
    }, null, 2))}</pre>
  `;
}
function renderDiagGlobals(host, state) {
  // Existing global diag content (klippy.log tail, raw JSON, ACE_LIST/ACE_HEAD_STATUS macros)
  // Move the previous per-tab content here unchanged.
}
```

Move the previous diag content (klippy.log tail, raw JSON snapshot, macro buttons) into `renderDiagGlobals`.

- [ ] **Step 2: Add CSS**

```css
.diag-toolbar { display: flex; align-items: center; gap: .75rem; padding: .5rem 0; }
.diag-toolbar select { background: var(--surface); color: var(--fg); border: 1px solid var(--border, rgba(255,255,255,.1)); border-radius: 6px; padding: .25rem .5rem; }
.diag-json { background: rgba(0,0,0,.25); padding: .5rem; border-radius: 6px; max-height: 200px; overflow: auto; font-size: .8rem; }
```

- [ ] **Step 3: Manual smoke**

Open Diag tab; switch dropdown ACE 0 → ACE 1 and verify the per-ACE block re-renders. Globals stay unchanged below.

- [ ] **Step 4: Commit**

```bash
git add multiace_web/src/multiace_web/static/app.js multiace_web/src/multiace_web/static/style.css
git -c user.name="Raul" -c user.email="raul@leadingbit.com" commit -m "$(cat <<'EOF'
feat(web): Diag tab — ACE dropdown scopes per-ACE blocks

Header dropdown switches the per-ACE diag block (USB path, gate_status,
head sources). Global diag content (klippy log tail, ACE_LIST output,
raw JSON) stays below the dropdown unchanged.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 14: Visual regression script extension

**Files:**
- Modify: `multiace_web/tools/visual_regression.py`

- [ ] **Step 1: Read the existing structure**

Run: `head -50 multiace_web/tools/visual_regression.py` to see the tab list / viewport list / save naming convention.

- [ ] **Step 2: Add the new screenshots**

Edit the script to capture:
- `dashboard-wide` at 1280×900 (both ACEs side-by-side visible)
- `dashboard-narrow` at 390×844 (stacked)
- `activity-filter-all` and `activity-filter-aceA` at 1280×900 (proves chips work; click is read-only — tagging filter state)
- `diag-ace0` and `diag-ace1` at 1280×900 (proves dropdown works)
- `dryer-card-multiace` at 1280×900

The existing helper for tab navigation should accept a click-after-load hook; use that for chip and dropdown interactions only — do NOT click any of the project-forbidden controls (Save & Restart, Load/Unload, Start dry).

- [ ] **Step 3: Manual run against the live printer**

```bash
cd multiace_web
python tools/visual_regression.py http://192.168.1.171/multiace/
```

Inspect the output PNGs. Verify the layouts render as designed.

- [ ] **Step 4: Commit**

```bash
git add multiace_web/tools/visual_regression.py
git -c user.name="Raul" -c user.email="raul@leadingbit.com" commit -m "$(cat <<'EOF'
test(web): visual_regression captures dual-ACE screens

Adds Dashboard wide/narrow, Activity filter states, Diag dropdown
positions, and the new stacked dryer card. Stays read-only per
project rule (no Save & Restart / Load / dry start clicks).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 15: Live-printer e2e walkthrough

**Files:**
- Create: `multiace_web/tools/e2e_dual_ace.py`

- [ ] **Step 1: Pre-flight — confirm no print is in progress**

Run:
```bash
curl -s http://192.168.1.171:7125/printer/objects/query?print_stats | jq -r '.result.status.print_stats.state'
```
Must be one of `standby`, `complete`, `cancelled`, `error`. If `printing` or `paused`, **stop** — this script issues real load gcode.

- [ ] **Step 2: Create the script**

```python
# multiace_web/tools/e2e_dual_ace.py
"""Manual Playwright golden-path for the dual-ACE GUI. Run only when no
print is in progress. Issues real ACE_LOAD_HEAD gcode against the printer.
Usage:
    python tools/e2e_dual_ace.py http://192.168.1.171/multiace/
"""
import sys, time, asyncio
import httpx
from playwright.async_api import async_playwright

PRINTER_HTTP = "http://192.168.1.171:7125"

async def assert_safe():
    async with httpx.AsyncClient(timeout=4) as c:
        r = await c.get(f"{PRINTER_HTTP}/printer/objects/query?print_stats")
        state = r.json()["result"]["status"]["print_stats"]["state"]
    assert state in ("standby", "complete", "cancelled", "error"), \
        f"Unsafe to run e2e — print state is {state!r}"

async def main(url: str) -> None:
    await assert_safe()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        ctx = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await ctx.new_page()
        await page.goto(url)
        # 1. Both ACE blocks visible
        ace_blocks = await page.locator(".ace-block").count()
        assert ace_blocks == 2, f"Expected 2 ACE blocks, got {ace_blocks}"
        # 2. Click 📖 on ACE B / slot 0 — popup opens to FilamentHub URL
        async with ctx.expect_page() as popup_info:
            await page.locator('.ace-block[data-ace="1"] .slot-row[data-slot="0"] button[data-action="picker"]').click()
        popup = await popup_info.value
        assert "picker=ace" in popup.url and "ace=1" in popup.url and "slot=0" in popup.url
        await popup.close()
        # 3. Click chevron on ACE B / slot 0 — menu shows
        await page.locator('.ace-block[data-ace="1"] .slot-row[data-slot="0"] button[data-action="load-menu"]').click()
        await page.locator('.head-target-menu').wait_for(state="visible", timeout=2000)
        # Check items
        item_count = await page.locator('.head-target-menu-item').count()
        assert item_count == 4
        # Pick → T0
        await page.locator('.head-target-menu-item').nth(0).click()
        # 4. Wait for head_source[0] to appear over WebSocket (poll /api/state)
        deadline = time.time() + 90
        async with httpx.AsyncClient(timeout=4) as c:
            while time.time() < deadline:
                r = await c.get(f"{PRINTER_HTTP}/printer/objects/query?ace")
                hs0 = r.json()["result"]["status"]["ace"]["head_source"].get("0")
                if hs0 and hs0.get("ace_index") == 1 and hs0.get("slot") == 0:
                    break
                await asyncio.sleep(2)
            else:
                raise SystemExit("head_source[0] never updated within 90s")
        # 5. Screenshot the success state
        await page.screenshot(path="e2e_dual_ace_success.png", full_page=True)
        print("OK — see e2e_dual_ace_success.png")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "http://192.168.1.171/multiace/"))
```

- [ ] **Step 3: Run it**

```bash
cd multiace_web
pip install playwright && playwright install chromium
python tools/e2e_dual_ace.py http://192.168.1.171/multiace/
```

Watch the browser execute the flow. The script asserts:
- Two ACE blocks visible.
- 📖 click opens FilamentHub URL with correct query params.
- Chevron menu appears with 4 items.
- Selecting T0 issues the load command and `head_source[0]` becomes `{ace_index:1, slot:0}` within 90 s.
- Final screenshot saved.

- [ ] **Step 4: Run full pytest suite once more**

Run: `cd multiace_web && pytest -q`
Expected: all tests pass; total time ≤25 s.

- [ ] **Step 5: Update README + project docs**

In `multiace_web/README.md`, add a paragraph under "Environment variables" mentioning `FILAMENTHUB_URL` and `FILAMENTHUB_PRINTER_ID`. Add a one-line entry under "Tools" pointing at `tools/e2e_dual_ace.py`.

In `README.md` (repo root, in the Web Console section), update the bullet list to mention dual-ACE GUI.

- [ ] **Step 6: Commit + final summary**

```bash
git add multiace_web/tools/e2e_dual_ace.py multiace_web/README.md README.md
git -c user.name="Raul" -c user.email="raul@leadingbit.com" commit -m "$(cat <<'EOF'
test(web): e2e_dual_ace.py — Playwright golden-path against live printer

Validates: both ACE blocks render, FilamentHub deep-link opens with
correct query params, chevron head menu issues ACE_LOAD_HEAD HEAD=0
ACE=1 SLOT=0, and head_source[0] resolves to the requested binding
within 90s. Pre-flights print_stats.state for safety.

Documents new FILAMENTHUB_URL and FILAMENTHUB_PRINTER_ID env vars.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-review notes

**Spec coverage check:**
- ✅ Side-by-side dashboard slots (Task 9)
- ✅ Dryer card stacked rows (Task 11)
- ✅ Slot Load split-button + chevron + 📖 deep-link (Task 10)
- ✅ Dryer tab independent autodry (Tasks 2-4 backend, Task 6 endpoint)
- ✅ Activity ACE column + chips (Task 12)
- ✅ Diag ACE dropdown (Task 13)
- ✅ Round-robin poller (Task 5)
- ✅ Spoolman client + cache (Tasks 1, 8)
- ✅ Endpoints `/api/slots`, `/api/dry/stop`, `/api/autodry?ace=` (Task 6)
- ✅ `/api/print` per-ACE block (Task 7)
- ✅ `/api/command` 409 pre-flight (Task 7)
- ✅ Persistence migration (Task 3)
- ✅ Visual regression updates (Task 14)
- ✅ Live e2e walkthrough (Task 15)

**Type/method consistency check:**
- `AutodryManager.with_defaults` / `.deserialize` / `.migrate_from_legacy` — used consistently in Tasks 2/3/4.
- `MultiAcePoller.__init__(moonraker, autodry, device_count, period_s)` matches Task 5 tests and Task 5 server wiring.
- `SpoolmanClient.list_all_bindings()` returns `dict[int, dict[int, SpoolBinding]]` consistently.
- `state.spool_cache` keyed by integers in Python; serialized with stringified keys for JSON in WS payload (Task 8) and read by frontend with both `String(ace)` and integer fallback (Task 10) — consistent.
- `buildFilamentHubPickerUrl(fhBase, printerId, ace, slot)` signature matches across Tasks 10 and 15.
- `ACE_LOAD_HEAD HEAD=N ACE=M SLOT=S` script form is identical in Tasks 7 (regex), 10 (frontend), 15 (e2e assertion).
