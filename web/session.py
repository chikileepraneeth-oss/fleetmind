"""
LiveSession: one running simulation that the live website controls.

This file has NO web code in it. It only knows how to:
    - build a Simulation for a scenario (the same way scripts/record.py does)
    - start, pause, step one tick, reset, change speed, switch scenario
    - turn the current state into plain dicts ("messages") that can be sent
      to the browser as JSON

The web server (web/server.py) is just a thin layer around this class:
it passes the browser's commands to handle() and sends the messages back.
Keeping the logic here means we can test it without a browser or a server.

Messages (all are dicts with a "type" field):

    server -> browser
        {"type": "hello", ...}   the parts that don't change every tick (map, scenario list)
        {"type": "state", ...}   everything that changes every tick
        {"type": "error", ...}   a command was wrong

    browser -> server
        {"type": "start"} {"type": "pause"} {"type": "step"} {"type": "reset"}
        {"type": "speed", "value": 10}
        {"type": "scenario", "key": "rush_hour"}
"""

from scripts.record import build_simulation, layout_data, snapshot
from scripts.scenarios import SCENARIOS

DEFAULT_LAYOUT = "layouts/main_warehouse.json"
MIN_SPEED, MAX_SPEED = 1, 60      # ticks per second
RECENT_EVENTS = 50                # how many past events a newly opened tab gets

# Which robot tasks count as "busy" and "charging" in the KPI bar.
# Everything else (no task, going home) counts as idle.
BUSY_TASKS = {"to_pickup", "to_dropoff"}
CHARGING_TASKS = {"to_charger", "charging", "wait_charger"}


class LiveSession:

    def __init__(self, layout_path=DEFAULT_LAYOUT, scenario="normal", speed=6):
        self.layout_path = layout_path
        self.speed = speed
        self.running = False
        self.load_scenario(scenario)

    # ------------------------------------------------------------------
    # Controls
    # ------------------------------------------------------------------

    def load_scenario(self, key):
        """Build a fresh simulation for this scenario, at tick 0, paused."""
        if key not in SCENARIOS:
            raise ValueError(f"unknown scenario '{key}'")
        self.scenario = key
        self.sim = build_simulation(key, self.layout_path)
        self.running = False
        self._events_sent = 0     # how many of sim.events the browsers have already received

    def reset(self):
        """Back to tick 0 of the same scenario. Same seed, so the same run again."""
        self.load_scenario(self.scenario)

    def start(self):
        self.running = True

    def pause(self):
        self.running = False

    def step(self):
        """Advance the simulation by exactly one tick."""
        self.sim.step()

    def set_speed(self, value):
        """Ticks per second. Kept between MIN_SPEED and MAX_SPEED."""
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("speed must be a number")
        self.speed = max(MIN_SPEED, min(MAX_SPEED, int(value)))

    def handle(self, command):
        """
        Carry out one command from the browser.

        Returns the messages that every open browser tab should receive now.
        Raises ValueError if the command is wrong (the server turns that into
        an "error" message for the tab that sent it).
        """
        if not isinstance(command, dict):
            raise ValueError("a command must be a JSON object")
        kind = command.get("type")

        if kind == "start":
            self.start()
            return [self.state_message()]
        if kind == "pause":
            self.pause()
            return [self.state_message()]
        if kind == "step":
            self.pause()                      # stepping by hand only makes sense while paused
            self.step()
            return [self.state_message()]
        if kind == "speed":
            self.set_speed(command.get("value"))
            return [self.state_message()]
        if kind == "reset":
            self.reset()
            return [self.hello_message(), self.state_message()]
        if kind == "scenario":
            self.load_scenario(command.get("key"))
            return [self.hello_message(), self.state_message()]
        raise ValueError(f"unknown command type '{kind}'")

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    def hello_message(self):
        """The fixed parts: map, scenario list, current scenario. Sent on connect and after a reset."""
        spec = SCENARIOS[self.scenario]
        return {
            "type": "hello",
            "scenarios": [{"key": k, "title": s["title"], "description": s["description"]}
                          for k, s in SCENARIOS.items()],
            "scenario": self.scenario,
            "seed": spec["seed"],
            "layout": layout_data(self.sim.warehouse),
            # the latest events, so a tab opened in the middle of a run isn't empty
            "events": [list(e) for e in self.sim.events[-RECENT_EVENTS:]],
        }

    def state_message(self, new_events=True):
        """
        Everything that changes every tick.

        "events" holds only the events that were not sent before, so the message
        stays small. With new_events=False the list is empty and nothing is marked
        as sent (used when one new tab connects: it gets past events in "hello").
        """
        if new_events:
            events = self.sim.events[self._events_sent:]
            self._events_sent = len(self.sim.events)
        else:
            events = []

        frame = snapshot(self.sim)            # robots, blocked cells, queue: same format as the replay viewer
        return {
            "type": "state",
            "tick": frame["tick"],
            "running": self.running,
            "speed": self.speed,
            "robots": frame["robots"],
            "blocked": frame["blocked"],
            "queue": frame["queue"],
            "events": [list(e) for e in events],
            "kpis": self.kpis(frame["robots"]),
        }

    def kpis(self, robots):
        """The simulation's own KPIs, plus throughput and robot counts for the KPI bar."""
        k = self.sim.kpis()
        tick = self.sim.tick
        k["throughput"] = round(k["orders_delivered"] / tick * 100, 1) if tick else 0.0
        broken = sum(1 for r in robots if r["status"] == "error")
        busy = sum(1 for r in robots if r["status"] != "error" and r["task"] in BUSY_TASKS)
        charging = sum(1 for r in robots if r["status"] != "error" and r["task"] in CHARGING_TASKS)
        k["robots_busy"] = busy
        k["robots_charging"] = charging
        k["robots_broken"] = broken
        k["robots_idle"] = len(robots) - busy - charging - broken
        return k
