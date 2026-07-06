# License: GPL-3.0
"""FilamentHub plugin FastAPI app: manifest + (later) picker endpoints."""
from __future__ import annotations
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from .config import Config
from .multiace_client import MultiAceClient
from .mapping import spool_to_override

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

    return app