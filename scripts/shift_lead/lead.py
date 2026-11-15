"""
The Shift Lead: after every tick it looks for incidents and decides what to do, by rules first.

    1. find new incidents: a spill, box or pallet; a broken, lost or nearly empty robot;
       an empty shelf; many late orders; a sudden pile of new orders
    2. a playbook for it? (scripts/shift_lead/playbooks.py) -> run it now, through the tools
    3. no playbook, or 3 or more incidents open at once? -> "needs a manager": escalate.
       The escalation is only put in the outbox here (a thread-safe queue). The LLM runs
       somewhere else, in the background (web/agent_desk.py, scripts/compare_shift_lead.py),
       so a tick never waits for it. In the public demo no manager may be asked at all.
    4. notice when an incident is solved, and how many minutes it took

Rules decide in microseconds, every tick. The LLM is only asked for what the rules don't cover.
The Shift Lead uses no randomness, so the same seed still gives the same run.
"""

from collections import deque
from dataclasses import dataclass, field

from scripts.classes import RobotStatus
from scripts.fleet_tools import FleetTools
from scripts.shift_lead.caselog import CaseLog
from scripts.shift_lead.playbooks import NEEDS_MANAGER, PLAYBOOKS, run_playbook
from scripts.shift_lead import plan as shift_plan
from scripts.shift_lead import report as day_report
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
    cells: tuple = ()                          # a spill can cover several touching cells: one incident
    robot: str = None
    shelf: str = None
    detail: str = ""
    decision: str = ""                         # see playbooks.py: work around / send a person / needs a manager
    reason: str = ""
    handled_by: str = ""                       # "rules" or "llm" (or "nobody" when no manager may be asked)
    actions: list = field(default_factory=list)
    option: str = None                         # which option the playbook used (the case log learns from it)
    advice: str = ""                           # why that option (from earlier cases)
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
                "cells": [list(c) for c in self.cells],
                "robot": self.robot, "shelf": self.shelf, "detail": self.detail, "decision": self.decision,
                "reason": self.reason, "handled_by": self.handled_by, "ticket": self.ticket,
                "option": self.option, "advice": self.advice,
                "status": self.status, "solved": self.solved, "minutes": self.minutes,
                "late_caused": self.late_caused, "actions": self.actions}


