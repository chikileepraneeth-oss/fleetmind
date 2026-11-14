"""
The FleetMind AI supervisor: an agent that handles incidents with the fleet's MCP tools.

It only acts through the MCP tools (web/mcp_server.py), like a person with buttons.
One task (an incident, or a request typed in the chat) goes through three steps:

    1. PLAN     look at the fleet with read-only tools and write a short plan
    2. ACT      carry out the plan with the tools. A RISKY tool (marked destructive in MCP,
                e.g. pause_robot, close_aisle) is only run after a person approves it on the
                website. If the person rejects it, the agent hears why and must find another way.
    3. CHECK    a separate critic looks at the fleet afterwards: "is the problem solved?"
                If not, the agent goes back to step 2 (at most MAX_REPLANS times).

Limits, so it can never run away: MAX_STEPS tool calls per task, MAX_TOOL_ERRORS failed
tool calls, MAX_REPLANS rounds. The cost of every task is counted.

The model is pluggable:
    ClaudeModel     the real Claude (needs an Anthropic API key)
    ScriptedModel   fixed answers, for tests and for trying the flow without a key
"""

import json
from dataclasses import dataclass, field

from mcp import Client

MODEL = "claude-opus-5-5"
MAX_STEPS = 12                     # tool calls per task
MAX_TOOL_ERRORS = 3
MAX_REPLANS = 2
PRICE_PER_MTOK = {"input": 4.0, "output": 20.0, "cache_read": 0.20, "cache_write": 5.0}   # claude-opus-5-5, USD

SYSTEM = """You are the AI supervisor of FleetMind, a warehouse with delivery robots.
A person is responsible; you help them by handling incidents with the tools you have.

How to work:
- Look first (get_fleet_status, get_robot), then act. Never guess cell numbers, robot ids or aisle names: read them.
- Change as little as needed. Prefer actions that are easy to undo.
- pause_robot and close_aisle need a person's approval. Always fill in `reason` with one clear sentence:
  the person decides from it. If they reject, don't ask for the same thing again; find another way or stop.
- Times are simulation minutes. Robots that are lost are being restarted by a technician; you don't need to do that.
- Keep every message short and plain: what you see, what you do, why."""

PLAN_PROMPT = """Task: {task}

Step 1 of 3, PLAN: look at the fleet with the read-only tools, then write a short numbered plan
(at most 4 steps) to handle the task. Do not change anything yet."""

ACT_PROMPT = """Step 2 of 3, ACT: carry out your plan now with the tools. When you are done, say in one or two
sentences what you did."""

CHECK_PROMPT = """You check the work of a warehouse AI supervisor. Be strict but fair.

Task: {task}
Plan: {plan}
Actions: {actions}
The fleet now: {status}

Is the task handled, as far as this fleet can do it? Answer with exactly one line:
DONE: <one sentence why>   or   NOT DONE: <one sentence: what is still wrong>"""

REPLAN_PROMPT = """The check says the task is NOT done yet: {feedback}
Look again and continue (you may change the plan). When you are done, say what you did."""


class AgentError(Exception):
    """The agent can't go on (e.g. the model declined or failed)."""


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

@dataclass
class Block:
    type: str                       # "text" or "tool_use"
    text: str = ""
    id: str = ""
    name: str = ""
    input: dict = field(default_factory=dict)


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class Reply:
    content: list
    stop_reason: str = "end_turn"
    usage: Usage = field(default_factory=Usage)


class ClaudeModel:
    """The real Claude, through the official Anthropic SDK."""

    def __init__(self, model=MODEL, effort="medium"):
        import anthropic
        self.client = anthropic.AsyncAnthropic()        # API key from ANTHROPIC_API_KEY (or `ant auth login`)
        self.model = model
        self.effort = effort

    async def create(self, system, messages, tools=None):
        kwargs = {"tools": tools} if tools else {}
        reply = await self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            system=system,
            messages=messages,
            output_config={"effort": self.effort},
            cache_control={"type": "ephemeral"},          # the system prompt and tools repeat every call
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",                          # if this model declines, another one continues
            **kwargs,
        )
        if reply.stop_reason == "refusal":
            raise AgentError("The model declined this request.")
        return reply


