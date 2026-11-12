"""
Tests for A* path planning and robots following their routes.
"""

from scripts.classes import Warehouse, Robot, Shelf
from scripts.layout import load_layout
from scripts.movement import astar, direction_to, manhattan
from scripts.simulation import Simulation, SimConfig

MAIN_LAYOUT = "layouts/main_warehouse.json"


def empty_warehouse():
    return Warehouse(height=7, width=7)          # 5x5 floor inside the walls


def is_connected(start, path):
    """Every step in the path must be exactly one cell from the one before."""
    cells = [start] + path
    return all(manhattan(a, b) == 1 for a, b in zip(cells, cells[1:]))


# ---------------------------------------------------------------------------
# A* itself
# ---------------------------------------------------------------------------

def test_path_on_empty_floor_is_as_short_as_possible():
    wh = empty_warehouse()
    path = astar(wh, (1, 1), (4, 5))
    assert len(path) == manhattan((1, 1), (4, 5))      # 7 steps
    assert path[-1] == (4, 5)


def test_path_does_not_include_the_start():
    wh = empty_warehouse()
    path = astar(wh, (1, 1), (1, 3))
    assert path == [(1, 2), (1, 3)]


def test_start_equals_goal_gives_empty_path():
    wh = empty_warehouse()
    assert astar(wh, (2, 2), (2, 2)) == []


def test_path_is_connected_step_by_step():
    wh = load_layout(MAIN_LAYOUT)
    path = astar(wh, (10, 4), (5, 14))
    assert is_connected((10, 4), path)


def test_path_goes_around_a_shelf_row():
    # shelf row B is between (3, 4) and (5, 4): the robot must go around its end
    wh = load_layout(MAIN_LAYOUT)
    path = astar(wh, (3, 4), (5, 4))
    assert path is not None
    assert len(path) > manhattan((3, 4), (5, 4))       # longer than straight down
    for cell in path:
        assert wh.cell_type(cell) not in ("#", "S")


def test_path_never_crosses_walls_or_shelves():
    wh = load_layout(MAIN_LAYOUT)
    path = astar(wh, (1, 1), (11, 14))
    for cell in path:
        assert wh.cell_type(cell) not in ("#", "S")


def test_path_avoids_blocked_cells():
    wh = empty_warehouse()
    wh.block_cell((1, 2))
    path = astar(wh, (1, 1), (1, 3))
    assert (1, 2) not in path
    assert len(path) == 4                               # down, right, right, up


def test_no_path_returns_none():
    wh = empty_warehouse()
    for cell in [(1, 2), (2, 1), (2, 2)]:               # wall in the corner (1, 1)
        wh.add_wall(cell)
    assert astar(wh, (1, 1), (5, 5)) is None


def test_goal_inside_a_shelf_returns_none():
    wh = empty_warehouse()
    wh.add_shelf(Shelf("A1", (3, 3)))
    assert astar(wh, (1, 1), (3, 3)) is None


def test_avoid_cells_are_treated_like_walls():
    wh = empty_warehouse()
    path = astar(wh, (1, 1), (1, 3), avoid={(1, 2)})
    assert (1, 2) not in path


def test_goal_is_allowed_even_if_in_avoid():
    wh = empty_warehouse()
    path = astar(wh, (1, 1), (1, 3), avoid={(1, 3)})
    assert path[-1] == (1, 3)


def test_astar_ignores_robots_unless_told_to_avoid_them():
    wh = empty_warehouse()
    wh.add_robot(Robot("R2", position=(1, 2)))
    path = astar(wh, (1, 1), (1, 3))
    assert path == [(1, 2), (1, 3)]                     # plans through; the robot waits if needed


def test_same_question_gives_same_path():
    wh = load_layout(MAIN_LAYOUT)
    assert astar(wh, (10, 4), (5, 14)) == astar(wh, (10, 4), (5, 14))


def test_direction_to_neighbour():
    assert direction_to((3, 3), (2, 3)) == "up"
    assert direction_to((3, 3), (4, 3)) == "down"
    assert direction_to((3, 3), (3, 2)) == "left"
    assert direction_to((3, 3), (3, 4)) == "right"
    assert direction_to((3, 3), (5, 5)) is None


# ---------------------------------------------------------------------------
# Robots using A* in the simulation
# ---------------------------------------------------------------------------

CALM = dict(breakdown_chance=0, drop_chance=0, block_chance=0)


def one_robot_sim(movement, ticks=300):
    wh = load_layout(MAIN_LAYOUT)
    for robot_id in sorted(wh.robots)[1:]:              # keep only the first robot
        robot = wh.robots.pop(robot_id)
        del wh.occupied[robot.position]
    sim = Simulation(wh, seed=42, config=SimConfig(movement=movement, order_rate=0.05, **CALM))
    sim.run(ticks)
    return sim


def test_single_robot_with_astar_delivers_orders():
    sim = one_robot_sim("astar")
    assert sim.kpis()["orders_delivered"] > 0


def test_single_robot_with_astar_never_gets_stuck():
    # alone in the warehouse, there is nothing to wait for
    sim = one_robot_sim("astar")
    assert sim.kpis()["stuck_steps"] == 0


def test_single_robot_astar_beats_greedy():
    greedy = one_robot_sim("greedy").kpis()
    smart = one_robot_sim("astar").kpis()
    assert smart["orders_delivered"] > greedy["orders_delivered"]


def test_astar_gets_stuck_less_than_greedy_with_5_robots():
    def stuck(movement):
        total = 0
        for seed in range(1, 6):
            wh = load_layout(MAIN_LAYOUT)
            sim = Simulation(wh, seed=seed, config=SimConfig(movement=movement, **CALM))
            sim.run(500)
            total += sim.kpis()["stuck_steps"]
        return total
    assert stuck("astar") < stuck("greedy")


def test_robot_follows_its_planned_route():
    wh = load_layout(MAIN_LAYOUT)
    for robot_id in sorted(wh.robots)[1:]:
        robot = wh.robots.pop(robot_id)
        del wh.occupied[robot.position]
    sim = Simulation(wh, seed=1, config=SimConfig(order_rate=0, **CALM))
    robot = wh.robots[sorted(wh.robots)[0]]
    robot.task = "to_home"
    robot.target = (1, 1)

    sim.step()                                          # plans and takes the first step
    planned = [robot.position] + list(robot.path)
    for expected in planned[1:]:
        sim.step()
        assert robot.position == expected


def test_idle_robot_goes_back_home():
    wh = load_layout(MAIN_LAYOUT)
    sim = Simulation(wh, seed=1, config=SimConfig(order_rate=0, **CALM))
    robot = wh.robots[sorted(wh.robots)[0]]
    home = robot.position
    wh.move_robot(robot, (1, 1))                        # put it somewhere else

    sim.run(60)

    assert robot.position == home
    assert robot.task is None


def test_robot_on_any_charger_charges_there():
    wh = load_layout(MAIN_LAYOUT)
    sim = Simulation(wh, seed=1, config=SimConfig(order_rate=0, **CALM))
    robot = wh.robots[sorted(wh.robots)[0]]
    wh.move_robot(robot, (3, 15))                       # a charger
    robot.battery = 10.0
    robot.task = "to_charger"
    robot.target = (1, 15)                              # a different charger

    sim.step()

    assert robot.task == "charging"
    assert robot.battery > 10.0