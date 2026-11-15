"""
The Shift Lead's playbooks (scripts/shift_lead/playbooks.py): rules first, only through the tools.
"""

from scripts.fleet_tools import ToolInputError
from scripts.shift_lead.lead import Incident
from scripts.shift_lead.playbooks import NEEDS_MANAGER, SEND_PERSON, WORK_AROUND, run_playbook
from scripts.shift_lead.staff import NEVER
from web.session import LiveSession

LAYOUT = "layouts/main_warehouse.json"


class FakeTools:
    """Only the fleet tools, and no simulation at all: a playbook that went around them would crash."""

    def __init__(self, robot=None, charger_free=True):
        self.calls = []
        self.robot = robot or {"id": "R2", "order": {"id": "O-0007"}, "carrying": None}
        self.charger_free = charger_free

    def _log(self, name, **kw):
        self.calls.append((name, kw))

    def open_ticket(self, kind, cell=None, robot_id=None, shelf=None, what=""):
        self._log("open_ticket", kind=kind, cell=cell, robot_id=robot_id, shelf=shelf)
        return {"ticket": {"id": "W1", "kind": kind}}

    def reroute_around(self, cell):
        self._log("reroute_around", cell=cell)
        return {"cell": cell, "rerouted": ["R1"]}

    def get_robot(self, robot_id):
        self._log("get_robot", robot_id=robot_id)
        return self.robot

    def reassign_order(self, robot_id, reason=""):
        self._log("reassign_order", robot_id=robot_id)
        return {"order_back_in_queue": self.robot["order"]["id"]}

    def send_to_charger(self, robot_id):
        self._log("send_to_charger", robot_id=robot_id)
        if not self.charger_free:
            raise ToolInputError("Every charger is taken right now.")
        return {"robot": robot_id, "charger": [1, 15], "battery": 12.5}


def names(tools):
    return [name for name, _ in tools.calls]


def test_a_spill_gets_a_cleaner_and_robots_reroute():
    tools, incident = FakeTools(), Incident("I1", "spill", 10, cell=(3, 4))
    decision, reason, calls = run_playbook(tools, incident)
    assert decision == SEND_PERSON and "cleaner" in reason and "8 min" in reason
    assert names(tools) == ["open_ticket", "reroute_around"]
    assert tools.calls[0][1]["kind"] == "clean" and incident.ticket == "W1"
    assert [c["tool"] for c in calls] == ["open_ticket", "reroute_around"]


def test_a_box_and_a_pallet_use_the_same_playbook_with_their_own_time():
    for kind, minutes in (("box", 4), ("pallet", 6)):
        decision, reason, _ = run_playbook(FakeTools(), Incident("I1", kind, 10, cell=(3, 4)))
        assert decision == SEND_PERSON and f"{minutes} min" in reason


def test_a_broken_robot_gets_a_technician_and_its_order_goes_to_another_robot():
    tools = FakeTools()
    decision, reason, _ = run_playbook(tools, Incident("I1", "robot_broken", 10, cell=(5, 5), robot="R2"))
    assert decision == SEND_PERSON
    assert names(tools) == ["get_robot", "open_ticket", "reassign_order"]
    assert "O-0007 goes to another robot" in reason


def test_a_broken_robot_that_carries_an_item_keeps_it():
    tools = FakeTools(robot={"id": "R2", "order": {"id": "O-0007"}, "carrying": "milk"})
    _, reason, _ = run_playbook(tools, Incident("I1", "robot_broken", 10, cell=(5, 5), robot="R2"))
    assert "reassign_order" not in names(tools) and "keeps milk" in reason


def test_a_lost_robot_gets_a_restart_ticket():
    tools = FakeTools()
    decision, reason, _ = run_playbook(tools, Incident("I1", "robot_lost", 10, cell=(5, 5), robot="R4"))
    assert decision == SEND_PERSON and tools.calls == [("open_ticket", {"kind": "restart", "cell": None,
                                                                         "robot_id": "R4", "shelf": None})]
    assert "back in the queue" in reason


def test_an_empty_shelf_is_restocked_first():
    tools = FakeTools()
    decision, _, _ = run_playbook(tools, Incident("I1", "shelf_empty", 10, cell=(3, 2), shelf="C3"))
    assert decision == SEND_PERSON and tools.calls[0][1]["kind"] == "restock" and tools.calls[0][1]["shelf"] == "C3"


