"""
The Shift Lead: after every tick it looks for incidents and decides what to do, by rules first.

    1. find new incidents: a spill, box or pallet; a broken, lost or nearly empty robot;
       an empty shelf; many late orders; a sudden pile of new orders
    2. a playbook for it? (scripts/shift_lead/playbooks.py) -> run it now, through the tools
    3. no playbook, or 3 or more incidents open at once? -> "needs a manager": escalate.
       The escalation is only put in a queue here; the LLM runs somewhere else, in the
       background (web/agent_desk.py, scripts/compare_shift_lead.py), so a tick never waits for it.
    4. notice when an incident is solved, and how many minutes it took

Rules decide in microseconds, every tick. The LLM is only asked for what the rules don't cover.
The Shift Lead uses no randomness, so the same seed still gives the same run.
"""

from dataclasses import dataclass, field

from scripts.classes import RobotStatus
from scripts.fleet_tools import FleetTools
from scripts.shift_lead.playbooks import NEEDS_MANAGER, PLAYBOOKS, run_playbook
from scripts.shift_lead.staff import CLEANABLE

CRITICAL_BATTERY = 15.0
LATE_ORDERS = 5                  # orders waiting past their deadline: an incident
SURGE_ORDERS = 8                 # this many new orders within one tick: an incident
MANY_OPEN = 3                    # this many open incidents at once: ask the manager what goes first
REPEAT_AFTER = 120               # ticks before the same kind of incident without a playbook counts again
KEEP = 200                       # incidents kept for the screen


@dataclass
class Incident:
    id: str
    kind: str
    tick: int
    cell: tuple = None
    robot: str = None
    shelf: str = None
    detail: str = ""
    decision: str = ""                         # see playbooks.py: work around / send a person / needs a manager
    reason: str = ""
    handled_by: str = ""                       # "rules" or "llm" (or "nobody" when no manager may be asked)
    actions: list = field(default_factory=list)
    ticket: str = None
    status: str = "open"                       # open, solved
    solved: int = None
    late_at_start: int = 0
    late_caused: int = None
    queue_at_start: int = 0

    @property
    def minutes(self):
        return None if self.solved is None else self.solved - self.tick

    def to_dict(self):
        return {"id": self.id, "kind": self.kind, "tick": self.tick, "cell": list(self.cell) if self.cell else None,
                "robot": self.robot, "shelf": self.shelf, "detail": self.detail, "decision": self.decision,
                "reason": self.reason, "handled_by": self.handled_by, "ticket": self.ticket,
                "status": self.status, "solved": self.solved, "minutes": self.minutes,
                "late_caused": self.late_caused, "actions": self.actions}


