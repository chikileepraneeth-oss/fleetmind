"""
Restocking: goods that arrive every day are carried from the loading docks to the shelves.

Two ways to do it (SimConfig.restock):

    "people"  2 people (P1, P2). The robots DON'T KNOW where they are. A robot that
              sees a person (within `sight_range` cells, not through a shelf) reports it,
              and from then on every robot plans around that cell: the person's cell is
              booked in the shared reservation table, and every robot whose route went
              through it plans a new route at once ("rerouted"). When a robot sees the
              person somewhere else, it updates the spot for everyone; when a robot looks
              at an old spot and the person is gone, it clears it.

    "robots"  2 restocking robots (S1, S2) doing the same jobs. They are part of the fleet:
              they book their route in the reservation table like every robot, so nobody
              has to discover them.

A restock job: walk to a dock and load (3 ticks), carry the goods to the shelf with the
lowest stock, restock it (8 ticks: the shelf is full again), then the next job.

All restock randomness uses its own random generator, so the orders, breakdowns and
blocked cells of a day stay comparable with the same day without restocking.
"""

import random

from scripts.classes import DOCK, SHELF, WALL
from scripts.movement import astar
from scripts.traffic import cooperative_astar

LOAD_TICKS = 3
RESTOCK_TICKS = 8
FULL_STOCK = 20
KNOWN_FOR = 40                 # ticks a reported spot is kept without anyone seeing the person again


class Restocker:

    def __init__(self, rid, kind, cell):
        self.id = rid
        self.kind = kind                 # "person" or "robot"
        self.position = cell
        self.task = "idle"               # idle, to_dock, loading, to_shelf, restocking
        self.target = None
        self.shelf = None                # shelf id of the current job
        self.carrying = None
        self.path = []
        self.timer = 0
        self.waited = 0


