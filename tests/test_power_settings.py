import time


def test_power_needs_confirm(call):
    assert call("power", {"node": "spark2", "action": "shutdown"}, status=400)["code"] == "confirm_required"


def test_shutdown_unloads_models_first(call, services, world):
    call("deploy_start", {"recipe": "qwen38-27b-1m", "wait": True})
    out = call("power", {"node": "spark3", "action": "shutdown", "confirm": True})
    assert out["results"][0] == {"unloaded": "qwen38-27b-1m"}
    assert world.power["spark3"] == "off"
    services.cluster.probe_all()
    node = next(n for n in call("sparks_overview")["nodes"] if n["id"] == "spark3")
    assert not node["online"] and node["power_state"] in ("off", "shutting_down")


def test_wake_uses_remembered_mac(call, services, world):
    services.power.remember()
    world.power["spark1"] = "off"
    services.cluster.probe_all()
    out = call("power", {"node": "spark1", "action": "wake"})
    assert world.wol == ["30:c5:99:00:00:00"]
    assert out["results"][0]["ok"]


def test_history_tool(call):
    assert "samples" in call("spark_history", {"node": "spark1", "minutes": 5})


def test_lock_and_all(call, world):
    call("power", {"node": "all", "action": "lock"})
    assert sum(1 for _, c in world.commands if c.startswith("loginctl")) == 3


def test_exec_needs_confirm(call):
    assert call("spark_exec", {"node": "spark1", "command": "uname -a"}, status=400)["code"] == "confirm_required"
    assert call("spark_exec", {"node": "spark1", "command": "uname -a", "confirm": True})["rc"] == 0


def test_settings_roundtrip(call, services):
    s = call("settings_get")
    assert [n["id"] for n in s["nodes"]] == ["spark1", "spark2", "spark3"] and s["hf_token"] is False
    nodes = s["nodes"] + [{"id": "spark4", "name": "Spark4", "ssh": "Spark4"}]
    call("settings_set", {"nodes": nodes, "poll_s": 0.1})
    assert "spark4" in services.cluster.nodes and services.settings["poll_s"] == 1.0
    assert call("settings_set", {"nodes": nodes + [nodes[0]]}, status=400)["code"] == "invalid"
    call("hf_token_set", {"hf_token": "hf_secret"})
    assert call("settings_get")["hf_token"] is True
    assert "hf_secret" not in str(call("settings_get"))


def test_agent_route_needs_token(client, services):
    r = client.post("/api/agent/call", json={"name": "sparks_overview", "arguments": {}})
    assert r.status_code == 401
    r = client.post("/api/agent/call", json={"name": "sparks_overview", "arguments": {}}, headers={"Authorization": f"Bearer {services.token}"})
    assert r.status_code == 200
    tools = client.get("/api/agent/tools").json()["tools"]
    assert all(len(t["description"].split("\n")[0]) <= 110 for t in tools)


def test_every_tool_is_tested():
    import pathlib
    from prometheus_hoard.agent_tools import TOOLS

    text = "".join(p.read_text(encoding="utf-8") for p in pathlib.Path(__file__).parent.glob("test_*.py"))
    missing = [t.name for t in TOOLS if f'"{t.name}"' not in text]
    assert not missing, missing
