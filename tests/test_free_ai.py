"""
The supervisor with a free AI (Groq, Gemini, OpenRouter, Ollama): the OpenAI-compatible adapter.
A fake chat server (httpx.MockTransport) answers like those services do, so no key is needed.
"""

import asyncio
import json

import httpx
import pytest

from scripts import agent as agent_module
from scripts.agent import (AgentError, ClaudeModel, FleetAgent, OpenAICompatibleModel, PROVIDERS,
                           model_from_env)
from web.server import create_app

LAYOUT = "layouts/main_warehouse.json"


def tool_call(name, args, call_id):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def answer(content=None, calls=None):
    return {"choices": [{"message": {"role": "assistant", "content": content, "tool_calls": calls}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 50}}


SCRIPT = [
    answer("Looking first.", [tool_call("get_fleet_status", {}, "c1")]),
    answer("1. Close aisle BC because of the spill."),
    answer(None, [tool_call("close_aisle", {"aisle": "BC", "reason": "A spill in aisle BC."}, "c2")]),
    answer("I closed aisle BC."),
    answer("DONE: aisle BC is closed."),
]


class FakeChatServer:
    """Answers /chat/completions from a script and remembers every request."""

    def __init__(self, script, statuses=None):
        self.script = list(script)
        self.statuses = list(statuses or [])
        self.requests = []

    def __call__(self, request):
        self.requests.append(json.loads(request.content))
        if self.statuses:
            status, headers = self.statuses.pop(0)
            return httpx.Response(status, headers=headers, json={"error": "nope"})
        return httpx.Response(200, json=self.script.pop(0))


def run(server, approve=True):
    app = create_app(LAYOUT, scenario="calm")
    model = OpenAICompatibleModel("free-model", PROVIDERS["groq"], "key", transport=httpx.MockTransport(server))

    async def ask(request):
        return approve, ""

    result = asyncio.run(FleetAgent(app.state.mcp, model, ask).handle("A spill was reported in aisle BC."))
    return result, app.state.hub.session.sim


def test_a_whole_incident_with_a_free_openai_compatible_model():
    server = FakeChatServer(SCRIPT)
    result, sim = run(server)
    assert result.outcome == "done"
    assert "BC" in sim.closed_aisles
    assert result.cost_usd == 0 and result.tokens_in == 5000 and result.tokens_out == 250


def test_requests_are_translated_to_the_openai_format():
    server = FakeChatServer(SCRIPT)
    run(server)
    first = server.requests[0]
    assert first["model"] == "free-model" and first["messages"][0]["role"] == "system"
    assert {t["function"]["name"] for t in first["tools"]} >= {"get_fleet_status", "close_aisle"}
    assert first["tools"][0]["type"] == "function" and "parameters" in first["tools"][0]["function"]
    second = server.requests[1]["messages"]
    assert second[-2]["role"] == "assistant" and second[-2]["tool_calls"][0]["id"] == "c1"
    assert second[-1]["role"] == "tool" and second[-1]["tool_call_id"] == "c1"
    json.loads(second[-1]["content"])                     # the fleet status, as compact JSON
    assert "\n" not in second[-1]["content"]


def test_too_many_requests_is_waited_out(monkeypatch):
    async def no_sleep(seconds):
        return None
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    server = FakeChatServer(SCRIPT, statuses=[(429, {"retry-after": "2"})])
    result, _ = run(server)
    assert result.outcome == "done"


def test_a_used_up_free_quota_gives_a_clear_message():
    server = FakeChatServer(SCRIPT, statuses=[(429, {"retry-after": "3600"})])
    result, sim = run(server)
    assert result.outcome == "error" and "free AI quota is used up" in result.summary
    assert not sim.closed_aisles


def test_a_wrong_key_gives_a_clear_message():
    server = FakeChatServer(SCRIPT, statuses=[(401, {})])
    result, _ = run(server)
    assert result.outcome == "error" and "key is missing or wrong" in result.summary


def test_a_broken_tool_call_from_a_small_model_does_not_crash():
    script = [answer(None, [{"id": "x", "type": "function",
                             "function": {"name": "get_robot", "arguments": "{not json"}}]),
              answer("1. Nothing."), answer("Done."), answer("DONE: nothing to do.")]
    result, _ = run(FakeChatServer(script))
    assert result.outcome == "done"
    assert result.actions[0]["ok"] is False                # the tool said what was wrong


# ----- choosing the AI with settings -----

@pytest.mark.parametrize("provider", ["groq", "gemini", "openrouter", "ollama"])
def test_settings_pick_a_free_provider(provider):
    model = model_from_env({"AI_PROVIDER": provider, "AI_MODEL": "some-model", "AI_API_KEY": "k"})
    assert isinstance(model, OpenAICompatibleModel)
    assert model.url == PROVIDERS[provider] + "/chat/completions" and model.prices["input"] == 0


def test_settings_for_any_openai_compatible_service():
    model = model_from_env({"AI_PROVIDER": "openai", "AI_MODEL": "m", "AI_BASE_URL": "https://example.test/v1/"})
    assert model.url == "https://example.test/v1/chat/completions"


def test_no_provider_means_no_ai():
    assert model_from_env({}) is None


def test_claude_is_still_an_option():
    assert isinstance(model_from_env({"AI_PROVIDER": "claude"}), ClaudeModel)


@pytest.mark.parametrize("env, error", [
    ({"AI_PROVIDER": "grok-free"}, "Unknown AI_PROVIDER"),
    ({"AI_PROVIDER": "groq"}, "Set AI_MODEL"),
])
def test_wrong_settings_give_a_clear_error(env, error):
    with pytest.raises(ValueError, match=error):
        model_from_env(env)
