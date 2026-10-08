# Prometheus's Hoard

Runs a small cluster of NVIDIA DGX Spark computers from a Windows PC. Each Spark is a drive in a file explorer, its GPU, CPU, unified memory, power, disks and network are on a live panel, the models on its disks are listed and downloaded, models are loaded and unloaded through recipes, and every Spark can be locked, put to sleep, shut down, restarted or woken from the same window. Part of the Hoard family: it works on its own, from the Hoard Hub, from Faustus and from any MCP client.

![Equipo](docs/screens/equipo.png)

## What it does

- **Equipo**: one card per Spark with state, uptime, GPU use, temperature, power and clock, CPU use per core, unified memory, disk, CX7 links and the models it serves (head or worker). A Task Manager view per Spark (`#/spark/<id>`) with large charts, processes, containers, detected inference servers and network interfaces.
- **Archivos**: an explorer with every Spark as a drive. Address bar, back/forward/up, search, details and icon views, hidden files, preview (text with line numbers, JSON, images, video, audio) and in-place editing of text files, upload by drag and drop, download, new folder and file, rename (F2), cut/copy/paste (Ctrl+X/C/V), send to other Sparks over the CX7 cables, properties with folder size, and a trash per Spark with restore. Writes stay inside the home folder of the Spark user; the rest of the disk can be shown read-only.
- **Modelos**: what is loaded now with its OpenAI-compatible URL; recipes with a Load button (refused with the conflict when another model uses the same Sparks, with an option to unload it first); models on each disk with size, architecture, quantisation and context; downloads from Hugging Face; copies between Sparks over CX7 (rsync, resumable); jobs with progress and logs.
- **Power**: lock, sleep, shut down and restart one Spark or all of them; turning on uses wake-on-LAN with the wired MAC address learnt while the Spark was on. Before a shutdown the models of that Spark are unloaded.
- **Endpoints for other programs**: `GET /api/endpoints` lists the inference servers running now (the default recipe first). Faustus reads it to use the Sparks as its default backend.

![Archivos](docs/screens/archivos.png)

![Modelos](docs/screens/modelos.png)

## Recipes

Measured recipes show input tokens, estimated generation speed, time to the first token and retrieval results. Expand “Show all measurements” for every recorded value, including nested baseline tests, retrieval positions and cache memory. These values come from the recipe's `measured` data; the panel does not run a benchmark or certify its results.

A recipe is a folder with `recipe.json` and the scripts that start and stop a server (`start.sh`, `stop.sh`, optional `health.sh` and `logs.sh`). The recipes folder is a setting (default: `Sparks cluster/recipes` next to this repository). Loading copies the folder to each Spark it uses, runs `start.sh` on each one (the head first unless `start_order` says otherwise) as a detached job, and waits until the head answers on its port. Scripts get `PROM_NODE`, `PROM_ROLE`, `PROM_RANK`, `PROM_HEAD`, `PROM_HEAD_IP` (the head's address on the CX7 cable shared with this Spark), `PROM_PORT`, `PROM_MODEL`, `PROM_SERVED_NAME`, `PROM_MAX_MODEL_LEN`, `PROM_FABRIC_<NODE>` and `SPARK_NODE`. See `prometheus_hoard/recipes.py` for every key.

## Running

```
python -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\python -m prometheus_hoard        # http://127.0.0.1:5205
```

The Sparks are reached with the SSH configuration of the PC (`~/.ssh/config` and its `Include`s; NVIDIA Sync writes the `Spark1`..`Spark3` aliases there). In Ajustes each Spark can have a different address or a jump host. `PROMETHEUS_FAKE=1` starts an invented three-Spark cluster in a temporary folder, for trying the interface without hardware.

The MCP bridge is `python mcp_server.py` (37 tools; `faustus-plugin.json` describes it for Faustus and the Hoard Hub). Destructive tools (permanent deletes, emptying the trash, deleting a model, power actions, shell commands) need `confirm=true`.

## What it cannot do

- It does not install drivers, change the network of the Sparks or build inference engines: recipes do that.
- Shutting down, restarting and sleeping need `sudo` without a password (or a polkit rule) on the Sparks; without it the action is refused with the line to add.
- Wake-on-LAN works only if the firmware of the Spark has it enabled.
- Whole folders cannot be downloaded to the PC in one go (send them to another Spark or compress them first).

## Development

```
python -m pytest           # backend, against the invented cluster
npx vite build             # interface into prometheus_hoard/static
python scripts/make_icon.py --family ..\Icons
```

MIT licence.