class RestockCrew:

    def __init__(self, sim):
        self.sim = sim
        cfg = sim.config
        self.kind = "person" if cfg.restock == "people" else "robot"
        self.rng = random.Random(sim.seed * 104729 + 17)
        wh = sim.warehouse
        self.docks = wh.cells_of_type(DOCK)
        prefix = "P" if self.kind == "person" else "S"
        free = [c for c in self.docks if c not in wh.occupied]
        self.members = []
        for i in range(cfg.restockers):
            cell = free[i % len(free)]
            if cell in wh.people:
                continue
            member = Restocker(f"{prefix}{i + 1}", self.kind, cell)
            wh.people[cell] = member.id
            self.members.append(member)
        self.claimed = {}                # shelf id -> restocker id
        self.known = {}                  # person id -> {"cell", "seen_by", "tick"}: what the ROBOTS know
        self.restocks = 0
        self.reports = 0
        self.reroutes = 0

    # ------------------------------------------------------------------
    # One tick: the crew moves and works, then the robots look around
    # ------------------------------------------------------------------

    def step(self):
        for m in self.members:
            self._work(m)
        if self.kind == "person":
            if self.sim.config.people_tracking == "badges":
                self._track_badges()
            else:
                self._robots_look()
            self._book_known_people()

    def _track_badges(self):
        """People wear a location badge (e.g. UWB): the fleet always knows where they are, nobody has to look."""
        for m in self.members:
            known = self.known.get(m.id)
            if known and known["cell"] == m.position:
                known["tick"] = self.sim.tick
                continue
            self.known[m.id] = {"cell": m.position, "seen_by": "badge", "tick": self.sim.tick}
            self._reroute_through(m.id, m.position)

    def _work(self, m):
        sim, wh = self.sim, self.sim.warehouse
        if m.task == "idle":
            self._next_job(m)
        if m.task in ("loading", "restocking"):
            m.timer -= 1
            if m.timer > 0:
                return
            if m.task == "loading":
                m.carrying = f"goods for {m.shelf}"
                shelf = wh.shelves[m.shelf]
                m.target = self._spot_at(m, shelf)
                m.task, m.path = "to_shelf", []
                return
            shelf = wh.shelves[m.shelf]
            for item in shelf.items:
                shelf.items[item] = FULL_STOCK
            sim.log("restocked", m.id, f"{m.shelf} is full again ({FULL_STOCK} {', '.join(shelf.items)})")
            self.restocks += 1
            self.claimed.pop(m.shelf, None)
            m.carrying, m.shelf, m.task = None, None, "idle"
            return
        if m.task in ("to_dock", "to_shelf"):
            if m.position == m.target:
                m.task, m.timer = ("loading", LOAD_TICKS) if m.task == "to_dock" else ("restocking", RESTOCK_TICKS)
                m.path = []
                if m.task == "restocking":
                    sim.log("restocking", m.id, f"{m.shelf} from {m.position}")
                return
            self._step_towards(m)

    def _next_job(self, m):
        wh = self.sim.warehouse
        open_shelves = [s for s in wh.shelves.values() if s.shelf_id not in self.claimed]
        if not open_shelves:
            return
        lowest = min(sum(s.items.values()) for s in open_shelves)
        candidates = sorted(s.shelf_id for s in open_shelves if sum(s.items.values()) == lowest)
        shelf_id = self.rng.choice(candidates)
        self.claimed[shelf_id] = m.id
        m.shelf = shelf_id
        m.target = min(self.docks, key=lambda d: (abs(d[0] - m.position[0]) + abs(d[1] - m.position[1]), d))
        m.task, m.path = "to_dock", []
        self.sim.log("restock_job", m.id, f"goods for {shelf_id} (stock {lowest}): to the dock first")

    def _spot_at(self, m, shelf):
        wh = self.sim.warehouse
        spots = [c for c in wh.access_cells(shelf) if c not in wh.blocked]
        return min(spots or wh.access_cells(shelf),
                   key=lambda c: (abs(c[0] - m.position[0]) + abs(c[1] - m.position[1]), c))

    def _step_towards(self, m):
        sim, wh = self.sim, self.sim.warehouse
        if not m.path or m.path[-1] != m.target:
            m.path = self._plan(m)
            if not m.path:
                return
        nxt = m.path[0]
        if nxt == m.position:                          # a planned wait (restock robots)
            m.path.pop(0)
            return
        ok, _ = wh.can_enter(nxt)
        if not ok:
            m.waited += 1
            m.path = []                                # plan again next tick (people walk around)
            if m.kind == "robot":
                sim.table.release(m.id)
            return
        del wh.people[m.position]
        m.position = nxt
        wh.people[nxt] = m.id
        m.path.pop(0)
        m.waited = 0

    def _plan(self, m):
        sim, wh = self.sim, self.sim.warehouse
        if m.kind == "person":
            # people see the robots and other people, and walk around them
            avoid = set(wh.occupied) | (set(wh.people) - {m.position})
            return astar(wh, m.position, m.target, avoid) or astar(wh, m.position, m.target) or []
        # restock robots plan like the fleet: with bookings, so everyone knows their route
        now = sim.tick - 1
        sim.table.release(m.id)
        path = cooperative_astar(wh, m.position, m.target, now, sim.table, m.id, sim.dist,
                                 window=sim.config.window) or []
        if path:
            sim.table.reserve_path(m.id, m.position, now, path)
        else:
            sim.table.reserve_stay(m.id, m.position, now, now + 2)
        return path

    def book_standing(self, now):
        """Restock robots that stand still (loading, restocking, waiting) book their cell, like robots do."""
        if self.kind != "robot":
            return
        for m in self.members:
            if not m.path:
                self.sim.table.release(m.id)
                self.sim.table.reserve_stay(m.id, m.position, now, now + self.sim.config.window)

    # ------------------------------------------------------------------
    # People: robots see them, tell everyone, and everyone plans around them
    # ------------------------------------------------------------------

    def _robots_look(self):
        sim, wh = self.sim, self.sim.warehouse
        robots = [r for r in sim._robots() if r.robot_id not in sim.lost]
        where = {m.id: m.position for m in self.members}
        for robot in robots:
            # everyone this robot can see
            for pid, cell in sorted(where.items()):
                if not self._can_see(robot.position, cell):
                    continue
                known = self.known.get(pid)
                if known and known["cell"] == cell:
                    known["tick"] = sim.tick                     # still there: keep the spot fresh
                    continue
                moved = f"moved from {known['cell']} to" if known else "is at"
                self.known[pid] = {"cell": cell, "seen_by": robot.robot_id, "tick": sim.tick}
                self.reports += 1
                sim.log("person_seen", robot.robot_id, f"{pid} {moved} {cell}: told every robot")
                self._reroute_through(pid, cell)
            # old spots this robot can see where the person isn't any more
            for pid, known in list(self.known.items()):
                if known["cell"] != where.get(pid) and self._can_see(robot.position, known["cell"]):
                    sim.log("person_gone", robot.robot_id, f"{pid} is no longer at {known['cell']}")
                    del self.known[pid]
                    sim.table.release(pid)
        for pid, known in list(self.known.items()):
            if sim.tick - known["tick"] > KNOWN_FOR:
                del self.known[pid]
                sim.table.release(pid)

    def _reroute_through(self, pid, cell):
        """Every robot whose planned route goes through this cell plans a new one."""
        sim = self.sim
        sim.table.release(pid)
        rerouted = []
        for robot in sim._robots():
            if cell in robot.path:
                robot.path = []
                sim.table.release(robot.robot_id)
                rerouted.append(robot.robot_id)
        if rerouted:
            self.reroutes += len(rerouted)
            sim.log("rerouted", details=f"{', '.join(rerouted)} around {pid} at {cell}")

    def _book_known_people(self):
        """The spots the robots know are booked for the planning window, so routes go around them."""
        sim = self.sim
        now = sim.tick - 1
        for pid, known in self.known.items():
            sim.table.release(pid)
            sim.table.reserve_stay(pid, known["cell"], now, now + sim.config.window)

    def _can_see(self, eye, cell):
        """Within sight_range cells, and not through a shelf or a wall."""
        dist = abs(eye[0] - cell[0]) + abs(eye[1] - cell[1])
        if dist > self.sim.config.sight_range:
            return False
        wh = self.sim.warehouse

        def clear(path):
            return all(wh.cell_type(c) not in (SHELF, WALL) for c in path)

        if eye[0] == cell[0] or eye[1] == cell[1]:
            step_r = (cell[0] > eye[0]) - (cell[0] < eye[0])
            step_c = (cell[1] > eye[1]) - (cell[1] < eye[1])
            between = [(eye[0] + step_r * i, eye[1] + step_c * i) for i in range(1, dist)]
            return clear(between)
        corner_a, corner_b = (eye[0], cell[1]), (cell[0], eye[1])     # around the corner: one side must be open
        return clear([corner_a]) or clear([corner_b])

    def known_cells(self):
        """What the robots know: restock robots share their position; people only where a robot saw them."""
        if self.kind == "robot":
            return {m.position for m in self.members}
        return {k["cell"] for k in self.known.values()}

    # ------------------------------------------------------------------
    # For the screen
    # ------------------------------------------------------------------

    def snapshot(self):
        return {
            "people": [{"id": m.id, "kind": m.kind, "pos": list(m.position), "task": m.task,
                        "carrying": m.carrying, "shelf": m.shelf} for m in self.members],
            "known_people": [{"id": pid, "cell": list(k["cell"]), "seen_by": k["seen_by"], "tick": k["tick"]}
                             for pid, k in sorted(self.known.items())],
        }
