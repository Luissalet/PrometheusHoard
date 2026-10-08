# Architecture

```
browser / MCP bridge / Faustus
        │  HTTP (127.0.0.1:5205)
        ▼
  main.py ─ api/ui.py (overview, endpoints, files bytes, /api/ui/call) ─ api/agent.py (Bearer, MCP)
        │
  agent_tools.py ── one catalogue of tools
        │
  services.py ── Settings · Cluster · Files · Models · Jobs · Recipes · Power
        │
  transport.py ── one paramiko SSH connection per Spark (commands + SFTP), ~/.ssh/config with Include, optional jump host
        │
  remote/*.py ── helpers run on the Spark with python3: probe, models, jobs, fsops
```

- **cluster.py**: nodes from the settings, a poller (every `poll_s` while someone looks, `idle_poll_s` otherwise), rates computed between two probes (CPU from `/proc/stat`, network from `/proc/net/dev`), a short history per Spark, and `fabric_peer_ip(a, b)`: the address of `b` on a CX7 subnet that `a` also has.
- **files.py**: SFTP explorer, home-only writes, trash with metadata (`<trash>/<id>/.prometheus-trash.json`).
- **jobs.py**: detached jobs on a Spark (`<remote_dir>/jobs/<id>/{cmd,pid,log,rc,watch}`); progress = size of the watched folder / expected bytes.
- **models.py**: inventory (folders with weights in the models folders and the Hugging Face cache), `hf download` jobs, `rsync` copies over CX7.
- **recipes.py**: recipe folders → deployments. Start: conflicts check (Sparks used by another running recipe, free memory), upload, `start.sh` per Spark in order as jobs, wait for the head's health URL. State in `data/state.json`; a recipe that answers without having been started here is shown as running (external).
- **power.py**: lock (`loginctl`), sleep/shutdown/restart (`sudo -n systemctl …`, polkit fallback), wake-on-LAN from the remembered MAC (`data/macs.json`).
- **fake.py**: the invented cluster for the demo and the tests.

Data folder (`data/`): `settings.json`, `state.json`, `jobs.json`, `macs.json`, `secrets.json` (Hugging Face token), `mcp-token`, `logs/`.

Recipe measurements pass unchanged through `recipes_list` (UI and MCP). `client/src/measurements.js` formats units and nested fields in Spanish and English; `RecipeMeasurements.jsx` presents a short summary and expandable full observations. This is presentation of recorded evidence, not a runtime context or accuracy certification.
