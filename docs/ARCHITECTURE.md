# Architecture

```
browser / MCP bridge / Faustus
        │  HTTP (127.0.0.1:5205)
        ▼
  main.py ─ api/ui.py (overview, endpoints, files bytes, /api/ui/call) ─ api/agent.py (Bearer, MCP)
        │
  agent_tools.py ── one catalogue of tools
        │
  services.py ── Settings · Cluster · Files · Models · Jobs · Recipes · Power · Serving
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
- **serving.py**: live inference figures. A parser of the Prometheus text page (only `vllm:` lines, quoted labels with commas and escapes, `+Inf`, NaN; `*_created` ignored) and the `Serving` sampler, owned by `Services`. A thread refreshes the endpoint list of `Recipes.endpoints()` at most every 15 s and scrapes `<server>/metrics` (the `/v1` of the base URL dropped; 2 s timeout, an injectable text getter, `world.http_text` in the demo) every 2 s while the page or the `serving_stats` tool asked in the last 60 s (`touch`) and every 10 s otherwise; without the thread (`background=False`, the tests) the view samples on demand. Per endpoint it keeps 15 minutes of samples (counters, gauges and histogram buckets summed across label sets) and derives, over the last ~10 s, decode tokens/s (Δgeneration), prefill tokens/s (Δ(prompt − cached)), running, waiting, KV %, tokens per step (`1 + Δaccepted/Δdrafts`) and acceptance (`Δaccepted/Δdraft tokens`), both falling back to the whole life of the server and flagged `spec_cumulative`; over the last 5 min, p50/p95 of each latency histogram with Prometheus' linear interpolation (whole life when nothing moved); preemptions in the window and in 5 min; and the sparkline series (last 10 min, at most 120 points). An endpoint whose `/metrics` fails is reported with `ok=false` and the error and does not affect the others; its figures go blank after 10 s without a good scrape. `GET /api/serving` and the `serving_stats` tool return `{endpoints, totals, poll_s, now}`; `sparks_overview` reads only the sampler's cache.
- **fake.py**: the invented cluster for the demo and the tests.

Data folder (`data/`): `settings.json`, `state.json`, `jobs.json`, `macs.json`, `secrets.json` (Hugging Face token), `serving.json`, `mcp-token`, `logs/`.

`serving.json` holds the cumulative tokens served per endpoint (prompt, generated, cached, requests), saved at most every 30 s and on exit with `write_json_atomic`: `offset` + `last` seen per counter, so a counter that drops (the container restarted) adds the last value seen to the offset and the totals keep growing; plus the `since` stamp and an overall total over every endpoint ever seen. Limits: counters live as long as the server's container, the totals only count what Prometheus' page showed while this app ran, and the in-memory samples (hence the charts) are lost on restart.

Recipe measurements pass unchanged through `recipes_list` (UI and MCP). `client/src/measurements.js` formats units and nested fields in Spanish and English; `RecipeMeasurements.jsx` presents a short summary and expandable full observations. This is presentation of recorded evidence, not a runtime context or accuracy certification.