def test_a_critical_battery_goes_to_a_charger_without_a_person():
    tools = FakeTools()
    decision, reason, _ = run_playbook(tools, Incident("I1", "battery_critical", 10, robot="R1"))
    assert decision == WORK_AROUND and names(tools) == ["send_to_charger"] and "12.5%" in reason


def test_a_failed_tool_call_is_written_down_not_a_crash():
    tools = FakeTools(charger_free=False)
    decision, reason, calls = run_playbook(tools, Incident("I1", "battery_critical", 10, robot="R1"))
    assert decision == WORK_AROUND and calls[0]["ok"] is False and "taken" in calls[0]["result"]


def test_no_playbook_means_no_rules_decision():
    assert run_playbook(FakeTools(), Incident("I1", "late_orders", 10)) is None


def lead_session(scenario="calm"):
    return LiveSession(LAYOUT, scenario=scenario, shift_lead=True)


def test_the_lead_decides_a_spill_by_rules_and_the_ticket_comes_from_the_playbook():
    s = lead_session()
    assert s.sim.staff.auto is False                     # tickets only from playbooks
    cell = (3, 4)
    s.sim.warehouse.block_cell(cell)
    s.sim.blocked_until[cell], s.sim.block_reasons[cell] = NEVER, "spill"
    s.step()
    incident = s.lead.incidents[0]
    assert (incident.kind, incident.decision, incident.handled_by) == ("spill", SEND_PERSON, "rules")
    assert s.sim.staff.tickets[0].id == incident.ticket
    assert any(e[2] == "decision" and "send a person" in e[3] for e in s.sim.events)
    for _ in range(200):
        s.step()
        if incident.status == "solved":
            break
    assert incident.status == "solved" and incident.minutes > 8
    assert s.lead.counts == {"rules": 1, "llm": 0, "nobody": 0}


def test_a_broken_robots_order_is_delivered_by_another_robot():
    s = lead_session("normal")
    for _ in range(30):
        s.step()
    busy = next(r for r in s.sim._robots() if r.order is not None and r.carrying is None)
    order = busy.order
    busy.break_down(s.sim.config.repair_ticks)
    s.step()
    assert busy.order is None
    for _ in range(200):
        s.step()
        if order.status.value == "delivered":
            break
    assert order.status.value == "delivered"
    assert any(e[2] == "order_returned" and e[1] == busy.robot_id for e in s.sim.events)


def test_without_a_manager_the_lead_only_writes_it_down():
    s = lead_session("rush_hour")
    s.lead.manager_allowed = False
    for _ in range(400):
        s.step()
    unknown = [i for i in s.lead.incidents if i.decision == NEEDS_MANAGER]
    assert unknown and all(i.handled_by == "nobody" for i in unknown)
    assert s.lead.counts["llm"] == 0


def test_the_staff_tools_give_clear_errors():
    import pytest
    from scripts.fleet_tools import FleetTools
    plain = FleetTools(LiveSession(LAYOUT, scenario="calm"))
    with pytest.raises(ToolInputError, match="no cleaners or technicians"):
        plain.list_tickets()
    tools = FleetTools(lead_session())
    with pytest.raises(ToolInputError, match="not blocked"):
        tools.open_ticket("clean", cell=[3, 4])
    with pytest.raises(ToolInputError, match="is not broken"):
        tools.open_ticket("repair", robot_id="R1")
    with pytest.raises(ToolInputError, match="no order"):
        tools.reassign_order("R1")
    with pytest.raises(ToolInputError, match="outside the warehouse"):
        tools.reroute_around([40, 2])


def test_the_mcp_server_has_the_staff_tools_only_with_the_shift_lead():
    import asyncio
    from mcp import Client
    from web.server import create_app

    def tool_names(app):
        async def go():
            async with Client(app.state.mcp) as client:
                return {t.name for t in (await client.list_tools()).tools}
        return asyncio.run(go())

    assert "open_ticket" not in tool_names(create_app(scenario="calm"))
    names = tool_names(create_app(scenario="calm", shift_lead=True))
    assert {"list_tickets", "open_ticket", "reassign_order", "send_to_charger", "reroute_around"} <= names


def test_the_state_message_carries_the_decisions():
    s = lead_session("breakdown_day")
    for _ in range(150):
        s.step()
    lead = s.state_message()["shift_lead"]
    assert lead["counts"]["rules"] > 0
    assert all(i["reason"] for i in lead["incidents"])
