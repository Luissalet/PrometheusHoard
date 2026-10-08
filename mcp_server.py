"""Stdio MCP bridge for Prometheus's Hoard.

It never talks to the Sparks itself: every tool call is proxied to the running app (`POST /api/agent/call`) with the Bearer token from
`<DATA_DIR>/mcp-token`; the tool list comes from `GET /api/agent/tools`. When nothing answers, the bridge starts the app
(`python -m prometheus_hoard`) and waits for it; PROMETHEUS_BRIDGE_AUTOSTART=0 turns that off."""

from __future__ import annotations

import sys

from prometheus_hoard.hoard_link.bridge import CatalogBridge


def main() -> int:
    CatalogBridge(app="prometheus", service="prometheus-hoard", package="prometheus_hoard", default_port=5205, data_dir_env="PROMETHEUS_DATA_DIR",
                  title="Prometheus's Hoard", root=__file__, default_timeout=900.0).run_bridge()
    return 0


if __name__ == "__main__":
    sys.exit(main())
