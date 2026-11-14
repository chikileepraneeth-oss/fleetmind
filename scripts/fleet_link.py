"""
The fleet manager's side of MQTT: it runs the Simulation's decisions and talks
to the robots only with VDA 5050-style messages.

One tick:
    1. check heartbeats: a robot we have not heard from for `lost_after` seconds
       (or whose connection broke) is marked LOST; a lost robot that answers again
       is taken back
    2. sim.step(): the fleet brain decides (assignment, routes, charging) and
       PREDICTS what every robot will do this tick
    3. for every robot, turn what it should do into messages:
         moved along its route  -> order (new route) or order update (release the next node)
         picks / delivers       -> a one-node order with a pick / drop action
         starts / stops charging-> instantActions startCharging / stopCharging
         simulated accidents    -> instantActions simBreakdown / simDropLoad / simBatterySwap
    4. publish the clock tick: every robot now does its tick and sends its state
    5. wait for the states (at most `state_timeout` seconds) and compare them with
       the prediction. The ROBOT is the truth: if it says it is somewhere else, or
       has a different battery, the manager believes it (and logs "state_mismatch").

Because a healthy robot does exactly what it is told, a run over MQTT is exactly
the same as a run without it (same seed, same run). A test checks this.

Run a scenario over MQTT without the website:
    python -m scripts.fleet_link --scenario normal --ticks 300                 # in-memory broker
    python -m scripts.fleet_link --scenario normal --broker 127.0.0.1:1883     # Mosquitto, robots in this process
    python -m scripts.fleet_link --broker 127.0.0.1:1883 --robots external     # robots from scripts.robot_agent
"""

import argparse
import threading
import time
from collections import defaultdict

from scripts import vda5050 as v
from scripts.classes import Robot

MANAGER_ID = "fleet-manager"


