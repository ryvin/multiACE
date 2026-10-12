# License: GPL-3.0
"""FilamentHub plugin FastAPI app: manifest + (later) picker endpoints."""
from __future__ import annotations
import logging
from pathlib import Path
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from .config import Config
from .multiace_client import MultiAceClient
from .mapping import ace_state_row_to_override, spool_to_override
from .ace_state import AceStateClient, AceStateError
from .desired_store import load_desired, save_desired
from .reconcile import plan_reconcile, reconcile_slots
from .filaments import (create_spool_from_filament, fetch_filaments,
                        filament_summary, list_filaments)

log = logging.getLogger("filamenthub.plugin")

MANIFEST = {"name": "filamenthub", "label": "FilamentHub",
            "version": "0.1.0", "ui_url": "/"}


class AssignReq(BaseModel):
    spool_id: int
    ace: int
    slot: int


class AssignProfileReq(BaseModel):
    filament_id: int
    ace: int
    slot: int
    # False: label the slot from the profile only (no FilamentHub write).
    # True: mint a new spool of this filament (virtual RFID tag) and assign it.
    create_spool: bool = False


class UnassignReq(BaseModel):
    ace: int
    slot: int


class PullReq(BaseModel):
    # prune=True runs a full reconcile including destructive clears of vacated
    # slots (explicit "Pull" button). prune=False is additive-only — applies /
    # updates labels but never deletes — used by the passive auto-pull-on-open so
    # a transient seam drop can't churn or delete valid labels.
    prune: bool = True


