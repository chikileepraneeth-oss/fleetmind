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


# ---------------------------------------------------------------------------
# A realistic day has a clock, an opening and a closing time
# ---------------------------------------------------------------------------

def test_realistic_day_has_a_clock_and_a_date():
    s = LiveSession(LAYOUT, scenario="realistic_friday")
    hello = s.hello_message()
    assert hello["day"]["date"] == "Friday 02 October 2026"
    assert hello["day"]["open"] == "06:00" and hello["day"]["close"] == "22:00"
    assert s.state_message()["clock"] == "06:00"
    for _ in range(90):
        s.step()
    assert s.state_message()["clock"] == "07:30"


def test_realistic_day_closes_when_the_last_order_is_delivered():
    """No new orders after 22:00, but every order of the day is still delivered before closing."""
    s = LiveSession(LAYOUT, scenario="realistic_friday")
    s.start()
    while s.running:
        s.step()
    msg = s.state_message()
    assert msg["finished"] is True and msg["running"] is False
    assert s.sim.tick >= 960                                 # never before 22:00
    assert s.sim.open_orders() == 0                          # every order of the day is done
    assert max(o.created_at for o in s.sim.orders) < 960   # no orders after 22:00
    assert msg["clock"] == "22:24"                           # this Friday: the last delivery
    with pytest.raises(ValueError, match="Reset"):
        s.handle({"type": "start"})
    with pytest.raises(ValueError, match="Reset"):
        s.handle({"type": "step"})
    s.handle({"type": "reset"})                  # reset opens the day again
    assert s.sim.tick == 0 and s.state_message()["finished"] is False


def test_a_busy_day_stays_open_until_the_work_is_done():
    """With 4 robots instead of 5, the last order is delivered at 00:49: the day stays open until then."""
    sim = build_simulation("realistic_friday", LAYOUT)
    for robot_id in sorted(sim.warehouse.robots)[4:]:
        robot = sim.warehouse.robots.pop(robot_id)
        del sim.warehouse.occupied[robot.position]
    while not sim.day_over():
        sim.step()
    assert sim.tick == 1129 and sim.open_orders() == 0          # 00:49


def test_a_day_that_cannot_finish_stops_the_next_morning():
    from scripts.simulation import LATEST_END_TICK
    sim = build_simulation("realistic_friday", LAYOUT)
    for robot_id in sorted(sim.warehouse.robots):
        sim.mark_lost(robot_id)                  # nobody can work
    while not sim.day_over():
        sim.step()
    assert sim.tick == LATEST_END_TICK and sim.open_orders() > 0


def test_other_scenarios_run_forever_without_a_clock():
    s = LiveSession(LAYOUT, scenario="normal")
    assert s.hello_message()["day"] is None
    for _ in range(600):                         # longer than the scenario's 500 ticks
        s.step()
    msg = s.state_message()
    assert msg["clock"] is None and msg["finished"] is False and s.sim.tick == 600


# ---------------------------------------------------------------------------
# A new random day, and WHY a cell is blocked
# ---------------------------------------------------------------------------

def test_a_new_random_day_differs_and_reset_repeats_it():
    s = LiveSession(LAYOUT, scenario="normal")
    first_seed = s.seed
    s.handle({"type": "new_day"})
    assert s.seed != first_seed and s.hello_message()["random_day"] is True
    for _ in range(150):
        s.step()
    events = list(s.sim.events)
    s.handle({"type": "reset"})                       # reset = the SAME random day again
    for _ in range(150):
        s.step()
    assert s.sim.events == events
    normal = build_simulation("normal", LAYOUT)
    normal.run(150)
    assert normal.events != events                     # and it really is another day


def test_every_blocked_cell_says_why():
    s = LiveSession(LAYOUT, scenario="blocked_aisles")
    for _ in range(40):
        s.step()
    info = s.state_message()["blocked_info"]
    assert info and len(info) == len(s.sim.warehouse.blocked)
    assert {b["kind"] for b in info} <= {"box", "spill", "pallet", "person", "closed", "lost_robot"}
    assert all(b["why"] and b["until"] > s.sim.tick for b in info)


def test_block_reasons_do_not_change_the_run():
    """The reasons come from their own random generator: the same seed still gives the same run."""
    a = build_simulation("blocked_aisles", LAYOUT)
    a.run(200)
    assert a.kpis()["blocked_cells"] > 0
    b = build_simulation("blocked_aisles", LAYOUT)
    b.story_rng.random()                               # even if the story generator is used differently
    b.run(200)
    assert [e[:3] for e in a.events] == [e[:3] for e in b.events]
