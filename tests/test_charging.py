"""
Tests for opportunity charging (SimConfig(charging="opportunity")).

Idea: robots with nothing to do top up their battery, so they are ready for
the busy hours. A robot on a charger can still take an order.
"""

from scripts.classes import Order, Priority, RobotStatus
from scripts.layout import load_layout
from scripts.simulation import Simulation, SimConfig
from scripts import assignment

MAIN_LAYOUT = "layouts/main_warehouse.json"
CALM = dict(order_rate=0, breakdown_chance=0, drop_chance=0, block_chance=0)
CHARGER = (1, 15)


def make_sim(robots=None, **config):
    wh = load_layout(MAIN_LAYOUT)
    if robots is not None:                       # keep only the robots we want, at chosen spots
        for robot_id in list(wh.robots):
            wh.occupied.pop(wh.robots[robot_id].position)
            if robot_id not in robots:
                del wh.robots[robot_id]
        for robot_id, position in robots.items():
            wh.robots[robot_id].position = position
            wh.occupied[position] = robot_id
    return Simulation(wh, seed=1, config=SimConfig(**{**CALM, "charging": "opportunity", **config}))


def make_order(sim, item):
    shelf = sim.warehouse.find_shelf_with(item)
    return Order(f"O-{item}", item, shelf.shelf_id, sim.packing[0], Priority.NORMAL, 0, 100)


def plug_in(robot, battery):
    """Put a robot on a charger, topping up."""
    robot.task = "charging"
    robot.target = robot.position
    robot.status = RobotStatus.CHARGING
    robot.battery = battery


# ---------------------------------------------------------------------------
# Who goes to top up
# ---------------------------------------------------------------------------

def test_idle_robot_that_is_not_full_goes_to_top_up():
    sim = make_sim(robots={"R1": (10, 4)})
    sim.warehouse.robots["R1"].battery = 80.0

    assignment.charge_opportunity(sim)

    assert sim.warehouse.robots["R1"].task == "to_charger"


def test_full_robot_waits_at_its_parking_spot():
    sim = make_sim(robots={"R1": (10, 4)})
    sim.warehouse.robots["R1"].battery = 95.0          # above top_up_full_below (90)

    assignment.charge_opportunity(sim)

    assert sim.warehouse.robots["R1"].task is None


def test_no_top_up_while_orders_are_waiting():
    sim = make_sim(robots={"R1": (10, 4)})
    sim.warehouse.robots["R1"].battery = 80.0
    sim.queue.append(make_order(sim, "apples"))

    assignment.charge_opportunity(sim)

    assert sim.warehouse.robots["R1"].task is None     # it should take the order instead


def test_low_battery_robot_charges_even_when_orders_wait():
    sim = make_sim(robots={"R1": (10, 4)})
    sim.warehouse.robots["R1"].battery = 20.0
    sim.queue.append(make_order(sim, "apples"))

    assignment.charge_opportunity(sim)

    assert sim.warehouse.robots["R1"].task == "to_charger"


def test_lowest_batteries_get_the_chargers_others_wait():
    sim = make_sim()                                   # 5 robots, 3 chargers
    batteries = {"R1": 85.0, "R2": 60.0, "R3": 80.0, "R4": 70.0, "R5": 88.0}
    for rid, battery in batteries.items():
        sim.warehouse.robots[rid].battery = battery

    assignment.charge_opportunity(sim)

    going = {rid for rid, r in sim.warehouse.robots.items() if r.task == "to_charger"}
    assert going == {"R2", "R4", "R3"}                 # the 3 lowest
    targets = [sim.warehouse.robots[rid].target for rid in going]
    assert len(set(targets)) == 3                      # each to its own charger


# ---------------------------------------------------------------------------
# Robots on a charger can still work
# ---------------------------------------------------------------------------

def test_robot_topping_up_takes_an_order():
    sim = make_sim(robots={"R1": CHARGER})
    robot = sim.warehouse.robots["R1"]
    plug_in(robot, 70.0)
    order = make_order(sim, "apples")
    sim.queue.append(order)

    assignment.assign_smart(sim)

    assert robot.order is order
    assert robot.task == "to_pickup"
    assert robot.status != RobotStatus.CHARGING        # unplugged
    assert any(e[2] == "left_charger_for_order" for e in sim.events)


def test_nearly_empty_robot_on_charger_keeps_charging():
    sim = make_sim(robots={"R1": CHARGER})
    robot = sim.warehouse.robots["R1"]
    plug_in(robot, 30.0)                               # below leave_charger_at (60)
    sim.queue.append(make_order(sim, "apples"))

    assignment.assign_smart(sim)

    assert robot.order is None
    assert robot.task == "charging"


def test_robot_charging_in_the_middle_of_a_delivery_gets_no_new_order():
    sim = make_sim(robots={"R1": CHARGER})
    robot = sim.warehouse.robots["R1"]
    plug_in(robot, 80.0)
    carried = make_order(sim, "milk")
    robot.order = carried
    robot.carrying = "milk"
    sim.resume["R1"] = ("to_dropoff", tuple(carried.dropoff))

    assert assignment.can_take_order(sim, robot) is False


def test_smart_charging_does_not_give_orders_to_charging_robots():
    sim = make_sim(robots={"R1": CHARGER}, charging="smart")
    robot = sim.warehouse.robots["R1"]
    plug_in(robot, 80.0)

    assert assignment.can_take_order(sim, robot) is False


def test_battery_safety_still_works_with_opportunity_charging():
    sim = make_sim(robots={"R1": (10, 4)})
    robot = sim.warehouse.robots["R1"]
    order = make_order(sim, "apples")
    robot.order = order
    robot.carrying = "apples"
    robot.status = RobotStatus.CARRYING
    robot.task = "to_dropoff"
    robot.target = tuple(order.dropoff)
    robot.battery = 6.0

    assignment.battery_safety(sim)

    assert robot.task in ("to_charger", "wait_charger")
    assert sim.resume["R1"][0] == "to_dropoff"


# ---------------------------------------------------------------------------
# The whole simulation
# ---------------------------------------------------------------------------

def run(seed, ticks=400):
    wh = load_layout(MAIN_LAYOUT)
    sim = Simulation(wh, seed=seed, config=SimConfig(charging="opportunity"))
    sim.run(ticks)
    return sim


def test_same_seed_gives_same_run_with_opportunity_charging():
    a, b = run(42), run(42)
    assert a.events == b.events
    assert a.kpis() == b.kpis()


def test_opportunity_charging_runs_safely():
    sim = run(7)
    k = sim.kpis()
    assert k["orders_delivered"] > 0
    assert k["collisions"] == 0
    assert any(e[2] == "left_charger_for_order" for e in sim.events)   # the new behaviour really happens
