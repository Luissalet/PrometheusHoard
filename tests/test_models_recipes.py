import time

import pytest


def test_inventory(call):
    inv = call("models_list", {})["nodes"]
    assert [m["name"] for m in inv["spark1"]] == ["glm-5.3-flash-nvfp4"]
    m = inv["spark1"][0]
    assert m["max_context"] == 1048576 and m["quant"] == "modelopt" and m["repo"] == "demo/glm-5.3-flash-nvfp4"


def test_download_job(call, services):
    job = call("model_download", {"node": "spark2", "repo": "https://huggingface.co/demo/tiny-model"})
    assert job["kind"] == "download" and job["meta"]["target"] == "~/models/tiny-model"
    assert call("model_download", {"node": "spark2", "repo": "demo/tiny-model"}, status=409)["code"] == "busy"
    time.sleep(0.4)
    done = call("job_get", {"job": job["id"]})
    assert done["state"] == "done" and done["progress"] == 1.0
    names = [m["name"] for m in call("models_list", {"node": "spark2", "refresh": True})["nodes"]["spark2"]]
    assert "tiny-model" in names


def test_bad_repo(call):
    assert call("model_download", {"node": "spark2", "repo": "no slash"}, status=400)["code"] == "invalid"


def test_copy_uses_the_fabric(call, world):
    jobs = call("model_copy", {"node": "spark1", "model": "glm-5.3-flash-nvfp4", "to": ["spark2", "spark3"]})["jobs"]
    assert [j["meta"]["via"] for j in jobs] == ["cx7", "cx7"]
    cmds = [c for n, c in world.commands if "rsync" in c] or [j for j in jobs]
    assert jobs[0]["meta"]["host"].startswith("10.100.")


def test_model_delete_confirm(call):
    assert call("model_delete", {"node": "spark3", "model": "qwen3.8-27b-nvfp4"}, status=400)["code"] == "confirm_required"
    call("model_delete", {"node": "spark3", "model": "qwen3.8-27b-nvfp4", "confirm": True})
    assert call("models_list", {"node": "spark3", "refresh": True})["nodes"]["spark3"] == []


def test_recipes_and_deploy_cycle(call):
    names = [r["name"] for r in call("recipes_list")["recipes"]]
    assert names == ["glm53-tp3", "qwen38-27b-1m"]
    d = call("deploy_start", {"recipe": "qwen38-27b-1m", "wait": True})
    assert d["state"] == "running" and d["served"] == ["qwen3.8-27b"]
    eps = call("endpoints")["endpoints"]
    assert eps[0]["base_url"].endswith(":8001/v1") and eps[0]["max_model_len"] == 1048576
    conflict = call("deploy_start", {"recipe": "glm53-tp3"}, status=409)
    assert conflict["conflicts"] == ["qwen38-27b-1m"]
    d = call("deploy_start", {"recipe": "glm53-tp3", "stop_conflicts": True, "wait": True})
    assert d["state"] == "running"
    states = {x["recipe"]: x["state"] for x in call("deployments")["deployments"]}
    assert states == {"glm53-tp3": "running", "qwen38-27b-1m": "stopped"}
    ov = call("sparks_overview")
    assert ov["nodes"][2]["deployments"][0]["role"] == "worker"
    call("deploy_stop", {"recipe": "glm53-tp3"})
    assert call("endpoints")["endpoints"] == []


def test_recipe_env_and_order(call, world):
    call("deploy_start", {"recipe": "glm53-tp3", "wait": True})
    starts = sorted((j["start"], j["node"]) for j in world.jobs.values() if "start.sh" in j["cmd"])
    assert [n for _, n in starts] == ["spark1", "spark2", "spark3"]  # head first
    job = next(j for j in world.jobs.values() if "start.sh" in j["cmd"] and j["node"] == "spark2")
    env = job["env"]
    assert env["PROM_ROLE"] == "worker" and env["PROM_HEAD"] == "spark1" and env["PROM_HEAD_IP"] == "10.100.36.1"
    assert env["SPARK_NODE"] == "spark2"  # the recipe's own reference, passed through
    call("deploy_stop", {"recipe": "glm53-tp3"})


