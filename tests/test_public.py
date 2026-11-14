"""
The public demo (python -m web.server --public): one shared warehouse anyone can open,
with NO tools, NO AI and NO keys, and limits so nobody can overload it.
"""

from fastapi.testclient import TestClient

from web import server
from web.server import create_app

LAYOUT = "layouts/main_warehouse.json"


def receive_until(ws, kind):
    while True:
        msg = ws.receive_json()
        if msg["type"] == kind:
            return msg


def test_the_public_demo_has_no_mcp_tools():
    with TestClient(create_app(LAYOUT, public=True)) as client:
        assert client.post("/mcp", json={}).status_code == 404
        assert client.get("/mcp").status_code == 404
        assert client.get("/docs").status_code == 404
        assert client.app.state.mcp is None


def test_the_normal_website_still_has_its_tools():
    app = create_app(LAYOUT)
    assert app.state.mcp is not None
    assert any(getattr(r, "path", "") == "/mcp" for r in app.router.routes)


def test_health_check():
    with TestClient(create_app(LAYOUT, public=True)) as client:
        answer = client.get("/health").json()
        assert answer["ok"] is True and answer["public"] is True


def test_the_demo_runs_by_itself_and_says_it_is_public():
    with TestClient(create_app(LAYOUT, public=True)) as client:
        with client.websocket_connect("/ws") as ws:
            hello = receive_until(ws, "hello")
            assert hello["public"] is True
            state = receive_until(ws, "state")
            assert state["running"] is True


def test_visitors_can_still_use_the_buttons_but_not_too_fast():
    with TestClient(create_app(LAYOUT, public=True)) as client:
        with client.websocket_connect("/ws") as ws:
            receive_until(ws, "state")
            errors = 0
            for _ in range(10):                   # 10 clicks at once
                ws.send_json({"type": "pause"})
            for _ in range(10):
                msg = ws.receive_json()
                while msg["type"] == "state" and msg["running"]:
                    msg = ws.receive_json()          # the ticker's own updates
                if msg["type"] == "error":
                    assert "Slow down" in msg["message"]
                    errors += 1
            assert 1 <= errors <= 10 - server.PUBLIC_COMMANDS_PER_SECOND


def test_the_speed_is_capped():
    with TestClient(create_app(LAYOUT, public=True)) as client:
        with client.websocket_connect("/ws") as ws:
            receive_until(ws, "state")
            ws.send_json({"type": "speed", "value": 60})
            msg = ws.receive_json()
            while msg["type"] != "state" or msg["speed"] == 6:
                msg = ws.receive_json()
            assert msg["speed"] == server.PUBLIC_MAX_SPEED


def test_the_normal_website_is_not_limited():
    with TestClient(create_app(LAYOUT)) as client:
        with client.websocket_connect("/ws") as ws:
            assert receive_until(ws, "hello")["public"] is False
            receive_until(ws, "state")                   # the state every new tab gets first
            ws.send_json({"type": "speed", "value": 60})
            assert receive_until(ws, "state")["speed"] == 60
