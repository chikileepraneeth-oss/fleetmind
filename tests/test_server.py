"""
Tests for the FastAPI server (web/server.py).

TestClient runs the app inside the test, without a real network, and
websocket_connect() opens a WebSocket to it, just like the browser does.
Every test builds a fresh app, so tests don't share a simulation.
"""

import time
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from scripts.record import build_simulation
from web.server import create_app


@pytest.fixture
def client():
    with TestClient(create_app()) as c:      # "with" also starts the background ticker
        yield c


@contextmanager
def connect(client):
    """Open the WebSocket, read the two welcome messages, and close it again at the end."""
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        state = ws.receive_json()
        yield ws, hello, state


def hub(client):
    return client.app.state.hub


def test_live_page_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "FleetMind" in response.text


def test_connect_gives_hello_then_state(client):
    with connect(client) as (ws, hello, state):
        assert hello["type"] == "hello"
        assert hello["scenario"] == "normal"
        assert "layout" in hello and "scenarios" in hello
        assert state["type"] == "state"
        assert state["tick"] == 0
        assert state["running"] is False


def test_state_message_format(client):
    with connect(client) as (ws, _, _):
        ws.send_json({"type": "step"})
        msg = ws.receive_json()
        assert {"type", "tick", "running", "speed", "robots", "blocked", "queue", "events", "kpis"} <= msg.keys()
        assert {"id", "pos", "status", "task", "battery", "carrying", "order", "target", "path", "repair"} <= msg["robots"][0].keys()
        assert {"throughput", "avg_delivery_time", "on_time_rate", "orders_waiting",
                "robots_busy", "robots_idle", "robots_charging"} <= msg["kpis"].keys()
        for event in msg["events"]:
            assert len(event) == 4 and event[0] == msg["tick"]     # [tick, robot, event, details]


def test_step_advances_one_tick_at_a_time(client):
    with connect(client) as (ws, _, _):
        ws.send_json({"type": "step"})
        assert ws.receive_json()["tick"] == 1
        ws.send_json({"type": "step"})
        assert ws.receive_json()["tick"] == 2


def test_start_runs_by_itself_and_pause_stops_it(client):
    with connect(client) as (ws, _, _):
        ws.send_json({"type": "speed", "value": 60})
        ws.receive_json()
        ws.send_json({"type": "start"})
        msg = ws.receive_json()
        while msg["tick"] < 5:                    # the ticker keeps sending new ticks
            msg = ws.receive_json()
        ws.send_json({"type": "pause"})
        time.sleep(0.1)                           # let anything already on its way arrive
        stopped_at = hub(client).session.sim.tick
        time.sleep(0.3)
        assert hub(client).session.sim.tick == stopped_at
        assert hub(client).session.running is False


def test_reset_goes_back_to_tick_zero(client):
    with connect(client) as (ws, _, first):
        for _ in range(10):
            ws.send_json({"type": "step"})
            ws.receive_json()
        ws.send_json({"type": "reset"})
        assert ws.receive_json()["type"] == "hello"
        state = ws.receive_json()
        assert state["tick"] == 0
        assert state["robots"] == first["robots"]


def test_choose_scenario(client):
    with connect(client) as (ws, _, _):
        ws.send_json({"type": "scenario", "key": "single_robot"})
        hello = ws.receive_json()
        state = ws.receive_json()
        assert hello["scenario"] == "single_robot"
        assert len(state["robots"]) == 1


def test_change_speed(client):
    with connect(client) as (ws, _, _):
        ws.send_json({"type": "speed", "value": 20})
        assert ws.receive_json()["speed"] == 20


@pytest.mark.parametrize("text", [
    '{"type": "fly"}',
    '{"type": "scenario", "key": "nope"}',
    '{"type": "speed", "value": "fast"}',
    'this is not json',
])
def test_bad_command_gives_error_and_connection_stays_open(client, text):
    with connect(client) as (ws, _, _):
        ws.send_text(text)
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"type": "step"})            # still works afterwards
        assert ws.receive_json()["tick"] == 1


def test_every_tab_sees_the_same_commands(client):
    with connect(client) as (ws_a, _, _), connect(client) as (ws_b, _, _):
        ws_a.send_json({"type": "step"})
        assert ws_a.receive_json()["tick"] == 1
        assert ws_b.receive_json()["tick"] == 1


def test_same_seed_gives_same_run_over_the_websocket(client):
    with connect(client) as (ws, _, _):
        events = []
        for _ in range(200):
            ws.send_json({"type": "step"})
            msg = ws.receive_json()
            events += msg["events"]

        direct = build_simulation("normal", "layouts/main_warehouse.json")
        direct.run(200)
        assert events == [list(e) for e in direct.events]
        assert msg["tick"] == direct.tick
        assert {k: msg["kpis"][k] for k in direct.kpis()} == direct.kpis()
