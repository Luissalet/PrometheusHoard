# Working on Prometheus's Hoard

Runs a cluster of DGX Spark computers from Windows (package `prometheus_hoard`, service `prometheus-hoard`, app id `prometheus`, port 5205). Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) first.

## Commands

```bash
python -m pytest                     # the whole suite, against the invented cluster (no SSH, no network)
npx vite build                       # rebuild the UI into prometheus_hoard/static
python -m prometheus_hoard           # http://127.0.0.1:5205 (PROMETHEUS_DATA_DIR=<folder> for a scratch copy)
PROMETHEUS_FAKE=1 python -m prometheus_hoard   # demo cluster in data/demo-cluster
```

## Rules of the code

- The UI, the REST agent route and MCP call the same handlers in `agent_tools.py`. A new tool needs a pydantic argument model, a first description line of at most 110 characters (English, then Spanish keywords), synonyms, annotations and a test (`test_every_tool_is_tested` fails otherwise).
- Nothing about the cluster is fixed in code: the Sparks, their addresses, the folders and the recipes are settings or files. Never hardcode a host, a model or a port.
- Remote work goes through `remote/*.py` helpers (Python sent on stdin, one JSON line back) or detached jobs (`jobs.py`); never block a request on a long remote command.
- Writes stay inside the Spark user's home (`files.writable`). Deletes go to the trash unless `permanent` + `confirm`.
- Destructive tools need `confirm`. Power actions unload the Spark's deployments first.
- `hoard_link/` is vendored and byte-identical to upstream HoardLink: never edit it here.
- User-facing text in `client/src/i18n.js` (castellano de España first, English second). No taglines, no names of other products.
- Tests use `fake.py` (files really written under a temp folder, the rest simulated). Never add real names, paths or personal data.

## Before finishing a change

1. `python -m pytest` is green.
2. If the UI changed: `npx vite build`, then open the running app (demo mode is enough) and look at every page in Spanish and English, at desktop and phone width.
3. README.md, README.es.md and docs/ARCHITECTURE.md say what the app can and cannot do now.
