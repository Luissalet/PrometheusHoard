import json
import posixpath
import shlex
from types import SimpleNamespace

import pytest

from prometheus_hoard.errors import SparkError
from prometheus_hoard.models import HF_BIN, Models


@pytest.fixture
def download_setup():
    calls = []
    active = []
    sizes = []
    nodes = {key: SimpleNamespace(id=key, conf={"name": key}) for key in ("spark1", "spark2")}

    def start(node, cmd, **kwargs):
        job = {"id": f"job-{len(calls)}", "node": node, "cmd": cmd, **kwargs}
        calls.append(job)
        return job

    def size(repo, revision):
        sizes.append((repo, revision))
        return 12345

    jobs = SimpleNamespace(active=lambda kind: active, start=start)
    cluster = SimpleNamespace(node=nodes.__getitem__, settings={"models_dirs": ["~/models"]})
    models = Models(cluster, jobs, hf_size=size)
    return models, calls, active, sizes


def shell_tokens(command):
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    return list(lexer)


def download_argv(job):
    tokens = shell_tokens(job["cmd"])
    start = next(i for i, token in enumerate(tokens[:-1]) if token == HF_BIN and tokens[i + 1] == "download")
    end = tokens.index("&&", start)
    return tokens[start:end]


def test_download_uses_one_file_worker(download_setup):
    models, calls, _, _ = download_setup
    job = models.download("spark1", "demo/test-model")
    assert job is calls[0]
    assert download_argv(job) == [HF_BIN, "download", "demo/test-model", "--local-dir", "~/models/test-model",
                                  "--max-workers", "1"]


@pytest.mark.parametrize("token", ["", "synthetic-token-with 'quotes' $and; metacharacters"])
def test_download_disables_xet_in_the_job_environment(download_setup, token):
    models, _, _, _ = download_setup
    models.secrets = lambda: {"HF_TOKEN": token}
    job = models.download("spark1", "demo/test-model")
    expected = {"HF_HUB_DISABLE_XET": "1"}
    if token:
        expected["HF_TOKEN"] = token
    assert job["env"] == expected
    if token:
        assert token not in job["cmd"]


def test_options_and_shell_quoting_are_preserved(download_setup):
    models, calls, _, sizes = download_setup
    folder = "/home/demo/Model's data;$(false)"
    name = "model copy"
    revision = "refs/test's branch;$(false)"
    patterns = ["*.safetensors", "configs/it's a file;$(false).json"]
    target = posixpath.join(folder, name)
    job = models.download("spark2", " https://huggingface.co/demo/Test-Model/ ", name=name,
                          revision=revision, include=patterns, folder=folder)
    assert job is calls[0]
    assert download_argv(job) == [HF_BIN, "download", "demo/Test-Model", "--local-dir", target,
                                  "--max-workers", "1", "--revision", revision,
                                  "--include", patterns[0], "--include", patterns[1]]
    tokens = shell_tokens(job["cmd"])
    marker = tokens.index("printf")
    assert json.loads(tokens[marker + 2]) == {"repo": "demo/Test-Model", "revision": revision}
    assert tokens[marker + 4] == target + "/.prometheus.json"
    assert job["node"] == "spark2" and job["kind"] == "download"
    assert job["watch"] == target and job["expected_bytes"] == 12345
    assert job["meta"] == {"repo": "demo/Test-Model", "target": target, "revision": revision}
    assert sizes == [("demo/Test-Model", revision)]


def test_duplicate_active_target_still_returns_the_existing_job_error(download_setup):
    models, calls, active, sizes = download_setup
    active.append({"id": "existing-job", "node": "spark1", "watch": "~/models/test-model"})
    with pytest.raises(SparkError) as error:
        models.download("spark1", "demo/test-model")
    assert error.value.code == "busy" and error.value.details["job"] == "existing-job"
    assert calls == [] and sizes == []


def test_other_targets_and_nodes_remain_available(download_setup):
    models, calls, active, _ = download_setup
    active.append({"id": "existing-job", "node": "spark1", "watch": "~/models/test-model"})
    models.download("spark1", "demo/other-model")
    models.download("spark2", "demo/test-model")
    assert len(calls) == 2


def test_retry_preserves_the_destination_and_normal_cache_resume(download_setup):
    models, calls, _, sizes = download_setup
    first = models.download("spark1", "demo/test-model", revision="pinned-revision")
    # A completed/cancelled job is absent from Jobs.active; retry uses the same
    # local directory and normal HF cache semantics, without forcing a redownload.
    second = models.download("spark1", "demo/test-model", revision="pinned-revision")
    assert second is calls[1] and first["id"] != second["id"]
    assert first["cmd"] == second["cmd"] and first["watch"] == second["watch"]
    assert "--force-download" not in download_argv(second)
    assert sizes == [("demo/test-model", "pinned-revision")] * 2


def test_unknown_progress_size_still_starts_the_download(download_setup):
    models, calls, _, _ = download_setup

    def unavailable(*args):
        raise OSError("synthetic metadata failure")

    models.hf_size = unavailable
    job = models.download("spark1", "demo/test-model")
    assert job is calls[0] and job["expected_bytes"] is None
