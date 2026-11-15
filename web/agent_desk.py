"""
The AI supervisor on the website: the chat panel, the Approve / Reject buttons, and incidents.

    - A person types a request in the chat ("Prepare for a big delivery at 2 pm"), or the
      fleet finds an INCIDENT by itself (a robot lost, several robots broken, many late orders).
      Either becomes a task for the agent (scripts/agent.py).
    - Everything the agent does is sent to every open tab ({"type": "agent", ...}).
    - A risky tool waits for a person: the tabs show Approve / Reject. No answer in
      APPROVAL_TIMEOUT seconds counts as Reject.
    - One task at a time; a few more can wait in a queue.

With the Shift Lead (scripts/shift_lead/lead.py) the rules handle the known incidents; the desk only
gets what the rules hand over ("needs a manager"), from the lead's outbox, after a tick. The LLM runs
here, in the background: the simulation never waits for it.

The desk is OFF in the public demo (web/server.py --public): no LLM runs there and no key is read.
"""

import asyncio
import uuid

import datetime
import random

from scripts.agent import FleetAgent
from scripts.classes import Priority, RobotStatus
from scripts.fleet_tools import find_aisles

APPROVAL_TIMEOUT = 600             # seconds
MAX_WAITING_TASKS = 3
INCIDENT_COOLDOWN = 120            # ticks before the same kind of incident starts the agent again
LATE_ORDERS_INCIDENT = 5           # orders waiting past their deadline
KEEP_HISTORY = 60
PUBLIC_DAILY_TASKS = 20            # public demo: AI tasks per day for everyone together (stays in a free quota)
SPILL_TICKS = 90
RUSH_ORDERS = 12

# the incidents a visitor of the public demo can start with a button
INCIDENT_BUTTONS = {
    "spill": "A spill blocks an aisle",
    "breakdowns": "Two robots break down",
    "rush": "A big delivery arrives",
}


