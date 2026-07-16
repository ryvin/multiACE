# License: GPL-3.0
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
    ace_state_url: str
    desired_state_path: str


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
        desired_state_path=os.environ.get(
            "FILAMENTHUB_DESIRED_PATH", ".filamenthub_desired.json"),
    )