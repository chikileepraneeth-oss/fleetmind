"""
A robot that is controlled over MQTT with VDA 5050-style messages.

The robot only knows the map (walls, shelves, chargers) and itself: where it
is, its battery, what it carries, and the order it is driving. It does NOT
know the other robots, the order queue or the plan. That is the fleet
manager's job (scripts/fleet_link.py).

It listens to:
    uagv/v2/FleetMind/<id>/order            a route; drive the released nodes, do their actions
    uagv/v2/FleetMind/<id>/instantActions   do it now (charging, pause, init position, ...)
    fleetmind/sim/clock                     one simulation tick has passed: do this tick's work

It publishes:
    uagv/v2/FleetMind/<id>/state            after every tick, and every second as a heartbeat
    uagv/v2/FleetMind/<id>/connection       ONLINE when it starts, OFFLINE when it stops cleanly,
                                            CONNECTIONBROKEN (sent by the broker, its last will)

One tick, in this fixed order (the same order as the simulation, so a healthy
robot ends every tick exactly where the manager expects it):
    1. a broken robot counts down its repair time
    2. instant actions that arrived for this tick
    3. orders that arrived for this tick: drive the released nodes, do the node actions
    4. charging: +5% if it is charging on a charger

Run robots on their own (the manager must run too, e.g. python -m web.server --mqtt external):
    python -m scripts.robot_agent R1 R2 R3 R4 R5
    python -m scripts.robot_agent --count 5 --broker 127.0.0.1:1883
"""

import argparse
import threading
import time

from scripts import vda5050 as v
from scripts.classes import Robot, CHARGER, DRIVABLE
from scripts.layout import load_layout

DEFAULT_LAYOUT = "layouts/main_warehouse.json"
HEARTBEAT_SECONDS = 1.0
KEEP_ACTION_STATES = 20


