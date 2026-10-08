"""Prometheus's Hoard — runs a small cluster of DGX Spark computers from Windows: each Spark is a drive in a file explorer, its GPU,
CPU, memory and network are on a live panel, the models on its disks and the ones it serves are listed, and models are loaded and
unloaded through recipes that start and stop the inference servers over SSH."""

__version__ = "0.1.0"
SERVICE = "prometheus-hoard"
APP_ID = "prometheus"
DEFAULT_PORT = 5205
