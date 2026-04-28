# multiACE Web Console

Web console for managing Anycubic ACE Pro filament changers on a Snapmaker U1 running multiACE.

## Local development

```bash
cd multiace_web
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest
uvicorn multiace_web.server:app --reload --port 7126
```

Then open http://localhost:7126/.

## Install on printer

The parent multiACE installer (`install_multiace.sh`) runs `install/install_web.sh` after copying the multiACE files. See that script for printer-side details.

## License

GPL-3.0
