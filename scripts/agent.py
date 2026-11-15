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
        self.label = model
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


PROVIDERS = {
    "groq": "https://api.groq.com/openai/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": "http://localhost:11434/v1",
}
FREE = {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0}
MAX_WAIT_SECONDS = 30              # a free tier says "too many requests": wait at most this long per try
MAX_TRIES = 4


class OpenAICompatibleModel:
    """
    Any model behind an OpenAI-compatible chat API: free tiers of Groq, Gemini or OpenRouter,
    or a model running on your own computer with Ollama. Tool calls are translated both ways,
    so the agent works the same as with Claude.
    """

    def __init__(self, model, base_url, api_key="", prices=None, max_tokens=2048, transport=None, timeout=120):
        import httpx
        self.model = model
        self.label = model
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.prices = prices or FREE
        self.max_tokens = max_tokens
        self.client = httpx.AsyncClient(timeout=timeout, transport=transport)

    async def create(self, system, messages, tools=None):
        import asyncio
        body = {"model": self.model, "max_tokens": self.max_tokens,
                "messages": [{"role": "system", "content": system}] + _to_openai(messages)}
        if tools:
            body["tools"] = [{"type": "function", "function": {
                "name": t["name"], "description": t["description"], "parameters": t["input_schema"]}} for t in tools]
            body["tool_choice"] = "auto"
        for attempt in range(MAX_TRIES):
            response = await self.client.post(self.url, json=body, headers=self.headers)
            if response.status_code == 429 and attempt < MAX_TRIES - 1:
                wait = _retry_after(response)
                if wait <= MAX_WAIT_SECONDS:
                    await asyncio.sleep(wait)
                    continue
            break
        if response.status_code == 429:
            raise AgentError("The free AI quota is used up for now. Try again later.")
        if response.status_code in (401, 403):
            raise AgentError("The AI key is missing or wrong (check AI_API_KEY).")
        if response.status_code >= 400:
            raise AgentError(f"The AI service answered {response.status_code}: {response.text[:200]}")
        return _from_openai(response.json())


def _retry_after(response):
    try:
        return float(response.headers.get("retry-after", "5"))
    except ValueError:
        return 5.0


def _to_openai(messages):
    """The agent's messages (Anthropic style: content blocks) -> OpenAI chat messages."""
    out = []
    for message in messages:
        content = message["content"]
        if isinstance(content, str):
            out.append({"role": message["role"], "content": content})
            continue
        if message["role"] == "user":
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    text = block["content"] if not block.get("is_error") else "ERROR: " + block["content"]
                    out.append({"role": "tool", "tool_call_id": block["tool_use_id"], "content": text})
                elif isinstance(block, dict) and block.get("type") == "text":
                    out.append({"role": "user", "content": block["text"]})
            continue
        text = " ".join(_get(b, "text") for b in content if _get(b, "type") == "text" and _get(b, "text"))
        calls = [{"id": _get(b, "id"), "type": "function",
                  "function": {"name": _get(b, "name"), "arguments": json.dumps(_get(b, "input") or {})}}
                 for b in content if _get(b, "type") == "tool_use"]
        entry = {"role": "assistant", "content": text or None}
        if calls:
            entry["tool_calls"] = calls
        out.append(entry)
    return out


def _from_openai(data):
    """An OpenAI chat answer -> the agent's Reply (text and tool_use blocks)."""
    choice = data["choices"][0]
    message = choice.get("message") or {}
    blocks = [Block("text", text=message["content"])] if message.get("content") else []
    for call in message.get("tool_calls") or []:
        try:
            args = json.loads(call["function"].get("arguments") or "{}")
        except ValueError:
            args = {}                                      # a broken tool call: the tool answers with an error
        blocks.append(Block("tool_use", id=call.get("id") or f"call_{len(blocks)}",
                            name=call["function"]["name"], input=args if isinstance(args, dict) else {}))
    usage = data.get("usage") or {}
    stop = "tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn"
    return Reply(blocks, stop, Usage(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)))


