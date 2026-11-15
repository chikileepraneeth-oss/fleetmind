"""
The supervisor in the public demo (FLEETMIND_PUBLIC_AI=on, e.g. with a free AI): visitors start
incidents with buttons, there is a daily limit and one task per tab every few minutes,
and /mcp stays closed. Tested with a scripted model: no key, no cost.
"""

from fastapi.testclient import TestClient

from scripts.agent import ScriptedModel
from scripts.classes import RobotStatus
from web import server
from web.server import create_app

LAYOUT = "layouts/main_warehouse.json"
QUICK = ["1. Look at the fleet and keep it moving.", "Done: the fleet works around it.", "DONE: handled."]


def public_ai_app(replies=None):
    return create_app(LAYOUT, public=True, public_ai=True, agent_model=ScriptedModel(replies or QUICK * 5))


def receive(ws, check):
    while True:
        msg = ws.receive_json()
        if check(msg):
            return msg


def test_without_public_ai_the_public_demo_has_no_supervisor():
    app = create_app(LAYOUT, public=True, agent_model=ScriptedModel(QUICK))
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            assert ws.receive_json()["agent"]["enabled"] is False


def test_public_ai_shows_the_supervisor_but_never_opens_mcp():
    app = public_ai_app()
    with TestClient(app) as client:
        assert client.post("/mcp", json={}).status_code == 404
        with client.websocket_connect("/ws") as ws:
            agent = ws.receive_json()["agent"]
            assert agent["enabled"] and agent["public"]
            assert agent["left_today"] == 20
            assert set(agent["incidents"]) == {"spill", "breakdowns", "rush"}
    assert app.state.hub.desk.incidents is False          # no automatic incidents: only visitors' clicks


def test_a_spill_button_blocks_an_aisle_and_the_supervisor_handles_it():
    app = public_ai_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "agent_incident", "kind": "spill"})
            queued = receive(ws, lambda m: m["type"] == "agent" and m["event"] == "queued")
            assert "spill blocks aisle" in queued["text"] and queued["data"]["source"] == "visitor"
            assert queued["data"]["left_today"] == 19
            done = receive(ws, lambda m: m["type"] == "agent" and m["event"] == "finished")
            assert done["data"]["outcome"] == "done"
    sim = app.state.hub.session.sim
    assert [c for c, why in sim.block_reasons.items() if why == "spill"]


def test_one_ai_task_per_visitor_every_few_minutes():
    app = public_ai_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "agent_incident", "kind": "rush"})
            receive(ws, lambda m: m["type"] == "agent" and m["event"] == "queued")
            ws.send_json({"type": "agent_incident", "kind": "spill"})
            error = receive(ws, lambda m: m["type"] == "error")
            assert f"One AI task every {server.PUBLIC_AI_COOLDOWN // 60} minutes" in error["message"]


def test_the_daily_limit_is_shared_by_everyone():
    app = public_ai_app()
    app.state.hub.desk.daily_tasks = 1
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as first:
            first.receive_json()
            first.send_json({"type": "agent_incident", "kind": "rush"})
            receive(first, lambda m: m["type"] == "agent" and m["event"] == "queued")
        with client.websocket_connect("/ws") as second:          # another visitor: no cooldown, but the day is used up
            assert second.receive_json()["agent"]["left_today"] == 0
            second.send_json({"type": "agent_incident", "kind": "spill"})
            error = receive(second, lambda m: m["type"] == "error")
            assert "free AI tasks are used up" in error["message"]
    sim = app.state.hub.session.sim
    assert not [c for c, why in sim.block_reasons.items() if why == "spill"]   # nothing happened


def test_the_breakdown_and_rush_incidents_change_the_warehouse():
    app = public_ai_app()
    desk, sim = app.state.hub.desk, app.state.hub.session.sim
    task = desk.make_incident("breakdowns")
    broken = [r for r in sim.warehouse.robots.values() if r.status == RobotStatus.ERROR]
    assert len(broken) == 2 and all(r.robot_id in task for r in broken)
    before = len(sim.orders)
    task = desk.make_incident("rush")
    assert len(sim.orders) == before + 12 and "12 orders" in task


def test_an_unknown_incident_is_refused():
    app = public_ai_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "agent_incident", "kind": "earthquake"})
            assert "Unknown incident" in receive(ws, lambda m: m["type"] == "error")["message"]
