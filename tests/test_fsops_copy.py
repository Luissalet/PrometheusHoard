"""The remote copy answers a clear error when the destination folder or a source is missing."""
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "prometheus_hoard" / "remote" / "fsops.py"


def run(args):
    out = subprocess.run([sys.executable, str(SCRIPT), json.dumps(args)], capture_output=True, text=True, timeout=30)
    return json.loads([ln for ln in out.stdout.splitlines() if ln.strip()][-1])


def test_copy_into_a_missing_folder_is_a_not_found_error(tmp_path):
    src = tmp_path / "a.txt"
    src.write_text("x")
    out = run({"op": "copy", "paths": [str(src)], "dest": str(tmp_path / "nope")})
    assert out["code"] == "not_found" and "does not exist" in out["error"]
    assert not (tmp_path / "nope").exists()


def test_copy_of_a_missing_source_is_a_not_found_error(tmp_path):
    out = run({"op": "copy", "paths": [str(tmp_path / "ghost.txt")], "dest": str(tmp_path)})
    assert out["code"] == "not_found" and "ghost.txt" in out["error"]


def test_copy_still_copies_and_renames_on_collision(tmp_path):
    src, dest = tmp_path / "a.txt", tmp_path / "out"
    src.write_text("x")
    dest.mkdir()
    (dest / "a.txt").write_text("old")
    out = run({"op": "copy", "paths": [str(src)], "dest": str(dest)})
    assert out["copied"] == [str(dest / "a (2).txt")] and (dest / "a (2).txt").read_text() == "x"
