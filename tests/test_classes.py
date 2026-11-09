"""
Tests for the FleetMind warehouse rules (scripts/classes.py).

Run from the fleetmind folder with:
    pytest

Every test follows the same three steps:
    1. set up a small situation
    2. do one action
    3. check the result with assert
"""

import pytest

from scripts.classes import (
    Warehouse,
    Robot,
    Shelf,
    Order,
    RobotStatus,
    OrderStatus,
    Priority,
    CHARGER,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
# A small reusable warehouse, so every test doesn't repeat the same setup.
#
#   row 0:  # # # # # # #
#   row 1:  # . . . . . #
#   row 2:  # . S . . . #     S = shelf A1 at (2, 2), 3 apples
#   row 3:  # . . . . . #
#   row 4:  # . . . . P #     P = packing station at (4, 5)
#   row 5:  # # # # # # #

def make_warehouse():
    wh = Warehouse(height=6, width=7)
    wh.add_shelf(Shelf("A1", (2, 2), {"apples": 3}))
    wh.add_packing_station((4, 5))
    return wh


def make_order(created_at=0, deadline=50):
    return Order(
        "O-0001",
        item="apples",
        pickup="A1",
        dropoff=(4, 5),
        priority=Priority.NORMAL,
        created_at=created_at,
        deadline=deadline,
    )


# ---------------------------------------------------------------------------
# Warehouse
# ---------------------------------------------------------------------------

def test_outer_edge_is_walls():
    wh = make_warehouse()
    assert wh.cell_type((0, 0)) == "#"
    assert wh.cell_type((0, 3)) == "#"
    assert wh.cell_type((5, 6)) == "#"
    assert wh.cell_type((3, 0)) == "#"


def test_inside_cells_start_as_floor():
    wh = make_warehouse()
    assert wh.cell_type((1, 1)) == "."


def test_cannot_place_robot_on_a_shelf():
    wh = make_warehouse()
    robot = Robot("R1", position=(2, 2))     # (2, 2) is shelf A1
    with pytest.raises(ValueError):
        wh.add_robot(robot)


def test_cannot_place_two_robots_in_same_cell():
    wh = make_warehouse()
    wh.add_robot(Robot("R1", position=(1, 1)))
    with pytest.raises(ValueError):
        wh.add_robot(Robot("R2", position=(1, 1)))


def test_is_adjacent_only_up_down_left_right():
    wh = make_warehouse()
    assert wh.is_adjacent((3, 3), (2, 3)) is True     # up
    assert wh.is_adjacent((3, 3), (3, 4)) is True     # right
    assert wh.is_adjacent((3, 3), (2, 2)) is False    # diagonal
    assert wh.is_adjacent((3, 3), (3, 3)) is False    # same cell
    assert wh.is_adjacent((3, 3), (1, 3)) is False    # two cells away


# ---------------------------------------------------------------------------
# Robot movement
# ---------------------------------------------------------------------------

def test_robot_moves_onto_free_floor():
    wh = make_warehouse()
    robot = Robot("R1", position=(1, 1))
    wh.add_robot(robot)

    success, reason = robot.move("right", wh)

    assert success is True
    assert robot.position == (1, 2)


def test_robot_cannot_move_into_wall():
    wh = make_warehouse()
    robot = Robot("R1", position=(1, 1))
    wh.add_robot(robot)

    success, reason = robot.move("up", wh)    # (0, 1) is a wall

    assert success is False
    assert reason == "wall"
    assert robot.position == (1, 1)


def test_robot_cannot_move_into_shelf():
    wh = make_warehouse()
    robot = Robot("R1", position=(1, 2))
    wh.add_robot(robot)

    success, reason = robot.move("down", wh)  # (2, 2) is shelf A1

    assert success is False
    assert "shelf" in reason
    assert robot.position == (1, 2)


def test_robot_cannot_move_into_occupied_cell():
    wh = make_warehouse()
    r1 = Robot("R1", position=(1, 1))
    r2 = Robot("R2", position=(1, 2))
    wh.add_robot(r1)
    wh.add_robot(r2)

    success, reason = r1.move("right", wh)    # R2 is there

    assert success is False
    assert "R2" in reason
    assert r1.position == (1, 1)


def test_robot_cannot_leave_the_grid():
    # A warehouse whose edge is floor would let a robot walk off the map,
    # so we turn an edge cell into floor on purpose to test is_inside().
    wh = make_warehouse()
    wh.grid[0][1] = "."
    robot = Robot("R1", position=(0, 1))
    wh.add_robot(robot)

    success, reason = robot.move("up", wh)    # (-1, 1) is outside

    assert success is False
    assert reason == "outside the warehouse"


def test_unknown_direction_fails():
    wh = make_warehouse()
    robot = Robot("R1", position=(1, 1))
    wh.add_robot(robot)

    success, reason = robot.move("jump", wh)

    assert success is False
    assert robot.position == (1, 1)


def test_move_uses_battery():
    wh = make_warehouse()
    robot = Robot("R1", position=(1, 1), battery=100.0)
    wh.add_robot(robot)

    robot.move("right", wh)

    assert robot.battery == 100.0 - Robot.BATTERY_PER_STEP


def test_failed_move_does_not_use_battery():
    wh = make_warehouse()
    robot = Robot("R1", position=(1, 1), battery=100.0)
    wh.add_robot(robot)

    robot.move("up", wh)                      # into a wall

    assert robot.battery == 100.0


def test_robot_with_empty_battery_cannot_move():
    wh = make_warehouse()
    robot = Robot("R1", position=(1, 1), battery=0.0)
    wh.add_robot(robot)

    success, reason = robot.move("right", wh)

    assert success is False
    assert reason == "battery empty"
    assert robot.position == (1, 1)


def test_broken_robot_cannot_move():
    wh = make_warehouse()
    robot = Robot("R1", position=(1, 1))
    wh.add_robot(robot)
    robot.status = RobotStatus.ERROR

    success, reason = robot.move("right", wh)

    assert success is False
    assert robot.position == (1, 1)


def test_moving_frees_the_old_cell():
    wh = make_warehouse()
    r1 = Robot("R1", position=(1, 1))
    wh.add_robot(r1)

    r1.move("right", wh)                      # R1 leaves (1, 1)
    r2 = Robot("R2", position=(1, 1))
    wh.add_robot(r2)                          # should now be allowed

    assert wh.occupied[(1, 1)] == "R2"
    assert wh.occupied[(1, 2)] == "R1"


# ---------------------------------------------------------------------------
# Picking
# ---------------------------------------------------------------------------

def test_pick_without_order_fails():
    wh = make_warehouse()
    robot = Robot("R1", position=(3, 2))      # next to shelf A1
    wh.add_robot(robot)

    success, reason = robot.pick(wh)

    assert success is False
    assert reason == "no order assigned"


def test_pick_when_not_next_to_shelf_fails():
    wh = make_warehouse()
    robot = Robot("R1", position=(4, 4))      # far from shelf A1
    wh.add_robot(robot)
    robot.order = make_order()

    success, reason = robot.pick(wh)

    assert success is False
    assert robot.carrying is None


def test_pick_next_to_shelf_works():
    wh = make_warehouse()
    robot = Robot("R1", position=(3, 2))      # directly below shelf A1
    wh.add_robot(robot)
    order = make_order()
    robot.order = order

    success, reason = robot.pick(wh)

    assert success is True
    assert robot.carrying == "apples"
    assert robot.status == RobotStatus.CARRYING
    assert order.status == OrderStatus.PICKED_UP
    assert wh.get_shelf("A1").items["apples"] == 2   # was 3


def test_pick_while_already_carrying_fails():
    wh = make_warehouse()
    robot = Robot("R1", position=(3, 2))
    wh.add_robot(robot)
    robot.order = make_order()
    robot.pick(wh)                            # first pick works

    success, reason = robot.pick(wh)          # second pick should fail

    assert success is False
    assert "already carrying" in reason
    assert wh.get_shelf("A1").items["apples"] == 2   # only one taken


def test_pick_from_empty_shelf_fails():
    wh = make_warehouse()
    wh.get_shelf("A1").items["apples"] = 0
    robot = Robot("R1", position=(3, 2))
    wh.add_robot(robot)
    robot.order = make_order()

    success, reason = robot.pick(wh)

    assert success is False
    assert robot.carrying is None


# ---------------------------------------------------------------------------
# Delivering
# ---------------------------------------------------------------------------

def test_place_without_carrying_fails():
    wh = make_warehouse()
    robot = Robot("R1", position=(4, 5))      # on the packing station
    wh.add_robot(robot)

    success, reason = robot.place(wh, tick=10)

    assert success is False
    assert reason == "not carrying anything"


def test_place_when_not_at_dropoff_fails():
    wh = make_warehouse()
    robot = Robot("R1", position=(3, 2))
    wh.add_robot(robot)
    robot.order = make_order()
    robot.pick(wh)

    success, reason = robot.place(wh, tick=10)   # still next to the shelf

    assert success is False
    assert robot.carrying == "apples"


def test_full_delivery_works():
    wh = make_warehouse()
    robot = Robot("R1", position=(3, 2))
    wh.add_robot(robot)
    order = make_order(created_at=0)
    robot.order = order

    robot.pick(wh)
    # From (3, 2) to the packing station at (4, 5): 1 down, 3 right
    for direction in ["down", "right", "right", "right"]:
        ok, _ = robot.move(direction, wh)
        assert ok is True
    success, reason = robot.place(wh, tick=4)

    assert success is True
    assert order.status == OrderStatus.DELIVERED
    assert order.delivered_at == 4
    assert robot.carrying is None
    assert robot.order is None
    assert robot.status == RobotStatus.IDLE


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

def test_new_order_is_waiting():
    order = make_order()
    assert order.status == OrderStatus.WAITING
    assert order.delivered_at is None


def test_delivery_time_is_delivered_minus_created():
    order = make_order(created_at=10)
    order.mark_delivered(tick=35)
    assert order.delivery_time() == 25


def test_delivery_time_is_none_before_delivery():
    order = make_order()
    assert order.delivery_time() is None


def test_order_delivered_after_deadline_is_late():
    order = make_order(created_at=0, deadline=50)
    order.mark_delivered(tick=60)
    assert order.is_late(current_tick=60) is True


def test_order_delivered_before_deadline_is_not_late():
    order = make_order(created_at=0, deadline=50)
    order.mark_delivered(tick=40)
    assert order.is_late(current_tick=100) is False


def test_undelivered_order_past_deadline_is_late():
    order = make_order(created_at=0, deadline=50)
    assert order.is_late(current_tick=49) is False
    assert order.is_late(current_tick=51) is True


# ---------------------------------------------------------------------------
# Shelf
# ---------------------------------------------------------------------------

def test_shelves_do_not_share_items():
    # This is the items=None lesson: each shelf needs its own dictionary.
    a1 = Shelf("A1", (2, 3))
    a2 = Shelf("A2", (2, 4))

    a1.items["apples"] = 5

    assert a2.items == {}


def test_take_item_reduces_quantity():
    shelf = Shelf("A1", (2, 3), {"apples": 2})
    assert shelf.take_item("apples") is True
    assert shelf.items["apples"] == 1


def test_take_missing_item_fails():
    shelf = Shelf("A1", (2, 3), {"apples": 2})
    assert shelf.take_item("rice") is False



def test_charging_on_charger_increases_battery():
    wh = make_warehouse()
    wh.add_charger((1, 1))
    robot = Robot("R1", position=(1, 1), battery=50.0)
    wh.add_robot(robot)

    success, reason = robot.charge(wh)

    assert success is True
    assert robot.battery == 50.0 + Robot.CHARGE_PER_TICK