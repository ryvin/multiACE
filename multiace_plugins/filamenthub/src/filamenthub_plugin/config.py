# License: GPL-3.0
"""Environment configuration for the FilamentHub plugin.

Defaults mirror install/S66filamenthub-plugin (a test guards against drift),
so a bare ``python -m filamenthub_plugin`` on the printer behaves like the
init-script launch.
"""
from __future__ import annotations
import os
from dataclasses import dataclass

DEFAULT_FILAMENTHUB_URL = "http://192.168.1.174:3000"
DEFAULT_PRINTER_ID = "u1-b"
DEFAULT_MULTIACE_URL = "http://127.0.0.1:7126"
DEFAULT_PORT = 8089
# /userdata survives reboots and firmware (PAXX) overlay wipes; /etc and
# /home/lava do not.
DEFAULT_DESIRED_PATH = "/userdata/filamenthub-plugin/state/desired.json"


@dataclass(frozen=True)
class Config:
    filamenthub_url: str
    printer_id: str
    multiace_url: str
    port: int
    ace_state_url: str
    desired_state_path: str


def load_config() -> Config:
    filamenthub_url = os.environ.get("FILAMENTHUB_URL", DEFAULT_FILAMENTHUB_URL)
    return Config(
        filamenthub_url=filamenthub_url,
        printer_id=os.environ.get("MULTIACE_PRINTER_ID", DEFAULT_PRINTER_ID),
        multiace_url=os.environ.get("MULTIACE_URL", DEFAULT_MULTIACE_URL),
        port=int(os.environ.get("FILAMENTHUB_PLUGIN_PORT", str(DEFAULT_PORT))),
        ace_state_url=os.environ.get(
            "FILAMENTHUB_ACE_STATE_URL",
            f"{filamenthub_url.rstrip('/')}/fleet/api/ace-state",
        ),
        desired_state_path=os.environ.get(
            "FILAMENTHUB_DESIRED_PATH", DEFAULT_DESIRED_PATH),
    )
