"""
Talking to the warehouse in plain words: the AI chat (quick, one round of tools) and the command reader
for places without an AI (the public demo). "Order 2 cokes" must become 2 soda orders the robots deliver.
"""

import pytest
from fastapi.testclient import TestClient

from scripts.agent import ScriptedModel
from scripts.fleet_tools import FleetTools, ToolInputError
from scripts.shift_lead.commands import answer
from web import server
from web.server import create_app
from web.session import LiveSession

LAYOUT = "layouts/main_warehouse.json"


def receive(ws, check, limit=200):
    for _ in range(limit):
        msg = ws.receive_json()
        if check(msg):
            return msg
    raise AssertionError("message not received")


@pytest.fixture
def tools():
    s = LiveSession(LAYOUT, scenario="realistic_friday", shift_lead=True)
    for _ in range(60):
        s.step()
    return FleetTools(s)


# ----- the tools -----

def test_an_everyday_name_and_a_quantity_make_several_orders(tools):
    before = len(tools.sim.orders)
    result = tools.create_order("coke", quantity=2)
    assert result["item"] == "soda" and len(result["orders"]) == 2
    assert result["note"] == "'coke' is sold here as 'soda'."
    assert len(tools.sim.orders) == before + 2
    assert all(o.item == "soda" for o in tools.sim.orders[-2:])


def test_an_unknown_item_lists_the_whole_catalogue(tools):
    with pytest.raises(ToolInputError) as error:
        tools.create_order("unicorn")
    text = str(error.value)
    assert "Drinks: water, juice, soda, coffee, tea" in text and "Fruit: apples" in text


def test_quantity_is_checked(tools):
    for bad in (0, 11, 2.5, True):
        with pytest.raises(ToolInputError, match="quantity"):
            tools.create_order("soda", quantity=bad)
    tools.sim.warehouse.find_shelf_with("milk").items["milk"] = 2
    with pytest.raises(ToolInputError, match="Only 2 milk left"):
        tools.create_order("milk", quantity=3)


def test_the_robot_report_has_every_robot_and_the_totals(tools):
    report = tools.robot_report()
    assert [r["id"] for r in report["robots"]] == ["R1", "R2", "R3", "R4", "R5"]
    assert {"cell", "doing", "battery", "delivered_today", "problem"} <= set(report["robots"][0])
    assert report["fleet"]["delivered"] == tools.sim.kpis()["orders_delivered"]


# ----- the command reader (no AI) -----

def test_order_2_cokes_is_understood_and_delivered(tools):
    reply = answer(tools, "Order 2 cokes for me")
    assert reply.startswith("OK: 2 soda ordered") and "'coke' is sold here as 'soda'" in reply
    ids = [o.order_id for o in tools.sim.orders[-2:]]
    assert all(i in reply for i in ids)
    for _ in range(300):
        tools.session.step()
    assert all(o.status.value == "delivered" for o in tools.sim.orders if o.order_id in ids)


@pytest.mark.parametrize("line, item, n", [
    ("bring me 3 apples please", "apples", 3),
    ("I want a croissant", "croissants", 1),
    ("can you get me two bottles of water", "water", 2),
    ("order an apple", "apples", 1),
])
def test_other_ways_to_order(tools, line, item, n):
    reply = answer(tools, line)
    assert reply.startswith(f"OK: {n} {item} ordered"), reply


def test_the_command_reader_says_what_it_can_not_do(tools):
    assert "1 to 3 at a time" in answer(tools, "order 20 soda", max_quantity=3)
    assert "We have:" in answer(tools, "order 2 unicorns")
    assert "Try: order 2 soda" in answer(tools, "close every aisle")
    assert "Try:" in answer(tools, "help")


def test_reports_and_robots(tools):
    report = answer(tools, "how are the robots doing?")
    assert report.count("\n- R") == 5 and "Orders:" in report
    assert answer(tools, "where is R3").startswith("R3 is at")
    assert "no robot 'R9'" in answer(tools, "where is robot 9")
    assert "Staff:" in answer(tools, "tickets")


# ----- the website -----

def test_the_public_chat_orders_without_any_ai():
    model = ScriptedModel(["never"])
    app = create_app(LAYOUT, scenario="realistic_friday", public=True, shift_lead=True, agent_model=model)
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            hello = ws.receive_json()
            assert hello["agent"]["chat"] == "commands" and hello["agent"]["enabled"] is False
            ws.send_json({"type": "agent_ask", "text": "order 2 coke"})
            reply = receive(ws, lambda m: m["type"] == "agent" and m["event"] == "reply")
            assert reply["text"].startswith("OK: 2 soda ordered")
            ws.send_json({"type": "agent_ask", "text": "report"})
            error = receive(ws, lambda m: m["type"] == "error")
            assert f"every {server.PUBLIC_CHAT_COOLDOWN} seconds" in error["message"]
    assert model.calls == []


def test_the_ai_chat_answers_in_one_round_without_plan_and_check():
    model = ScriptedModel([
        ("", [("create_order", {"item": "coke", "quantity": 2})]),
        "Done: 2 soda ordered (coke is sold here as soda).",
    ])
    app = create_app(LAYOUT, scenario="realistic_friday", shift_lead=True, agent_model=model)
    session = app.state.hub.session
    before = len(session.sim.orders)
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            hello = ws.receive_json()
            assert hello["agent"]["chat"] == "ai"
            ws.send_json({"type": "agent_ask", "text": "Order 2 cokes for me"})
            done = receive(ws, lambda m: m["type"] == "agent" and m["event"] == "finished")
    assert done["data"]["outcome"] == "done" and "2 soda" in done["text"]
    assert len(model.calls) == 2                                    # no plan step, no check step
    assert all("PLAN" not in str(c["messages"][0]["content"]) for c in model.calls)
    assert [o.item for o in session.sim.orders[before:]] == ["soda", "soda"]