class FleetLink:

    def __init__(self, sim, broker, client_id=MANAGER_ID, lost_after=3.0, state_timeout=0.5,
                 clock=time.monotonic, wait=0.0):
        self.broker = broker
        self.lost_after = lost_after          # seconds without any message -> lost
        self.state_timeout = state_timeout    # seconds to wait for the states after each tick
        self.clock = clock
        self.cond = threading.Condition()

        self.states = {}                      # robot id -> latest state message
        self.state_tick = {}                  # robot id -> tick of that state
        self.connection = {}                  # robot id -> ONLINE / OFFLINE / CONNECTIONBROKEN
        self.last_seen = {}                   # robot id -> clock() of the last message from it
        self.sent = 0                         # messages sent to robots
        self.received = 0                     # messages received from robots
        self.mismatches = 0

        self.conn = broker.connect(client_id)
        self.conn.subscribe(f"{v.INTERFACE}/{v.MAJOR}/{v.MANUFACTURER}/+/state", self._on_state)
        self.conn.subscribe(f"{v.INTERFACE}/{v.MAJOR}/{v.MANUFACTURER}/+/connection", self._on_connection)
        self.attach(sim, wait)

    # ------------------------------------------------------------------
    # Starting
    # ------------------------------------------------------------------

    def attach(self, sim, wait=0.0):
        """
        Control this simulation (a new one after a reset or a scenario change).
        Every robot gets initPosition with the simulation's start position, battery
        and load, so robots that are already running start the new day correctly.
        """
        self.sim = sim
        self.headers = {rid: v.Header(rid) for rid in sim.warehouse.robots}
        self.orders = {}                      # robot id -> the order it is driving now
        self.order_count = defaultdict(int)
        self.action_count = defaultdict(int)
        self.charging = defaultdict(bool)
        self.trace = defaultdict(list)
        self.started = self.clock()
        self.events_sent = len(sim.events)
        self._install_trace()
        for rid in sorted(sim.warehouse.robots):
            self._init_position(rid)
        if wait:
            self._wait_until(lambda: all(self._initialized(r) for r in sim.warehouse.robots), wait)

    def _install_trace(self):
        """Write down every cell each robot moves into during a tick (the route to send)."""
        wh = self.sim.warehouse
        original = type(wh).move_robot

        def traced(robot, new_position):
            original(wh, robot, new_position)
            self.trace[robot.robot_id].append(new_position)

        wh.move_robot = traced

    def _initialized(self, rid):
        state = self.states.get(rid)
        return bool(state) and state["agvPosition"]["positionInitialized"] \
            and self.state_tick.get(rid, -1) >= 0

    # ------------------------------------------------------------------
    # One tick
    # ------------------------------------------------------------------

    def step(self):
        sim = self.sim
        self.check_heartbeats()
        robots = sim.warehouse.robots
        start = {rid: (r.position, r.battery) for rid, r in robots.items()}
        first_event = self.events_sent           # also what happened between ticks (e.g. a pause by the agent)
        self.trace.clear()

        sim.step()
        tick = sim.tick

        events = defaultdict(list)
        for _, rid, event, details in sim.events[first_event:]:
            if rid:
                events[rid].append(event)
        for rid in sorted(robots):
            if rid not in sim.lost:
                self._send_tick(rid, tick, *start[rid], events[rid])

        self.conn.publish(v.CLOCK_TOPIC, {"tick": tick, "timestamp": v.timestamp(tick)})
        self.events_sent = len(sim.events)
        alive = [rid for rid in robots if rid not in sim.lost]
        self._wait_until(lambda: all(self.state_tick.get(rid, -1) >= tick for rid in alive), self.state_timeout)
        self._reconcile(tick)

    def run(self, ticks):
        for _ in range(ticks):
            self.step()

    def _send_tick(self, rid, tick, start_cell, start_battery, events):
        sim, cfg = self.sim, self.sim.config
        robot = sim.warehouse.robots[rid]
        instant = []

        # what the robot's battery would be WITHOUT charging, to see if it charged this tick
        battery = start_battery
        for event in events:
            if event == "battery_rescue":
                instant.append(self._action(rid, "simBatterySwap", {"battery": cfg.rescue_battery}))
                battery = cfg.rescue_battery
            elif event == "breakdown":
                instant.append(self._action(rid, "simBreakdown", {"repairTicks": cfg.repair_ticks}))
            elif event == "dropped_item":
                instant.append(self._action(rid, "simDropLoad"))
            elif event == "robot_paused":
                instant.append(self._action(rid, "startPause"))
            elif event == "robot_resumed":
                instant.append(self._action(rid, "stopPause"))
        moves = list(self.trace.get(rid, []))
        for _ in moves:
            battery -= Robot.BATTERY_PER_STEP
        charged = robot.battery > battery + 1e-9
        if charged and not self.charging[rid]:
            instant.append(self._action(rid, "startCharging"))
            self.charging[rid] = True
        elif not charged and self.charging[rid]:
            instant.append(self._action(rid, "stopCharging"))
            self.charging[rid] = False
        if instant:
            self._publish(rid, "instantActions", v.instant_actions(self.headers[rid], tick, instant))

        if moves:
            self._send_route(rid, tick, start_cell, moves)
        if "picked" in events:
            self._send_job(rid, tick, "pick", {"item": robot.carrying, "orderId": robot.order.order_id,
                                               "shelf": robot.order.pickup})
        if "delivered" in events:
            self._send_job(rid, tick, "drop", {"orderId": self._last_order_id(rid)})

    def _send_route(self, rid, tick, start_cell, moves):
        """Release the cells the robot drives this tick; the rest of its plan is the horizon."""
        robot = self.sim.warehouse.robots[rid]
        key = (robot.task, robot.target, robot.order.order_id if robot.order else None)
        horizon = []
        for cell in robot.path:
            if cell != (horizon[-1] if horizon else moves[-1]):
                horizon.append(cell)
        current = self.orders.get(rid)
        if current is None or current["key"] != key or current["cell"] != start_cell:
            current = self._new_order(rid, key, start_cell)
            first_sequence, update = 0, 0
        else:
            first_sequence, update = current["seq"], current["update"] + 1
        cells = [start_cell] + moves + horizon
        msg = v.order(self.headers[rid], tick, current["id"], update, cells,
                      released=1 + len(moves), first_sequence=first_sequence)
        current.update(seq=first_sequence + 2 * len(moves), update=update, cell=moves[-1])
        self._publish(rid, "order", msg)

    def _send_job(self, rid, tick, kind, params):
        """Pick or drop where the robot stands: a new order with one node and the action on it."""
        robot = self.sim.warehouse.robots[rid]
        current = self._new_order(rid, (kind, robot.position, params.get("orderId")), robot.position)
        act = self._action(rid, kind, params)
        msg = v.order(self.headers[rid], tick, current["id"], 0, [robot.position], released=1,
                      node_actions={0: [act]})
        self._publish(rid, "order", msg)

    def _new_order(self, rid, key, cell):
        self.order_count[rid] += 1
        self.orders[rid] = {"id": f"{rid}-{self.order_count[rid]}", "key": key, "seq": 0, "update": 0, "cell": cell}
        return self.orders[rid]

    def _last_order_id(self, rid):
        for _, robot_id, event, details in reversed(self.sim.events):
            if robot_id == rid and event == "delivered":
                return details.split()[0]
        return ""

    def _action(self, rid, kind, params=None):
        self.action_count[rid] += 1
        return v.action(kind, f"{rid}-a{self.action_count[rid]}", params)

    def _init_position(self, rid, battery=None):
        robot = self.sim.warehouse.robots[rid]
        pos = v.position(robot.position)
        act = self._action(rid, "initPosition", {
            "x": pos["x"], "y": pos["y"], "theta": 0.0, "mapId": pos["mapId"],
            "lastNodeId": v.node_id(robot.position),
            "battery": robot.battery if battery is None else battery,
            "load": robot.carrying or "", "repairLeft": robot.repair_left,
        })
        self.charging[rid] = False
        self.orders.pop(rid, None)
        self._publish(rid, "instantActions", v.instant_actions(self.headers[rid], self.sim.tick, [act]))

    def _publish(self, rid, kind, msg):
        self.sent += 1
        self.conn.publish(v.topic(rid, kind), msg)

    # ------------------------------------------------------------------
    # Receiving
    # ------------------------------------------------------------------

    def _on_state(self, topic, msg):
        parsed = v.parse_topic(topic)
        if not parsed:
            return
        try:
            v.validate("state", msg)
        except v.MessageError:
            return                            # a broken state message counts as no message
        rid = parsed[0]
        with self.cond:
            self.received += 1
            self.states[rid] = msg
            self.state_tick[rid] = v.tick_of(msg["timestamp"])
            self.last_seen[rid] = self.clock()
            if self.connection.get(rid) in ("OFFLINE", "CONNECTIONBROKEN"):
                # states aren't retained: a robot that sends one NOW is online, whatever an
                # older connection message said (e.g. it reconnected after a network blip)
                self.connection[rid] = "ONLINE"
            self.cond.notify_all()

    def _on_connection(self, topic, msg):
        parsed = v.parse_topic(topic)
        if not parsed:
            return
        try:
            v.validate("connection", msg)
        except v.MessageError:
            return
        rid = parsed[0]
        with self.cond:
            self.received += 1
            self.connection[rid] = msg["connectionState"]
            if msg["connectionState"] == "ONLINE":
                self.last_seen[rid] = self.clock()
            self.cond.notify_all()

    def _wait_until(self, done, timeout):
        deadline = time.monotonic() + timeout
        with self.cond:
            while not done():
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self.cond.wait(left)
        return True

    # ------------------------------------------------------------------
    # Heartbeats, lost and found
    # ------------------------------------------------------------------

    def check_heartbeats(self):
        """Mark silent robots as lost and take back lost robots that answer again. True if anything changed."""
        sim = self.sim
        now = self.clock()
        changed = False
        for rid in sorted(sim.warehouse.robots):
            with self.cond:
                link = self.connection.get(rid)
                last = self.last_seen.get(rid)
                has_state = rid in self.states
            silent_for = now - max(last if last is not None else self.started, self.started)
            gone = link in ("OFFLINE", "CONNECTIONBROKEN")
            if rid not in sim.lost:
                if gone or silent_for > self.lost_after:
                    if link == "CONNECTIONBROKEN":
                        reason = "connection broken (MQTT last will)"
                    elif link == "OFFLINE":
                        reason = "robot went offline"
                    else:
                        reason = f"no heartbeat for {silent_for:.1f} s"
                    sim.mark_lost(rid, reason)
                    self.orders.pop(rid, None)
                    self.charging[rid] = False
                    changed = True
            elif not gone and has_state and silent_for <= self.lost_after:
                self._rejoin(rid)
                changed = True
        return changed

    def _rejoin(self, rid):
        """A lost robot answers again. Believe its position and battery if it knows them."""
        sim, wh = self.sim, self.sim.warehouse
        robot = wh.robots[rid]
        with self.cond:
            state = self.states[rid]
        sim.mark_found(rid)                   # first: this frees the cell it blocked
        battery = None
        if state["agvPosition"]["positionInitialized"]:
            cell = v.cell_at(state["agvPosition"]["x"], state["agvPosition"]["y"])
            if cell != robot.position and wh.is_inside(cell) and wh.can_enter(cell)[0]:
                type(wh).move_robot(wh, robot, cell)
            robot.battery = battery = state["batteryState"]["batteryCharge"]
        self._init_position(rid, battery)     # start fresh: no order, no load, at this cell

    def _reconcile(self, tick):
        """The robot is the truth: compare its state with what the simulation predicted."""
        wh = self.sim.warehouse
        for rid in sorted(wh.robots):
            with self.cond:
                state = self.states.get(rid)
                fresh = self.state_tick.get(rid) == tick
            if rid in self.sim.lost or not fresh or not state["agvPosition"]["positionInitialized"]:
                continue
            robot = wh.robots[rid]
            cell = v.cell_at(state["agvPosition"]["x"], state["agvPosition"]["y"])
            battery = state["batteryState"]["batteryCharge"]
            load = state["loads"][0]["loadId"] if state["loads"] else None
            if cell == robot.position and abs(battery - robot.battery) < 1e-6 and load == robot.carrying:
                continue
            self.mismatches += 1
            self.sim.log("state_mismatch", rid,
                         f"expected {robot.position} {robot.battery:.1f}% {robot.carrying}, "
                         f"robot says {cell} {battery:.1f}% {load}")
            if cell != robot.position and wh.is_inside(cell) and wh.can_enter(cell)[0]:
                type(wh).move_robot(wh, robot, cell)
                robot.path = []
                self.orders.pop(rid, None)
            robot.battery = battery

    # ------------------------------------------------------------------
    # For the website and the agent later
    # ------------------------------------------------------------------

    def link_status(self):
        """Per robot: online / lost, and seconds since we last heard from it."""
        now = self.clock()
        with self.cond:
            return {rid: {"link": "lost" if rid in self.sim.lost else (self.connection.get(rid) or "unknown").lower(),
                          "seen": round(now - self.last_seen[rid], 1) if rid in self.last_seen else None}
                    for rid in sorted(self.sim.warehouse.robots)}

    def close(self):
        self.conn.close()


