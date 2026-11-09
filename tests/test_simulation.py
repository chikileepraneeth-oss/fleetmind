"""
Tests for week 3: order generator, movement, and the running simulation.

The most important one is the week 3 checkpoint:
the same seed must always give exactly the same run.
"""

import random

from scripts.classes import (
    Warehouse, Robot, Shelf, Order, Priority,
    RobotStatus, OrderStatus, PACKING,
)
from scripts.layout import load_layout
from scripts.orders import OrderGenerator, DEADLINE_TICKS
from scripts.movement import manhattan, greedy_directions, greedy_step, nearest
from scripts.simulation import Simulation, SimConfig

MAIN_LAYOUT = "layouts/main_warehouse.json"


def run_sim(seed, ticks=500, config=None):
    wh = load_layout(MAIN_LAYOUT)
    sim = Simulation(wh, seed=seed, config=config)
    sim.run(ticks)
    return sim


# ---------------------------------------------------------------------------
# Week 3 checkpoint: repeatable runs
# ---------------------------------------------------------------------------

def test_same_seed_gives_identical_500_tick_runs():
    a = run_sim(seed=42)
    b = run_sim(seed=42)
    assert a.events == b.events
    assert a.kpis() == b.kpis()


def test_different_seeds_give_different_runs():
    a = run_sim(seed=42)
    b = run_sim(seed=7)
    assert a.events != b.events


# ---------------------------------------------------------------------------
# The simulation as a whole
# ---------------------------------------------------------------------------

def test_clock_advances_one_tick_per_step():
    wh = load_layout(MAIN_LAYOUT)
    sim = Simulation(wh, seed=1)
    sim.step()
    sim.step()
    sim.step()
    assert sim.tick == 3


def test_orders_get_delivered():
    sim = run_sim(seed=42)
    assert sim.kpis()["orders_delivered"] > 0


def test_order_counts_add_up():
    sim = run_sim(seed=42)
    k = sim.kpis()
    assert (k["orders_delivered"] + k["orders_waiting"]
            + k["orders_in_progress"] + k["orders_failed"]) == k["orders_created"]


def test_no_two_robots_ever_share_a_cell():
    wh = load_layout(MAIN_LAYOUT)
    sim = Simulation(wh, seed=3)
    for _ in range(500):
        sim.step()
        positions = [r.position for r in wh.robots.values()]
        assert len(positions) == len(set(positions))
        # the occupancy record must match the robots' real positions
        assert wh.occupied == {r.position: r.robot_id for r in wh.robots.values()}


def test_robots_never_stand_on_walls_shelves_or_blocked_cells():
    wh = load_layout(MAIN_LAYOUT)
    sim = Simulation(wh, seed=5)
    for _ in range(500):
        sim.step()
        for robot in wh.robots.values():
            assert wh.cell_type(robot.position) not in ("#", "S")
            assert robot.position not in wh.blocked


def test_battery_stays_between_0_and_100():
    wh = load_layout(MAIN_LAYOUT)
    sim = Simulation(wh, seed=9)
    for _ in range(1500):
        sim.step()
        for robot in wh.robots.values():
            assert 0 <= robot.battery <= 100


def test_robots_go_to_charge_in_a_long_run():
    sim = run_sim(seed=42, ticks=2000)
    event_names = [e[2] for e in sim.events]
    assert "going_to_charge" in event_names
    assert "charged" in event_names


def test_delivery_times_are_positive():
    sim = run_sim(seed=42)
    for order in sim.orders:
        if order.status == OrderStatus.DELIVERED:
            assert order.delivery_time() > 0


def test_log_has_tick_robot_event_details():
    sim = run_sim(seed=42, ticks=50)
    for event in sim.events:
        tick, robot, name, details = event
        assert isinstance(tick, int) and 1 <= tick <= 50
        assert isinstance(name, str) and name


