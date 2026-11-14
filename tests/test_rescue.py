"""
A lost robot is not given up: a technician walks to it and restarts it (web/session.py).
"""

from collections import Counter

from scripts.bus import InMemoryBroker
from web.session import LOST_RESCUE_TICKS, LiveSession

LAYOUT = "layouts/main_warehouse.json"


def session(scenario="calm", robots="inproc"):
    return LiveSession(LAYOUT, scenario=scenario, broker=InMemoryBroker(), robots=robots,
                       link_options={"state_timeout": 0})


def test_a_lost_robot_is_restarted_by_a_technician_and_works_again():
    s = session()
    for _ in range(20):
        s.step()
    s.agents["R2"].crash()                                 # its connection drops
    s.step()
    assert "R2" in s.sim.lost
    assert any(e[2] == "technician_sent" and e[1] == "R2" for e in s.sim.events)
    assert s.state_message()["robots"][1]["rescue_in"] > 0  # the 3D view sends a technician
    for _ in range(LOST_RESCUE_TICKS + 2):
        s.step()
    assert "R2" not in s.sim.lost
    assert any(e[2] == "technician_restarted" and e[1] == "R2" for e in s.sim.events)
    assert any(e[2] == "robot_found" and e[1] == "R2" for e in s.sim.events)
    for _ in range(150):
        s.step()
    assert s.sim.warehouse.robots["R2"].task != "lost"
    assert s.link.mismatches == 0
    s.close()


def test_a_whole_day_of_connection_trouble_ends_with_every_robot_back():
    s = session("connection_trouble")
    for _ in range(500):
        s.step()
    events = Counter(e[2] for e in s.sim.events)
    assert events["robot_lost"] >= 3
    assert events["technician_restarted"] == events["robot_lost"] - len(s.sim.lost)
    assert len(s.sim.lost) <= 1                          # at most one is still waiting for its technician
    assert s.sim.kpis()["collisions"] == 0 and s.link.mismatches == 0
    s.close()


def test_a_robot_program_in_someone_elses_terminal_is_only_reported():
    s = session(robots="external")
    from scripts.fleet_link import start_robots
    agents = {a.serial: a for a in start_robots(s.broker, sorted(s.sim.warehouse.robots), LAYOUT, heartbeat=False)}
    s.link.attach(s.sim)
    s.step()
    agents["R3"].crash()
    for _ in range(LOST_RESCUE_TICKS + 3):
        s.step()
    assert "R3" in s.sim.lost                            # nobody here can switch it on again
    assert any(e[2] == "technician_report" and e[1] == "R3" for e in s.sim.events)
    s.close()


def test_without_mqtt_there_is_nothing_to_rescue():
    s = LiveSession(LAYOUT, scenario="connection_trouble")
    for _ in range(100):
        s.step()
    assert not s.sim.lost and not s.rescue