class ShiftLead:

    def __init__(self, session, manager_allowed=False, caselog=None, reports_dir=None):
        """
        session: web.session.LiveSession (its simulation changes on reset; the lead follows).
        manager_allowed: an LLM may be asked (web/agent_desk.py takes the tasks from `outbox`).
            False (no AI, or the public demo): "needs a manager" is only written down.
        """
        self.session = session
        self.tools = FleetTools(session)
        self.manager_allowed = manager_allowed
        self.outbox = deque()                          # tasks for the manager: {"text", "incidents", "tick"}
        self.caselog = caselog or CaseLog()            # earlier incidents and how well each option worked
        self.reports_dir = reports_dir                 # where day reports are saved (None: not saved)
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
        self.plan = None                               # today's shift plan (realistic days), made at 06:00
        self.report = None                             # the end-of-day report, when the day closes

    # ------------------------------------------------------------------
    # The shift plan
    # ------------------------------------------------------------------

    def make_plan(self, again=False):
        """Make today's plan from the forecast (rules). It waits for a person's Approve."""
        if not self.session.realistic or (self.plan and not again):
            return self.plan
        try:
            self.plan = shift_plan.make_plan(self.tools)
        except Exception as error:                     # no order history, no LightGBM: the day runs without a plan
            self.plan = {"date": "", "made_by": "rules", "text": f"No plan today: {error}", "steps": [],
                         "status": "none", "periods": [], "actions": []}
            return self.plan
        self.sim.log("plan_made", details=f"{len(self.plan['steps'])} steps: {self.plan['text']}")
        if self.manager_allowed and self.plan["steps"]:
            self.outbox.append({"kind": "plan", "tick": self.sim.tick, "text": shift_plan.explain_prompt(self.plan),
                                "incidents": []})
        return self.plan

    def answer_plan(self, approved):
        """The person's one click. Approve: the steps run through the tools. Reject: nothing changes."""
        if not self.plan or self.plan["status"] != "waiting":
            raise ValueError("There is no plan waiting for an answer.")
        if not approved:
            self.plan["status"] = "rejected"
            self.sim.log("plan_rejected", details="the shift plan was rejected: nothing changes")
            return self.plan
        self.plan["actions"] = shift_plan.apply_plan(self.tools, self.plan)
        self.plan["status"] = "approved"
        self.sim.log("plan_approved", details=f"{len(self.plan['actions'])} plan steps are on")
        return self.plan

    def make_report(self):
        """The day is over: the template report (no LLM), saved; the LLM may rewrite it in the background."""
        self.report = day_report.make_report(self.session, self)
        if self.reports_dir:
            self.report["file"] = day_report.save_report(self.report, self.reports_dir,
                                                         self.session.scenario, self.session.seed)
        self.sim.log("day_report", details=self.report["text"].splitlines()[2])
        if self.manager_allowed:
            self.outbox.append({"kind": "report", "tick": self.sim.tick, "text": day_report.explain_prompt(self.report),
                                "incidents": []})
        return self.report

    # ------------------------------------------------------------------
    # After every tick
    # ------------------------------------------------------------------

    def after_tick(self):
        if self.session.sim is not self.sim:
            self.attach()
        if self.plan is None and self.session.realistic and self.sim.tick >= 1:
            self.make_plan()                           # 06:00: the plan for the day (it waits for Approve)
        new = self._find_new()
        for incident in new:
            self._decide(incident)
        self._check_solved()
        self._maybe_many_open()
        if self.report is None and self.session.realistic and self.session.finished:
            self.make_report()
        self._last_queue = len(self.sim.queue)
        del self.incidents[:-KEEP]
        return new

    def _find_new(self):
        sim, wh = self.sim, self.sim.warehouse
        found = []
        lost_cells = set(sim.lost_cells.values())
        new_cells = {cell: sim.block_reasons.get(cell) for cell in sorted(wh.blocked)
                     if sim.block_reasons.get(cell) in CLEANABLE and cell not in lost_cells
                     and not self._open(kind=sim.block_reasons.get(cell), cell=cell)}
        for group in _touching_groups(new_cells):
            kind = new_cells[group[0]]
            where = f"{list(group[0])}" if len(group) == 1 else f"{len(group)} cells from {list(group[0])}"
            found.append(self._new(kind, cell=group[0], cells=tuple(group), detail=f"a {kind} at {where}"))
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

    def _new(self, kind, cell=None, robot=None, shelf=None, detail="", cells=()):
        sim = self.sim
        incident = Incident(f"I{self.next_number}", kind, sim.tick, cell=cell, cells=cells or ((cell,) if cell else ()),
                            robot=robot, shelf=shelf, detail=detail,
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
            if self.manager_allowed:
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
        if not self.manager_allowed:
            return
        lines = "; ".join(f"{i.id} {i.detail} ({i.decision}{', ticket ' + i.ticket if i.ticket else ''})" for i in open_now)
        self._escalate(f"{len(open_now)} INCIDENTS ARE OPEN AT THE SAME TIME: {lines}. The playbooks already did "
                       f"their part. Decide what matters most now and what else the fleet should do.", open_now)

    def _escalate(self, text, incidents):
        task = {"tick": self.sim.tick, "text": text, "incidents": [i.id for i in incidents]}
        self.escalations.append(task)
        self.outbox.append(task)                       # never wait here: the LLM runs in the background

    def manager_busy(self, task, why):
        """The manager could not take a task: those incidents are only written down."""
        if task.get("kind") in ("plan", "report"):
            return                                     # the rules' plan / the template report stays
        for i in self.incidents:
            if i.id in task["incidents"] and i.handled_by == "llm":
                i.handled_by = "nobody"
                i.reason += f" The manager could not take it ({why}): written down only."
                self.counts["llm"] -= 1
                self.counts["nobody"] += 1

    def advice(self, incident):
        """Which option worked best for this kind of incident so far (the case log), or None."""
        return self.caselog.best(incident.kind)

    def _check_solved(self):
        sim, wh = self.sim, self.sim.warehouse
        for i in self.incidents:
            if i.status != "open":
                continue
            solved = False
            if i.kind in CLEANABLE:
                solved = not any(c in wh.blocked for c in i.cells)
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
        """Every solved incident goes into the case log, so the next decision can learn from it."""
        self.caselog.add(incident, scenario=self.session.scenario, seed=self.session.seed)

    def _open(self, kind, cell=None, robot=None, shelf=None):
        for i in self.incidents:
            if i.status == "open" and i.kind == kind and (
                    (cell is not None and cell in i.cells) or (robot is not None and i.robot == robot)
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
            "counts": dict(self.counts),                    # incidents decided by rules / by the LLM / by nobody
            "manager_tasks": len(self.escalations),         # tasks handed to the LLM (incl. "3 open at once")
            "manager_allowed": self.manager_allowed,
            "open": len(open_now),
            "incidents": [i.to_dict() for i in self.incidents[-25:]],
            "plan": self.plan,
            "report": self.report,
            "playbooks": sorted(PLAYBOOKS),
        }


def _touching_groups(cells):
    """Cells of the same kind that touch each other form one group (one spill over 3 cells = 1 incident)."""
    left, groups = set(cells), []
    for start in sorted(cells):
        if start not in left:
            continue
        group, todo = [], [start]
        left.discard(start)
        while todo:
            c = todo.pop()
            group.append(c)
            for n in ((c[0] - 1, c[1]), (c[0] + 1, c[1]), (c[0], c[1] - 1), (c[0], c[1] + 1)):
                if n in left and cells[n] == cells[start]:
                    left.discard(n)
                    todo.append(n)
        groups.append(sorted(group))
    return groups
