"""
The morning shift plan (scripts/shift_lead/plan.py): made from the forecast at 06:00, it waits for
one Approve, and only then does the fleet follow it, through the tools.
"""

import pytest
from fastapi.testclient import TestClient

from scripts.agent import ScriptedModel
from scripts.fleet_tools import FleetTools, ToolInputError
from scripts.shift_lead.plan import busy_periods
from web.server import create_app
from web.session import LiveSession

LAYOUT = "layouts/main_warehouse.json"


def friday():
    s = LiveSession(LAYOUT, scenario="realistic_friday", shift_lead=True)
    s.step()                                             # 06:01: the plan is made
    return s


def clock_tick(hhmm):
    h, m = (int(x) for x in hhmm.split(":"))
    return (h - 6) * 60 + m


def test_busy_periods_join_hours_that_follow_each_other():
    forecast = {"hours_over_capacity": ["09:00", "10:00", "11:00", "15:00", "16:00", "17:00"]}
    assert busy_periods(forecast) == [(9, 12), (15, 18)]
    assert busy_periods({"hours_over_capacity": []}) == []


def test_there_is_a_plan_every_morning_and_it_waits_for_approval():
    s = friday()
    plan = s.lead.plan
    assert plan and plan["status"] == "waiting" and plan["made_by"] == "rules"
    kinds = [step["kind"] for step in plan["steps"]]
    assert kinds.count("charge") == len(plan["periods"]) >= 1
    assert "park" in kinds and "restock_pause" in kinds
    assert all(step["why"] for step in plan["steps"])
    assert s.state_message()["shift_lead"]["plan"]["status"] == "waiting"
    s.reset()                                            # a new day: a new plan
    s.step()
    assert s.lead.plan is not plan and s.lead.plan["status"] == "waiting"


def test_nothing_changes_without_approval():
    s = friday()
    charge = next(step for step in s.lead.plan["steps"] if step["kind"] == "charge")
    while s.sim.tick < clock_tick(charge["start"]) + 5:
        s.step()
    assert s.sim.plan == {"charge": [], "park": [], "restock_pause": []}
    assert s.sim.charge_target() is None
    assert not any(e[2] == "plan_window" for e in s.sim.events)


def test_after_approve_charging_starts_before_the_peak():
    s = friday()
    plan = s.lead.answer_plan(True)
    assert plan["status"] == "approved"
    assert {a["tool"] for a in plan["actions"]} == {"plan_charging", "plan_parking", "plan_restock_pause"}
    assert all(a["ok"] for a in plan["actions"])
    charge = next(step for step in plan["steps"] if step["kind"] == "charge")
    start, peak = clock_tick(charge["start"]), clock_tick(charge["end"])
    sent = []
    while s.sim.tick < peak:
        s.step()
        if start <= s.sim.tick < peak:
            assert s.sim.charge_target() == 95
            sent += [e for e in s.sim.events[-20:] if e[0] == s.sim.tick and e[2] == "going_to_charge"]
    assert sent, "no robot went to charge in the hour before the peak"
    assert s.sim.charge_target() is None                  # the peak itself: the charging window is over


def test_reject_changes_nothing_and_a_second_answer_is_refused():
    s = friday()
    assert s.lead.answer_plan(False)["status"] == "rejected"
    assert s.sim.plan["charge"] == []
    with pytest.raises(ValueError, match="no plan waiting"):
        s.lead.answer_plan(True)


def test_the_plan_tools_check_their_input():
    tools = FleetTools(friday())
    with pytest.raises(ToolInputError, match="not a time"):
        tools.plan_charging("soon", "17:00")
    with pytest.raises(ToolInputError, match="must end after"):
        tools.plan_parking("17:00", "16:00")
    with pytest.raises(ToolInputError, match="from 50 to 100"):
        tools.plan_charging("15:00", "16:00", target=20)
    plain = FleetTools(LiveSession(LAYOUT, scenario="calm", shift_lead=True))
    with pytest.raises(ToolInputError, match="realistic day"):
        plain.plan_parking("09:00", "10:00")


def test_one_click_on_the_website_approves_the_plan():
    app = create_app(LAYOUT, scenario="realistic_friday", shift_lead=True)
    session = app.state.hub.session
    session.step()
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "plan_answer", "ok": True})
            for _ in range(20):
                msg = ws.receive_json()
                if msg["type"] == "state" and msg["shift_lead"]["plan"]["status"] == "approved":
                    break
            else:
                raise AssertionError("the plan was not approved")
    assert session.sim.plan["charge"]


def test_with_an_llm_it_only_writes_the_explanation():
    model = ScriptedModel(["Two busy periods today. We charge first and park near packing."])
    app = create_app(LAYOUT, scenario="realistic_friday", shift_lead=True, agent_model=model)
    session = app.state.hub.session
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "step"})
            for _ in range(40):
                msg = ws.receive_json()
                if msg["type"] == "agent" and msg["event"] == "plan":
                    break
            else:
                raise AssertionError("the explanation never came")
    plan = session.lead.plan
    assert plan["explained"].startswith("Two busy periods") and plan["status"] == "waiting"
    assert session.sim.plan["charge"] == []                # still nothing without the person's Approve
    assert len(model.calls) == 1 and model.calls[0]["tools"] is None


def test_the_public_demo_makes_the_plan_by_rules_only():
    app = create_app(LAYOUT, scenario="realistic_friday", public=True, shift_lead=True,
                     agent_model=ScriptedModel(["never"]))
    session = app.state.hub.session
    session.step()
    assert session.lead.plan["made_by"] == "rules" and not session.lead.outbox
