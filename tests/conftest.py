import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from prometheus_hoard.config import Config  # noqa: E402
from prometheus_hoard.fake import FakeWorld  # noqa: E402
from prometheus_hoard.main import create_app  # noqa: E402
from prometheus_hoard.services import Services  # noqa: E402


@pytest.fixture(autouse=True)
def hermetic(monkeypatch, tmp_path):
    for key in ("PROMETHEUS_DATA_DIR", "PROMETHEUS_FAKE", "PROMETHEUS_RECIPES_DIR", "HOARD_HUB_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PROMETHEUS_SSH_DIR", str(tmp_path / "ssh"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


@pytest.fixture
def world(tmp_path):
    return FakeWorld(tmp_path / "cluster", job_s=0.3)


@pytest.fixture
def services(tmp_path, world):
    cfg = Config(data_dir=tmp_path / "data", fake=True, background=False)
    s = Services(cfg, world=world)
    s.cluster.probe_all()
    time.sleep(0.05)
    s.cluster.probe_all()
    yield s
    s.stop()


@pytest.fixture
def client(services):
    app = create_app(services.config, services=services)
    with TestClient(app, base_url="http://127.0.0.1:5205") as c:
        yield c


@pytest.fixture
def call(client):
    def _call(name, args=None, status=200):
        r = client.post("/api/ui/call", json={"name": name, "arguments": args or {}})
        assert r.status_code == status, r.text
        return r.json()
    return _call