class ScriptedModel:
    """Fixed replies, in order, for tests or a dry run. Each reply: text, or (text, [tool calls])."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []                                   # what the agent sent, for tests

    async def create(self, system, messages, tools=None):
        self.calls.append({"system": system, "messages": list(messages), "tools": tools})
        if not self.replies:
            return Reply([Block("text", text="DONE: nothing more to do.")])
        reply = self.replies.pop(0)
        if callable(reply):
            reply = reply(messages)
        if isinstance(reply, str):
            return Reply([Block("text", text=reply)], usage=Usage(100, 20))
        text, calls = reply
        blocks = ([Block("text", text=text)] if text else []) + [
            Block("tool_use", id=f"call_{len(self.calls)}_{i}", name=name, input=args)
            for i, (name, args) in enumerate(calls)]
        return Reply(blocks, stop_reason="tool_use", usage=Usage(100, 20))


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------

@dataclass
class Result:
    task: str
    outcome: str = "running"        # done, not_done, stopped (a limit), error
    plan: str = ""
    summary: str = ""
    actions: list = field(default_factory=list)       # every tool call: name, input, ok, short result
    approvals: list = field(default_factory=list)     # every approval: tool, input, approved, note
    cost_usd: float = 0.0
    tool_calls: int = 0


class FleetAgent:

    def __init__(self, mcp_server, model, ask_approval=None, report=None):
        """
        ask_approval(request) -> (approved: bool, note: str): asks a person (the website's buttons).
        report(kind, text, data): tells the person what happens (the website's chat panel).
        """
        self.mcp_server = mcp_server
        self.model = model
        self.ask_approval = ask_approval or _always_reject
        self.report = report or _quiet

    async def handle(self, task):
        result = Result(task)
        try:
            async with Client(self.mcp_server) as client:
                listed = (await client.list_tools()).tools
                self.tools = [{"name": t.name, "description": t.description, "input_schema": t.input_schema}
                              for t in listed]
                self.read_only = {t.name for t in listed if t.annotations and t.annotations.read_only_hint}
                self.risky = {t.name for t in listed if t.annotations and t.annotations.destructive_hint}
                await self._run(client, task, result)
        except AgentError as error:
            result.outcome, result.summary = "error", str(error)
        except Exception as error:                     # the agent must never take the website down
            result.outcome, result.summary = "error", f"{type(error).__name__}: {error}"
        await self.report("finished", f"{result.outcome.upper()}: {result.summary}", {
            "outcome": result.outcome, "cost_usd": round(result.cost_usd, 4), "tool_calls": result.tool_calls})
        return result

    async def _run(self, client, task, result):
        await self.report("task", task, {})
        messages = [{"role": "user", "content": PLAN_PROMPT.format(task=task)}]
        result.plan = await self._loop(client, messages, result, planning=True)
        await self.report("plan", result.plan, {})

        messages.append({"role": "user", "content": ACT_PROMPT})
        for round_ in range(MAX_REPLANS + 1):
            result.summary = await self._loop(client, messages, result, planning=False)
            if result.outcome == "stopped":
                return
            verdict = await self._check(client, task, result)
            await self.report("check", verdict, {})
            if verdict.upper().startswith("DONE"):
                result.outcome = "done"
                return
            if round_ == MAX_REPLANS:
                result.outcome = "not_done"
                result.summary = f"{result.summary} (check: {verdict})"
                return
            messages.append({"role": "user", "content": REPLAN_PROMPT.format(feedback=verdict)})

    async def _loop(self, client, messages, result, planning):
        """Model -> tools -> model ... until the model answers without tools. Returns its final text."""
        errors = 0
        while True:
            reply = await self.model.create(SYSTEM, messages, self.tools)
            self._count(reply, result)
            messages.append({"role": "assistant", "content": reply.content})
            text = " ".join(b.text for b in reply.content if b.type == "text" and b.text).strip()
            calls = [b for b in reply.content if b.type == "tool_use"]
            if not calls:
                return text
            if text:
                await self.report("thinking", text, {})
            outputs = []
            for call in calls:
                if result.tool_calls >= MAX_STEPS:
                    result.outcome = "stopped"
                    outputs.append(_tool_result(call, f"Stopped: the limit of {MAX_STEPS} tool calls is reached.", True))
                    continue
                output, failed = await self._call(client, call, result, planning)
                errors += failed
                outputs.append(_tool_result(call, output, failed))
            messages.append({"role": "user", "content": outputs})
            if result.outcome == "stopped":
                result.summary = f"Stopped after {MAX_STEPS} tool calls."
                return result.summary
            if errors >= MAX_TOOL_ERRORS:
                result.outcome = "stopped"
                return f"Stopped after {errors} failed tool calls."

    async def _call(self, client, call, result, planning):
        name, args = call.name, dict(call.input or {})
        if planning and name not in self.read_only:
            return f"Not now: step 1 is only for looking. {name} changes the warehouse; use it in step 2.", True
        if name in self.risky:
            await self.report("approval_needed", f"{name} {json.dumps(args)}", {"tool": name, "input": args})
            approved, note = await self.ask_approval({"tool": name, "input": args, "reason": args.get("reason", "")})
            result.approvals.append({"tool": name, "input": args, "approved": approved, "note": note})
            if not approved:
                await self.report("rejected", f"{name} was rejected" + (f": {note}" if note else ""), {})
                return f"A person REJECTED this action{': ' + note if note else ''}. Do not ask again for the same thing.", True
        result.tool_calls += 1
        answer = await client.call_tool(name, args)
        output = "\n".join(c.text for c in answer.content if c.type == "text")
        failed = bool(answer.is_error)
        result.actions.append({"tool": name, "input": args, "ok": not failed, "result": output[:300]})
        await self.report("tool", f"{name}({_short(args)}) -> {'ERROR ' if failed else ''}{_short_text(output)}",
                          {"tool": name, "input": args, "ok": not failed})
        return output, failed

    async def _check(self, client, task, result):
        status = await client.call_tool("get_fleet_status", {})
        status_text = "\n".join(c.text for c in status.content if c.type == "text")
        prompt = CHECK_PROMPT.format(task=task, plan=result.plan, status=status_text[:6000],
                                     actions=json.dumps([{k: a[k] for k in ("tool", "input", "ok")} for a in result.actions]))
        reply = await self.model.create("You are a strict, fair reviewer.", [{"role": "user", "content": prompt}])
        self._count(reply, result)
        return " ".join(b.text for b in reply.content if b.type == "text").strip() or "NOT DONE: no answer"

    @staticmethod
    def _count(reply, result):
        u = reply.usage
        result.cost_usd += (
            (getattr(u, "input_tokens", 0) or 0) * PRICE_PER_MTOK["input"]
            + (getattr(u, "output_tokens", 0) or 0) * PRICE_PER_MTOK["output"]
            + (getattr(u, "cache_read_input_tokens", 0) or 0) * PRICE_PER_MTOK["cache_read"]
            + (getattr(u, "cache_creation_input_tokens", 0) or 0) * PRICE_PER_MTOK["cache_write"]) / 1_000_000


def _tool_result(call, output, failed):
    block = {"type": "tool_result", "tool_use_id": call.id, "content": output or "(no output)"}
    if failed:
        block["is_error"] = True
    return block


def _short(args):
    return ", ".join(f"{k}={v!r}" for k, v in args.items() if k != "reason")


def _short_text(text, limit=140):
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + " ..."


async def _always_reject(request):
    return False, "no person is available to approve"


async def _quiet(kind, text, data):
    return None
