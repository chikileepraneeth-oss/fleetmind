"""
Simple robot movement: the "greedy step".

Each tick, the robot tries to move one cell closer to its target.
It does NOT plan a route, so it can get stuck behind shelves or other
robots. That is expected: A* path planning (below) replaces this,
and we can measure how much better it is.
"""


def manhattan(a, b):
    """Grid distance between two cells: steps up/down plus steps left/right."""
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def greedy_directions(position, target):
    """
    Directions that bring the robot closer to the target, best first.
    The axis with the bigger distance is tried first.
    """
    d_row = target[0] - position[0]
    d_col = target[1] - position[1]

    vertical = "down" if d_row > 0 else "up"
    horizontal = "right" if d_col > 0 else "left"

    options = []
    if abs(d_row) >= abs(d_col):
        if d_row != 0:
            options.append(vertical)
        if d_col != 0:
            options.append(horizontal)
    else:
        if d_col != 0:
            options.append(horizontal)
        if d_row != 0:
            options.append(vertical)
    return options


def greedy_step(robot, target, warehouse):
    """
    Try to move the robot one cell closer to the target.
    Returns (moved, reason). moved is False when the robot is stuck this tick.
    """
    if robot.position == target:
        return False, "already at target"

    last_reason = "no direction"
    for direction in greedy_directions(robot.position, target):
        moved, reason = robot.move(direction, warehouse)
        if moved:
            return True, direction
        last_reason = reason
    return False, last_reason


def nearest(position, cells):
    """The cell closest to position. Ties are broken by (row, col) so runs repeat."""
    return min(cells, key=lambda cell: (manhattan(position, cell), cell)) if cells else None


# ---------------------------------------------------------------------------
# A* path planning
# ---------------------------------------------------------------------------
#
# A* finds the SHORTEST route from start to goal on the grid, going around
# walls, shelves and blocked cells. It explores cells in order of
#     f = g + h
#     g = steps already taken from the start
#     h = Manhattan distance still left to the goal (a guess that is never too big)
# so it looks at the most promising cells first and never needs to wobble.

import heapq

from scripts.classes import DIRECTIONS, DRIVABLE, CHARGER, PACKING, DOCK

STATIONS = {CHARGER, PACKING, DOCK}


def astar(warehouse, start, goal, avoid=None, through_stations=False):
    """
    Shortest path from start to goal as a list of cells, NOT including start.
    Example: start (1,1), goal (1,3)  ->  [(1, 2), (1, 3)]

    avoid: extra cells to treat as walls (e.g. where other robots stand now).
           The goal itself is never avoided.
    through_stations: may drive through chargers and packing stations (only to get
           out of a corner that a lost robot closed off).
    Returns [] if start == goal, or None if there is no way to get there.
    """
    if start == goal:
        return []
    avoid = (avoid or set()) - {goal}

    def passable(cell):
        return (warehouse.is_inside(cell)
                and warehouse.cell_type(cell) in DRIVABLE
                and cell not in warehouse.blocked
                and cell not in avoid
                and (cell == goal or through_stations or not is_station(warehouse, cell)))

    if not passable(goal):
        return None

    counter = 0                                  # tie-breaker so runs always repeat
    open_heap = [(manhattan(start, goal), 0, counter, start)]
    came_from = {start: None}
    best_g = {start: 0}

    while open_heap:
        _, g, _, cell = heapq.heappop(open_heap)
        if cell == goal:
            break
        if g > best_g[cell]:
            continue                             # an old, worse entry for this cell
        for d_row, d_col in DIRECTIONS.values():
            nxt = (cell[0] + d_row, cell[1] + d_col)
            if not passable(nxt):
                continue
            new_g = g + 1
            if new_g < best_g.get(nxt, float("inf")):
                best_g[nxt] = new_g
                came_from[nxt] = cell
                counter += 1
                heapq.heappush(open_heap, (new_g + manhattan(nxt, goal), new_g, counter, nxt))
    else:
        return None                              # heap ran empty: goal unreachable

    # walk back from the goal to the start
    path = []
    cell = goal
    while cell != start:
        path.append(cell)
        cell = came_from[cell]
    path.reverse()
    return path


def direction_to(position, next_cell):
    """Which direction gets from position to a neighbouring next_cell."""
    step = (next_cell[0] - position[0], next_cell[1] - position[1])
    for name, delta in DIRECTIONS.items():
        if delta == step:
            return name
    return None


# ---------------------------------------------------------------------------
# Distance table
# ---------------------------------------------------------------------------

from collections import deque


def distance_table(warehouse):
    """
    The true walking distance between every pair of drivable cells, going
    around walls and shelves (but ignoring robots and blocked cells).

    dist[a][b] = number of steps from a to b.

    It is computed once at the start with a breadth-first search (BFS) from
    every cell. After that, "how far is it?" is an instant lookup - much
    faster than running A* every time the fleet manager compares robots.
    """
    cells = [(r, c) for r in range(warehouse.height) for c in range(warehouse.width)
             if warehouse.cell_type((r, c)) in DRIVABLE]
    table = {}
    for start in cells:
        dist = {start: 0}
        queue = deque([start])
        while queue:
            cell = queue.popleft()
            if cell != start and is_station(warehouse, cell):
                continue                         # you can arrive at a station, but not drive through it
            for d_row, d_col in DIRECTIONS.values():
                nxt = (cell[0] + d_row, cell[1] + d_col)
                if nxt not in dist and warehouse.is_inside(nxt) \
                        and warehouse.cell_type(nxt) in DRIVABLE:
                    dist[nxt] = dist[cell] + 1
                    queue.append(nxt)
        table[start] = dist
    return table


def is_station(warehouse, cell):
    """
    Chargers, packing stations and docks are places robots STOP at.
    Robots never drive through them on the way somewhere else, so they don't
    block a robot that actually needs the station.
    """
    return warehouse.cell_type(cell) in STATIONS