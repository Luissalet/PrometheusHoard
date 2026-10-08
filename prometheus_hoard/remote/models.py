# prometheus:models
"""Models on the disks of a Spark: every folder under the models folders (and the Hugging Face cache) that holds weights.
For each: size, files, architecture, quantisation, context length, and the repository it came from when a download left a trace."""
import json, os, sys

ARGS = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
WEIGHTS = (".safetensors", ".gguf", ".bin", ".pt", ".pth", ".exl2", ".exl3")


def size_of(path):
    total, files, weights = 0, 0, 0
    for root, dirs, names in os.walk(path):
        dirs[:] = [d for d in dirs if d not in (".git",)]
        for n in names:
            p = os.path.join(root, n)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if os.path.islink(p):
                try:
                    st = os.stat(p)
                except OSError:
                    continue
            total += st.st_size
            files += 1
            if n.endswith(WEIGHTS):
                weights += 1
    return total, files, weights


def read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def describe(path, source):
    cfg = read_json(os.path.join(path, "config.json")) or {}
    text = cfg.get("text_config") if isinstance(cfg.get("text_config"), dict) else {}
    quant = cfg.get("quantization_config") or read_json(os.path.join(path, "hf_quant_config.json")) or {}
    qmethod = ""
    if isinstance(quant, dict):
        qmethod = quant.get("quant_method") or quant.get("quant_algo") or (quant.get("quantization") or {}).get("quant_algo", "") or ""
        if not qmethod and isinstance(quant.get("config_groups"), dict):
            qmethod = "compressed-tensors"
    total, files, weights = size_of(path)
    gguf = [n for n in os.listdir(path) if n.endswith(".gguf")] if os.path.isdir(path) else []
    repo = ""
    meta = os.path.join(path, ".cache", "huggingface")
    if os.path.isdir(meta):
        marker = read_json(os.path.join(path, ".prometheus.json")) or {}
        repo = marker.get("repo", "")
    marker = read_json(os.path.join(path, ".prometheus.json")) or {}
    repo = repo or marker.get("repo", "")
    incomplete = False
    if os.path.isdir(os.path.join(meta, "download")):
        for root, _d, names in os.walk(os.path.join(meta, "download")):
            if any(n.endswith(".incomplete") or n.endswith(".lock") for n in names):
                incomplete = True
                break
    return {
        "path": path, "name": os.path.basename(path.rstrip("/")), "source": source, "bytes": total, "files": files, "weight_files": weights,
        "repo": repo, "architectures": cfg.get("architectures") or [], "model_type": cfg.get("model_type") or text.get("model_type") or "",
        "quant": str(qmethod), "max_context": cfg.get("max_position_embeddings") or text.get("max_position_embeddings"),
        "gguf": gguf[:20], "incomplete": incomplete, "mtime": os.path.getmtime(path),
    }


found = []
for base in ARGS.get("dirs", ["~/models"]):
    base = os.path.expanduser(base)
    if not os.path.isdir(base):
        continue
    for name in sorted(os.listdir(base)):
        p = os.path.join(base, name)
        if not os.path.isdir(p) or name.startswith("."):
            continue
        try:
            listing = os.listdir(p)
        except OSError:
            continue
        if "config.json" in listing or any(n.endswith(WEIGHTS) for n in listing) or os.path.isdir(os.path.join(p, ".cache", "huggingface")):
            found.append(describe(p, "folder"))
if ARGS.get("hf_cache", True):
    hub = os.path.expanduser("~/.cache/huggingface/hub")
    if os.path.isdir(hub):
        for name in sorted(os.listdir(hub)):
            if not name.startswith("models--"):
                continue
            snaps = os.path.join(hub, name, "snapshots")
            if not os.path.isdir(snaps):
                continue
            for rev in os.listdir(snaps):
                d = describe(os.path.join(snaps, rev), "hf-cache")
                d["repo"] = name[len("models--"):].replace("--", "/")
                d["name"] = d["repo"]
                found.append(d)
print(json.dumps({"models": found}))