class RobotAgent:

    def __init__(self, serial, broker, layout_path=DEFAULT_LAYOUT, warehouse=None):
        self.serial = serial
        self.broker = broker
        self.map = warehouse or load_layout(layout_path)   # only walls/shelves/chargers are used
        self.header = v.Header(serial)
        self.lock = threading.RLock()
        self.conn = None
        self._stop = threading.Event()

        # what the robot knows about itself
        self.position = None               # unknown until the manager sends initPosition
        self.battery = 0.0
        self.load = None
        self.charging = False
        self.paused = False
        self.repair_left = 0
        self.tick = 0
        self.driving = False

        # the order it is working on
        self.order_id = ""
        self.update_id = 0
        self.nodes = []                    # node dicts from the order (released and horizon)
        self.last_node_id = ""
        self.last_seq = 0
        self.action_states = {}            # actionId -> state, newest last
        self.order_errors = []             # rejected orders, reported in "errors" until the next good order

        self.inbox = []                    # (kind, message) waiting for the next clock tick

    # ------------------------------------------------------------------
    # Connecting
    # ------------------------------------------------------------------

    def start(self, heartbeat=False):
        """Connect, say ONLINE, listen. heartbeat=True also sends the state every second (real MQTT)."""
        will = (v.topic(self.serial, "connection"),
                v.connection(self.header, self.tick, "CONNECTIONBROKEN"), True)
        self.conn = self.broker.connect(self.serial, will=will)
        self.conn.subscribe(v.topic(self.serial, "order"), lambda t, m: self._receive("order", m))
        self.conn.subscribe(v.topic(self.serial, "instantActions"), lambda t, m: self._receive("instantActions", m))
        self.conn.subscribe(v.CLOCK_TOPIC, lambda t, m: self.on_clock(m))
        self.conn.publish(v.topic(self.serial, "connection"),
                          v.connection(self.header, self.tick, "ONLINE"), retain=True)
        self.publish_state()
        if heartbeat:
            threading.Thread(target=self._heartbeat, daemon=True, name=f"{self.serial}-heartbeat").start()
        return self

    def stop(self):
        """A clean shutdown: say OFFLINE, then disconnect."""
        self._stop.set()
        if self.conn and self.conn.open:
            self.conn.publish(v.topic(self.serial, "connection"),
                              v.connection(self.header, self.tick, "OFFLINE"), retain=True)
            self.conn.close()

    def crash(self):
        """Vanish without a goodbye (in-memory broker only). The broker sends our last will."""
        self._stop.set()
        self.conn.crash()

    def go_silent(self):
        """Stay connected but stop answering, like a robot whose computer froze."""
        self._stop.set()
        self.conn.subscriptions.clear()

    def _heartbeat(self):
        while not self._stop.wait(HEARTBEAT_SECONDS):
            try:
                self.publish_state()
            except Exception:
                pass                       # the broker went away; paho reconnects by itself

    # ------------------------------------------------------------------
    # Receiving
    # ------------------------------------------------------------------

    def _receive(self, kind, message):
        with self.lock:
            try:
                v.validate(kind, message)
            except v.MessageError as error:
                self.order_errors.append(_error("validationError", "WARNING", str(error)))
                self.publish_state()
                return
            if kind == "instantActions":
                # these can't wait for the next tick: the manager needs the answer now
                now = [a for a in message["actions"] if a["actionType"] in ("initPosition", "stateRequest", "cancelOrder")]
                later = [a for a in message["actions"] if a not in now]
                for act in now:
                    self._instant(act)
                if now:
                    self.publish_state()
                if not later:
                    return
                message = {**message, "actions": later}
            self.inbox.append((kind, message))

    def on_clock(self, message):
        """One tick: repair, instant actions, orders (drive), charging. Then report."""
        with self.lock:
            self.tick = int(message["tick"])
            self.driving = False
            if self.repair_left > 0:
                self.repair_left -= 1
            inbox, self.inbox = self.inbox, []
            for kind, msg in inbox:
                if kind == "instantActions":
                    for act in msg["actions"]:
                        self._instant(act)
            for kind, msg in inbox:
                if kind == "order":
                    self._accept_order(msg)
                    self._drive()
            self._drive()
            if self.charging and self.repair_left == 0 and self.position is not None \
                    and self.map.cell_type(self.position) == CHARGER and self.battery < 100:
                self.battery = min(100.0, self.battery + Robot.CHARGE_PER_TICK)
                if self.battery >= 100:
                    self.charging = False
            self.publish_state()

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def _accept_order(self, msg):
        nodes = msg["nodes"]
        first = v.cell_of(nodes[0]["nodeId"])
        if msg["orderId"] != self.order_id:
            # a new order must start where the robot stands
            if self.position is None or first != self.position:
                self.order_errors.append(_error(
                    "orderNoRoute", "WARNING",
                    f"order {msg['orderId']} starts at {first}, but I am at {self.position}"))
                return
            self.order_id = msg["orderId"]
            self.update_id = msg["orderUpdateId"]
            self.nodes = [dict(n) for n in nodes]
            self.last_seq = nodes[0]["sequenceId"]
            self.last_node_id = nodes[0]["nodeId"]
            self.order_errors = []
            self._node_actions(nodes[0])      # we are already on the first node: do its actions now
            return

        if msg["orderUpdateId"] <= self.update_id:
            return                            # an old or repeated update: ignore it (VDA 5050)
        if nodes[0]["nodeId"] != self.last_node_id or nodes[0]["sequenceId"] != self.last_seq:
            self.order_errors.append(_error(
                "orderUpdateError", "WARNING",
                f"update {msg['orderUpdateId']} of {msg['orderId']} starts at {nodes[0]['nodeId']}/"
                f"{nodes[0]['sequenceId']}, but my last node is {self.last_node_id}/{self.last_seq}"))
            return
        self.update_id = msg["orderUpdateId"]
        self.nodes = [n for n in self.nodes if n["sequenceId"] < nodes[0]["sequenceId"]] + [dict(n) for n in nodes]
        self.order_errors = []

    def _drive(self):
        """Drive to every released node we have not reached yet, one cell at a time."""
        for node in self.nodes:
            if node["sequenceId"] <= self.last_seq:
                continue
            if not node["released"]:
                return                        # the horizon: not allowed to go there yet
            if self.repair_left > 0 or self.paused or self.battery < Robot.BATTERY_PER_STEP:
                return
            cell = v.cell_of(node["nodeId"])
            if cell != self.position:
                if abs(cell[0] - self.position[0]) + abs(cell[1] - self.position[1]) != 1:
                    self.order_errors.append(_error("orderNoRoute", "WARNING",
                                                    f"{node['nodeId']} is not next to {self.position}"))
                    return
                if self.map.cell_type(cell) not in DRIVABLE:
                    self.order_errors.append(_error("orderNoRoute", "WARNING",
                                                    f"{node['nodeId']} is not a floor cell"))
                    return
                self.position = cell
                self.battery -= Robot.BATTERY_PER_STEP
                self.driving = True
            self.last_seq = node["sequenceId"]
            self.last_node_id = node["nodeId"]
            self._node_actions(node)

    def _node_actions(self, node):
        for act in node.get("actions", []):
            params = v.params_of(act)
            kind = act["actionType"]
            if kind == "pick":
                if self.load is not None:
                    self._action_done(act, "FAILED", f"already carrying {self.load}")
                    continue
                self.load = params.get("item")
                self._action_done(act, "FINISHED", f"picked {self.load}")
            elif kind == "drop":
                dropped, self.load = self.load, None
                self._action_done(act, "FINISHED", f"dropped {dropped}")
            else:
                self._action_done(act, "FAILED", f"unknown node action '{kind}'")

    # ------------------------------------------------------------------
    # Instant actions
    # ------------------------------------------------------------------

    def _instant(self, act):
        kind = act["actionType"]
        params = v.params_of(act)
        if kind == "initPosition":
            cell = v.cell_at(float(params.get("x", 0)), float(params.get("y", 0)))
            if not self.map.is_inside(cell) or self.map.cell_type(cell) not in DRIVABLE:
                return self._action_done(act, "FAILED", f"{cell} is not a floor cell")
            self.position = cell
            if "battery" in params:
                self.battery = float(params["battery"])
            if "load" in params:
                self.load = params["load"] or None
            self.charging = False
            self.paused = False
            self.repair_left = int(params.get("repairLeft", 0))
            self.order_id, self.update_id, self.nodes = "", 0, []
            self.last_node_id, self.last_seq = v.node_id(cell), 0
            self.order_errors = []
            self.inbox = []
            return self._action_done(act, "FINISHED", f"at {cell}")
        if kind == "stateRequest":
            return self._action_done(act, "FINISHED", "")
        if kind == "cancelOrder":
            self.nodes = [n for n in self.nodes if n["sequenceId"] <= self.last_seq]
            return self._action_done(act, "FINISHED", f"cancelled {self.order_id}")
        if kind == "startCharging":
            if self.position is None or self.map.cell_type(self.position) != CHARGER:
                return self._action_done(act, "FAILED", "not on a charger")
            self.charging = True
            return self._action_done(act, "FINISHED", "")
        if kind == "stopCharging":
            self.charging = False
            return self._action_done(act, "FINISHED", "")
        if kind == "startPause":
            self.paused = True
            return self._action_done(act, "FINISHED", "")
        if kind == "stopPause":
            self.paused = False
            return self._action_done(act, "FINISHED", "")
        # made-up accidents from the simulation (see scripts/vda5050.py)
        if kind == "simBreakdown":
            self.repair_left = int(params["repairTicks"])
            return self._action_done(act, "FINISHED", f"broken for {self.repair_left} ticks")
        if kind in ("simDropLoad", "simRemoveLoad"):
            self.load = None
            return self._action_done(act, "FINISHED", "")
        if kind == "simBatterySwap":
            self.battery = float(params["battery"])
            return self._action_done(act, "FINISHED", f"battery {self.battery}%")
        return self._action_done(act, "FAILED", f"unknown instant action '{kind}'")

    def _action_done(self, act, status, description):
        self.action_states.pop(act["actionId"], None)
        self.action_states[act["actionId"]] = {
            "actionId": act["actionId"], "actionType": act["actionType"],
            "actionStatus": status, "resultDescription": description,
        }
        while len(self.action_states) > KEEP_ACTION_STATES:
            self.action_states.pop(next(iter(self.action_states)))

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def state_message(self):
        with self.lock:
            ahead = [n for n in self.nodes if n["sequenceId"] > self.last_seq]
            node_states = [{"nodeId": n["nodeId"], "sequenceId": n["sequenceId"], "released": n["released"],
                            "nodePosition": n["nodePosition"]} for n in ahead]
            edge_states = []
            prev = self.last_node_id
            for n in ahead:
                edge_states.append({"edgeId": f"{prev}-{n['nodeId']}", "sequenceId": n["sequenceId"] - 1,
                                    "released": n["released"]})
                prev = n["nodeId"]
            errors = list(self.order_errors)
            if self.repair_left > 0:
                errors.append(_error("breakdown", "FATAL", f"repair in {self.repair_left} ticks"))
            if self.position is not None and self.battery < Robot.BATTERY_PER_STEP:
                errors.append(_error("batteryEmpty", "WARNING", "battery empty, waiting for a battery swap"))
            pos = self.position or (0, 0)
            return {
                **self.header.make("state", self.tick),
                "orderId": self.order_id,
                "orderUpdateId": self.update_id,
                "lastNodeId": self.last_node_id,
                "lastNodeSequenceId": self.last_seq,
                "nodeStates": node_states,
                "edgeStates": edge_states,
                "driving": self.driving,
                "paused": self.paused,
                "newBaseRequest": bool(ahead) and not any(n["released"] for n in ahead),
                "distanceSinceLastNode": 0.0,
                "operatingMode": "AUTOMATIC",
                "actionStates": list(self.action_states.values()),
                "batteryState": {"batteryCharge": self.battery, "charging": self.charging},
                "errors": errors,
                "information": [],
                "safetyState": {"eStop": "NONE", "fieldViolation": False},
                "agvPosition": {**v.position(pos), "theta": 0.0, "positionInitialized": self.position is not None},
                "loads": [{"loadId": self.load, "loadType": "item"}] if self.load else [],
            }

    def publish_state(self):
        if self.conn and self.conn.open:
            self.conn.publish(v.topic(self.serial, "state"), self.state_message())


def _error(kind, level, description):
    return {"errorType": kind, "errorLevel": level, "errorDescription": description, "errorReferences": []}


def main():
    from scripts.bus import MqttBroker

    parser = argparse.ArgumentParser(description="Run simulated robots that listen to the fleet manager over MQTT.")
    parser.add_argument("robots", nargs="*", help="robot ids, e.g. R1 R2 R3")
    parser.add_argument("--count", type=int, help="start R1 .. R<count>")
    parser.add_argument("--broker", default="127.0.0.1:1883", help="host:port of the MQTT broker")
    parser.add_argument("--layout", default=DEFAULT_LAYOUT)
    args = parser.parse_args()

    serials = args.robots or [f"R{i}" for i in range(1, (args.count or 5) + 1)]
    host, _, port = args.broker.partition(":")
    broker = MqttBroker(host, int(port or 1883))
    agents = [RobotAgent(s, broker, args.layout).start(heartbeat=True) for s in serials]
    print(f"Robots {', '.join(serials)} are online at {args.broker}. Press Ctrl+C to stop them.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    for agent in agents:
        agent.stop()
    print("Robots are offline.")


if __name__ == "__main__":
    main()
