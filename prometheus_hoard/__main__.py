"""`python -m prometheus_hoard` — run the app with uvicorn on 127.0.0.1 (the shared Hoard Link launcher)."""

from __future__ import annotations

from . import DEFAULT_PORT, SERVICE
from .hoard_link.service import run_main


def main() -> int:
    return run_main(service=SERVICE, package="prometheus_hoard", default_port=DEFAULT_PORT, app_factory="prometheus_hoard.main:create_app",
                    data_dir_env="PROMETHEUS_DATA_DIR", port_env="PROMETHEUS_PORT", open_browser_default=False, title="Prometheus's Hoard")


if __name__ == "__main__":
    raise SystemExit(main())
