"""`python -m prometheus_hoard` — run the app with uvicorn on 127.0.0.1 (the shared Hoard Link launcher)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from . import DEFAULT_PORT, SERVICE
from .hoard_link.service import run_main

# Written by the Hoard Hub while Prometheus runs on a Spark (so the PC can be switched off): a second one here would
# drive the same cluster from a stale copy of its state.
MARKER = Path(__file__).resolve().parent.parent / "data" / "placement-elsewhere.json"


def placed_elsewhere():
    try:
        where = json.loads(MARKER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return where if isinstance(where, dict) and where.get("location") not in (None, "", "pc") else None


def main() -> int:
    where = placed_elsewhere()
    if where:
        print(f"Prometheus is running on {where.get('node') or 'a Spark'}; bring it back from the Hoard Hub (Run on: PC) first.",
              file=sys.stderr)
        return 3
    return run_main(service=SERVICE, package="prometheus_hoard", default_port=DEFAULT_PORT, app_factory="prometheus_hoard.main:create_app",
                    data_dir_env="PROMETHEUS_DATA_DIR", port_env="PROMETHEUS_PORT", open_browser_default=False, title="Prometheus's Hoard")


if __name__ == "__main__":
    raise SystemExit(main())
