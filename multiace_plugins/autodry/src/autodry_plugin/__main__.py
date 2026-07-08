# License: GPL-3.0
"""Run the Auto-Dry plugin sidecar."""
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