def create_app(cfg: Config) -> FastAPI:
    app = FastAPI(title="FilamentHub Plugin")
    app.state.cfg = cfg

    @app.get("/integration-manifest")
    def integration_manifest():
        return MANIFEST

    from .spoolman import SpoolmanClient

    @app.get("/spools")
    async def spools():
        sm = SpoolmanClient(cfg.filamenthub_url, cfg.printer_id)
        return {"spools": await sm.list_spools()}

    async def _assign_spool(spool_id: int, ace: int, slot: int) -> tuple[dict, dict, dict]:
        """Shared /assign path: FilamentHub location write, then multiACE label.
        Returns (location, override_payload, override_response)."""
        sm = SpoolmanClient(cfg.filamenthub_url, cfg.printer_id)
        try:
            spools = await sm.list_spools(raise_on_error=True)
        except httpx.HTTPError as e:
            log.warning("FilamentHub inventory lookup failed: %s", e)
            raise HTTPException(status_code=502, detail="FilamentHub unreachable")
        spool = next((s for s in spools if s["spool_id"] == spool_id), None)
        if spool is None:
            raise HTTPException(status_code=404, detail="spool not found in FilamentHub")
        # 1. write-back to FilamentHub
        try:
            location = await sm.assign_spool(spool_id, ace, slot)
        except httpx.HTTPError as e:
            log.warning("FilamentHub write failed: %s", e)
            raise HTTPException(status_code=502, detail="FilamentHub write failed")
        # 2. label the slot in multiACE
        ma = MultiAceClient(cfg.multiace_url)
        payload = spool_to_override(spool, ace, slot,
                                    materials_db=await ma.get_materials())
        try:
            override = await ma.set_override(**payload)
        except httpx.HTTPError as e:
            log.warning("multiACE slot-override failed: %s", e)
            raise HTTPException(status_code=502,
                detail="multiACE slot-override failed (FilamentHub already updated)")
        return location, payload, override

    def _record_desired(payload: dict, *, spool_id, filament_id) -> None:
        entry = dict(payload, spool_id=spool_id, filament_id=filament_id)
        merged = dict(load_desired(cfg.desired_state_path))
        merged[f"{payload['ace']}_{payload['slot']}"] = entry
        save_desired(cfg.desired_state_path, cfg.printer_id, merged)

    @app.post("/assign")
    async def assign(req: AssignReq):
        location, payload, override = await _assign_spool(req.spool_id, req.ace, req.slot)
        _record_desired(payload, spool_id=req.spool_id, filament_id=None)
        return {"ok": True, "location": location, "override": override}

    @app.get("/filaments")
    async def filaments():
        try:
            return {"filaments": await list_filaments(cfg.filamenthub_url)}
        except httpx.HTTPError as e:
            log.warning("FilamentHub filament list failed: %s", e)
            raise HTTPException(status_code=502, detail="FilamentHub unreachable")

    @app.post("/assign-profile")
    async def assign_profile(req: AssignProfileReq):
        try:
            raw = await fetch_filaments(cfg.filamenthub_url)
        except httpx.HTTPError as e:
            log.warning("FilamentHub filament lookup failed: %s", e)
            raise HTTPException(status_code=502, detail="FilamentHub unreachable")
        fil = next((f for f in raw if isinstance(f, dict) and f.get("id") == req.filament_id),
                   None)
        if fil is None:
            raise HTTPException(status_code=404, detail="filament not found in FilamentHub")

        if req.create_spool:
            try:
                spool_id = await create_spool_from_filament(cfg.filamenthub_url, fil)
            except httpx.HTTPError as e:
                log.warning("FilamentHub spool create failed: %s", e)
                raise HTTPException(status_code=502, detail="FilamentHub spool create failed")
            location, payload, override = await _assign_spool(spool_id, req.ace, req.slot)
        else:
            # Profile-only: label the slot; FilamentHub location is NOT written
            # (there is no spool to place).
            spool_id, location = None, None
            summary = filament_summary(fil)
            ma = MultiAceClient(cfg.multiace_url)
            payload = spool_to_override(summary, req.ace, req.slot,
                                        materials_db=await ma.get_materials())
            try:
                override = await ma.set_override(**payload)
            except httpx.HTTPError as e:
                log.warning("multiACE slot-override failed: %s", e)
                raise HTTPException(status_code=502, detail="multiACE slot-override failed")

        _record_desired(payload, spool_id=spool_id, filament_id=req.filament_id)
        return {"ok": True, "spool_id": spool_id, "location": location,
                "override": override}

    @app.post("/unassign")
    async def unassign(req: UnassignReq):
        key = f"{req.ace}_{req.slot}"
        entry = load_desired(cfg.desired_state_path).get(key)
        sm = SpoolmanClient(cfg.filamenthub_url, cfg.printer_id)
        cleared = None
        # The plugin's own record says which spool it placed here. Clear only
        # that one; a profile-only label placed no spool, so touch none.
        # Without a record, fall back to the first spool at the slot.
        if entry is None or entry.get("spool_id") is not None:
            try:
                cleared = await sm.unassign_slot(
                    req.ace, req.slot, raise_on_error=True,
                    spool_id=None if entry is None else int(entry["spool_id"]))
            except httpx.HTTPError as e:
                log.warning("FilamentHub clear failed: %s", e)
                raise HTTPException(status_code=502, detail="FilamentHub unreachable")
        ma = MultiAceClient(cfg.multiace_url)
        try:
            await ma.clear_override(req.ace, req.slot)
        except httpx.HTTPError as e:
            log.warning("multiACE clear failed: %s", e)
            raise HTTPException(status_code=502,
                detail="multiACE clear failed (FilamentHub already cleared)")
        merged = dict(load_desired(cfg.desired_state_path))
        if merged.pop(f"{req.ace}_{req.slot}", None) is not None:
            save_desired(cfg.desired_state_path, cfg.printer_id, merged)
        return {"ok": True, "cleared_spool_id": cleared}

    @app.get("/slots")
    async def slots():
        desired = load_desired(cfg.desired_state_path)
        ma = MultiAceClient(cfg.multiace_url)
        try:
            state = await ma.get_state()
            observed_ok = True
        except Exception as e:  # noqa: BLE001 - degrade to desired-only view
            log.warning("plugin-api/state fetch failed: %s", e)
            state, observed_ok = {}, False
        return {"slots": reconcile_slots(desired, state.get("aces", [])),
                "observed_ok": observed_ok}

    @app.get("/ace-state")
    async def ace_state():
        client = AceStateClient(cfg.ace_state_url)
        try:
            return await client.get_ace_state(cfg.printer_id)
        except AceStateError as e:
            raise HTTPException(status_code=502, detail=str(e))

    @app.post("/pull")
    async def pull(req: PullReq = PullReq()):
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
        disputed_keys = {
            (d["ace"], d["slot"]) for d in disputed
            if d.get("ace") is not None and d.get("slot") is not None
        }
        materials_db = await ma.get_materials()
        to_apply, to_clear = plan_reconcile(
            winners, current.keys(), brand_by_spool_id, disputed_keys,
            aces_covered=state.get("aces_covered"), materials_db=materials_db)

        # 4. Execute — collect failures, never abort mid-loop.
        applied, cleared, errors, stale = [], [], [], []
        for payload in to_apply:
            try:
                await ma.set_override(**payload)
                applied.append(payload)
            except httpx.HTTPError as e:
                errors.append({"action": "apply", "ace": payload["ace"],
                               "slot": payload["slot"], "error": str(e)})
        if req.prune:
            for ace_idx, slot_idx in to_clear:
                try:
                    await ma.clear_override(ace_idx, slot_idx)
                    cleared.append({"ace": ace_idx, "slot": slot_idx})
                except httpx.HTTPError as e:
                    errors.append({"action": "clear", "ace": ace_idx,
                                   "slot": slot_idx, "error": str(e)})
        else:
            # Additive-only: report what a full reconcile WOULD prune, but do not
            # delete — the seam can't reliably distinguish "empty" from
            # "transiently absent".
            stale = [{"ace": a, "slot": s} for a, s in to_clear]

        # Persist desired (durable; survives decay71 eject-debounce GC).
        winner_desired = {}
        for row in winners:
            if row.get("slot") is None:
                continue
            a, s = int(row["ace"]), int(row["slot"])
            payload = ace_state_row_to_override(
                row, brand_by_spool_id.get(row.get("spool_id"), ""),
                materials_db=materials_db)
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
        except Exception as e:  # noqa: BLE001 - recon summary is cosmetic; never fail the pull
            log.warning("reconciliation summary skipped: %s", e)
            recon = None

        return {"applied": applied, "cleared": cleared, "stale": stale,
                "disputed": disputed, "errors": errors,
                "reconciliation": recon, "warning": warning}

    static_dir = Path(__file__).parent / "static"
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app