def test_recipe_write_and_foreign_schema(call, services):
    definition = {"nodes": ["Spark1", "Spark2"], "head_node": "Spark1", "port": 8010, "served_model_name": "x", "start_timeout": 30,
                  "endpoint": "http://192.168.0.236:8010/v1", "scripts": {"start": "start.sh", "stop": "stop.sh", "health": "health.sh"}}
    r = call("recipe_write", {"recipe": "foreign", "definition": definition, "scripts": {"start.sh": "echo a\r\n", "stop.sh": "echo b", "health.sh": "echo {}"}})
    assert r["nodes"] == ["spark1", "spark2"] and r["head"] == "spark1" and r["ready_timeout_s"] == 30
    assert r["scripts"]["health"] == "health.sh"
    dep = next(d for d in call("deployments")["deployments"] if d["recipe"] == "foreign")
    assert dep["base_url"] == "http://192.168.0.236:8010/v1"
    assert (services.recipes.folder / "foreign" / "start.sh").read_bytes() == b"echo a\n"
    assert call("recipe_write", {"recipe": "foreign", "definition": definition, "scripts": {"start.sh": "", "stop.sh": ""}}, status=409)


def test_start_refuses_offline_node(call, world, services):
    world.power["spark3"] = "off"
    services.cluster.probe_all()
    assert call("deploy_start", {"recipe": "qwen38-27b-1m"}, status=503)["code"] == "unreachable"


def test_logs_and_jobs(call):
    call("deploy_start", {"recipe": "qwen38-27b-1m", "wait": True})
    logs = call("deploy_logs", {"recipe": "qwen38-27b-1m"})
    assert logs["node"] == "spark3" and logs["jobs"][0]["state"] == "done"
    jobs = call("jobs_list", {"state": "done"})["jobs"]
    assert any(j["kind"] == "recipe" for j in jobs)
    call("deploy_stop", {"recipe": "qwen38-27b-1m"})


def test_job_cancel(call, world):
    world.job_s = 30
    job = call("model_download", {"node": "spark1", "repo": "demo/slow"})
    out = call("job_cancel", {"job": job["id"]})
    assert out["state"] == "cancelled"


def test_files_transfer(call):
    jobs = call("files_transfer", {"src_node": "spark1", "paths": ["~/Documentos"], "dst_nodes": ["spark3"]})["jobs"]
    assert jobs[0]["kind"] == "copy" and jobs[0]["meta"]["via"] == "cx7"


def test_servers_started_outside_a_recipe_are_offered(call, services, world, monkeypatch):
    node = services.cluster.node("spark2")
    node.metrics["servers"] = [{"engine": "vllm", "model": "/model", "port": 8003, "served_name": "manual", "max_len": 4096, "tp": None, "pid": 7}]
    monkeypatch.setattr(services.recipes, "http", lambda url, timeout=2.0: (200, {"data": [{"id": "manual"}]}) if ":8003/" in url else (0, "refused"))
    eps = call("endpoints")["endpoints"]
    assert eps == [{"recipe": "spark2-8003", "title": "manual (Spark2:8003)", "base_url": "http://spark-spark2.local:8003/v1", "models": ["manual"],
                    "max_model_len": 4096, "nodes": ["spark2"], "head": "spark2", "engine": "vllm", "default": False, "detected": True}]
    ov = call("sparks_overview")
    assert ov["detected"][0]["up"] and ov["nodes"][1]["deployments"][0]["detected"]


def test_a_shared_port_is_not_taken_for_the_wrong_recipe(call, services, monkeypatch):
    # a server answers on the qwen recipe's port but serves another model or another context
    monkeypatch.setattr(services.recipes, "http", lambda url, timeout=2.0: (200, {"data": [{"id": "otro", "max_model_len": 4096}]}) if ":8001/" in url else (0, "x"))
    dep = next(d for d in call("deployments")["deployments"] if d["recipe"] == "qwen38-27b-1m")
    assert dep["state"] == "stopped" and "another server" in dep["health"]["error"]
    monkeypatch.setattr(services.recipes, "http", lambda url, timeout=2.0: (200, {"data": [{"id": "qwen3.8-27b", "max_model_len": 262144}]}) if ":8001/" in url else (0, "x"))
    dep = next(d for d in call("deployments")["deployments"] if d["recipe"] == "qwen38-27b-1m")
    assert dep["state"] == "stopped" and "context" in dep["health"]["error"]


def test_a_failed_stop_is_reported(call, services, world):
    call("deploy_start", {"recipe": "qwen38-27b-1m", "wait": True})
    folder = services.recipes.folder / "qwen38-27b-1m"
    (folder / "stop.sh").write_text("exit 3\n")
    out = call("deploy_stop", {"recipe": "qwen38-27b-1m"})
    assert out["state"] == "failed" and "stop.sh" in out["message"]


