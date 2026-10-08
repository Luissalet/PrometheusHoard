import io


def test_list_and_read(call):
    listing = call("files_list", {"node": "spark1", "path": "~"})
    names = [e["name"] for e in listing["entries"]]
    assert "Documentos" in names and listing["writable"]
    text = call("file_read", {"node": "spark1", "path": "~/Documentos/notas.md"})
    assert text["text"].startswith("# Notas")


def test_outside_home_is_forbidden_until_allowed(call):
    assert call("files_list", {"node": "spark1", "path": "/etc"}, status=403)["code"] == "forbidden"
    call("settings_set", {"show_system": True})
    listing = call("files_list", {"node": "spark1", "path": "/etc"})
    assert not listing["writable"]
    assert call("file_write", {"node": "spark1", "path": "/etc/x", "content": "y"}, status=403)["code"] == "forbidden"


def test_write_rename_move_copy(call):
    call("folder_create", {"node": "spark2", "path": "~/a/b"})
    call("file_write", {"node": "spark2", "path": "~/a/b/f.txt", "content": "hola"})
    assert call("file_write", {"node": "spark2", "path": "~/a/b/f.txt", "content": "x"}, status=409)["code"] == "conflict"
    call("file_rename", {"node": "spark2", "path": "~/a/b/f.txt", "new_name": "g.txt"})
    call("files_move", {"node": "spark2", "paths": ["~/a/b/g.txt"], "dest": "~/a"})
    call("files_copy", {"node": "spark2", "paths": ["~/a/g.txt"], "dest": "~/a"})
    names = [e["name"] for e in call("files_list", {"node": "spark2", "path": "~/a"})["entries"]]
    assert sorted(names) == ["b", "g (2).txt", "g.txt"]
    assert call("files_move", {"node": "spark2", "paths": ["~/a"], "dest": "~/a/b"}, status=400)["code"] == "invalid"


def test_trash_cycle(call):
    call("file_write", {"node": "spark1", "path": "~/borrar.txt", "content": "z"})
    out = call("files_delete", {"node": "spark1", "paths": ["~/borrar.txt"]})
    item = out["trashed"][0]["id"]
    assert "borrar.txt" not in [e["name"] for e in call("files_list", {"node": "spark1"})["entries"]]
    trash = call("trash_list", {"node": "spark1"})
    assert trash["items"][0]["origin"].endswith("/borrar.txt")
    call("trash_restore", {"node": "spark1", "ids": [item]})
    assert "borrar.txt" in [e["name"] for e in call("files_list", {"node": "spark1"})["entries"]]
    call("files_delete", {"node": "spark1", "paths": ["~/borrar.txt"]})
    assert call("trash_empty", {"node": "spark1"}, status=400)["code"] == "confirm_required"
    call("trash_empty", {"node": "spark1", "confirm": True})
    assert call("trash_list", {"node": "spark1"})["items"] == []


def test_permanent_delete_needs_confirm(call):
    call("file_write", {"node": "spark1", "path": "~/p.txt", "content": "z"})
    assert call("files_delete", {"node": "spark1", "paths": ["~/p.txt"], "permanent": True}, status=400)["code"] == "confirm_required"
    call("files_delete", {"node": "spark1", "paths": ["~/p.txt"], "permanent": True, "confirm": True})


def test_home_cannot_be_deleted(call):
    assert call("files_delete", {"node": "spark1", "paths": ["~"]}, status=403)["code"] == "forbidden"


def test_upload_download(client, call):
    r = client.post("/api/files/upload", data={"node": "spark3", "folder": "~"}, files=[("files", ("subida.bin", io.BytesIO(b"\x00\x01" * 1000)))])
    assert r.status_code == 200, r.text
    assert r.json()["uploaded"][0]["bytes"] == 2000
    r = client.get("/api/files/download", params={"node": "spark3", "path": "~/subida.bin"})
    assert r.status_code == 200 and r.content == b"\x00\x01" * 1000
    assert "attachment" in r.headers["content-disposition"]
    r2 = client.post("/api/files/upload", data={"node": "spark3", "folder": "~"}, files=[("files", ("subida.bin", io.BytesIO(b"x")))])
    assert r2.json()["uploaded"][0]["path"].endswith("subida (2).bin")


def test_search_and_info(call):
    hits = call("files_search", {"node": "spark1", "path": "~", "pattern": "*.safetensors"})["hits"]
    assert len(hits) == 2
    info = call("file_info", {"node": "spark1", "path": "~/models"})
    assert info["dir"] and info["bytes"] > 6000


def test_svg_is_never_rendered_inline(client, call):
    call("file_write", {"node": "spark1", "path": "~/x.svg", "content": "<svg onload='alert(1)'/>"})
    r = client.get("/api/files/raw", params={"node": "spark1", "path": "~/x.svg"})
    assert r.headers["content-type"].startswith("text/plain")
