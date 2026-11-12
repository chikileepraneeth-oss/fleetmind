"""
Tests for LiveSession (web/session.py), the logic behind the live website.
No server or browser here; tests/test_server.py tests the web layer on top.
"""

import json

import pytest

from scripts.record import build_simulation
from web.session import LiveSession, MAX_SPEED, MIN_SPEED

LAYOUT = "layouts/main_warehouse.json"


def test_new_session_is_paused_at_tick_zero():
    s = LiveSession(LAYOUT)
    assert s.running is False
    assert s.sim.tick == 0
    assert s.state_message()["tick"] == 0


def test_step_advances_exactly_one_tick():
    s = LiveSession(LAYOUT)
    s.handle({"type": "step"})
    s.handle({"type": "step"})
    assert s.sim.tick == 2


def test_start_and_pause_switch_running():
    s = LiveSession(LAYOUT)
    assert s.handle({"type": "start"})[0]["running"] is True
    assert s.handle({"type": "pause"})[0]["running"] is False


def test_step_pauses_a_running_session():
    s = LiveSession(LAYOUT)
    s.handle({"type": "start"})
    s.handle({"type": "step"})
    assert s.running is False


def test_reset_goes_back_to_the_start():
    s = LiveSession(LAYOUT)
    start = s.state_message()["robots"]
    for _ in range(30):
        s.step()
    messages = s.handle({"type": "reset"})
    assert [m["type"] for m in messages] == ["hello", "state"]
    assert messages[1]["tick"] == 0
    assert messages[1]["robots"] == start


def test_speed_is_kept_in_range():
    s = LiveSession(LAYOUT)
    s.handle({"type": "speed", "value": 12})
    assert s.speed == 12
    s.handle({"type": "speed", "value": 1000})
    assert s.speed == MAX_SPEED
    s.handle({"type": "speed", "value": 0})
    assert s.speed == MIN_SPEED


@pytest.mark.parametrize("command", [
    {"type": "speed", "value": "fast"},
    {"type": "scenario", "key": "does_not_exist"},
    {"type": "fly"},
    {"no_type": True},
    "start",
])
def test_bad_commands_raise_value_error(command):
    with pytest.raises(ValueError):
        LiveSession(LAYOUT).handle(command)


def test_switching_scenario_builds_that_scenario():
    s = LiveSession(LAYOUT)
    hello, state = s.handle({"type": "scenario", "key": "single_robot"})
    assert hello["scenario"] == "single_robot"
    assert len(state["robots"]) == 1
    assert state["tick"] == 0


def test_hello_message_has_layout_and_all_scenarios():
    hello = LiveSession(LAYOUT).hello_message()
    assert hello["type"] == "hello"
    assert {"height", "width", "walls", "shelves", "chargers", "packing", "docks"} <= hello["layout"].keys()
    assert "rush_hour" in [s["key"] for s in hello["scenarios"]]


def test_state_message_format():
    s = LiveSession(LAYOUT)
    for _ in range(5):
        s.step()
    msg = s.state_message()
    assert {"type", "tick", "running", "speed", "robots", "blocked", "queue", "events", "kpis"} <= msg.keys()
    robot = msg["robots"][0]
    assert {"id", "pos", "status", "task", "battery", "carrying", "order", "target", "path", "repair"} <= robot.keys()
    k = msg["kpis"]
    for key in ("throughput", "avg_delivery_time", "on_time_rate", "orders_waiting",
                "robots_busy", "robots_idle", "robots_charging", "robots_broken"):
        assert key in k
    assert k["robots_busy"] + k["robots_idle"] + k["robots_charging"] + k["robots_broken"] == len(msg["robots"])
    json.dumps(msg)            # must be sendable as JSON (no tuples of enums, no sets, ...)


def test_state_message_only_sends_new_events():
    s = LiveSession(LAYOUT)
    received = []
    for _ in range(100):
        s.step()
        received += s.state_message()["events"]
    assert received == [list(e) for e in s.sim.events]     # every event exactly once, in order
    assert s.state_message()["events"] == []               # nothing new since the last message


def test_same_seed_gives_same_run_as_plain_simulation():
    s = LiveSession(LAYOUT, scenario="normal")
    for _ in range(200):
        s.handle({"type": "step"})
    direct = build_simulation("normal", LAYOUT)
    direct.run(200)
    assert s.sim.events == direct.events
    assert s.sim.kpis() == direct.kpis()
