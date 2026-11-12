"""
Tests for traffic control: reservations, cooperative A*, deadlocks, waiting zones,
and the 20-robot stress test.
"""

from scripts.classes import Warehouse, Robot
from scripts.layout import load_layout, add_robots
from scripts.movement import distance_table, manhattan
from scripts.simulation import Simulation, SimConfig
from scripts.traffic import ReservationTable, cooperative_astar, find_wait_cycles

MAIN_LAYOUT = "layouts/main_warehouse.json"


def corridor():
    """
    A 1-wide corridor with a side pocket:
        # # # # # # #
        # . . . . . #      row 1: the corridor
        # # # . # # #      row 2: a pocket at (2, 3)
        # # # # # # #
    """
    wh = Warehouse(height=4, width=7)
    for c in range(7):
        wh.add_wall((2, c))
    wh.grid[2][3] = "."
    return wh


# ---------------------------------------------------------------------------
# Reservation table
# ---------------------------------------------------------------------------

def test_reserved_cell_is_not_free_for_others():
    table = ReservationTable()
    table.reserve("R1", (1, 1), 5)
    assert table.is_free((1, 1), 5, "R1") is True
    assert table.is_free((1, 1), 5, "R2") is False
    assert table.is_free((1, 1), 6, "R2") is True


def test_reserve_path_books_every_step_and_holds_the_end():
    table = ReservationTable()
    table.reserve_path("R1", (1, 1), 0, [(1, 2), (1, 3)], hold=2)
    assert table.owner((1, 2), 1) == "R1"
    assert table.owner((1, 3), 2) == "R1"
    assert table.owner((1, 3), 4) == "R1"        # held 2 ticks after arriving


def test_swap_is_detected():
    table = ReservationTable()
    table.reserve_path("R1", (1, 1), 0, [(1, 2)])
    # R2 wants to move from (1, 2) to (1, 1) at the same time: they would pass through each other
    assert table.swap_conflict((1, 2), (1, 1), 0, "R2") is True


def test_release_removes_all_bookings():
    table = ReservationTable()
    table.reserve_path("R1", (1, 1), 0, [(1, 2), (1, 3)])
    table.release("R1")
    assert table.owner((1, 2), 1) is None
    assert table.owner((1, 3), 2) is None


def test_prune_forgets_the_past():
    table = ReservationTable()
    table.reserve("R1", (1, 1), 3)
    table.reserve("R1", (1, 1), 10)
    table.prune(5)
    assert table.owner((1, 1), 3) is None
    assert table.owner((1, 1), 10) == "R1"


# ---------------------------------------------------------------------------
# Cooperative A*
# ---------------------------------------------------------------------------

def test_cooperative_astar_without_bookings_is_shortest():
    wh = load_layout(MAIN_LAYOUT)
    dist = distance_table(wh)
    path = cooperative_astar(wh, (10, 4), (1, 1), 0, ReservationTable(), "R1", dist, window=40)
    assert path[-1] == (1, 1)
    assert len(path) == dist[(10, 4)][(1, 1)]


def test_cooperative_astar_waits_for_a_booked_cell():
    wh = corridor()
    dist = distance_table(wh)
    table = ReservationTable()
    table.reserve("R2", (1, 2), 1)               # R2 passes through (1, 2) at tick 1
    path = cooperative_astar(wh, (1, 1), (1, 5), 0, table, "R1", dist)
    assert path[0] == (1, 1)                     # so R1 waits one tick first
    assert path[-1] == (1, 5)


def test_cooperative_astar_uses_the_pocket_to_let_another_robot_pass():
    wh = corridor()
    dist = distance_table(wh)
    table = ReservationTable()
    # R2 waits 2 ticks at the right end, then drives all the way to (1, 1), R1's start cell
    table.reserve_path("R2", (1, 5), 0, [(1, 5), (1, 5), (1, 4), (1, 3), (1, 2), (1, 1)])
    # R1 starts at (1, 1) and must end up at (1, 5): it has to get out of R2's way
    path = cooperative_astar(wh, (1, 1), (1, 5), 0, table, "R1", dist, window=20)
    assert path is not None and path[-1] == (1, 5)
    assert (2, 3) in path                        # it waited in the pocket
    for tick, cell in enumerate(path, start=1):
        assert table.is_free(cell, tick, "R1")


