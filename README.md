# Prometheus's Hoard

Runs a small cluster of NVIDIA DGX Spark computers from a Windows PC. Each Spark is a drive in a file explorer, its GPU, CPU, unified memory, power, disks and network are on a live panel, the models on its disks are listed and downloaded, models are loaded and unloaded through recipes, and every Spark can be locked, put to sleep, shut down, restarted or woken from the same window. Part of the Hoard family: it works on its own, from the Hoard Hub, from Faustus and from any MCP client.

![Equipo](docs/screens/equipo.png)

## What it does

- **Equipo**: one card per Spark with state, uptime, GPU use, temperature, power and clock, CPU use per core, unified memory, disk, CX7 links and the models it serves (head or worker). A Task Manager view per Spark (`#/spark/<id>`) with large charts, processes, containers, detected inference servers and network interfaces.
- **Sirviendo**: live figures of every running inference server (recipes and servers started by hand), read from the Prometheus page each vLLM server publishes at `/metrics`: generation and prompt-reading tokens per second with a ten-minute chart, requests running and waiting, KV cache use, tokens per step and acceptance of speculative decoding when the server has a drafter, median and 95th percentile of the time to the first token and of the time between tokens (also as tokens per second per stream), a warning when requests were preempted, and the tokens served so far with the base URL ready to copy. The page refreshes every 2 s while it is open. Each server card ends with **Clientes**: who is using it, read from the HTTP access log of its container (vLLM does not record who asks, but its web server logs every request): per client address the requests per API (OpenAI chat, completions, Responses, Anthropic Messages, embeddings, other), requests per hour, errors (non-2xx), when it was last seen and a tag when it only polls `/health`, `/v1/models` or `/metrics`. Names are editable in place (`client_names` setting, `client_name_set` tool); this PC is named automatically, the Sparks by their own addresses, and expanding **This PC** lists the programs here that hold connections to the server right now (Hoard apps by their module, Faustus, assistants, otherwise the executable). The server's own 127.0.0.1 probes are only counted.
- **Speed and capacity cards** (in each server card of Sirviendo, separate on purpose): **Velocidad** is a measurement you start with **Medir** (it asks first: it sends hundreds of streaming chat requests to the shared server for several minutes). It runs as a job at 1, 8, 16 and 64 concurrent streams with a unique prompt per request (a random nonce first, so the prefix cache never answers), one discarded warm-up round and the median of three rounds, and reports per level the aggregate output tokens per second, the median decode tokens per second of one stream, the time to the first token (p50 and p95) and the failed requests. Every generated token counts, reasoning included (the server's `usage` when it sends one, otherwise the stream chunks). Results are kept in `data/speedcards.json` (the last 20 per server). **Capacidad** is read live and costs two small requests: the context the server accepts (`/v1/models`), the KV pool in tokens (`vllm:cache_config_info`: blocks × block size, with how many full-context requests fit) and what the recipe recorded as verified. Tools: `speed_card` (read) and `speed_card_run` (starts the job; needs `confirm=true`; the caller should hold the principal-model lock; one run per server at a time; cancel with `job_cancel`).
- **GPU errors**: every 60 s (`xid_interval_s`; `xid_watch` switches it off in Ajustes) it reads the new kernel messages of each Spark (`journalctl -k --since ... -o short-iso` over SSH) and keeps the lines that report an NVIDIA `Xid` (with its code), a full-chip reset (`NV_ERR_GPU_IN_FULLCHIP_RESET`) or a GSP error, timeout or failure, in `data/xid_events.json` (the last 500). They are listed at the top of Sirviendo, flagged on the Spark card and returned by the `xid_events` tool, and a new one is announced through the family hub's notification channel (`xid_notify`; the hub decides how). It only warns: it never restarts or resets anything.
- **Archivos**: an explorer with every Spark as a drive. Address bar, back/forward/up, search, details and icon views, hidden files, preview (text with line numbers, JSON, images, video, audio) and in-place editing of text files, upload by drag and drop, download, new folder and file, rename (F2), cut/copy/paste (Ctrl+X/C/V), send to other Sparks over the CX7 cables, properties with folder size, and a trash per Spark with restore. Writes stay inside the home folder of the Spark user; the rest of the disk can be shown read-only.
- **Modelos**: what is loaded now with its OpenAI-compatible URL; recipes with a Load button (refused with the conflict when another model uses the same Sparks, with an option to unload it first); models on each disk with size, architecture, quantisation and context; downloads from Hugging Face; copies between Sparks over CX7 (rsync, resumable); jobs with progress and logs.
- **Power**: lock, sleep, shut down and restart one Spark or all of them; turning on uses wake-on-LAN with the wired MAC address learnt while the Spark was on. Before a shutdown the models of that Spark are unloaded.
- **Endpoints for other programs**: `GET /api/endpoints` lists the inference servers running now (the default recipe first). Faustus reads it to use the Sparks as its default backend. `GET /api/serving` returns the live figures of those servers (`{endpoints, totals, poll_s, now}`; `?recipe=` filters, `?series=false` leaves out the chart points) and the `serving_stats` tool gives the same to an assistant (each endpoint carries `clients` unless `?clients=false` / `clients: false`). `sparks_overview` carries a short `serving` summary per endpoint.

![Sirviendo](docs/screens/sirviendo.png)

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

The MCP bridge is `python mcp_server.py` (42 tools; `faustus-plugin.json` describes it for Faustus and the Hoard Hub). Destructive tools (permanent deletes, emptying the trash, deleting a model, power actions, shell commands) need `confirm=true`.

## What it cannot do

- The serving figures come only from vLLM's `/metrics`: a server of another engine is listed with the reason it has no figures. Sampling runs every 2 s while the page or an assistant is looking and every 10 s otherwise; about 15 minutes of samples are kept in memory, so the charts start empty after a restart of the app.
- Clients come from the container's access log, read on the head Spark with `docker logs --since` (`sudo -n docker` when the user is not in the docker group; with neither the card says so) every ~20 s while the page or an assistant looks and every 2 min otherwise; a summary of 24 h is kept in `data/clients.json`. One address is one device: programs on other computers are not told apart, a client behind a proxy shows the proxy, and the log only holds what the container has written since it started. Only on this PC are programs told apart, from the operating system's connection table (`psutil`): a program of another user may show without a name, and the list is a snapshot of what is connected now, not a history.
- The tokens-served totals (`data/serving.json`, saved every 30 s and on exit) only count what the app saw while it was running. The servers' counters restart with their container; a drop is detected and the earlier value is added to an offset, but what was served while the app was closed (and a server that restarted in that gap) is lost.
- The speed card measures the server as it is when asked: it competes with whatever else uses the model, a server that rejects `ignore_eos` may end some streams early, and a card of another engine's server works only if it speaks the OpenAI streaming chat API. The KV pool is the one vLLM reports for each worker; it is not a promise that a request of that length will be served. Nothing is saved from a cancelled run.
- The GPU error watcher only sees what the kernel journal of the Spark user can read (`journalctl -k` without sudo), polls instead of following the log, and notifies only when the Hoard Hub answers; without a hub the event is still kept and shown. Spark clocks decide the cursor of each scan.
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