def start_robots(broker, serials, layout_path, heartbeat):
    """Simulated robots, one connection each (used when the robots run in the same process)."""
    from scripts.robot_agent import RobotAgent
    return [RobotAgent(s, broker, layout_path).start(heartbeat=heartbeat) for s in serials]


def main():
    from scripts.bus import InMemoryBroker, MqttBroker
    from scripts.record import build_simulation
    from scripts.scenarios import SCENARIOS

    parser = argparse.ArgumentParser(description="Run a scenario where the manager and robots talk over MQTT.")
    parser.add_argument("--scenario", default="normal", choices=sorted(SCENARIOS))
    parser.add_argument("--ticks", type=int, help="default: the scenario's length")
    parser.add_argument("--broker", help="host:port of an MQTT broker (default: in-memory, no broker needed)")
    parser.add_argument("--robots", choices=("inproc", "external"), default="inproc",
                        help="inproc: start the robots here; external: they run in python -m scripts.robot_agent")
    parser.add_argument("--layout", default="layouts/main_warehouse.json")
    args = parser.parse_args()

    if args.broker:
        host, _, port = args.broker.partition(":")
        broker = MqttBroker(host, int(port or 1883))
    else:
        broker = InMemoryBroker()
    sim = build_simulation(args.scenario, args.layout)
    agents = []
    if args.robots == "inproc":
        agents = start_robots(broker, sorted(sim.warehouse.robots), args.layout, heartbeat=bool(args.broker))
    link = FleetLink(sim, broker, wait=3.0 if args.broker else 0.0)

    realistic = sim.config.demand == "realistic" and not args.ticks
    ticks = args.ticks or SCENARIOS[args.scenario]["ticks"]
    began = time.perf_counter()
    # a realistic day runs until its last order is delivered; otherwise a fixed number of ticks
    while (not sim.day_over()) if realistic else sim.tick < ticks:
        link.step()
        if sim.tick % 100 == 0:
            print(f"tick {sim.tick}: {sim.kpis()['orders_delivered']} delivered, lost: {sorted(sim.lost) or 'none'}")
    ticks = sim.tick
    took = time.perf_counter() - began

    k = sim.kpis()
    print(f"\n{args.scenario}: {ticks} ticks in {took:.1f} s over {'MQTT ' + args.broker if args.broker else 'the in-memory bus'}")
    print(f"  delivered {k['orders_delivered']} / {k['orders_created']}, late {k['late_orders']}, "
          f"on time {k['on_time_rate']}, robots lost {k['robots_lost']}")
    print(f"  messages: {link.sent} to robots, {link.received} from robots, state mismatches: {link.mismatches}")
    for agent in agents:
        agent.stop()
    link.close()


if __name__ == "__main__":
    main()
