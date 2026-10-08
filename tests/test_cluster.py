from prometheus_hoard.cluster import cpu_percent, parse_server


def test_cpu_percent():
    assert cpu_percent([0, 0, 0, 100, 0, 0, 0, 0], [50, 0, 0, 150, 0, 0, 0, 0]) == 50.0
    assert cpu_percent([], [1]) is None


def test_parse_server_vllm():
    info = parse_server("python3 -m vllm.entrypoints.openai.api_server --model /m/glm --served-model-name glm --port 8002 --max-model-len 1048576 --tensor-parallel-size 3")
    assert info == {"engine": "vllm", "model": "/m/glm", "port": 8002, "served_name": "glm", "max_len": 1048576, "tp": 3}
    assert parse_server("vllm serve /m/q --port=8000")["model"] == "/m/q"


def test_overview(client):
    o = client.get("/api/overview").json()
    assert [n["id"] for n in o["nodes"]] == ["spark1", "spark2", "spark3"]
    assert all(n["online"] for n in o["nodes"])
    assert o["nodes"][0]["cpu"]["percent"] is not None
    assert o["cluster"]["online"] == 3


def test_fabric_pairs(call):
    pairs = call("fabric_status")["pairs"]
    assert {(p["a"], p["b"]) for p in pairs} == {("spark1", "spark2"), ("spark1", "spark3"), ("spark2", "spark3")}
    assert all(p["ip_b_from_a"] for p in pairs)


def test_history(client, services):
    services.cluster.probe_all()
    h = client.get("/api/nodes/spark1/history?minutes=5").json()
    assert h["samples"] and "gpu" in h["samples"][0]


def test_unknown_node(call):
    assert call("spark_status", {"node": "spark9"}, status=404)["code"] == "not_found"


def test_node_by_name(call):
    assert call("spark_status", {"node": "Spark2"})["id"] == "spark2"
