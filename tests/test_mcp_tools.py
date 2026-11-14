"""
The fleet's MCP tools (scripts/fleet_tools.py + web/mcp_server.py).

Every tool is called through a real MCP client connected in-process to the MCP
server of the live website, so this tests what an AI agent will see: the tool
list, the data, and the error messages. Then the simulation runs on, to check
that the tool really did what it said (a paused robot stays put, nobody drives
into a closed aisle, a new order gets delivered).
"""

import asyncio

import pytest
from mcp import Client

from scripts.bus import InMemoryBroker
from scripts.classes import OrderStatus
from web.server import create_app
from web.session import LiveSession

LAYOUT = "layouts/main_warehouse.json"


class Fleet:
    """The live website's session and its MCP server, driven from a test."""

    def __init__(self, scenario="calm"):
        self.app = create_app(scenario=scenario)
        self.session = self.app.state.hub.session
        self.mcp = self.app.state.mcp

    def call(self, tool, **arguments):
        async def go():
            async with Client(self.mcp) as client:
                return await client.call_tool(tool, arguments)
        result = asyncio.run(go())
        if result.is_error:
            return {"error": result.content[0].text}
        return result.structured_content

    def tools(self):
        async def go():
            async with Client(self.mcp) as client:
                return (await client.list_tools()).tools
        return asyncio.run(go())

    def run(self, ticks):
        for _ in range(ticks):
            self.session.step()

    @property
    def sim(self):
        return self.session.sim


@pytest.fixture
def fleet():
    return Fleet()


# ----- the tool list -----

def test_the_server_lists_every_tool_with_a_description(fleet):
    tools = {t.name: t for t in fleet.tools()}
    assert set(tools) == {"get_fleet_status", "get_robot", "create_order", "pause_robot", "resume_robot",
                          "close_aisle", "reopen_aisle", "get_forecast"}
    for tool in tools.values():
        assert len(tool.description) > 40
    assert tools["get_robot"].input_schema["required"] == ["robot_id"]


def test_risky_tools_are_marked_so_the_agent_can_ask_first(fleet):
    tools = {t.name: t for t in fleet.tools()}
    for name in ("get_fleet_status", "get_robot", "get_forecast"):
        assert tools[name].annotations.read_only_hint is True
    for name in ("pause_robot", "close_aisle"):
        assert tools[name].annotations.destructive_hint is True


# ----- reading -----

def test_fleet_status_shows_robots_orders_problems_and_aisles(fleet):
    fleet.run(30)
    status = fleet.call("get_fleet_status")
    assert status["tick"] == 30 and len(status["robots"]) == 5
    assert {"waiting", "delivered", "late", "on_time_rate"} <= status["orders"].keys()
    assert status["problems"]["lost_robots"] == [] and status["problems"]["closed_aisles"] == []
    assert set(status["aisles"]) == {"A", "AB", "BC", "CD", "D", "E", "EF", "FG", "GH", "H"}


def test_get_robot_shows_its_order_and_route(fleet):
    fleet.run(15)
    busy = next(r for r in fleet.sim.warehouse.robots.values() if r.order)
    info = fleet.call("get_robot", robot_id=busy.robot_id.lower())     # "r2" works too
    assert info["id"] == busy.robot_id
    assert info["order"]["id"] == busy.order.order_id and info["order"]["item"] == busy.order.item
    assert info["recent_events"]


@pytest.mark.parametrize("robot_id", ["R9", "robot", ""])
def test_a_wrong_robot_gives_a_clear_error(fleet, robot_id):
    answer = fleet.call("get_robot", robot_id=robot_id)
    assert "There is no robot" in answer["error"] and "R1, R2, R3, R4, R5" in answer["error"]


# ----- orders -----

def test_a_created_order_is_queued_and_delivered(fleet):
    answer = fleet.call("create_order", item="Apples", priority="high", deadline_minutes=45)
    assert answer["item"] == "apples" and answer["shelf"] == "A1" and answer["priority"] == "high"
    order = next(o for o in fleet.sim.orders if o.order_id == answer["order"])
    assert order in fleet.sim.queue and order.deadline == order.created_at + 45
    fleet.run(120)
    assert order.status == OrderStatus.DELIVERED
    assert any("(by agent)" in e[3] for e in fleet.sim.events)


@pytest.mark.parametrize("arguments, error", [
    ({"item": "banana"}, "Did you mean: bananas?"),
    ({"item": "spaceship"}, "There is no item 'spaceship'"),
    ({"item": "milk", "priority": "urgent"}, "priority must be 'high', 'normal' or 'low'"),
    ({"item": "milk", "deadline_minutes": 0}, "deadline_minutes must be a number from 1 to 1440"),
])
def test_a_wrong_order_gives_a_clear_error(fleet, arguments, error):
    assert error in fleet.call("create_order", **arguments)["error"]


def test_an_item_out_of_stock_is_refused(fleet):
    fleet.sim.warehouse.find_shelf_with("milk").items["milk"] = 0
    assert "'milk' is out of stock" in fleet.call("create_order", item="milk")["error"]


# ----- pausing robots -----

