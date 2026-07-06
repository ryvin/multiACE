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