def _get(block, key):
    return block.get(key) if isinstance(block, dict) else getattr(block, key, None)


def model_from_env(env=None):
    """
    Which AI the supervisor uses, from settings (environment or .env):
        AI_PROVIDER = claude | groq | gemini | openrouter | ollama | openai   (empty = no AI)
        AI_MODEL    = the model name of that provider
        AI_API_KEY  = the key (claude: ANTHROPIC_API_KEY; ollama: none)
        AI_BASE_URL = only for AI_PROVIDER=openai (any OpenAI-compatible service)
    """
    import os
    env = os.environ if env is None else env
    provider = (env.get("AI_PROVIDER") or "").strip().lower()
    if not provider:
        return None
    if provider == "claude":
        return ClaudeModel(model=env.get("AI_MODEL") or MODEL)
    base = env.get("AI_BASE_URL") if provider == "openai" else PROVIDERS.get(provider)
    if not base:
        raise ValueError(f"Unknown AI_PROVIDER '{provider}'. Use: claude, {', '.join(PROVIDERS)} or openai.")
    if not env.get("AI_MODEL"):
        raise ValueError(f"Set AI_MODEL to a model name of {provider} (see its model list).")
    return OpenAICompatibleModel(env["AI_MODEL"], base, env.get("AI_API_KEY", ""))


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
    tokens_in: int = 0
    tokens_out: int = 0


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
        except Exception as error:                     # the agent must never take the website down
            error = _unwrap(error)                     # errors inside the MCP client come wrapped in a group
            result.outcome = "error"
            result.summary = str(error) if isinstance(error, AgentError) else f"{type(error).__name__}: {error}"
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
        output = _compact("\n".join(c.text for c in answer.content if c.type == "text"))
        failed = bool(answer.is_error)
        result.actions.append({"tool": name, "input": args, "ok": not failed, "result": output[:300]})
        await self.report("tool", f"{name}({_short(args)}) -> {'ERROR ' if failed else ''}{_short_text(output)}",
                          {"tool": name, "input": args, "ok": not failed})
        return output, failed

    async def _check(self, client, task, result):
        status = await client.call_tool("get_fleet_status", {})
        status_text = _compact("\n".join(c.text for c in status.content if c.type == "text"))
        prompt = CHECK_PROMPT.format(task=task, plan=result.plan, status=status_text[:6000],
                                     actions=json.dumps([{k: a[k] for k in ("tool", "input", "ok")} for a in result.actions]))
        reply = await self.model.create("You are a strict, fair reviewer.", [{"role": "user", "content": prompt}])
        self._count(reply, result)
        return " ".join(b.text for b in reply.content if b.type == "text").strip() or "NOT DONE: no answer"

    def _count(self, reply, result):
        u = reply.usage
        prices = getattr(self.model, "prices", PRICE_PER_MTOK)
        tokens_in = getattr(u, "input_tokens", 0) or 0
        tokens_out = getattr(u, "output_tokens", 0) or 0
        result.tokens_in += tokens_in
        result.tokens_out += tokens_out
        result.cost_usd += (
            tokens_in * prices["input"]
            + tokens_out * prices["output"]
            + (getattr(u, "cache_read_input_tokens", 0) or 0) * prices["cache_read"]
            + (getattr(u, "cache_creation_input_tokens", 0) or 0) * prices["cache_write"]) / 1_000_000


def _unwrap(error):
    """The first real error inside (nested) exception groups."""
    while isinstance(error, BaseExceptionGroup) and error.exceptions:
        error = error.exceptions[0]
    return error


def _compact(text):
    """Tool results are JSON: the same data without spaces and line breaks costs about half the tokens."""
    try:
        return json.dumps(json.loads(text), separators=(",", ":"), ensure_ascii=False)
    except (ValueError, TypeError):
        return text


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
