"""
The warehouse staff and their work tickets.

Some problems need a person, not a robot: a spill must be cleaned, a broken robot repaired,
a lost robot restarted, an empty shelf filled. Each job is a WorkTicket:

    open -> assigned (a person walks there) -> working -> done

The people (cleaners and technicians) wait at the staff desk in the loading-dock corner,
where robots never drive. A free person takes the oldest open ticket of their kind, walks
there with A* (around robots and other people, like the restock people), does the job and
walks back. A person can squeeze past a robot that stands still and walk across a station, but a
robot never drives into a person (people move first in a tick). Without that, robots queueing at a
packing station where a robot broke down kept the technician out for hours. While people walk, their
cell is booked, so the robots plan around them. For the job
they step onto the blocked cell itself (into the spill, next to the broken robot): standing
beside it in a 1-cell aisle would close the aisle for the robots for the whole job.

    cleaner      clean (spill 8 min, box 4 min, pallet 6 min), restock (8 min)
    technician   repair (the robot's repair time runs only while the technician works on it: 70% of
                 the repair time, because the old fixed repair time already had the walk in it),
                 restart (a lost robot: 5 min at the robot, then the session switches it on)

A blocked cell with a spill, box or pallet stays blocked until a cleaner has cleaned it.

Who opens the tickets: with auto=True the desk opens them itself when it sees a problem.
With the Shift Lead (scripts/shift_lead/lead.py) auto is off: its playbooks open them
through the fleet tools.

The staff use no randomness at all, so a run with staff is still the same run every time.
"""

from dataclasses import dataclass, field

from scripts.classes import CHARGER, DOCK, FLOOR, PACKING, RobotStatus
from scripts.movement import astar, is_station

CLEAN_MINUTES = {"spill": 8, "box": 4, "pallet": 6}
CLEANABLE = set(CLEAN_MINUTES)
RESTOCK_MINUTES = 8
RESTART_MINUTES = 5
REPAIR_WORK_SHARE = 0.7           # without staff a repair took repair_ticks, walk included (the 3D view showed
                                  # the walk as the first 30%): with a real walk, the work itself is the other 70%
FULL_STOCK = 20
WALK_HOLD = 2                     # ticks the robots plan around a walking person's cell
STEP_ASIDE_AFTER = 3              # a person who waits this long for a robot steps aside (a robot may wait for them)
KINDS = ("clean", "repair", "restart", "restock")
WHO = {"clean": "cleaner", "restock": "cleaner", "repair": "technician", "restart": "technician"}
NEVER = 10 ** 9


@dataclass
class WorkTicket:
    id: str
    kind: str                                  # clean, repair, restart, restock
    cell: tuple                                # where the job is
    created: int                               # tick
    assigned: str = None                       # who does it (C1, T1, ...)
    status: str = "open"                       # open, assigned, working, done
    done: int = None                           # tick when it was done
    what: str = ""                             # spill, box, pallet; or the robot / shelf id
    robot: str = None
    shelf: str = None
    started: int = None                        # tick when the person started working
    start_point: str = None                    # "desk" or "on the way back" (where the person started)
    prefer: str = "nearest"                    # "nearest" free person, or only one "from desk"
    note: str = ""

    def to_dict(self):
        return {"id": self.id, "kind": self.kind, "cell": list(self.cell), "created": self.created,
                "assigned": self.assigned, "status": self.status, "done": self.done, "what": self.what,
                "robot": self.robot, "shelf": self.shelf, "started": self.started,
                "start_point": self.start_point, "prefer": self.prefer, "note": self.note}


@dataclass
class StaffMember:
    id: str
    kind: str                                  # cleaner or technician
    position: tuple = None                     # None = at the staff desk (in the staff room)
    task: str = "free"                         # free, walking, working, returning
    ticket: WorkTicket = None
    target: tuple = None
    path: list = field(default_factory=list)
    timer: int = 0
    waited: int = 0


