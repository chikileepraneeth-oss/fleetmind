"""
Tests for the fleet running over MQTT messages:
    scripts/robot_agent.py  - a robot that only follows VDA 5050-style messages
    scripts/fleet_link.py   - the fleet manager's side: orders out, states in, lost robots
All with the in-memory broker, so no Docker is needed. tests/test_mqtt_broker.py
does the same against a real Mosquitto broker (skipped if none is running).
"""

import pytest

from scripts import vda5050 as v
from scripts.bus import InMemoryBroker
from scripts.fleet_link import FleetLink, start_robots
from scripts.layout import load_layout
from scripts.record import build_simulation, snapshot
from scripts.robot_agent import RobotAgent
from scripts.classes import CHARGER, OrderStatus
from web.session import LiveSession

LAYOUT = "layouts/main_warehouse.json"


class FakeClock:
    """Seconds that only move when the test says so, so heartbeat tests are exact."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def fleet(key="normal", lost_after=3.0):
    broker = InMemoryBroker()
    sim = build_simulation(key, LAYOUT)
    agents = {a.serial: a for a in start_robots(broker, sorted(sim.warehouse.robots), LAYOUT, heartbeat=False)}
    clock = FakeClock()
    link = FleetLink(sim, broker, state_timeout=0, lost_after=lost_after, clock=clock)
    return broker, sim, agents, link, clock


def run(link, clock, ticks, seconds_per_tick=0.1):
    for _ in range(ticks):
        clock.now += seconds_per_tick
        link.step()


# ---------------------------------------------------------------------------
# One robot on its own
# ---------------------------------------------------------------------------

@pytest.fixture
def robot():
    broker = InMemoryBroker()
    agent = RobotAgent("R1", broker, LAYOUT).start()
    manager = broker.connect("manager")
    states = []
    manager.subscribe(v.topic("R1", "state"), lambda t, m: states.append(m))
    header = v.Header("R1")

    def send(kind, msg):
        manager.publish(v.topic("R1", kind), msg)

    def tick(n):
        manager.publish(v.CLOCK_TOPIC, {"tick": n})

    def init(cell, battery=80.0, load=""):
        pos = v.position(cell)
        send("instantActions", v.instant_actions(header, 0, [v.action(
            "initPosition", f"init{cell}", {"x": pos["x"], "y": pos["y"], "battery": battery, "load": load})]))

    return agent, header, send, tick, init, states, broker


def test_a_new_robot_is_online_but_does_not_know_where_it_is(robot):
    agent, header, send, tick, init, states, broker = robot
    connection = broker.retained[v.topic("R1", "connection")]
    assert connection["connectionState"] == "ONLINE"
    v.validate("state", agent.state_message())
    assert agent.state_message()["agvPosition"]["positionInitialized"] is False


def test_init_position_is_applied_at_once(robot):
    agent, header, send, tick, init, states, broker = robot
    init((1, 1), battery=55.0)
    last = states[-1]
    assert last["agvPosition"]["positionInitialized"] is True
    assert v.cell_at(last["agvPosition"]["x"], last["agvPosition"]["y"]) == (1, 1)
    assert last["batteryState"]["batteryCharge"] == 55.0


def test_robot_drives_only_the_released_nodes_one_tick_at_a_time(robot):
    agent, header, send, tick, init, states, broker = robot
    init((1, 1))
    send("order", v.order(header, 1, "R1-1", 0, [(1, 1), (1, 2), (1, 3), (1, 4)], released=2))
    assert agent.position == (1, 1)                     # nothing happens before the clock tick
    tick(1)
    assert agent.position == (1, 2)
    assert agent.battery == 79.5
    tick(2)
    assert agent.position == (1, 2)                     # (1, 3) is horizon: not allowed yet
    assert [n["nodeId"] for n in states[-1]["nodeStates"]] == ["n1_3", "n1_4"]
    send("order", v.order(header, 3, "R1-1", 1, [(1, 2), (1, 3), (1, 4)], released=2, first_sequence=2))
    tick(3)
    assert agent.position == (1, 3)
    assert states[-1]["lastNodeId"] == "n1_3" and states[-1]["driving"] is True


def test_an_old_order_update_is_ignored(robot):
    agent, header, send, tick, init, states, broker = robot
    init((1, 1))
    send("order", v.order(header, 1, "R1-1", 4, [(1, 1), (1, 2)], released=2))
    tick(1)
    send("order", v.order(header, 2, "R1-1", 4, [(1, 2), (1, 3)], released=2, first_sequence=2))
    tick(2)
    assert agent.position == (1, 2)


def test_a_new_order_that_does_not_start_here_is_rejected(robot):
    agent, header, send, tick, init, states, broker = robot
    init((1, 1))
    send("order", v.order(header, 1, "R1-1", 0, [(3, 1), (3, 2)], released=2))
    tick(1)
    assert agent.position == (1, 1)
    assert states[-1]["errors"][0]["errorType"] == "orderNoRoute"


def test_a_broken_message_is_reported_not_crashed_on(robot):
    agent, header, send, tick, init, states, broker = robot
    init((1, 1))
    send("order", {"orderId": "nonsense"})
    assert states[-1]["errors"][0]["errorType"] == "validationError"
    assert "missing 'headerId'" in states[-1]["errors"][0]["errorDescription"]


def test_pick_and_drop_actions_change_the_load(robot):
    agent, header, send, tick, init, states, broker = robot
    init((1, 1))
    pick = v.action("pick", "p1", {"item": "apples"})
    send("order", v.order(header, 1, "R1-2", 0, [(1, 1)], released=1, node_actions={0: [pick]}))
    tick(1)
    assert states[-1]["loads"] == [{"loadId": "apples", "loadType": "item"}]
    assert states[-1]["actionStates"][-1]["actionStatus"] == "FINISHED"
    drop = v.action("drop", "d1")
    send("order", v.order(header, 2, "R1-3", 0, [(1, 1)], released=1, node_actions={0: [drop]}))
    tick(2)
    assert states[-1]["loads"] == []


def test_charging_only_works_on_a_charger(robot):
    agent, header, send, tick, init, states, broker = robot
    init((1, 1), battery=50.0)
    send("instantActions", v.instant_actions(header, 1, [v.action("startCharging", "c1")]))
    tick(1)
    assert states[-1]["actionStates"][-1]["actionStatus"] == "FAILED"
    charger = load_layout(LAYOUT).cells_of_type(CHARGER)[0]
    init(charger, battery=97.0)
    send("instantActions", v.instant_actions(header, 2, [v.action("startCharging", "c2")]))
    tick(2)
    assert agent.battery == 100.0 and agent.charging is False       # full: stops by itself


def test_a_broken_robot_reports_an_error_and_does_not_drive(robot):
    agent, header, send, tick, init, states, broker = robot
    init((1, 1))
    send("instantActions", v.instant_actions(header, 1, [v.action("simBreakdown", "b1", {"repairTicks": 2})]))
    send("order", v.order(header, 1, "R1-1", 0, [(1, 1), (1, 2)], released=2))
    tick(1)
    assert agent.position == (1, 1)
    assert states[-1]["errors"][0]["errorType"] == "breakdown"
    tick(2)
    tick(3)
    assert agent.position == (1, 2)                     # repaired: continues its order
    assert states[-1]["errors"] == []


def test_stop_says_offline(robot):
    agent, header, send, tick, init, states, broker = robot
    agent.stop()
    assert broker.retained[v.topic("R1", "connection")]["connectionState"] == "OFFLINE"


# ---------------------------------------------------------------------------
# The whole fleet over messages
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", ["normal", "breakdown_day", "low_battery", "realistic_friday"])
def test_a_run_over_messages_is_exactly_the_same_run(key):
    """Same seed, same run: the robots do exactly what the manager predicted."""
    direct = build_simulation(key, LAYOUT)
    broker, sim, agents, link, clock = fleet(key)
    for _ in range(250):
        direct.step()
        link.step()
        assert snapshot(sim) == snapshot(direct)
    assert link.mismatches == 0
    assert sim.kpis() == direct.kpis()
    # and the robots themselves agree with the manager
    for rid, agent in agents.items():
        assert agent.position == sim.warehouse.robots[rid].position
        assert agent.battery == sim.warehouse.robots[rid].battery
        assert agent.load == sim.warehouse.robots[rid].carrying


def test_every_message_is_a_valid_vda5050_message():
    broker, sim, agents, link, clock = fleet("normal")
    run(link, clock, 150)
    kinds = set()
    for topic, msg in broker.messages("uagv/#"):
        serial, kind = v.parse_topic(topic)
        v.validate(kind, msg)
        assert msg["serialNumber"] == serial
        kinds.add(kind)
    assert kinds == {"order", "instantActions", "state", "connection"}


def test_orders_carry_the_route_ahead_as_horizon():
    broker, sim, agents, link, clock = fleet("normal")
    run(link, clock, 40)
    orders = [m for _, m in broker.messages("uagv/v2/FleetMind/+/order")]
    assert any(not n["released"] for m in orders for n in m["nodes"])
    assert any(m["orderUpdateId"] > 0 for m in orders)


def test_a_crashed_robot_is_lost_at_once_through_its_last_will():
    broker, sim, agents, link, clock = fleet("normal")
    run(link, clock, 60)
    busy = next(rid for rid, r in sorted(sim.warehouse.robots.items()) if r.order)
    order = sim.warehouse.robots[busy].order
    agents[busy].crash()
    run(link, clock, 1)
    assert busy in sim.lost
    assert sim.warehouse.robots[busy].task == "lost"
    # its order went back to the queue (and may already be given to another robot this tick)
    holders = [r.robot_id for r in sim.warehouse.robots.values() if r.order is order]
    assert busy not in holders
    assert order in sim.queue or holders
    assert any(e[2] == "robot_lost" and "last will" in e[3] for e in sim.events)


def test_a_silent_robot_is_lost_after_the_heartbeat_timeout():
    broker, sim, agents, link, clock = fleet("normal", lost_after=3.0)
    run(link, clock, 20)
    agents["R2"].go_silent()
    run(link, clock, 25, seconds_per_tick=0.1)          # 2.5 s: not yet
    assert "R2" not in sim.lost
    run(link, clock, 10, seconds_per_tick=0.1)          # 3.5 s
    assert "R2" in sim.lost
    assert any(e[2] == "robot_lost" and "no heartbeat" in e[3] for e in sim.events)


def test_while_paused_heartbeats_are_still_checked():
    broker, sim, agents, link, clock = fleet("normal")
    agents["R1"].go_silent()
    clock.now += 5
    assert link.check_heartbeats() is True
    assert "R1" in sim.lost
    assert link.link_status()["R1"]["link"] == "lost"


def test_the_fleet_keeps_working_around_a_lost_robot():
    broker, sim, agents, link, clock = fleet("calm")
    run(link, clock, 50)
    agents["R3"].crash()
    delivered = sim.kpis()["orders_delivered"]
    run(link, clock, 250)
    k = sim.kpis()
    assert k["orders_delivered"] > delivered + 10
    assert k["collisions"] == 0
    assert sim.warehouse.robots["R3"].order is None     # it never gets new work
    assert k["robots_lost"] == 1


@pytest.mark.parametrize("crash_at, where", [
    (100, "on a pick spot (3, 12)"),
    (280, "at (4, 14): the cell next to the chargers becomes a dead end"),
    (320, "on the packing station (5, 15)"),
])
def test_robots_do_not_wait_forever_for_a_lost_robot(crash_at, where):
    """
    Before: robots whose pick spot, packing station or only way out was the lost
    robot's cell waited there for the rest of the day. Now that cell counts as
    blocked: they pick from another spot, deliver to the other station, or drive
    through a station to get out.
    """
    broker, sim, agents, link, clock = fleet("normal")
    run(link, clock, crash_at)
    agents["R5"].crash()
    before = sim.kpis()["orders_delivered"]
    run(link, clock, 300)
    assert sim.kpis()["orders_delivered"] - before >= 35, where
    assert max(sim.no_progress.values()) < 30
    assert sim.kpis()["collisions"] == 0


def test_a_lost_robot_that_comes_back_rejoins_and_works_again():
    broker, sim, agents, link, clock = fleet("calm")
    run(link, clock, 30)
    agents["R1"].crash()
    run(link, clock, 5)
    assert "R1" in sim.lost
    restarted = RobotAgent("R1", broker, LAYOUT).start()   # a fresh process: doesn't know where it is
    run(link, clock, 1)
    assert "R1" not in sim.lost
    assert restarted.position == sim.warehouse.robots["R1"].position
    assert any(e[2] == "robot_found" for e in sim.events)
    run(link, clock, 200)
    assert sim.deliveries_by.get("R1", 0) > 0 or sim.warehouse.robots["R1"].order is not None
    assert link.mismatches == 0


def test_the_manager_believes_the_robot_when_they_disagree():
    broker, sim, agents, link, clock = fleet("calm")
    run(link, clock, 5)
    agents["R1"].battery = 42.0                        # e.g. a battery that is older than we thought
    run(link, clock, 1)
    assert link.mismatches >= 1
    assert any(e[2] == "state_mismatch" and e[1] == "R1" for e in sim.events)
    assert abs(sim.warehouse.robots["R1"].battery - agents["R1"].battery) < 1e-9


# ---------------------------------------------------------------------------
# The live session over messages
# ---------------------------------------------------------------------------

def test_live_session_over_mqtt_runs_the_same_day():
    direct = LiveSession(LAYOUT)
    s = LiveSession(LAYOUT, broker=InMemoryBroker(), link_options={"state_timeout": 0})
    assert s.hello_message()["transport"]["mode"] == "mqtt"
    assert direct.hello_message()["transport"] == {"mode": "local"}
    for _ in range(50):
        direct.step()
        s.step()
    assert s.sim.kpis() == direct.sim.kpis()
    robot = s.state_message()["robots"][0]
    assert robot["link"] == "online"
    s.close()


def test_live_session_shows_a_lost_robot():
    s = LiveSession(LAYOUT, broker=InMemoryBroker(), link_options={"state_timeout": 0})
    s.step()
    s.agents["R4"].crash()
    assert s.check_link() is True
    msg = s.state_message()
    r4 = next(r for r in msg["robots"] if r["id"] == "R4")
    assert r4["task"] == "lost" and r4["link"] == "lost"
    assert msg["kpis"]["robots_lost"] == 1
    assert msg["kpis"]["robots_broken"] == 0
    s.close()


def test_reset_over_mqtt_puts_the_robots_back():
    s = LiveSession(LAYOUT, broker=InMemoryBroker(), link_options={"state_timeout": 0})
    for _ in range(30):
        s.step()
    s.handle({"type": "reset"})
    for rid, agent in s.agents.items():
        assert agent.position == s.sim.warehouse.robots[rid].position
        assert agent.load is None
    s.handle({"type": "scenario", "key": "stress_20"})
    assert len(s.agents) == 20
    s.step()
    assert s.link.mismatches == 0
    s.close()
