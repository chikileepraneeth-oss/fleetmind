"""
The AI supervisor on the website: the chat panel, the Approve / Reject buttons, and incidents.

    - A person types a request in the chat ("Prepare for a big delivery at 2 pm"), or the
      fleet finds an INCIDENT by itself (a robot lost, several robots broken, many late orders).
      Either becomes a task for the agent (scripts/agent.py).
    - Everything the agent does is sent to every open tab ({"type": "agent", ...}).
    - A risky tool waits for a person: the tabs show Approve / Reject. No answer in
      APPROVAL_TIMEOUT seconds counts as Reject.
    - One task at a time; a few more can wait in a queue.

The desk is OFF in the public demo (web/server.py --public), so nobody can use the API key.
"""

import asyncio
import uuid

from scripts.agent import FleetAgent

APPROVAL_TIMEOUT = 600             # seconds
MAX_WAITING_TASKS = 3
INCIDENT_COOLDOWN = 120            # ticks before the same kind of incident starts the agent again
LATE_ORDERS_INCIDENT = 5           # orders waiting past their deadline
KEEP_HISTORY = 60


class AgentDesk:

    def __init__(self, hub, mcp_server, model, incidents=True):
        self.hub = hub
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
        return {"enabled": self.enabled, "history": self.history[-40:], "busy": self.current}

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
        if not self.enabled:
            raise ValueError("The AI supervisor is not switched on here.")
        task = " ".join(str(task).split())[:500]
        if not task:
            raise ValueError("Write what the supervisor should do.")
        try:
            self.queue.put_nowait((task, source))
        except asyncio.QueueFull:
            raise ValueError("The supervisor has too much to do. Try again in a minute.") from None
        await self._say("queued", task, {"source": source, "waiting": self.queue.qsize()})

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
