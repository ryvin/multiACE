# License: GPL-3.0
"""FilamentHub plugin FastAPI app: manifest + (later) picker endpoints."""
from __future__ import annotations
import logging
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from .config import Config
from .multiace_client import MultiAceClient
from .mapping import spool_to_override

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

    return app