def test_write_log_creates_csv(tmp_path):
    sim = run_sim(seed=42, ticks=50)
    path = tmp_path / "log.csv"
    sim.write_log(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "tick,robot,event,details"
    assert len(lines) == len(sim.events) + 1


def test_no_events_means_no_breakdowns_drops_or_blocks():
    calm = SimConfig(breakdown_chance=0, drop_chance=0, block_chance=0)
    sim = run_sim(seed=42, config=calm)
    k = sim.kpis()
    assert k["breakdowns"] == 0
    assert k["dropped_items"] == 0
    assert k["blocked_cells"] == 0


def test_first_free_robot_takes_oldest_order():
    calm = SimConfig(order_rate=0, breakdown_chance=0, drop_chance=0, block_chance=0)
    wh = load_layout(MAIN_LAYOUT)
    sim = Simulation(wh, seed=1, config=calm)
    first = sim.generator.create(tick=0)
    second = sim.generator.create(tick=0)
    sim.queue.extend([first, second])

    sim.step()

    robot_ids = sorted(wh.robots)
    assert wh.robots[robot_ids[0]].order is first
    assert wh.robots[robot_ids[1]].order is second


def test_dropped_item_puts_order_back_at_front_of_queue():
    calm = SimConfig(order_rate=0, breakdown_chance=0, drop_chance=0, block_chance=0)
    wh = load_layout(MAIN_LAYOUT)
    sim = Simulation(wh, seed=1, config=calm)
    robot = wh.robots[sorted(wh.robots)[0]]
    order = sim.generator.create(tick=0)
    robot.order = order
    robot.carrying = order.item
    robot.task = "to_dropoff"

    sim._drop_item(robot)

    assert sim.queue[0] is order
    assert order.status == OrderStatus.WAITING
    assert robot.carrying is None and robot.order is None


# ---------------------------------------------------------------------------
# Order generator
# ---------------------------------------------------------------------------

def make_generator(seed=1, rate=1.0):
    wh = load_layout(MAIN_LAYOUT)
    return wh, OrderGenerator(wh, random.Random(seed), order_rate=rate)


def test_generator_with_same_seed_makes_same_orders():
    _, gen_a = make_generator(seed=11)
    _, gen_b = make_generator(seed=11)
    orders_a = [gen_a.create(t) for t in range(20)]
    orders_b = [gen_b.create(t) for t in range(20)]
    assert [(o.item, o.dropoff, o.priority) for o in orders_a] == \
           [(o.item, o.dropoff, o.priority) for o in orders_b]


def test_order_ids_count_up():
    _, gen = make_generator()
    ids = [gen.create(0).order_id for _ in range(3)]
    assert ids == ["O-0001", "O-0002", "O-0003"]


def test_order_pickup_is_the_shelf_that_has_the_item():
    wh, gen = make_generator()
    for _ in range(30):
        order = gen.create(0)
        assert order.item in wh.get_shelf(order.pickup).items


def test_order_dropoff_is_a_packing_station():
    wh, gen = make_generator()
    stations = wh.cells_of_type(PACKING)
    for _ in range(30):
        assert gen.create(0).dropoff in stations


def test_deadline_depends_on_priority():
    _, gen = make_generator()
    for _ in range(30):
        order = gen.create(tick=10)
        assert order.deadline == 10 + DEADLINE_TICKS[order.priority]


def test_generator_skips_items_out_of_stock():
    wh, gen = make_generator()
    for shelf in wh.shelves.values():
        for item in shelf.items:
            shelf.items[item] = 0
    shelf_a = wh.get_shelf("A")
    shelf_a.items["apples"] = 1

    for _ in range(10):
        assert gen.create(0).item == "apples"


def test_generator_returns_none_when_nothing_in_stock():
    wh, gen = make_generator()
    for shelf in wh.shelves.values():
        for item in shelf.items:
            shelf.items[item] = 0
    assert gen.create(0) is None


def test_order_rate_zero_never_creates_orders():
    _, gen = make_generator(rate=0.0)
    assert all(gen.maybe_create(t) is None for t in range(100))


def test_order_rate_one_always_creates_orders():
    _, gen = make_generator(rate=1.0)
    assert all(gen.maybe_create(t) is not None for t in range(100))


# ---------------------------------------------------------------------------
# Movement
# ---------------------------------------------------------------------------

def test_manhattan_distance():
    assert manhattan((1, 1), (4, 5)) == 7
    assert manhattan((3, 3), (3, 3)) == 0


def test_greedy_tries_bigger_distance_first():
    assert greedy_directions((1, 1), (5, 2)) == ["down", "right"]
    assert greedy_directions((1, 1), (2, 6)) == ["right", "down"]
    assert greedy_directions((5, 5), (5, 1)) == ["left"]


def test_greedy_step_moves_closer():
    wh = Warehouse(height=7, width=7)
    robot = Robot("R1", position=(1, 1))
    wh.add_robot(robot)

    moved, _ = greedy_step(robot, (5, 1), wh)

    assert moved is True
    assert robot.position == (2, 1)


def test_greedy_step_goes_around_when_one_way_is_blocked():
    wh = Warehouse(height=7, width=7)
    wh.add_shelf(Shelf("A", (2, 1)))           # shelf directly below the robot
    robot = Robot("R1", position=(1, 1))
    wh.add_robot(robot)

    moved, direction = greedy_step(robot, (4, 3), wh)

    assert moved is True
    assert direction == "right"


def test_nearest_picks_closest_and_breaks_ties_the_same_way():
    assert nearest((0, 0), [(5, 5), (1, 2), (2, 1)]) == (1, 2)
    assert nearest((0, 0), []) is None