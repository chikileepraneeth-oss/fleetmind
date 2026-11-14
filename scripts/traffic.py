"""
Traffic control - reservations and deadlocks.

1. RESERVATIONS (cooperative A*)
   Normal A* only knows where walls and shelves are. Two robots can plan
   routes through the same cell at the same moment, and only find out when
   they bump into each other.

   Here every robot books ("reserves") the cells it will use, tick by tick:
       (cell, tick) -> robot
   The next robot to plan must respect those bookings. Its planner can also
   WAIT in a cell for a tick, so it plans things like "wait 2 ticks here,
   then go", instead of driving into another robot.

   Planning is done in time too (space-time A*): a state is (cell, tick).
   Robots only plan a few ticks ahead (a "window"), then plan again. That
   keeps planning fast and lets plans adapt to what other robots do.

2. DEADLOCKS
   Even with bookings, robots can end up waiting on each other in a circle:
       R1 waits for R2, R2 waits for R3, R3 waits for R1  -> nobody ever moves.
   Each tick we build a "wait-for" list (who is blocked by whom) and look
   for circles. If we find one, the least important robot backs off one
   cell and plans again, which breaks the circle.
"""

import heapq

from scripts.classes import DIRECTIONS, DRIVABLE
from scripts.movement import is_station


class ReservationTable:
    """Who has booked which cell at which tick."""

    def __init__(self):
        self.cells = {}        # (cell, tick) -> robot_id
        self.moves = {}        # (from_cell, to_cell, tick) -> robot_id   (move from tick to tick+1)
        self.by_robot = {}     # robot_id -> list of keys it owns, so we can release them

    def owner(self, cell, tick):
        return self.cells.get((cell, tick))

    def is_free(self, cell, tick, robot_id):
        owner = self.cells.get((cell, tick))
        return owner is None or owner == robot_id

    def swap_conflict(self, from_cell, to_cell, tick, robot_id):
        """Would we swap places with another robot? (Two robots can't pass through each other.)"""
        owner = self.moves.get((to_cell, from_cell, tick))
        return owner is not None and owner != robot_id

    def reserve(self, robot_id, cell, tick):
        key = (cell, tick)
        if self.cells.get(key) not in (None, robot_id):
            return False
        self.cells[key] = robot_id
        self.by_robot.setdefault(robot_id, []).append(("c", key))
        return True

    def reserve_path(self, robot_id, start_cell, start_tick, path, hold=2):
        """
        Book a planned path. path[i] is the cell for tick start_tick + 1 + i.
        After the last step, the final cell is held for `hold` more ticks
        (the robot usually stops there to pick, deliver or charge).
        """
        prev = start_cell
        for i, cell in enumerate(path):
            tick = start_tick + 1 + i
            self.reserve(robot_id, cell, tick)
            self.moves[(prev, cell, tick - 1)] = robot_id
            self.by_robot.setdefault(robot_id, []).append(("m", (prev, cell, tick - 1)))
            prev = cell
        last_tick = start_tick + len(path)
        for extra in range(1, hold + 1):
            self.reserve(robot_id, prev, last_tick + extra)

    def reserve_stay(self, robot_id, cell, from_tick, to_tick):
        """A robot that isn't moving books its own cell for a while."""
        for tick in range(from_tick, to_tick + 1):
            self.reserve(robot_id, cell, tick)

    def release(self, robot_id):
        for kind, key in self.by_robot.pop(robot_id, []):
            table = self.cells if kind == "c" else self.moves
            if table.get(key) == robot_id:
                del table[key]

    def prune(self, now):
        """Forget bookings in the past."""
        for key in [k for k in self.cells if k[1] < now]:
            del self.cells[key]
        for key in [k for k in self.moves if k[2] < now - 1]:
            del self.moves[key]
        for robot_id in list(self.by_robot):
            self.by_robot[robot_id] = [
                (kind, key) for kind, key in self.by_robot[robot_id]
                if (key[1] if kind == "c" else key[2]) >= now - 1
            ]


def cooperative_astar(warehouse, start, goal, start_tick, table, robot_id, dist,
                      window=16, max_expansions=4000, through_stations=False):
    """
    Space-time A*: plan from `start` (where the robot is at start_tick) towards
    `goal`, respecting other robots' bookings. Waiting in place is allowed.

    Returns a list of cells for ticks start_tick+1, start_tick+2, ...
    (a cell can repeat = waiting). It ends at the goal, or - if the goal is
    further than `window` ticks away - at the best cell reached within the
    window. Returns None if the goal can't be reached at all.
    """
    to_goal = dist.get(goal)
    if to_goal is None or start not in to_goal:
        return None
    if start == goal:
        return []

    def passable(cell):
        return (warehouse.is_inside(cell)
                and warehouse.cell_type(cell) in DRIVABLE
                and cell not in warehouse.blocked
                and (cell == goal or through_stations or not is_station(warehouse, cell)))

    moves = list(DIRECTIONS.values()) + [(0, 0)]            # 4 moves + wait
    counter = 0
    start_state = (start, start_tick)
    heap = [(to_goal[start], 0, counter, start, start_tick)]
    came_from = {start_state: None}
    best_g = {start_state: 0}
    best_partial = None                                     # (h, g, state) at the window edge
    expansions = 0

    while heap and expansions < max_expansions:
        _, g, _, cell, tick = heapq.heappop(heap)
        state = (cell, tick)
        if g > best_g.get(state, float("inf")):
            continue
        expansions += 1

        if cell == goal:
            return _rebuild(came_from, state)

        if tick - start_tick >= window:
            candidate = (to_goal.get(cell, 10**6), g, state)
            if best_partial is None or candidate < best_partial:
                best_partial = candidate
            continue

        for d_row, d_col in moves:
            nxt = (cell[0] + d_row, cell[1] + d_col)
            if nxt != cell and not passable(nxt):
                continue
            if nxt not in to_goal:
                continue
            nt = tick + 1
            if not table.is_free(nxt, nt, robot_id):
                continue
            if nxt != cell and table.swap_conflict(cell, nxt, tick, robot_id):
                continue
            new_state = (nxt, nt)
            new_g = g + 1
            if new_g < best_g.get(new_state, float("inf")):
                best_g[new_state] = new_g
                came_from[new_state] = state
                counter += 1
                # tiny extra cost for waiting, so moving is preferred when equal
                f = new_g + to_goal[nxt] + (0.001 if nxt == cell else 0)
                heapq.heappush(heap, (f, new_g, counter, nxt, nt))

    if best_partial is not None:
        return _rebuild(came_from, best_partial[2])
    return None


def _rebuild(came_from, state):
    path = []
    while came_from[state] is not None:
        path.append(state[0])
        state = came_from[state]
    path.reverse()
    return path


# ---------------------------------------------------------------------------
# Deadlocks
# ---------------------------------------------------------------------------

def find_wait_cycles(waits_for):
    """
    waits_for: robot_id -> robot_id it is waiting for.
    Returns a list of cycles, each a sorted list of robot ids.
    Example: {"R1": "R2", "R2": "R1"} -> [["R1", "R2"]]
    """
    cycles = []
    seen = set()
    for start in sorted(waits_for):
        if start in seen:
            continue
        chain = []
        position = {}
        robot = start
        while robot in waits_for and robot not in position and robot not in seen:
            position[robot] = len(chain)
            chain.append(robot)
            robot = waits_for[robot]
        if robot in position:                       # we came back to a robot in this chain: a circle
            cycles.append(sorted(chain[position[robot]:]))
        seen.update(chain)
    return cycles