def test_a_paused_robot_stays_put_and_gives_back_its_order(fleet):
    fleet.run(10)
    robot = next(r for r in fleet.sim.warehouse.robots.values() if r.order and not r.carrying)
    order, cell = robot.order, robot.position
    answer = fleet.call("pause_robot", robot_id=robot.robot_id, reason="checking a noise")
    assert answer["paused"] and answer["order_back_in_queue"] == order.order_id
    delivered = fleet.sim.kpis()["orders_delivered"]
    fleet.run(80)
    assert robot.position == cell and robot.order is None          # it didn't move, got no work
    assert fleet.sim.kpis()["orders_delivered"] > delivered         # the others kept working
    assert order.status == OrderStatus.DELIVERED                    # someone else delivered its order
    assert fleet.sim.kpis()["collisions"] == 0
    assert "already paused" in fleet.call("pause_robot", robot_id=robot.robot_id)["error"]

    assert fleet.call("resume_robot", robot_id=robot.robot_id)["paused"] is False
    fleet.run(80)
    assert fleet.sim.deliveries_by.get(robot.robot_id, 0) > 0 or robot.position != cell


def test_a_paused_robot_keeps_its_item_and_delivers_it_after_resume(fleet):
    for _ in range(200):
        fleet.run(1)
        carrier = next((r for r in fleet.sim.warehouse.robots.values() if r.carrying), None)
        if carrier:
            break
    order = carrier.order
    answer = fleet.call("pause_robot", robot_id=carrier.robot_id)
    assert answer["keeps_item"] == carrier.carrying and answer["order_back_in_queue"] is None
    fleet.run(20)
    assert order.status == OrderStatus.PICKED_UP and carrier.order is order
    fleet.call("resume_robot", robot_id=carrier.robot_id)
    fleet.run(60)
    assert order.status == OrderStatus.DELIVERED


def test_resuming_a_robot_that_is_not_paused_is_an_error(fleet):
    assert "is not paused" in fleet.call("resume_robot", robot_id="R1")["error"]


# ----- closing aisles -----

def test_nobody_drives_into_a_closed_aisle(fleet):
    answer = fleet.call("close_aisle", aisle="aisle bc", reason="spill")
    cells = {tuple(c) for c in answer["cells"]}
    assert answer["aisle"] == "BC" and len(cells) == 5 and answer["shelves_cut_off"] == []
    for _ in range(150):
        fleet.run(1)
        for robot in fleet.sim.warehouse.robots.values():
            if robot.position in cells:
                assert robot.robot_id in answer["robots_inside"]        # only robots that were inside
    assert fleet.sim.kpis()["collisions"] == 0
    assert fleet.call("reopen_aisle", aisle="BC")["closed"] is False
    assert not cells & fleet.sim.warehouse.blocked


def test_closing_both_aisles_of_a_shelf_row_reports_the_cut_off_shelves(fleet):
    fleet.call("close_aisle", aisle="A")
    answer = fleet.call("close_aisle", aisle="AB")
    assert answer["shelves_cut_off"] == ["A2", "A3", "A4"]     # A1 and A5 can be reached from the side
    assert "can't be picked" in answer["note"]


def test_a_closed_aisle_stays_closed_when_a_random_block_there_ends(fleet):
    cell = (5, 3)
    fleet.sim.warehouse.block_cell(cell)
    fleet.sim.blocked_until[cell] = fleet.sim.tick + 2           # a fallen box, cleared in 2 ticks
    fleet.call("close_aisle", aisle="BC")
    fleet.run(5)
    assert cell in fleet.sim.warehouse.blocked


@pytest.mark.parametrize("aisle", ["XY", "row 5", ""])
def test_a_wrong_aisle_lists_the_real_ones(fleet, aisle):
    error = fleet.call("close_aisle", aisle=aisle)["error"]
    assert "There is no aisle" in error and "BC (next to B1, C5)" in error


# ----- the forecast -----

def test_the_forecast_gives_every_opening_hour_and_the_peak():
    fleet = Fleet("realistic_friday")
    answer = fleet.call("get_forecast")
    assert answer["date"] == "2026-10-02" and answer["weekday"] == "Friday"
    assert [h["hour"] for h in answer["hours"]] == [f"{h:02d}:00" for h in range(6, 22)]
    assert 80 < answer["expected_orders_total"] < 250
    assert answer["peak_hour"] in {h["hour"] for h in answer["hours"]}
    assert answer["capacity_per_hour_estimate"] == 10.5


@pytest.mark.parametrize("date, error", [
    ("next friday", "is not a date"),
    ("2026-06-10", "is in the order history already"),
    ("2027-01-01", "only looks up to 14 days"),
])
def test_a_wrong_forecast_date_gives_a_clear_error(fleet, date, error):
    assert error in fleet.call("get_forecast", date=date)["error"]


# ----- the day and MQTT -----

def test_nothing_can_be_changed_after_the_day_is_over():
    fleet = Fleet("realistic_friday")
    while not fleet.session.finished:
        fleet.run(50)
    for tool, arguments in (("create_order", {"item": "milk"}), ("pause_robot", {"robot_id": "R1"}),
                            ("close_aisle", {"aisle": "BC"})):
        assert "The working day is over" in fleet.call(tool, **arguments)["error"]
    assert "robots" in fleet.call("get_fleet_status")          # reading still works


def test_over_mqtt_a_paused_robot_gets_the_vda5050_pause_message():
    from scripts.fleet_tools import FleetTools
    session = LiveSession(LAYOUT, scenario="calm", broker=InMemoryBroker(), link_options={"state_timeout": 0})
    tools = FleetTools(session)
    session.step()
    tools.pause_robot("R2", "test")
    session.step()
    assert session.agents["R2"].paused is True
    tools.resume_robot("R2")
    session.step()
    assert session.agents["R2"].paused is False
    for _ in range(60):
        session.step()
    assert session.link.mismatches == 0
    session.close()