def test_cooperative_path_steps_are_moves_or_waits():
    wh = load_layout(MAIN_LAYOUT)
    dist = distance_table(wh)
    path = cooperative_astar(wh, (10, 4), (5, 14), 0, ReservationTable(), "R1", dist, window=30)
    cells = [(10, 4)] + path
    assert all(manhattan(a, b) <= 1 for a, b in zip(cells, cells[1:]))


def test_robots_never_drive_through_stations():
    wh = load_layout(MAIN_LAYOUT)
    dist = distance_table(wh)
    # going from above the chargers to below the packing stations along the wall
    path = cooperative_astar(wh, (1, 14), (7, 15), 0, ReservationTable(), "R1", dist, window=30)
    for cell in path[:-1]:
        assert wh.cell_type(cell) not in ("C", "P", "D")


# ---------------------------------------------------------------------------
# Deadlocks
# ---------------------------------------------------------------------------

def test_two_robots_waiting_on_each_other_is_a_cycle():
    assert find_wait_cycles({"R1": "R2", "R2": "R1"}) == [["R1", "R2"]]


def test_three_robot_circle_is_found():
    assert find_wait_cycles({"R1": "R2", "R2": "R3", "R3": "R1"}) == [["R1", "R2", "R3"]]


def test_a_chain_is_not_a_cycle():
    assert find_wait_cycles({"R1": "R2", "R2": "R3"}) == []


def test_cycle_found_even_with_a_tail():
    # R4 waits for R1, and R1/R2 wait on each other
    assert find_wait_cycles({"R4": "R1", "R1": "R2", "R2": "R1"}) == [["R1", "R2"]]


# ---------------------------------------------------------------------------
# Waiting zones
# ---------------------------------------------------------------------------

def test_holding_cells_are_away_from_shelves_and_stations():
    wh = load_layout(MAIN_LAYOUT)
    sim = Simulation(wh, seed=1)
    stations = wh.cells_of_type("C") + wh.cells_of_type("P")
    for r, c in sim.holding_cells:
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                assert wh.cell_type((r + dr, c + dc)) != "S"
        assert all(max(abs(r - sr), abs(c - sc)) > 2 for sr, sc in stations)


def test_only_a_few_robots_approach_a_packing_station_at_once():
    wh = add_robots(load_layout(MAIN_LAYOUT), 20)
    sim = Simulation(wh, seed=2, config=SimConfig(order_rate=0.5))
    for _ in range(600):
        sim.step()
        for station in sim.packing:
            approaching = [r for r in wh.robots.values()
                           if r.task == "to_dropoff" and r.target == station]
            assert len(approaching) <= sim.config.station_admit


# ---------------------------------------------------------------------------
# Checkpoint: the stress test
# ---------------------------------------------------------------------------

def stress_run(seed, movement="cooperative", ticks=2000):
    wh = add_robots(load_layout(MAIN_LAYOUT), 20)
    sim = Simulation(wh, seed=seed, config=SimConfig(movement=movement, order_rate=0.5))
    sim.run(ticks)
    return sim


def test_add_robots_makes_20_robots():
    wh = add_robots(load_layout(MAIN_LAYOUT), 20)
    assert len(wh.robots) == 20
    assert len({r.position for r in wh.robots.values()}) == 20


def test_stress_test_20_robots_2000_ticks():
    sim = stress_run(seed=1)
    k = sim.kpis()
    assert k["collisions"] == 0
    # "no permanent deadlock": no robot that wants to drive is stuck for long
    assert k["longest_wait"] < 100
    assert k["orders_delivered"] > 300


def test_cooperative_beats_plain_astar_in_the_stress_test():
    smart = stress_run(seed=2, ticks=1000).kpis()
    plain = stress_run(seed=2, movement="astar", ticks=1000).kpis()
    assert smart["orders_delivered"] > plain["orders_delivered"]
    assert smart["stuck_steps"] < plain["stuck_steps"]