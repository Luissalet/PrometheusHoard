"""Inventory reads actual declared shards without treating persistent HF locks as work."""
import json
from pathlib import Path
import runpy
import sys
import pytest

SCRIPT = Path(__file__).parents[1] / "prometheus_hoard/remote/models.py"

@pytest.fixture
def inventory(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), json.dumps({"dirs": [], "hf_cache": False})])
    code = runpy.run_path(str(SCRIPT))
    capsys.readouterr()
    folder = tmp_path / "model"
    folder.mkdir()
    (folder / "config.json").write_text("{}")
    def write(name, content="weight"):
        dest = folder / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)
    def describe():
        return code["describe"](str(folder), "folder")
    return folder, write, describe

def test_persistent_lock_does_not_mark_loaded_single_file_incomplete(inventory):
    _, write, describe = inventory
    write("model.safetensors")
    write(".cache/huggingface/download/model.safetensors.lock", "")
    assert describe()["incomplete"] is False

def test_indexed_weights_ignore_stale_auxiliary_partial_but_keep_it_on_disk(inventory):
    folder, write, describe = inventory
    write("weights.safetensors")
    write("model.safetensors.index.json", json.dumps({"weight_map": {"layer": "weights.safetensors"}}))
    write(".cache/huggingface/download/auxiliary.hash.incomplete", "buffer")
    assert describe()["incomplete"] is False
    assert (folder / ".cache/huggingface/download/auxiliary.hash.incomplete").read_text() == "buffer"

@pytest.mark.parametrize("shard", ["missing.safetensors", "empty.safetensors", "../outside.safetensors"])
def test_declared_missing_empty_or_invalid_shard_is_incomplete(inventory, shard):
    _, write, describe = inventory
    write("empty.safetensors", "")
    write("model.safetensors.index.json", json.dumps({"weight_map": {"layer": shard}}))
    assert describe()["incomplete"] is True

@pytest.mark.parametrize("index", ["broken", "{}", '{"weight_map":{}}'])
def test_invalid_index_never_certifies_other_weights(inventory, index):
    _, write, describe = inventory
    write("model.safetensors")
    write("model.safetensors.index.json", index)
    assert describe()["incomplete"] is True

@pytest.mark.parametrize("shard", [[], {}, None, "", "."])
def test_invalid_shard_value_or_directory_is_incomplete_without_crashing(inventory, shard):
    _, write, describe = inventory
    write("model.safetensors.index.json", json.dumps({"weight_map": {"layer": shard}}))
    assert describe()["incomplete"] is True

def test_unindexed_partial_and_config_only_remain_incomplete(inventory):
    _, write, describe = inventory
    assert describe()["incomplete"] is True
    write("model.gguf")
    assert describe()["incomplete"] is False
    write(".cache/huggingface/download/model.hash.incomplete")
    assert describe()["incomplete"] is True
