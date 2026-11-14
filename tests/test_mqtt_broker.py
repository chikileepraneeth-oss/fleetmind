"""
The fleet over a REAL MQTT broker (Mosquitto in Docker).

These tests are skipped when no broker is running. Start one with:
    docker compose up -d
"""

import os
import socket
import subprocess
import sys
import time

import pytest

from scripts.bus import MqttBroker
from scripts.fleet_link import FleetLink, start_robots
from scripts.record import build_simulation, snapshot

LAYOUT = "layouts/main_warehouse.json"
HOST = "127.0.0.1"
PORT = int(os.environ.get("FLEETMIND_MQTT_PORT", "1883"))   # another broker, e.g. while a demo uses 1883


def broker_running():
    try:
        with socket.create_connection((HOST, PORT), timeout=0.5):
            return True
    except OSError:
        return False


def fleet_online():
    """Robots that talk on this broker right now (e.g. the live demo). The tests use the same ids."""
    if not broker_running():
        return []
    online = set()
    conn = MqttBroker(HOST, PORT).connect("test-probe")
    conn.subscribe("uagv/v2/FleetMind/+/connection",
                   lambda t, m: online.add(m["serialNumber"]) if m.get("connectionState") == "ONLINE" else None)
    conn.subscribe("uagv/v2/FleetMind/+/state", lambda t, m: online.add(m["serialNumber"]))   # heartbeats
    time.sleep(1.5)                                                                           # > 1 heartbeat
    conn.close()
    return sorted(online)


pytestmark = [
    pytest.mark.skipif(not broker_running(), reason=f"no MQTT broker on {HOST}:{PORT} (docker compose up -d)"),
    # robots with the same ids would throw a running fleet off the broker: never fight a live demo
    pytest.mark.skipif(bool(fleet_online()), reason=f"a fleet is running on {HOST}:{PORT}; "
                                                    f"use another broker: FLEETMIND_MQTT_PORT=1884"),
]


def test_a_run_over_mosquitto_is_exactly_the_same_run():
    broker = MqttBroker(HOST, PORT)
    direct = build_simulation("normal", LAYOUT)
    sim = build_simulation("normal", LAYOUT)
    agents = start_robots(broker, sorted(sim.warehouse.robots), LAYOUT, heartbeat=True)
    link = FleetLink(sim, broker, client_id="test-manager", wait=3.0)
    try:
        for _ in range(60):
            direct.step()
            link.step()
        assert snapshot(sim) == snapshot(direct)
        assert link.mismatches == 0
        assert not sim.lost
    finally:
        for agent in agents:
            agent.stop()
        link.close()


def test_a_killed_robot_process_is_marked_lost():
    """A robot in its own process is killed (no goodbye). The manager notices and works on."""
    broker = MqttBroker(HOST, PORT)
    sim = build_simulation("calm", LAYOUT)
    others = start_robots(broker, ["R1", "R2", "R3", "R4"], LAYOUT, heartbeat=True)
    robot = subprocess.Popen([sys.executable, "-m", "scripts.robot_agent", "R5", "--broker", f"{HOST}:{PORT}"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    link = FleetLink(sim, broker, client_id="test-manager-2", lost_after=2.0)
    try:
        deadline = time.monotonic() + 10
        while not link._initialized("R5") and time.monotonic() < deadline:
            link.attach(sim, wait=1.0)
        assert link._initialized("R5"), "the robot process did not start"
        for _ in range(20):
            link.step()
        assert "R5" not in sim.lost

        robot.kill()
        deadline = time.monotonic() + 15
        while "R5" not in sim.lost and time.monotonic() < deadline:
            link.step()
        assert "R5" in sim.lost
        assert sim.warehouse.robots["R5"].task == "lost"
        assert sim.kpis()["collisions"] == 0
    finally:
        robot.kill()
        for agent in others:
            agent.stop()
        link.close()
