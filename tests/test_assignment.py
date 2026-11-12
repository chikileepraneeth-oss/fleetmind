"""
Tests for smart assignment and smart charging.
"""

from scripts.classes import Order, Priority, RobotStatus
from scripts.layout import load_layout
from scripts.movement import distance_table
from scripts.simulation import Simulation, SimConfig
from scripts import assignment

MAIN_LAYOUT = "layouts/main_warehouse.json"
CALM = dict(order_rate=0, breakdown_chance=0, drop_chance=0, block_chance=0)


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
    return Simulation(wh, seed=1, config=SimConfig(**{**CALM, **config}))


def make_order(sim, item, priority=Priority.NORMAL, created_at=0, deadline=100):
    shelf = sim.warehouse.find_shelf_with(item)
    return Order(f"O-{item}", item, shelf.shelf_id, sim.packing[0], priority, created_at, deadline)


# ---------------------------------------------------------------------------
# Distance table
# ---------------------------------------------------------------------------

def test_distance_table_goes_around_shelves():
    wh = load_layout(MAIN_LAYOUT)
    dist = distance_table(wh)
    # (3, 4) and (5, 4) are 2 cells apart, but shelf row B is in between
    assert dist[(3, 4)][(5, 4)] > 2


def test_distance_table_is_symmetric():
    wh = load_layout(MAIN_LAYOUT)
    dist = distance_table(wh)
    assert dist[(1, 1)][(10, 12)] == dist[(10, 12)][(1, 1)]


# ---------------------------------------------------------------------------
# Smart assignment
# ---------------------------------------------------------------------------

def test_nearest_robot_gets_the_order():
    # apples are on A1 at (2, 2). R1 is right next to it, R2 is far away.
    sim = make_sim(robots={"R1": (3, 2), "R2": (10, 12)})
    order = make_order(sim, "apples")
    sim.queue.append(order)

    assignment.assign_smart(sim)

    assert sim.warehouse.robots["R1"].order is order
    assert sim.warehouse.robots["R2"].order is None


def test_first_free_gives_order_to_first_robot_even_if_far():
    sim = make_sim(robots={"R1": (10, 12), "R2": (3, 2)}, assignment="first_free")
    order = make_order(sim, "apples")
    sim.queue.append(order)

    assignment.assign_first_free(sim)

    assert sim.warehouse.robots["R1"].order is order       # far away, but first


def test_most_urgent_order_is_served_first():
    sim = make_sim(robots={"R1": (10, 4)})
    relaxed = make_order(sim, "apples", Priority.LOW, created_at=0, deadline=200)
    urgent = make_order(sim, "milk", Priority.HIGH, created_at=5, deadline=65)
    sim.queue.extend([relaxed, urgent])                    # relaxed arrived first

    assignment.assign_smart(sim)

    assert sim.warehouse.robots["R1"].order is urgent


def test_robot_without_enough_battery_does_not_get_the_order():
    sim = make_sim(robots={"R1": (3, 2), "R2": (10, 12)})
    sim.warehouse.robots["R1"].battery = 16.0              # next to the shelf but almost empty
    order = make_order(sim, "apples")
    sim.queue.append(order)

    assignment.assign_smart(sim)

    assert sim.warehouse.robots["R1"].order is None
    assert sim.warehouse.robots["R2"].order is order


def test_no_robot_with_enough_battery_means_order_waits():
    sim = make_sim(robots={"R1": (10, 4)})
    sim.warehouse.robots["R1"].battery = 16.0
    order = make_order(sim, "detergent")
    sim.queue.append(order)

    assignment.assign_smart(sim)

    assert order in sim.queue


def test_smart_assignment_sets_where_to_stand():
    sim = make_sim(robots={"R1": (10, 4)})
    sim.queue.append(make_order(sim, "apples"))

    assignment.assign_smart(sim)

    robot = sim.warehouse.robots["R1"]
    shelf = sim.warehouse.get_shelf("A1")
    assert robot.target in sim.warehouse.access_cells(shelf)


# ---------------------------------------------------------------------------
# Smart charging
# ---------------------------------------------------------------------------

def test_low_battery_robot_goes_to_charge():
    sim = make_sim(robots={"R1": (10, 4), "R2": (10, 6)})
    sim.warehouse.robots["R1"].battery = 20.0

    assignment.charge_smart(sim)

    assert sim.warehouse.robots["R1"].task == "to_charger"
    assert sim.warehouse.robots["R2"].task is None


def test_top_up_only_when_nothing_is_waiting():
    sim = make_sim(robots={"R1": (10, 4), "R2": (10, 6)})
    sim.warehouse.robots["R1"].battery = 50.0
    sim.queue.append(make_order(sim, "apples"))

    assignment.charge_smart(sim)

    assert sim.warehouse.robots["R1"].task is None          # orders are waiting: no top-up


def test_never_all_robots_top_up_at_once():
    sim = make_sim()                                          # 5 robots
    for robot in sim.warehouse.robots.values():
        robot.battery = 50.0                                  # all could use a top-up

    assignment.charge_smart(sim)

    charging = [r for r in sim.warehouse.robots.values() if r.task == "to_charger"]
    available = len(sim.warehouse.robots) - len(charging)
    assert available >= round(len(sim.warehouse.robots) * sim.config.min_available_share)


def test_no_more_robots_charge_than_there_are_chargers():
    sim = make_sim()
    for robot in sim.warehouse.robots.values():
        robot.battery = 10.0                                  # everyone needs a charger

    assignment.charge_smart(sim)

    going = [r for r in sim.warehouse.robots.values() if r.task == "to_charger"]
    assert len(going) <= len(sim.chargers)
    assert len({r.target for r in going}) == len(going)       # each to a different charger


def test_robot_leaves_charger_early_when_orders_pile_up():
    # a smart-charging rule (with opportunity charging, robots on a charger just take the order)
    sim = make_sim(robots={"R1": (10, 4)}, charging="smart")
    robot = sim.warehouse.robots["R1"]
    robot.task = "charging"
    robot.battery = 70.0
    sim.queue.extend([make_order(sim, "apples"), make_order(sim, "milk")])

    assert assignment.should_leave_charger_early(sim, robot) is True


def test_carrying_robot_with_low_battery_charges_then_continues():
    sim = make_sim(robots={"R1": (10, 4)})
    robot = sim.warehouse.robots["R1"]
    order = make_order(sim, "apples")
    robot.order = order
    robot.carrying = "apples"
    robot.status = RobotStatus.CARRYING
    robot.task = "to_dropoff"
    robot.target = tuple(order.dropoff)
    robot.battery = 6.0                                       # not enough to deliver and reach a charger

    assignment.battery_safety(sim)

    assert robot.task in ("to_charger", "wait_charger")
    assert robot.order is order and robot.carrying == "apples"   # keeps the item
    assert sim.resume["R1"][0] == "to_dropoff"                    # will continue later


# ---------------------------------------------------------------------------
# Checkpoint: v2 beats v1 on average delivery time
# ---------------------------------------------------------------------------

def average_delivery(config):
    times = []
    for seed in range(1, 11):
        wh = load_layout(MAIN_LAYOUT)
        sim = Simulation(wh, seed=seed, config=config)
        sim.run(500)
        times.append(sim.kpis()["avg_delivery_time"])
    return sum(times) / len(times)


def test_smart_assignment_beats_first_free_on_delivery_time():
    v1 = SimConfig(movement="astar", assignment="first_free", charging="simple")
    v2 = SimConfig(movement="astar", assignment="smart", charging="smart")
    assert average_delivery(v2) < average_delivery(v1)