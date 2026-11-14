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

Over MQTT (LiveSession(broker=...)): the session does not step the simulation
itself. A FleetLink (scripts/fleet_link.py) does: it sends VDA 5050 orders to
the robots and reads their states back. Robots that stop answering are "lost".
"""

from scripts.demand import clock, OPEN_HOUR, CLOSE_HOUR
from scripts.record import build_simulation, layout_data, snapshot
from scripts.scenarios import SCENARIOS
from scripts.simulation import LATEST_END_TICK
from scripts.fleet_link import FleetLink, start_robots

DEFAULT_LAYOUT = "layouts/main_warehouse.json"
MIN_SPEED, MAX_SPEED = 1, 60      # ticks per second
RECENT_EVENTS = 50                # how many past events a newly opened tab gets

# Which robot tasks count as "busy" and "charging" in the KPI bar.
# Everything else (no task, going home) counts as idle.
BUSY_TASKS = {"to_pickup", "to_dropoff"}
CHARGING_TASKS = {"to_charger", "charging", "wait_charger"}
INIT_WAIT = 2.0                   # seconds to wait for robots to answer initPosition (MQTT)


class LiveSession:

    def __init__(self, layout_path=DEFAULT_LAYOUT, scenario="normal", speed=6,
                 broker=None, robots="inproc", broker_name="", link_options=None):
        """
        broker=None: the simulation runs here, as before.
        broker=InMemoryBroker() or MqttBroker(...): the fleet runs over MQTT.
            robots="inproc"   the session starts a simulated robot for every robot of the scenario
            robots="external" the robots run somewhere else (python -m scripts.robot_agent)
        """
        self.layout_path = layout_path
        self.speed = speed
        self.running = False
        self.broker = broker
        self.robots_mode = robots
        self.broker_name = broker_name
        self.link_options = link_options or {}
        self.agents = {}               # robot id -> RobotAgent (only robots="inproc")
        self.link = None
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
        if self.broker is not None:
            self._connect_fleet()
        self._events_sent = 0     # how many of sim.events the browsers have already received
        # A realistic day (1 tick = 1 minute from 06:00) has an end: orders stop at 22:00,
        # and the day closes when the last order is delivered (Simulation.day_over).
        # Other scenarios run forever.
        self.realistic = self.sim.config.demand == "realistic"

    def _connect_fleet(self):
        """Make sure every robot of this scenario has a running robot (inproc), then take control."""
        if self.robots_mode == "inproc":
            missing = [rid for rid in sorted(self.sim.warehouse.robots) if rid not in self.agents]
            heartbeat = type(self.broker).__name__ != "InMemoryBroker"
            for agent in start_robots(self.broker, missing, self.layout_path, heartbeat=heartbeat):
                self.agents[agent.serial] = agent
        if self.link is None:
            self.link = FleetLink(self.sim, self.broker, wait=INIT_WAIT, **self.link_options)
        else:
            self.link.attach(self.sim, wait=INIT_WAIT)

    def check_link(self):
        """While paused: notice robots that went silent or came back. True if anything changed."""
        return self.link.check_heartbeats() if self.link else False

    def close(self):
        for agent in self.agents.values():
            agent.stop()
        if self.link:
            self.link.close()

    @property
    def finished(self):
        """True when a realistic day is over: after 22:00 and every order delivered."""
        return self.sim.day_over()

    def reset(self):
        """Back to tick 0 of the same scenario. Same seed, so the same run again."""
        self.load_scenario(self.scenario)

    def start(self):
        if self.finished:
            raise ValueError("The day is over. Press Reset to start it again.")
        self.running = True

    def pause(self):
        self.running = False

    def step(self):
        """Advance the simulation by exactly one tick (and stop when the day is over)."""
        if self.finished:
            self.running = False
            return
        if self.link:
            self.link.step()
        else:
            self.sim.step()
        if self.finished:
            self.running = False                  # closing time: stop by itself

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
            if self.finished:
                raise ValueError("The day is over. Press Reset to start it again.")
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
            "day": self.day_info(),
            "transport": self.transport_info(),
        }

    def transport_info(self):
        """How the manager talks to the robots: directly ("local") or over MQTT."""
        if self.link is None:
            return {"mode": "local"}
        return {"mode": "mqtt", "broker": self.broker_name or "in-memory", "robots": self.robots_mode}

    def day_info(self):
        """For a realistic day: the date and opening hours (None for scenarios that run forever)."""
        if not self.realistic:
            return None
        demand, cfg = self.sim.demand, self.sim.config
        return {
            "date": demand.date(cfg.demand_day).strftime("%A %d %B %Y"),   # e.g. Friday 02 October 2026
            "open": f"{OPEN_HOUR:02d}:00",
            "close": f"{CLOSE_HOUR:02d}:00",                                 # no new orders after this
            "latest_end": clock(LATEST_END_TICK),                            # stop anyway (work left over)
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
        if self.link:
            links = self.link.link_status()
            for robot in frame["robots"]:
                robot.update(links.get(robot["id"], {}))
        return {
            "type": "state",
            "tick": frame["tick"],
            "running": self.running,
            "speed": self.speed,
            "robots": frame["robots"],
            "blocked": frame["blocked"],
            "queue": frame["queue"],
            "events": [list(e) for e in events],
            "clock": clock(frame["tick"]) if self.realistic else None,   # 1 tick = 1 minute from 06:00
            "finished": self.finished,
            "kpis": self.kpis(frame["robots"]),
        }

    def kpis(self, robots):
        """The simulation's own KPIs, plus throughput and robot counts for the KPI bar."""
        k = self.sim.kpis()
        tick = self.sim.tick
        k["throughput"] = round(k["orders_delivered"] / tick * 100, 1) if tick else 0.0
        lost = sum(1 for r in robots if r["task"] == "lost")
        broken = sum(1 for r in robots if r["status"] == "error") - lost
        busy = sum(1 for r in robots if r["status"] != "error" and r["task"] in BUSY_TASKS)
        charging = sum(1 for r in robots if r["status"] != "error" and r["task"] in CHARGING_TASKS)
        k["robots_busy"] = busy
        k["robots_charging"] = charging
        k["robots_broken"] = broken
        k["robots_lost"] = lost
        k["robots_idle"] = len(robots) - busy - charging - broken - lost
        return k
