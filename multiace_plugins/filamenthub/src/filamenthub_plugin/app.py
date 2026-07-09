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
from .mapping import spool_to_override
from .ace_state import AceStateClient, AceStateError
from .reconcile import plan_reconcile

log = logging.getLogger("filamenthub.plugin")

MANIFEST = {"name": "filamenthub", "label": "FilamentHub",
            "version": "0.1.0", "ui_url": "/"}


class AssignReq(BaseModel):
    spool_id: int
    ace: int
    slot: int


class UnassignReq(BaseModel):
    ace: int
    slot: int


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

    @app.post("/assign")
    async def assign(req: AssignReq):
        sm = SpoolmanClient(cfg.filamenthub_url, cfg.printer_id)
        try:
            spools = await sm.list_spools(raise_on_error=True)
        except httpx.HTTPError as e:
            log.warning("FilamentHub inventory lookup failed: %s", e)
            raise HTTPException(status_code=502, detail="FilamentHub unreachable")
        spool = next((s for s in spools if s["spool_id"] == req.spool_id), None)
        if spool is None:
            raise HTTPException(status_code=404, detail="spool not found in FilamentHub")
        # 1. write-back to FilamentHub
        try:
            location = await sm.assign_spool(req.spool_id, req.ace, req.slot)
        except httpx.HTTPError as e:
            log.warning("FilamentHub write failed: %s", e)
            raise HTTPException(status_code=502, detail="FilamentHub write failed")
        # 2. label the slot in multiACE
        ma = MultiAceClient(cfg.multiace_url)
        try:
            override = await ma.set_override(**spool_to_override(spool, req.ace, req.slot))
        except httpx.HTTPError as e:
            log.warning("multiACE slot-override failed: %s", e)
            raise HTTPException(status_code=502,
                detail="multiACE slot-override failed (FilamentHub already updated)")
        return {"ok": True, "location": location, "override": override}

    @app.post("/unassign")
    async def unassign(req: UnassignReq):
        sm = SpoolmanClient(cfg.filamenthub_url, cfg.printer_id)
        try:
            cleared = await sm.unassign_slot(req.ace, req.slot, raise_on_error=True)
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
        return {"ok": True, "cleared_spool_id": cleared}

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
        disputed_keys = {
            (d["ace"], d["slot"]) for d in disputed
            if d.get("ace") is not None and d.get("slot") is not None
        }
        to_apply, to_clear = plan_reconcile(
            winners, current.keys(), brand_by_spool_id, disputed_keys)

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

    static_dir = Path(__file__).parent / "static"
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app