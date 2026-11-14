"""
The AI supervisor (scripts/agent.py and web/agent_desk.py), tested with a SCRIPTED model:
no API key, no cost, always the same answers. It checks the agent's rules, not Claude's mind:
plan before acting, risky tools only after approval, limits, re-planning, the website flow.
"""

import asyncio

from fastapi.testclient import TestClient

from scripts import agent as agent_module
from scripts.agent import FleetAgent, ScriptedModel
from scripts.bus import InMemoryBroker
from web.server import create_app

LAYOUT = "layouts/main_warehouse.json"

CLOSE_BC = [
    ("Let me look first.", [("get_fleet_status", {})]),
    "1. Close aisle BC because of the spill. 2. Check that robots drive around it.",
    ("Closing aisle BC.", [("close_aisle", {"aisle": "BC", "reason": "A spill in aisle BC: robots must not drive in."})]),
    "I closed aisle BC; the robots plan around it.",
    "DONE: aisle BC is closed and every shelf can still be reached.",
]


def run_agent(replies, approve=True, app=None):
    app = app or create_app(LAYOUT, scenario="calm")
    model = ScriptedModel(replies)
    asked, said = [], []

    async def ask(request):
        asked.append(request)
        return approve, "" if approve else "not now, staff is cleaning"

    async def report(kind, text, data):
        said.append((kind, text))

    result = asyncio.run(FleetAgent(app.state.mcp, model, ask, report).handle("A spill was reported in aisle BC."))
    return result, app.state.hub.session.sim, asked, said, model


def test_plan_act_check_with_approval():
    result, sim, asked, said, _ = run_agent(CLOSE_BC, approve=True)
    assert result.outcome == "done"
    assert "Close aisle BC" in result.plan
    assert [a["tool"] for a in result.actions] == ["get_fleet_status", "close_aisle"]
    assert asked and asked[0]["tool"] == "close_aisle" and "spill" in asked[0]["reason"].lower()
    assert "BC" in sim.closed_aisles                                   # it really happened
    steps = [k for k, _ in said if k != "thinking"]
    assert steps[:3] == ["task", "tool", "plan"]                      # it looked before it planned
    assert steps.index("approval_needed") < steps.index("check") < steps.index("finished")
    assert result.cost_usd > 0


def test_a_rejected_action_is_not_done_and_the_agent_is_told():
    replies = CLOSE_BC[:3] + ["The person rejected closing the aisle, so I left it open and told them.",
                              "DONE: the person decided; nothing else to do."]
    result, sim, asked, said, model = run_agent(replies, approve=False)
    assert "BC" not in sim.closed_aisles                               # never run
    assert result.approvals[0]["approved"] is False
    last_tool_result = model.calls[3]["messages"][-1]["content"][0]
    assert last_tool_result["is_error"] and "REJECTED" in last_tool_result["content"]
    assert "staff is cleaning" in last_tool_result["content"]


def test_the_plan_step_cannot_change_anything():
    replies = [("I'll just pause R1.", [("pause_robot", {"robot_id": "R1", "reason": "test"})]),
               "1. Pause R1.", "Nothing done.", "NOT DONE: nothing happened", "Still nothing.",
               "NOT DONE: nothing happened", "Still nothing.", "NOT DONE: nothing happened"]
    result, sim, asked, *_ = run_agent(replies)
    assert not sim.paused and not asked                                # refused during planning, never asked
    assert result.outcome == "not_done"                                # the check said so 3 times


def test_the_agent_stops_at_the_step_limit():
    looping = [("Look again.", [("get_robot", {"robot_id": "R1"})])] * 30
    result, *_ = run_agent(["1. Look at R1."] + looping)
    assert result.outcome == "stopped"
    assert result.tool_calls == agent_module.MAX_STEPS


def test_the_agent_stops_after_too_many_failed_tool_calls():
    failing = [("Try R9.", [("get_robot", {"robot_id": "R9"})])] * 10
    result, *_ = run_agent(["1. Look at R9."] + failing)
    assert result.outcome == "stopped" and "failed tool calls" in result.summary


def test_a_failed_check_makes_the_agent_try_again():
    replies = CLOSE_BC[:4] + ["NOT DONE: also reopen nothing, but say what robots do.",
                              "Robots drive around BC; nothing else needed.",
                              "DONE: handled."]
    result, *_ = run_agent(replies)
    assert result.outcome == "done"


# ----- on the website -----

def receive_agent(ws, event):
    while True:
        msg = ws.receive_json()
        if msg["type"] == "agent" and msg["event"] == event:
            return msg


def test_the_website_shows_the_agent_and_asks_for_approval():
    app = create_app(LAYOUT, scenario="calm", agent_model=ScriptedModel(CLOSE_BC), incidents=False)
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            hello = ws.receive_json()
            assert hello["agent"]["enabled"] is True
            ws.send_json({"type": "agent_ask", "text": "A spill was reported in aisle BC."})
            ask = receive_agent(ws, "approval")
            assert ask["data"]["tool"] == "close_aisle" and "spill" in ask["text"].lower()
            ws.send_json({"type": "agent_answer", "id": ask["data"]["id"], "ok": True})
            done = receive_agent(ws, "finished")
            assert done["data"]["outcome"] == "done"
    assert "BC" in app.state.hub.session.sim.closed_aisles


def test_the_public_demo_has_no_agent():
    app = create_app(LAYOUT, public=True, agent_model=ScriptedModel(CLOSE_BC))
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            hello = ws.receive_json()
            assert hello["agent"]["enabled"] is False
            ws.send_json({"type": "agent_ask", "text": "close every aisle"})
            msg = ws.receive_json()
            while msg["type"] != "error":
                msg = ws.receive_json()
            assert "not switched on" in msg["message"]


def test_a_lost_robot_starts_an_incident_for_the_agent():
    app = create_app(LAYOUT, scenario="calm", mqtt="memory", agent_model=ScriptedModel([]))
    hub = app.state.hub

    async def go():
        session = hub.session
        session.link.state_timeout = 0
        session.step()
        session.agents["R3"].crash()
        session.step()
        await hub.desk.watch()
        await hub.desk.watch()                       # the same incident doesn't start twice
        return [m for m in hub.desk.history if m["event"] == "queued"]

    queued = asyncio.run(go())
    assert len(queued) == 1 and "R3" in queued[0]["text"] and queued[0]["data"]["source"] == "incident"
    hub.session.close()