class AgentDesk:

    def __init__(self, hub, mcp_server, model, incidents=True, public=False, daily_tasks=PUBLIC_DAILY_TASKS):
        self.hub = hub
        self.public = public                   # public demo: a daily limit, tasks start from buttons
        self.daily_tasks = daily_tasks
        self.tasks_today, self.day = 0, datetime.date.today()
        self.rng = random.Random()
        self.mcp_server = mcp_server
        self.model = model                     # None = no agent (no key / public demo)
        self.incidents = incidents
        self.history = []                      # the chat, for tabs that open later
        self.pending = {}                      # approval id -> future
        self.queue = asyncio.Queue(maxsize=MAX_WAITING_TASKS)
        self.current = None
        self.worker = None
        self.last_incident = {}                # incident key -> tick
        self.known_lost = set()
        self.results = []                      # finished tasks (for the evaluation later)

    @property
    def enabled(self):
        return self.model is not None

    def hello(self):
        return {"enabled": self.enabled, "history": self.history[-40:], "busy": self.current,
                "provider": getattr(self.model, "label", "") if self.model else "",
                "free": bool(self.model) and getattr(self.model, "prices", {}).get("output", 1) == 0,
                "public": self.public, "left_today": self.left_today(), "incidents": INCIDENT_BUTTONS}

    def left_today(self):
        """Public demo: AI tasks still allowed today (None = no limit)."""
        if not self.public:
            return None
        if datetime.date.today() != self.day:
            self.tasks_today, self.day = 0, datetime.date.today()
        return max(0, self.daily_tasks - self.tasks_today)

    def check_can_take(self):
        """Raise ValueError (a message for the visitor) if no new task may start now."""
        if not self.enabled:
            raise ValueError("The AI supervisor is not switched on here.")
        if self.public and self.left_today() == 0:
            raise ValueError("Today's free AI tasks are used up. Come back tomorrow, or just watch the fleet.")
        if self.queue.full():
            raise ValueError("The supervisor has too much to do. Try again in a minute.")

    def start(self):
        if self.enabled and self.worker is None:
            self.worker = asyncio.create_task(self._work())

    def stop(self):
        if self.worker:
            self.worker.cancel()
        for future in self.pending.values():
            if not future.done():
                future.set_result((False, "the server is stopping"))

    # ----- tasks -----

    async def submit(self, task, source="person"):
        self.check_can_take()
        task = " ".join(str(task).split())[:200 if self.public else 500]
        if not task:
            raise ValueError("Write what the supervisor should do.")
        try:
            self.queue.put_nowait((task, source))
        except asyncio.QueueFull:
            raise ValueError("The supervisor has too much to do. Try again in a minute.") from None
        if self.public:
            self.tasks_today += 1
        await self._say("queued", task, {"source": source, "waiting": self.queue.qsize(),
                                         "left_today": self.left_today()})

    # ----- incidents a visitor starts with a button -----

    def make_incident(self, kind):
        """
        Make the incident happen in the live warehouse and return the task for the supervisor.
        Call it while holding the simulation lock.
        """
        if kind not in INCIDENT_BUTTONS:
            raise ValueError(f"Unknown incident '{kind}'. Choose: {', '.join(INCIDENT_BUTTONS)}.")
        sim = self.hub.session.sim
        if kind == "spill":
            aisles = find_aisles(sim.warehouse)
            free = [name for name, a in sorted(aisles.items())
                    if name not in sim.closed_aisles and not any(c in sim.warehouse.occupied for c in a["cells"])]
            if not free:
                raise ValueError("Every aisle has a robot in it right now. Try again in a moment.")
            name = self.rng.choice(free)
            cells = aisles[name]["cells"][1:4]
            for cell in cells:
                sim.warehouse.block_cell(cell)
                sim.blocked_until[cell] = sim.tick + SPILL_TICKS if not sim.staff else 10 ** 9   # staff: until cleaned
                sim.block_reasons[cell] = "spill"
            sim.log("cell_blocked", details=f"a spill in aisle {name}: {cells} for {SPILL_TICKS} ticks")
            return (f"INCIDENT: a spill blocks aisle {name} (cells {cells}) for about {SPILL_TICKS} minutes. "
                    f"Decide what the fleet should do, and do it.")
        if kind == "breakdowns":
            working = [r for r in sim._robots() if r.status != RobotStatus.ERROR
                       and r.robot_id not in sim.lost and r.robot_id not in sim.paused]
            if len(working) < 2:
                raise ValueError("There aren't two working robots right now.")
            broken = sorted(self.rng.sample(working, 2), key=lambda r: r.robot_id)
            for robot in broken:
                robot.break_down(sim.config.repair_ticks)
                robot.path = []
                sim.breakdowns += 1
                sim.log("breakdown", robot.robot_id, f"repair in {sim.config.repair_ticks} ticks")
            ids = " and ".join(r.robot_id for r in broken)
            return (f"INCIDENT: {ids} broke down at the same time (repair takes {sim.config.repair_ticks} minutes). "
                    f"Keep the most urgent orders moving.")
        items = [i for s in sim.warehouse.shelves.values() for i, n in s.items.items() if n > 0]
        for k in range(RUSH_ORDERS):
            priority = Priority.HIGH if k % 3 == 0 else Priority.NORMAL
            sim.add_order(self.rng.choice(items), priority, 60 if priority == Priority.HIGH else 100,
                          source="big delivery")
        return (f"INCIDENT: a big delivery: {RUSH_ORDERS} orders arrived at once, "
                f"{RUSH_ORDERS // 3} of them urgent. Make sure the urgent ones are delivered on time.")

    async def _explain_plan(self, lead, task):
        """One LLM call, in the background: plain words for the plan. The steps don't change."""
        plan = lead.plan
        try:
            reply = await self.model.create("You explain plans to warehouse staff in short, plain sentences.",
                                            [{"role": "user", "content": task["text"]}])
            text = " ".join(b.text for b in reply.content if b.type == "text" and b.text).strip()
        except Exception as error:                     # the rules' text stays
            await self._say("plan", f"The AI could not explain the plan ({type(error).__name__}).", {})
            return
        if text and lead.plan is plan:
            async with self.hub.lock:
                plan["explained"] = text
                plan["made_by"] = "rules (explained by the AI)"
                state = self.hub.session.state_message()
            await self.hub.broadcast([state])
            await self._say("plan", text, {})

    async def _work(self):
        while True:
            task, source = await self.queue.get()
            self.current = task
            agent = FleetAgent(self.mcp_server, self.model, ask_approval=self._ask, report=self._say)
            result = await agent.handle(task)
            self.results.append({"task": task, "source": source, "outcome": result.outcome,
                                 "tool_calls": result.tool_calls, "cost_usd": round(result.cost_usd, 4),
                                 "approvals": result.approvals})
            self.current = None

    # ----- talking to the tabs -----

    async def _say(self, event, text, data):
        tick = self.hub.session.sim.tick
        message = {"type": "agent", "event": event, "text": text, "data": data, "tick": tick}
        self.history.append(message)
        del self.history[:-KEEP_HISTORY]
        await self.hub.broadcast([message])

    async def _ask(self, request):
        approval_id = uuid.uuid4().hex[:8]
        future = asyncio.get_running_loop().create_future()
        self.pending[approval_id] = future
        await self._say("approval", request.get("reason") or "no reason given",
                        {"id": approval_id, "tool": request["tool"], "input": request["input"]})
        try:
            approved, note = await asyncio.wait_for(future, APPROVAL_TIMEOUT)
        except asyncio.TimeoutError:
            approved, note = False, f"nobody answered in {APPROVAL_TIMEOUT // 60} minutes"
        finally:
            self.pending.pop(approval_id, None)
        await self._say("approval_closed", "approved" if approved else "rejected",
                        {"id": approval_id, "approved": approved, "note": note})
        return approved, note

    def answer(self, approval_id, approved, note=""):
        future = self.pending.get(approval_id)
        if future is None or future.done():
            raise ValueError("This question was already answered.")
        future.set_result((bool(approved), " ".join(str(note or "").split())[:200]))

    # ----- incidents -----

    async def watch(self):
        """Called after every tick: does something need the supervisor?"""
        lead = getattr(self.hub.session, "lead", None)
        if lead is not None:                           # the Shift Lead decides what reaches the manager
            while lead.outbox:
                task = lead.outbox.popleft()
                if not self.enabled:
                    lead.manager_busy(task, "no AI is switched on")
                    continue
                if task.get("kind") == "plan":         # the LLM only writes the plan's explanation
                    asyncio.create_task(self._explain_plan(lead, task))
                    continue
                try:
                    await self.submit(task["text"], source="shift lead")
                except ValueError as error:
                    lead.manager_busy(task, str(error))
            return
        if not (self.enabled and self.incidents):
            return
        sim = self.hub.session.sim
        tick = sim.tick
        new_lost = sorted(sim.lost - self.known_lost)
        self.known_lost = set(sim.lost)
        found = []
        for rid in new_lost:
            cell = sim.warehouse.robots[rid].position
            found.append((f"lost-{rid}", f"INCIDENT: robot {rid} lost its connection at {cell}. A technician is on the way. "
                                          f"Check that no order is stuck and that the fleet works around it."))
        broken = sorted(r.robot_id for r in sim.warehouse.robots.values()
                        if r.status.value == "error" and r.robot_id not in sim.lost)
        if len(broken) >= 2:
            found.append(("broken", f"INCIDENT: {len(broken)} robots are broken at the same time ({', '.join(broken)}). "
                                    f"Keep the most urgent orders moving."))
        late = sum(1 for o in sim.queue if o.is_late(tick))
        if late >= LATE_ORDERS_INCIDENT:
            found.append(("late", f"INCIDENT: {late} orders are waiting past their deadline. Find out why and help."))
        for key, task in found:
            if tick - self.last_incident.get(key, -10 ** 9) < INCIDENT_COOLDOWN:
                continue
            self.last_incident[key] = tick
            try:
                await self.submit(task, source="incident")
            except ValueError:
                pass                                   # busy: the next check will see it again