class ShiftLead:

    def __init__(self, session, escalate=None, manager_allowed=True):
        """
        session: web.session.LiveSession (its simulation changes on reset; the lead follows).
        escalate(text, incidents): puts a task for the manager (the LLM) in a queue. Must not block.
        manager_allowed=False (the public demo): "needs a manager" is only written down.
        """
        self.session = session
        self.tools = FleetTools(session)
        self.escalate = escalate
        self.manager_allowed = manager_allowed
        self.sim = None
        self.attach()

    def attach(self):
        """A new day (reset, new scenario): start with an empty incident list."""
        self.sim = self.session.sim
        if self.sim.staff:
            self.sim.staff.auto = False                # tickets come from the playbooks now
        self.incidents = []
        self.next_number = 1
        self.counts = {"rules": 0, "llm": 0, "nobody": 0}
        self.escalations = []                          # what was handed to the manager
        self._last_queue = len(self.sim.queue)
        self._last_kind_tick = {}
        self._many_open_asked = False

    # ------------------------------------------------------------------
    # After every tick
    # ------------------------------------------------------------------

    def after_tick(self):
        if self.session.sim is not self.sim:
            self.attach()
        new = self._find_new()
        for incident in new:
            self._decide(incident)
        self._check_solved()
        self._maybe_many_open()
        self._last_queue = len(self.sim.queue)
        del self.incidents[:-KEEP]
        return new

    def _find_new(self):
        sim, wh = self.sim, self.sim.warehouse
        found = []
        lost_cells = set(sim.lost_cells.values())
        for cell in sorted(wh.blocked):
            kind = sim.block_reasons.get(cell)
            if kind in CLEANABLE and cell not in lost_cells and not self._open(kind=kind, cell=cell):
                found.append(self._new(kind, cell=cell, detail=f"a {kind} at {list(cell)}"))
        for robot in sim._robots():
            rid = robot.robot_id
            if rid in sim.lost:
                if not self._open(kind="robot_lost", robot=rid):
                    found.append(self._new("robot_lost", cell=robot.position, robot=rid,
                                           detail=f"{rid} lost its signal at {list(robot.position)}"))
            elif robot.status == RobotStatus.ERROR:
                if not self._open(kind="robot_broken", robot=rid):
                    found.append(self._new("robot_broken", cell=robot.position, robot=rid,
                                           detail=f"{rid} broke down at {list(robot.position)}"))
            elif robot.battery < CRITICAL_BATTERY and robot.task not in ("to_charger", "charging", "wait_charger") \
                    and rid not in sim.paused and not self._open(kind="battery_critical", robot=rid):
                found.append(self._new("battery_critical", cell=robot.position, robot=rid,
                                       detail=f"{rid} is at {robot.battery:.0f}% battery"))
        for sid in sorted(wh.shelves):
            if sum(wh.shelves[sid].items.values()) == 0 and not self._open(kind="shelf_empty", shelf=sid):
                found.append(self._new("shelf_empty", cell=wh.access_cells(wh.shelves[sid])[0], shelf=sid,
                                       detail=f"shelf {sid} is empty"))
        late = sum(1 for o in sim.queue if o.is_late(sim.tick))
        if late >= LATE_ORDERS and self._may_repeat("late_orders"):
            found.append(self._new("late_orders", detail=f"{late} orders are waiting past their deadline"))
        jump = len(sim.queue) - self._last_queue
        if jump >= SURGE_ORDERS and self._may_repeat("order_surge"):
            found.append(self._new("order_surge", detail=f"{jump} new orders arrived at once"))
        return found

    def _new(self, kind, cell=None, robot=None, shelf=None, detail=""):
        sim = self.sim
        incident = Incident(f"I{self.next_number}", kind, sim.tick, cell=cell, robot=robot, shelf=shelf, detail=detail,
                            late_at_start=sim.kpis()["late_orders"], queue_at_start=len(sim.queue))
        self.next_number += 1
        self.incidents.append(incident)
        return incident

    def _decide(self, incident):
        sim = self.sim
        outcome = run_playbook(self.tools, incident, self.advice(incident))
        if outcome is not None:
            incident.decision, incident.reason, incident.actions = outcome
            incident.handled_by = "rules"
        else:
            incident.decision = NEEDS_MANAGER
            if self.manager_allowed and self.escalate:
                incident.handled_by = "llm"
                incident.reason = f"No playbook for '{incident.kind}': asking the manager (the AI) what to do."
                self._escalate(f"INCIDENT {incident.id}: {incident.detail}. No playbook covers this. "
                               f"Decide what the fleet should do, and do it.", [incident])
            else:
                incident.handled_by = "nobody"
                incident.reason = f"No playbook for '{incident.kind}', and no manager can be asked here: written down only."
        self.counts[incident.handled_by] += 1
        sim.log("decision", incident.robot or "", f"{incident.id} {incident.kind}: {incident.decision}: {incident.reason}")

    def _maybe_many_open(self):
        """3 or more open incidents at once: the manager decides what goes first."""
        open_now = [i for i in self.incidents if i.status == "open"]
        if len(open_now) < MANY_OPEN:
            self._many_open_asked = False
            return
        if self._many_open_asked:
            return
        self._many_open_asked = True
        sim = self.sim
        sim.log("decision", "", f"{len(open_now)} incidents open at once: {NEEDS_MANAGER}")
        if not (self.manager_allowed and self.escalate):
            return
        lines = "; ".join(f"{i.id} {i.detail} ({i.decision}{', ticket ' + i.ticket if i.ticket else ''})" for i in open_now)
        self._escalate(f"{len(open_now)} INCIDENTS ARE OPEN AT THE SAME TIME: {lines}. The playbooks already did "
                       f"their part. Decide what matters most now and what else the fleet should do.", open_now)
        self.counts["llm"] += 1

    def _escalate(self, text, incidents):
        self.escalations.append({"tick": self.sim.tick, "text": text, "incidents": [i.id for i in incidents]})
        self.escalate(text, incidents)

    def advice(self, incident):
        """Advice from earlier cases (the case log); nothing yet."""
        return None

    def _check_solved(self):
        sim, wh = self.sim, self.sim.warehouse
        for i in self.incidents:
            if i.status != "open":
                continue
            solved = False
            if i.kind in CLEANABLE:
                solved = i.cell not in wh.blocked
            elif i.kind == "robot_broken":
                robot = wh.robots.get(i.robot)
                solved = robot is None or (robot.status != RobotStatus.ERROR and i.robot not in sim.lost)
            elif i.kind == "robot_lost":
                solved = i.robot not in sim.lost
            elif i.kind == "shelf_empty":
                solved = sum(wh.shelves[i.shelf].items.values()) > 0
            elif i.kind == "battery_critical":
                robot = wh.robots[i.robot]
                solved = robot.task in ("to_charger", "charging") or robot.battery >= 25 or robot.status == RobotStatus.ERROR
            elif i.kind == "late_orders":
                solved = sum(1 for o in sim.queue if o.is_late(sim.tick)) < LATE_ORDERS
            elif i.kind == "order_surge":
                solved = len(sim.queue) <= i.queue_at_start - SURGE_ORDERS // 2 or sim.tick - i.tick >= 120
            if solved:
                i.status, i.solved = "solved", sim.tick
                i.late_caused = max(0, sim.kpis()["late_orders"] - i.late_at_start)
                sim.log("incident_solved", i.robot or "", f"{i.id} {i.kind} after {i.minutes} min")
                self.on_solved(i)

    def on_solved(self, incident):
        """Hook for the case log."""

    def _open(self, kind, cell=None, robot=None, shelf=None):
        for i in self.incidents:
            if i.status == "open" and i.kind == kind and (
                    (cell is not None and i.cell == cell) or (robot is not None and i.robot == robot)
                    or (shelf is not None and i.shelf == shelf)):
                return i
        return None

    def _may_repeat(self, kind):
        if self._open(kind=kind) is None and self._open_kind(kind) is None \
                and self.sim.tick - self._last_kind_tick.get(kind, -10 ** 9) >= REPEAT_AFTER:
            self._last_kind_tick[kind] = self.sim.tick
            return True
        return False

    def _open_kind(self, kind):
        return next((i for i in self.incidents if i.status == "open" and i.kind == kind), None)

    # ------------------------------------------------------------------
    # For the screen
    # ------------------------------------------------------------------

    def snapshot(self):
        open_now = [i for i in self.incidents if i.status == "open"]
        return {
            "counts": dict(self.counts),
            "open": len(open_now),
            "incidents": [i.to_dict() for i in self.incidents[-25:]],
            "playbooks": sorted(PLAYBOOKS),
        }
