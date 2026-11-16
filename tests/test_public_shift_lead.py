"""
The public demo with the Shift Lead: cleaners, tickets and playbooks run, but the LLM never runs
and no API key is read. Visitors start incidents with buttons and watch the rules handle them.
"""

import sys
import types

import pytest
from fastapi.testclient import TestClient

from scripts.agent import ScriptedModel
from web import server
from web.server import create_app

LAYOUT = "layouts/main_warehouse.json"


def receive(ws, check, limit=200):
    for _ in range(limit):
        msg = ws.receive_json()
        if check(msg):
            return msg
    raise AssertionError("message not received")


def public_app(model=None):
    return create_app(LAYOUT, public=True, shift_lead=True, agent_model=model)


def test_the_public_demo_never_gets_an_llm_even_when_one_is_passed():
    model = ScriptedModel(["should never be used"])
    app = public_app(model)
    hub = app.state.hub
    assert hub.desk.enabled is False and hub.session.lead.manager_allowed is False
    assert app.state.mcp is None
    with TestClient(app) as client:
        assert client.post("/mcp", json={}).status_code == 404
        with client.websocket_connect("/ws") as ws:
            hello = ws.receive_json()
            assert hello["agent"]["enabled"] is False
            ws.send_json({"type": "agent_incident", "kind": "rush"})            # no playbook: needs a manager
            state = receive(ws, lambda m: m["type"] == "state" and m["shift_lead"]["incidents"])
            surge = [i for i in state["shift_lead"]["incidents"] if i["kind"] == "order_surge"]
            assert surge and surge[0]["handled_by"] == "nobody"
            ws.send_json({"type": "agent_ask", "text": "close every aisle"})          # only simple commands here
            reply = receive(ws, lambda m: m["type"] == "agent" and m["event"] == "reply")
            assert "no AI in this demo" in reply["text"]
    assert model.calls == []


def test_a_visitors_spill_is_handled_by_the_rules():
    app = public_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "agent_incident", "kind": "spill"})
            state = receive(ws, lambda m: m["type"] == "state" and m["shift_lead"]["incidents"])
    incident = state["shift_lead"]["incidents"][-1]
    assert incident["kind"] == "spill" and incident["handled_by"] == "rules"
    assert incident["decision"] == "send a person" and incident["ticket"]
    assert any(t["kind"] == "clean" for t in state["tickets"])


def test_one_incident_button_per_visitor_every_half_minute():
    app = public_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "agent_incident", "kind": "breakdowns"})
            ws.send_json({"type": "agent_incident", "kind": "spill"})
            error = receive(ws, lambda m: m["type"] == "error")
            assert f"One incident every {server.PUBLIC_INCIDENT_COOLDOWN} seconds" in error["message"]


def test_public_main_reads_no_api_key(monkeypatch):
    """Start the real public server (without serving): nothing may load .env or build a model."""
    def forbidden(*args, **kwargs):
        raise AssertionError("the public demo must not read AI settings or keys")

    import dotenv
    import scripts.agent
    monkeypatch.setattr(dotenv, "load_dotenv", forbidden)
    monkeypatch.setattr(scripts.agent, "model_from_env", forbidden)
    monkeypatch.setattr(scripts.agent, "ClaudeModel", forbidden)
    monkeypatch.setenv("AI_PROVIDER", "groq")
    monkeypatch.setenv("AI_API_KEY", "a-key-that-must-not-be-used")
    monkeypatch.setenv("FLEETMIND_PUBLIC_AI", "on")
    started = {}
    monkeypatch.setitem(sys.modules, "uvicorn", types.SimpleNamespace(run=lambda app, **kw: started.update(app=app)))
    monkeypatch.setattr(sys, "argv", ["server", "--public", "--agent", "--no-browser", "--port", "8799"])
    server.main()
    app = started["app"]
    assert app.state.hub.desk.enabled is False and app.state.mcp is None
    assert app.state.hub.session.lead is not None                   # the rules still run


def test_the_render_blueprint_has_no_ai_settings():
    text = open("render.yaml", encoding="utf-8").read()
    for name in ("AI_API_KEY", "AI_MODEL", "AI_PROVIDER", "FLEETMIND_PUBLIC_AI", "ANTHROPIC_API_KEY"):
        assert name not in text


@pytest.mark.parametrize("kind", ["spill", "breakdowns", "rush"])
def test_every_button_works_in_the_public_demo(kind):
    app = public_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "agent_incident", "kind": kind})
            state = receive(ws, lambda m: m["type"] == "state" and m["shift_lead"]["incidents"])
    assert state["shift_lead"]["counts"]["llm"] == 0