def test_health_script_gates_readiness(call, services, world, monkeypatch):
    folder = services.recipes.folder / "qwen38-27b-1m"
    (folder / "health.sh").write_text("echo '{\"ready\": false, \"state\": \"starting\"}'\n")
    r = services.recipes.load("qwen38-27b-1m")
    assert r["scripts"]["health"] == "health.sh"
    world.serving["qwen38-27b-1m"] = {"spark3"}
    monkeypatch.setattr(services.cluster.node("spark3").transport, "run",
                        lambda cmd, timeout=30.0, stdin=None: __import__("prometheus_hoard.transport", fromlist=["RunResult"]).RunResult(0, '{"ready": false, "state": "starting"}\n', ""))
    res = services.recipes.ready(r)
    assert res["up"] is False and "not ready" in res["error"]


def _with_health_script(call, services, name="hs"):
    definition = {"nodes": ["spark1"], "head": "spark1", "port": 8020, "served_model_name": "hs-model",
                  "scripts": {"start": "start.sh", "stop": "stop.sh", "health": "health.sh"}}
    call("recipe_write", {"recipe": name, "definition": definition, "scripts": {"start.sh": "echo a", "stop.sh": "echo b", "health.sh": "echo {}"}})
    return services.recipes.load(name)


@pytest.mark.parametrize("rc,out,up", [
    (1, '{"ready": true}', False),                 # non-zero exit is never ready
    (0, '{"ok": false, "ready": true}', False),    # ok:false wins
    (0, '{"ready": false, "state": "loading"}', False),
    (0, "no json at all", False),
    (0, '{"ok": true, "ready": true}', True),
])
def test_health_script_is_fail_closed(call, services, monkeypatch, rc, out, up):
    from prometheus_hoard.transport import RunResult

    r = _with_health_script(call, services)
    monkeypatch.setattr(services.recipes, "http", lambda url, timeout=2.0: (200, {"data": [{"id": "hs-model"}]}) if ":8020/" in url else (0, "x"))
    node = services.cluster.node("spark1")
    orig = node.transport.run
    monkeypatch.setattr(node.transport, "run", lambda cmd, **kw: RunResult(rc, out, "") if "health.sh" in cmd else orig(cmd, **kw))
    assert services.recipes.ready(r)["up"] is up
    services.recipes._owned_cache.clear()
    dep = next(d for d in call("deployments")["deployments"] if d["recipe"] == "hs")
    assert (dep["state"] == "running") is up


def test_an_empty_model_list_is_not_the_recipe(call, services, monkeypatch):
    monkeypatch.setattr(services.recipes, "http", lambda url, timeout=2.0: (200, {"data": []}) if ":8001/" in url else (0, "x"))
    dep = next(d for d in call("deployments")["deployments"] if d["recipe"] == "qwen38-27b-1m")
    assert dep["state"] == "stopped" and "lists no model" in dep["health"]["error"]


@pytest.mark.parametrize("state", ["failed", "stopping", "starting"])
def test_unsettled_recipes_keep_their_sparks(call, services, state):
    services.recipes._set("qwen38-27b-1m", state=state, nodes=["spark3"])
    assert "qwen38-27b-1m" in services.recipes.busy_nodes().get("spark3", [])
    out = call("deploy_start", {"recipe": "glm53-tp3"}, status=409)
    assert out["conflicts"] == ["qwen38-27b-1m"]


def test_a_server_outside_any_recipe_blocks_the_start(call, services, monkeypatch):
    node = services.cluster.node("spark2")
    node.metrics["servers"] = [{"engine": "vllm", "model": "/m", "port": 8003, "served_name": "manual", "max_len": 4096, "tp": None, "pid": 7}]
    monkeypatch.setattr(services.recipes, "http", lambda url, timeout=2.0: (200, {"data": [{"id": "manual"}]}) if ":8003/" in url else (0, "refused"))
    out = call("deploy_start", {"recipe": "glm53-tp3", "stop_conflicts": True}, status=409)
    assert out["conflicts"] == ["spark2-8003"] and "outside any recipe" in out["error"]


def test_a_second_stop_is_refused_while_the_first_runs(call, services, world):
    call("deploy_start", {"recipe": "qwen38-27b-1m", "wait": True})
    world.job_s = 2.0
    services.recipes.stop("qwen38-27b-1m", wait=False)
    out = call("deploy_stop", {"recipe": "qwen38-27b-1m"}, status=409)
    assert out["code"] == "busy"
    for _ in range(100):
        if services.recipes.state["qwen38-27b-1m"]["state"] == "stopped":
            break
        time.sleep(0.1)
    assert services.recipes.state["qwen38-27b-1m"]["state"] == "stopped"
    assert "qwen38-27b-1m" not in services.recipes._stopping
