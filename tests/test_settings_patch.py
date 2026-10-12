"""Saving settings must not undo what someone else changed meanwhile: scalar settings merge by key, and Sparks can be patched one by one."""
from __future__ import annotations


def _node(call, node_id):
    return next(n for n in call("settings_get")["nodes"] if n["id"] == node_id)


def test_scalar_settings_merge_and_leave_the_rest(call, services):
    before = call("settings_get")
    call("settings_set", {"poll_s": 5})
    call("settings_set", {"trash_dir": "~/bin"})   # a second writer that only knows about its own field
    after = call("settings_get")
    assert after["poll_s"] == 5.0 and after["trash_dir"] == "~/bin"
    assert after["nodes"] == before["nodes"] and after["models_dirs"] == before["models_dirs"]


def test_nodes_patch_changes_only_the_given_fields(call, services):
    before = _node(call, "spark2")
    call("settings_set", {"nodes_patch": {"spark1": {"name": "Head", "color": "#112233"}}})
    head = _node(call, "spark1")
    assert head["name"] == "Head" and head["color"] == "#112233"
    assert head["ssh"] == "Spark1" and head["enabled"] is True
    assert _node(call, "spark2") == before                      # untouched Sparks stay as they were
    assert services.cluster.node("spark1").conf["name"] == "Head"   # and the cluster follows


def test_an_agent_edit_survives_a_patch_of_another_field(call):
    call("settings_set", {"nodes_patch": {"spark3": {"api_host": "agent-host.local"}}})      # the assistant edits one field
    call("settings_set", {"nodes_patch": {"spark3": {"name": "Typed by hand"}}})             # the form saves only what the person changed
    node = _node(call, "spark3")
    assert node["api_host"] == "agent-host.local" and node["name"] == "Typed by hand"


def test_nodes_patch_renames_adds_and_removes(call):
    call("settings_set", {"nodes_patch": {"spark1": {"id": "head"}}})
    assert [n["id"] for n in call("settings_get")["nodes"]] == ["head", "spark2", "spark3"]
    call("settings_set", {"nodes_patch": {"spark4": {"ssh": "Spark4", "name": "Fourth"}}, "nodes_remove": ["spark2", "ghost"]})
    nodes = call("settings_get")["nodes"]
    assert [n["id"] for n in nodes] == ["head", "spark3", "spark4"]
    assert nodes[2]["name"] == "Fourth" and nodes[2]["enabled"] is True


def test_nodes_patch_refuses_what_it_cannot_apply(call):
    assert call("settings_set", {"nodes_patch": {"nope": {"name": "x"}}}, status=400)["code"] == "invalid"       # unknown Spark without ssh
    assert call("settings_set", {"nodes_patch": {"spark1": {"colour": "red"}}}, status=400)["code"] == "invalid"  # unknown field
    assert call("settings_set", {"nodes_patch": {"spark1": {"id": "spark2"}}}, status=400)["code"] == "invalid"   # duplicate id
    assert [n["name"] for n in call("settings_get")["nodes"]] == ["Spark1", "Spark2", "Spark3"]                    # nothing half-applied


def test_client_names_are_set_one_address_at_a_time(call):
    call("client_name_set", {"ip": "192.0.2.10", "name": "Torre"})
    call("client_name_set", {"ip": "192.0.2.11", "name": "Portátil"})
    assert call("settings_get")["client_names"] == {"192.0.2.10": "Torre", "192.0.2.11": "Portátil"}
    call("client_name_set", {"ip": "192.0.2.10", "name": ""})
    assert call("settings_get")["client_names"] == {"192.0.2.11": "Portátil"}
