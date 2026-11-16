"""
Escalation from the Shift Lead to the LLM (phase 3), with the ScriptedModel: no key, no cost, no network.

    a simple spill        -> rules only, the LLM is never called
    3 incidents at once   -> the LLM is asked what goes first (in the background)
    a rejected action     -> still never runs
    a tick                -> never waits for the LLM: it only fills the outbox
"""

from fastapi.testclient import TestClient

from scripts.agent import ScriptedModel
from scripts.shift_lead.staff import NEVER
from web.server import create_app

LAYOUT = "layouts/main_warehouse.json"


def receive(ws, check, limit=400):
    for _ in range(limit):
        msg = ws.receive_json()
        if check(msg):
            return msg
    raise AssertionError("message not received")


def lead_app(replies):
    model = ScriptedModel(replies)
    return create_app(LAYOUT, scenario="calm", agent_model=model, shift_lead=True), model


def test_a_simple_spill_never_calls_the_llm():
    app, model = lead_app(["should not be needed"])
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "agent_incident", "kind": "spill"})
            state = receive(ws, lambda m: m["type"] == "state" and m["shift_lead"]["incidents"])
    assert state["shift_lead"]["incidents"][-1]["handled_by"] == "rules"
    assert state["shift_lead"]["manager_tasks"] == 0
    assert model.calls == []


def test_three_incidents_at_once_go_to_the_llm():
    app, model = lead_app(["1. Look at the tickets. 2. Keep urgent orders moving.",
                           "The playbooks already did the right things; the repair goes first.",
                           "DONE: every incident has a ticket."])
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "agent_incident", "kind": "breakdowns"})   # 2 incidents
            ws.send_json({"type": "agent_incident", "kind": "spill"})        # the third
            queued = receive(ws, lambda m: m["type"] == "agent" and m["event"] == "queued")
            assert "3 INCIDENTS ARE OPEN" in queued["text"] and queued["data"]["source"] == "shift lead"
            done = receive(ws, lambda m: m["type"] == "agent" and m["event"] == "finished")
            assert done["data"]["outcome"] == "done"
    assert len(model.calls) == 3
    lead = app.state.hub.session.lead
    assert lead.counts["rules"] == 3 and len(lead.escalations) == 1


def test_an_incident_without_a_playbook_goes_to_the_llm():
    app, model = lead_app(["1. Look at the queue.", "Nothing to change: the fleet works through it.", "DONE: ok."])
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "agent_incident", "kind": "rush"})
            queued = receive(ws, lambda m: m["type"] == "agent" and m["event"] == "queued")
            assert "No playbook covers this" in queued["text"]
            receive(ws, lambda m: m["type"] == "agent" and m["event"] == "finished")
    lead = app.state.hub.session.lead
    surge = next(i for i in lead.incidents if i.kind == "order_surge")
    assert surge.handled_by == "llm" and lead.counts["llm"] == 1


def test_a_rejected_action_still_never_runs():
    app, model = lead_app([
        "1. Close aisle D for the cleaning.",
        ("I close aisle D.", [("close_aisle", {"aisle": "D", "reason": "keep robots away from the cleaning"})]),
        "Rejected: the robots drive around the spill anyway.",
        "DONE: the cleaner handles it.",
    ])
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "agent_incident", "kind": "rush"})        # no playbook: the LLM decides
            question = receive(ws, lambda m: m["type"] == "agent" and m["event"] == "approval")
            ws.send_json({"type": "agent_answer", "id": question["data"]["id"], "ok": False, "note": "not needed"})
            receive(ws, lambda m: m["type"] == "agent" and m["event"] == "finished")
    assert "D" not in app.state.hub.session.sim.closed_aisles


def test_a_tick_never_waits_for_the_llm():
    app, model = lead_app(["never called inside the tick"])
    session = app.state.hub.session
    sim = session.sim
    for cell, kind in (((3, 4), "spill"), ((7, 11), "box"), ((9, 3), "pallet")):
        sim.warehouse.block_cell(cell)
        sim.blocked_until[cell], sim.block_reasons[cell] = NEVER, kind
    session.step()                                    # the lead decides and fills the outbox ...
    assert len(session.lead.outbox) == 1
    assert model.calls == []                          # ... but nothing called the LLM