class StaffDesk:

    def __init__(self, sim, cleaners=2, technicians=2, auto=True):
        self.sim = sim
        self.auto = auto
        wh = sim.warehouse
        self.docks = sorted(wh.cells_of_type(DOCK))
        self.desk = self.docks[0] if self.docks else None          # the same desk as in the 3D world
        self.members = ([StaffMember(f"C{i + 1}", "cleaner") for i in range(cleaners)]
                        + [StaffMember(f"T{i + 1}", "technician") for i in range(technicians)])
        self.tickets = []
        self.next_number = 1
        self.done_count = 0

    # ------------------------------------------------------------------
    # Tickets
    # ------------------------------------------------------------------

    def open_ticket(self, kind, cell=None, what="", robot=None, shelf=None, note="", prefer="nearest"):
        """A new ticket, or the open one that already exists for the same job (so asking twice is safe)."""
        if kind not in KINDS:
            raise ValueError(f"Unknown ticket kind '{kind}'. Use: {', '.join(KINDS)}.")
        same = self.active(kind, cell=cell if robot is None and shelf is None else None, robot=robot, shelf=shelf)
        if same:
            return same
        ticket = WorkTicket(f"W{self.next_number}", kind, tuple(cell), self.sim.tick,
                            what=what or robot or shelf or "", robot=robot, shelf=shelf, note=note, prefer=prefer)
        self.next_number += 1
        self.tickets.append(ticket)
        self.sim.log("ticket_opened", robot or "", f"{ticket.id} {kind} {ticket.what} at {ticket.cell}")
        return ticket

    def active(self, kind, cell=None, robot=None, shelf=None):
        """The ticket that is not done yet for this job, or None."""
        for t in self.tickets:
            if t.status == "done" or t.kind != kind:
                continue
            if (robot is not None and t.robot == robot) or (shelf is not None and t.shelf == shelf) \
                    or (cell is not None and robot is None and shelf is None and t.cell == tuple(cell)):
                return t
        return None

    def open_tickets(self):
        return [t for t in self.tickets if t.status != "done"]

    def repairing(self, robot_id):
        """True while a technician works on this robot (only then does its repair time count down)."""
        return any(m.task == "working" and m.ticket.kind == "repair" and m.ticket.robot == robot_id
                   for m in self.members)

    def restart_done(self, robot_id):
        """The technician has restarted this lost robot (the session then switches it on)."""
        return any(t.kind == "restart" and t.robot == robot_id and t.status == "done" and t.note == "restarted"
                   and t.done == self.sim.tick for t in self.tickets)

    # ------------------------------------------------------------------
    # One tick
    # ------------------------------------------------------------------

    def step(self):
        if self.auto:
            self.scan()
        self._close_solved()
        self._assign()
        for m in self.members:
            self._act(m)
        self._book()

    def scan(self):
        """Open a ticket for every problem that needs a person (auto mode)."""
        sim, wh = self.sim, self.sim.warehouse
        for cell in sorted(wh.blocked):
            kind = sim.block_reasons.get(cell)
            if kind in CLEANABLE and cell not in sim.lost_cells.values():
                self.open_ticket("clean", cell, what=kind)
        for robot in sim._robots():
            rid = robot.robot_id
            if rid in sim.lost:
                self.open_ticket("restart", robot.position, robot=rid)
            elif robot.status == RobotStatus.ERROR:
                self.open_ticket("repair", robot.position, robot=rid)
        if sim.crew is None:
            for sid in sorted(wh.shelves):
                shelf = wh.shelves[sid]
                if sum(shelf.items.values()) == 0:
                    self.open_ticket("restock", wh.access_cells(shelf)[0], shelf=sid)

    def _close_solved(self):
        """A job that solved itself (the robot came back, the cell is open again) needs nobody."""
        sim, wh = self.sim, self.sim.warehouse
        for t in self.open_tickets():
            if t.kind == "repair" and t.robot in sim.lost:
                self._finish(t, "the robot is lost now: the restart ticket takes over")
                continue
            gone = ((t.kind == "clean" and t.cell not in wh.blocked)
                    or (t.kind == "repair" and t.status != "working"
                        and (t.robot not in wh.robots or wh.robots[t.robot].status != RobotStatus.ERROR))
                    or (t.kind == "restart" and t.robot not in sim.lost))
            if gone:
                self._finish(t, "solved by itself" if t.status != "working" else "done")

    def _assign(self):
        for t in sorted(self.open_tickets(), key=lambda x: (x.created, int(x.id[1:]))):
            if t.status != "open":
                continue
            free = [m for m in self.members if m.kind == WHO[t.kind] and m.task in ("free", "returning")]
            if not free:
                continue
            spawn = self._free_dock()
            choices = [m for m in free if m.task == "returning" or spawn is not None]
            if t.prefer == "from desk":
                choices = [m for m in choices if m.task == "free"]       # only someone who is back at the desk
            if not choices:
                continue
            m = min(choices, key=lambda x: (self._distance(x.position or spawn, t.cell), x.task != "returning", x.id))
            t.start_point = "on the way back" if m.task == "returning" else "desk"
            if m.position is None:
                m.position = spawn
                self.sim.warehouse.people[spawn] = m.id
            t.assigned, t.status = m.id, "assigned"
            m.ticket, m.task, m.path, m.waited = t, "walking", [], 0
            m.target = self._stand_spot(m, t)
            self.sim.log("ticket_assigned", t.robot or "", f"{t.id} {t.kind} {t.what}: {m.id} starts from the {t.start_point}")

    def _act(self, m):
        sim, wh = self.sim, self.sim.warehouse
        if m.task == "walking":
            t = m.ticket
            if self._arrived(m, t):
                self._step_onto_job(m, t)
                m.task, m.path = "working", []
                t.status, t.started = "working", sim.tick
                m.timer = self._minutes(t)
                if t.kind == "repair" and t.robot in wh.robots:
                    robot = wh.robots[t.robot]
                    robot.repair_left = min(robot.repair_left, max(1, round(sim.config.repair_ticks * REPAIR_WORK_SHARE)))
                sim.log("ticket_working", t.robot or "", f"{m.id} works on {t.id} ({t.kind} {t.what})")
                return
            if m.target is None or m.target in wh.blocked or (m.target in wh.occupied and m.target != m.position):
                m.target = self._stand_spot(m, t)
            self._walk(m)
        elif m.task == "working":
            t = m.ticket
            if t.kind == "repair":                         # the repair time itself runs in the simulation
                robot = wh.robots.get(t.robot)
                if robot is None or robot.status != RobotStatus.ERROR:
                    self._finish(t, "repaired")
                return
            m.timer -= 1
            if m.timer > 0:
                return
            if t.kind == "clean":
                closed = {c for cells in sim.closed_aisles.values() for c in cells}
                if t.cell not in closed:
                    wh.unblock_cell(t.cell)
                sim.block_reasons.pop(t.cell, None)
                sim.blocked_until.pop(t.cell, None)
                sim.log("cell_cleared", details=f"{t.cell} cleaned by {m.id} ({t.what})")
                self._finish(t, "cleaned")
            elif t.kind == "restock":
                shelf = wh.shelves[t.shelf]
                for item in shelf.items:
                    shelf.items[item] = FULL_STOCK
                sim.log("restocked", m.id, f"{t.shelf} is full again ({FULL_STOCK} {', '.join(shelf.items)})")
                self._finish(t, "restocked")
            elif t.kind == "restart":
                self._finish(t, "restarted")
        elif m.task == "returning":
            if m.position in self.docks:
                del wh.people[m.position]
                m.position, m.task, m.target, m.path = None, "free", None, []
                return
            if m.target is None or m.target in wh.people and wh.people[m.target] != m.id:
                m.target = self._free_dock(near=m.position) or self.desk
            self._walk(m)

    def _finish(self, t, note):
        t.status, t.done, t.note = "done", self.sim.tick, note
        self.done_count += 1
        self.sim.log("ticket_done", t.robot or "", f"{t.id} {t.kind} {t.what}: {note}")
        for m in self.members:
            if m.ticket is t:
                m.ticket, m.task, m.path = None, "returning", []
                m.target = self._free_dock(near=m.position) or self.desk

    # ------------------------------------------------------------------
    # Walking
    # ------------------------------------------------------------------

    def _walk(self, m):
        wh = self.sim.warehouse
        if m.target is None:
            return
        if not m.path or m.path[-1] != m.target:
            others = set(wh.people) - {m.position}
            m.path = (astar(wh, m.position, m.target, others | set(wh.occupied), through_stations=True)
                      or astar(wh, m.position, m.target, others, through_stations=True) or [])
            if not m.path:
                m.waited += 1
                return
        nxt = m.path[0]
        ok = self._can_walk(nxt)
        if not ok:
            m.waited += 1
            m.path = []                                    # plan again next tick (walk around)
            if m.waited >= STEP_ASIDE_AFTER:
                self._step_aside(m, nxt)
            return
        del wh.people[m.position]
        m.position = nxt
        wh.people[nxt] = m.id
        m.path.pop(0)
        m.waited = 0

    def _step_onto_job(self, m, t):
        """Work on the job's own cell (already closed to robots), not in the aisle next to it."""
        if t.kind == "restock":
            return                                     # restocking: at the shelf, in the aisle
        cell = self._job_cell(t)
        wh = self.sim.warehouse
        if cell in wh.people or cell == m.position:
            return
        del wh.people[m.position]
        m.position = cell
        wh.people[cell] = m.id

    def _can_walk(self, cell):
        """Where a person may step: floor, docks and stations, not blocked, nobody else standing there.
        A robot may be there (the person squeezes past it): robots never drive into people."""
        wh = self.sim.warehouse
        return (wh.is_inside(cell) and wh.cell_type(cell) in (FLOOR, DOCK, CHARGER, PACKING)
                and cell not in wh.blocked and cell not in wh.people)

    def _step_aside(self, m, blocked_by):
        """A robot and a person can wait for each other's cell for ever: the person steps into a free cell beside."""
        wh = self.sim.warehouse
        r, c = m.position
        for cell in sorted([(r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)],
                           key=lambda x: (-self._distance(x, blocked_by), x)):
            if cell != blocked_by and wh.can_enter(cell)[0] and not is_station(wh, cell):
                del wh.people[m.position]
                m.position = cell
                wh.people[cell] = m.id
                m.waited = 0
                return

    def _arrived(self, m, t):
        if t.kind == "restock":
            return m.position in self.sim.warehouse.access_cells(self.sim.warehouse.shelves[t.shelf])
        near = self._distance(m.position, self._job_cell(t))
        return near <= 1 or (near <= 2 and m.position == m.target)    # 2 away: every cell beside it was full

    def _job_cell(self, t):
        if t.robot and t.robot in self.sim.warehouse.robots:
            return self.sim.warehouse.robots[t.robot].position
        return t.cell

    def _stand_spot(self, m, t):
        """A free cell next to the job where a person can stand (not a charger or packing station)."""
        wh = self.sim.warehouse
        if t.kind == "restock":
            spots = wh.access_cells(wh.shelves[t.shelf])
        else:
            r, c = self._job_cell(t)
            spots = [(r + dr, c + dc) for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1))]
        start = m.position
        good = self._free_spots(spots, start) or [s for s in spots if self._can_walk(s) or s == start]
        if not good and t.kind != "restock":
            # Every cell beside the job is taken (e.g. robots queue at the packing station where the robot
            # broke down, and they can't move until it is repaired): stand 2 cells away and squeeze in.
            r, c = self._job_cell(t)
            ring = [(r + dr, c + dc) for dr in range(-2, 3) for dc in range(-2, 3) if abs(dr) + abs(dc) == 2]
            good = self._free_spots(ring, start)
        if not good:
            return None
        return min(good, key=lambda s: (self._distance(start, s), s))

    def _free_spots(self, spots, start):
        wh = self.sim.warehouse
        good = [s for s in spots if wh.is_inside(s) and wh.can_enter(s)[0] or s == start]
        return [s for s in good if s == start or not is_station(wh, s) or wh.cell_type(s) == DOCK]

    def _free_dock(self, near=None):
        wh = self.sim.warehouse
        free = [d for d in self.docks if d not in wh.people and d not in wh.occupied]
        if not free:
            return None
        if near is None:
            return free[0]
        return min(free, key=lambda d: (self._distance(near, d), d))

    @staticmethod
    def _distance(a, b):
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    def _minutes(self, t):
        if t.kind == "clean":
            return CLEAN_MINUTES.get(t.what, 6)
        if t.kind == "restock":
            return RESTOCK_MINUTES
        if t.kind == "restart":
            return RESTART_MINUTES
        return 0

    def _book(self):
        """Every person on the floor books their cell, so the robots plan around them."""
        sim = self.sim
        now = sim.tick - 1
        for m in self.members:
            sim.table.release(m.id)
            if m.position is not None and m.position not in self.docks:
                hold = sim.config.window if m.task == "working" else WALK_HOLD
                sim.table.reserve_stay(m.id, m.position, now, now + hold)

    # ------------------------------------------------------------------
    # For the screen and the tools
    # ------------------------------------------------------------------

    def label(self, m):
        if m.task == "working" and m.ticket:
            t = m.ticket
            return {"clean": f"cleaning {t.what}", "repair": f"repairing {t.robot}",
                    "restart": f"restarting {t.robot}", "restock": f"restocking {t.shelf}"}[t.kind]
        if m.task == "walking" and m.ticket:
            return f"going to {m.ticket.kind} {m.ticket.what}"
        return {"returning": "back to the desk", "free": "at the desk"}[m.task]

    def snapshot(self):
        return {
            "staff": [{"id": m.id, "kind": m.kind, "pos": list(m.position) if m.position else None,
                       "task": m.task, "doing": self.label(m), "ticket": m.ticket.id if m.ticket else None}
                      for m in self.members],
            "tickets": [t.to_dict() for t in self.tickets if t.status != "done" or t.done >= self.sim.tick - 30],
            "desk": list(self.desk) if self.desk else None,
        }
