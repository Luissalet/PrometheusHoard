import time

import pytest
from starlette.websockets import WebSocketDisconnect


OWN = {"host": "127.0.0.1:5205", "origin": "http://127.0.0.1:5205"}


def install(call, node="spark2"):
    out = call("desktop_install", {"node": node, "confirm": True})
    assert out["job"]["kind"] == "desktop_install"
    time.sleep(0.5)
    assert call("job_get", {"job": out["job"]["id"]})["state"] == "done"
    return out


def test_status_before_install(call):
    out = call("desktop_status")
    assert [n["node"] for n in out["nodes"]] == ["spark1", "spark2", "spark3"]
    assert out["display"] == ":5" and out["port"] == 5905
    one = call("desktop_status", {"node": "spark2"})
    assert one["installed"] is False and one["running"] is False and one["missing"]


def test_install_needs_confirm_and_runs_as_a_job(call, services, world):
    assert call("desktop_install", {"node": "spark2"}, status=400)["code"] == "confirm_required"
    out = install(call)
    cmd = next(c for n, c in world.commands if n == "spark2" and "apt-get install" in c)
    assert "sudo -n env DEBIAN_FRONTEND=noninteractive" in cmd and "apt-get install -y --no-install-recommends" in cmd and "tigervnc-standalone-server" in cmd and "dbus-x11" in cmd
    assert call("desktop_status", {"node": "spark2"})["installed"] is True
    again = call("desktop_install", {"node": "spark2", "confirm": True})
    assert again["job"] is None and again["installed"] is True
    assert out["job"]["node"] == "spark2"


def test_install_without_sudo_is_refused(call, world):
    call("desktop_status", {"node": "spark1"})
    world.desktop["spark1"]["sudo_ok"] = False
    out = call("desktop_install", {"node": "spark1", "confirm": True}, status=403)
    assert out["code"] == "forbidden" and "sudo" in out["hint"]


def test_start_needs_packages_then_runs_and_stops(call, services, world):
    assert call("desktop_start", {"node": "spark2"}, status=503)["code"] == "unavailable"
    install(call)
    out = call("desktop_start", {"node": "spark2"})
    assert out["running"] and out["started"] and out["display"] == ":5" and out["port"] == 5905
    assert "password" not in str(out)
    assert any("vncpasswd" in c and "-f" in c for n, c in world.commands if n == "spark2")
    assert len(world.desktop["spark2"]["password"]) == 8
    assert call("desktop_start", {"node": "spark2"})["already"] is True
    assert call("desktop_status", {"node": "spark2"})["running"] is True
    stopped = call("desktop_stop", {"node": "spark2"})
    assert stopped["running"] is False and stopped["stopped"] is True
    assert call("desktop_status", {"node": "spark2"})["running"] is False


def test_settings_reach_the_remote_helper(call, services, world):
    install(call)
    s = call("settings_set", {"desktop_display": ":7", "desktop_geometry": "1280x720", "desktop_idle_min": 5, "desktop_keymap": "us"})
    assert (s["desktop_display"], s["desktop_geometry"], s["desktop_idle_min"], s["desktop_keymap"]) == (":7", "1280x720", 5, "us")
    out = call("desktop_start", {"node": "spark2"})
    assert out["display"] == ":7" and out["port"] == 5907 and out["geometry"] == "1280x720" and out["idle_min"] == 5
    assert world.desktop["spark2"]["keymap"] == "us"
    for bad in ({"desktop_display": "5"}, {"desktop_display": ":0"}, {"desktop_geometry": "big"}, {"desktop_keymap": "es; rm -rf"}):
        assert call("settings_set", bad, status=400)["code"] == "invalid"


def test_tools_never_return_the_password(call, services):
    install(call)
    call("desktop_start", {"node": "spark2"})
    password = services.desktop._password(services.cluster.node("spark2"))
    for name, args in (("desktop_status", {}), ("desktop_start", {"node": "spark2"}), ("settings_get", {}), ("jobs_list", {})):
        assert password not in str(call(name, args))


def test_session_route_gives_the_password_only_while_running(client, call, services):
    install(call)
    assert client.get("/api/desktop/spark2/session").status_code == 503
    call("desktop_start", {"node": "spark2"})
    body = client.get("/api/desktop/spark2/session").json()
    assert body["running"] and len(body["password"]) == 8 and body["ws"] == "/api/desktop/spark2/ws"
    assert (services.config.data_dir / "desktop-passwords.json").is_file()


def test_websocket_bridges_to_the_tunnel(client, call, services):
    install(call)
    call("desktop_start", {"node": "spark2"})
    with client.websocket_connect("/api/desktop/spark2/ws", headers=OWN, subprotocols=["binary"]) as ws:
        assert ws.accepted_subprotocol == "binary"
        assert ws.receive_bytes() == b"RFB 003.008\n"
        ws.send_bytes(b"hello")
        assert ws.receive_bytes() == b"HELLO"
        assert services.desktop.status("spark2")["bridges"] == 1
    for _ in range(40):
        if services.desktop.status("spark2")["bridges"] == 0:
            break
        time.sleep(0.05)
    assert services.desktop.status("spark2")["bridges"] == 0


def test_websocket_reports_a_desktop_that_is_not_running(client, call):
    install(call)
    with client.websocket_connect("/api/desktop/spark2/ws", headers=OWN) as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_bytes()
    assert exc.value.code == 4404


def test_guard_lets_the_apps_own_page_in_and_refuses_other_origins(client, call):
    install(call)
    call("desktop_start", {"node": "spark2"})
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/desktop/spark2/ws", headers={**OWN, "origin": "https://evil.example"}):
            pass
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/desktop/spark2/ws", headers={"host": "evil.example"}):
            pass
    with client.websocket_connect("/api/desktop/spark2/ws", headers={"host": "localhost:5205", "origin": "http://localhost:5205"}) as ws:
        assert ws.receive_bytes() == b"RFB 003.008\n"
    r = client.get("/api/desktop/spark2/session", headers={"origin": "https://evil.example"})
    assert r.status_code == 403
