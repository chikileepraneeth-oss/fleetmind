"""
VDA 5050-style messages between the fleet manager and the robots.

VDA 5050 is a standard (from the German automotive industry) for how a fleet
manager talks to mobile robots over MQTT. Every message is JSON. The main ones:

    order           manager -> robot   a route (nodes and edges) with actions on the nodes
    instantActions  manager -> robot   do this NOW (start charging, pause, cancel the order, ...)
    state           robot -> manager   where I am, battery, load, errors, how far I got with the order
    connection      robot -> manager   ONLINE / OFFLINE / CONNECTIONBROKEN (the broker sends the last
                                       one for us if the robot disappears: the MQTT "last will")

Topics follow the standard: uagv/v2/<manufacturer>/<serialNumber>/<topic>,
for example uagv/v2/FleetMind/R1/state.

How a route works:
    An order is a list of nodes (cells) joined by edges. Nodes can be "released"
    (the robot may drive there) or not yet released (the "horizon": the planned
    rest of the route, so the robot knows where it is going). The manager sends
    order UPDATES that release more nodes. This is how it does traffic control:
    a robot only drives as far as it is allowed to.

What is FleetMind's own (not in the standard):
    - The simulation clock topic fleetmind/sim/clock. In a simulation all robots
      must do one tick together. A real robot (or Isaac Sim) has its own clock.
    - Timestamps are SIMULATION time: 1 tick = 1 minute after SIM_EPOCH.
    - Instant actions starting with "sim" (simBreakdown, simDropLoad,
      simBatterySwap, simRemoveLoad). They only exist because accidents are
      made up by the simulation. In Isaac Sim, physics or staff would cause them.
    - initPosition has two extra parameters, battery and load, because a
      simulated robot that restarts has no battery or load of its own to remember.
"""

from datetime import datetime, timedelta, timezone

VERSION = "2.0.0"
INTERFACE = "uagv"
MAJOR = "v2"
MANUFACTURER = "FleetMind"
MAP_ID = "main_warehouse"
CELL_SIZE = 1.0                        # metres per grid cell (x = column, y = row)
SIM_EPOCH = datetime(2026, 6, 1, 6, 0, tzinfo=timezone.utc)   # tick 0

CLOCK_TOPIC = "fleetmind/sim/clock"
KINDS = ("order", "instantActions", "state", "connection")
CONNECTION_STATES = ("ONLINE", "OFFLINE", "CONNECTIONBROKEN")
BLOCKING_TYPES = ("NONE", "SOFT", "HARD")
ACTION_STATUSES = ("WAITING", "INITIALIZING", "RUNNING", "PAUSED", "FINISHED", "FAILED")


class MessageError(ValueError):
    """A message is missing a field or has a wrong value. The text says exactly what."""


# ---------------------------------------------------------------------------
# Topics, nodes and time
# ---------------------------------------------------------------------------

def topic(serial, kind):
    """uagv/v2/FleetMind/R1/state"""
    if kind not in KINDS:
        raise ValueError(f"unknown topic kind '{kind}'")
    return f"{INTERFACE}/{MAJOR}/{MANUFACTURER}/{serial}/{kind}"


def parse_topic(name):
    """'uagv/v2/FleetMind/R1/state' -> ('R1', 'state'), or None if it is not a robot topic."""
    parts = name.split("/")
    if len(parts) != 5 or parts[:3] != [INTERFACE, MAJOR, MANUFACTURER] or parts[4] not in KINDS:
        return None
    return parts[3], parts[4]


def node_id(cell):
    """(3, 4) -> 'n3_4'. Node ids are just the cell, so both sides agree without a node list."""
    return f"n{cell[0]}_{cell[1]}"


def cell_of(node):
    """'n3_4' -> (3, 4)"""
    try:
        row, col = node[1:].split("_")
        return int(row), int(col)
    except (ValueError, AttributeError, TypeError):
        raise MessageError(f"bad nodeId '{node}' (expected like 'n3_4')") from None


def position(cell):
    """A cell as a VDA 5050 position in metres."""
    return {"x": cell[1] * CELL_SIZE, "y": cell[0] * CELL_SIZE, "mapId": MAP_ID}


def cell_at(x, y):
    """Metres back to the nearest cell."""
    return round(y / CELL_SIZE), round(x / CELL_SIZE)


def timestamp(tick):
    """Simulation time as ISO 8601: tick 0 = 2026-06-01T06:00:00.000Z, 1 tick = 1 minute."""
    t = SIM_EPOCH + timedelta(minutes=tick)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def tick_of(stamp):
    """The tick a timestamp belongs to."""
    try:
        t = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        raise MessageError(f"bad timestamp '{stamp}'") from None
    return round((t - SIM_EPOCH).total_seconds() / 60)


# ---------------------------------------------------------------------------
# Building messages
# ---------------------------------------------------------------------------

class Header:
    """VDA 5050 header. headerId counts up per topic, so the receiver can spot lost messages."""

    def __init__(self, serial):
        self.serial = serial
        self.counters = {}

    def make(self, kind, tick):
        self.counters[kind] = self.counters.get(kind, -1) + 1
        return {"headerId": self.counters[kind], "timestamp": timestamp(tick), "version": VERSION,
                "manufacturer": MANUFACTURER, "serialNumber": self.serial}


def action(action_type, action_id, params=None, blocking="HARD"):
    return {
        "actionType": action_type,
        "actionId": action_id,
        "blockingType": blocking,
        "actionParameters": [{"key": k, "value": v} for k, v in (params or {}).items()],
    }


def params_of(act):
    """An action's parameters as a dict."""
    return {p["key"]: p["value"] for p in act.get("actionParameters", [])}


def order(header, tick, order_id, update_id, cells, released, first_sequence=0, node_actions=None):
    """
    A route over `cells`. The first `released` cells may be driven to, the rest is the horizon.
    Node i gets sequenceId first_sequence + 2*i, the edge after it first_sequence + 2*i + 1
    (VDA 5050 numbers nodes and edges together: 0, 1, 2, ...).
    node_actions: {index: [action, ...]} actions to do on arriving at that node.
    """
    if not cells:
        raise ValueError("an order needs at least one node")
    node_actions = node_actions or {}
    nodes, edges = [], []
    for i, cell in enumerate(cells):
        nodes.append({
            "nodeId": node_id(cell), "sequenceId": first_sequence + 2 * i, "released": i < released,
            "nodePosition": position(cell), "actions": list(node_actions.get(i, [])),
        })
        if i + 1 < len(cells):
            nxt = cells[i + 1]
            edges.append({
                "edgeId": f"{node_id(cell)}-{node_id(nxt)}", "sequenceId": first_sequence + 2 * i + 1,
                "released": i + 1 < released, "startNodeId": node_id(cell), "endNodeId": node_id(nxt),
                "actions": [],
            })
    return {**header.make("order", tick), "orderId": order_id, "orderUpdateId": update_id,
            "nodes": nodes, "edges": edges}


def instant_actions(header, tick, actions):
    return {**header.make("instantActions", tick), "actions": list(actions)}


def connection(header, tick, state):
    if state not in CONNECTION_STATES:
        raise ValueError(f"unknown connection state '{state}'")
    return {**header.make("connection", tick), "connectionState": state}


# ---------------------------------------------------------------------------
# Checking messages
# ---------------------------------------------------------------------------

def _need(msg, key, kind, where):
    if not isinstance(msg, dict):
        raise MessageError(f"{where} must be a JSON object")
    if key not in msg:
        raise MessageError(f"{where}: missing '{key}'")
    value = msg[key]
    if kind is int and (isinstance(value, bool) or not isinstance(value, int)):
        raise MessageError(f"{where}: '{key}' must be an integer, got {value!r}")
    if kind is not int and not isinstance(value, kind):
        raise MessageError(f"{where}: '{key}' must be {_name(kind)}, got {value!r}")
    return value


def _name(kind):
    names = {str: "a string", list: "a list", dict: "an object", bool: "true or false", float: "a number"}
    if isinstance(kind, tuple):
        return " or ".join(names.get(k, k.__name__) for k in kind)
    return names.get(kind, kind.__name__)


def _check_action(act, where):
    _need(act, "actionType", str, where)
    _need(act, "actionId", str, where)
    if _need(act, "blockingType", str, where) not in BLOCKING_TYPES:
        raise MessageError(f"{where}: blockingType must be one of {', '.join(BLOCKING_TYPES)}")
    for i, p in enumerate(act.get("actionParameters", [])):
        _need(p, "key", str, f"{where}.actionParameters[{i}]")
        if "value" not in p:
            raise MessageError(f"{where}.actionParameters[{i}]: missing 'value'")


def validate(kind, msg):
    """Raise MessageError if the message is not a valid `kind` message. Returns the message."""
    if kind not in KINDS:
        raise MessageError(f"unknown message kind '{kind}'")
    for key, typ in (("headerId", int), ("timestamp", str), ("version", str),
                     ("manufacturer", str), ("serialNumber", str)):
        _need(msg, key, typ, kind)
    tick_of(msg["timestamp"])

    if kind == "order":
        _need(msg, "orderId", str, kind)
        _need(msg, "orderUpdateId", int, kind)
        nodes = _need(msg, "nodes", list, kind)
        edges = _need(msg, "edges", list, kind)
        if not nodes:
            raise MessageError("order: needs at least one node")
        if len(edges) != len(nodes) - 1:
            raise MessageError(f"order: {len(nodes)} nodes need {len(nodes) - 1} edges, got {len(edges)}")
        last_seq, released_ended = None, False
        for i, node in enumerate(nodes):
            where = f"order.nodes[{i}]"
            cell_of(_need(node, "nodeId", str, where))
            seq = _need(node, "sequenceId", int, where)
            if last_seq is not None and seq != last_seq + 2:
                raise MessageError(f"{where}: sequenceId {seq} should be {last_seq + 2}")
            last_seq = seq
            released = _need(node, "released", bool, where)
            if released and released_ended:
                raise MessageError(f"{where}: a released node can't come after the horizon")
            released_ended = released_ended or not released
            for j, act in enumerate(_need(node, "actions", list, where)):
                _check_action(act, f"{where}.actions[{j}]")
        if not nodes[0]["released"]:
            raise MessageError("order: the first node must be released")
        for i, edge in enumerate(edges):
            where = f"order.edges[{i}]"
            _need(edge, "edgeId", str, where)
            if _need(edge, "sequenceId", int, where) != nodes[i]["sequenceId"] + 1:
                raise MessageError(f"{where}: sequenceId should be {nodes[i]['sequenceId'] + 1}")
            _need(edge, "released", bool, where)
            if _need(edge, "startNodeId", str, where) != nodes[i]["nodeId"] or \
                    _need(edge, "endNodeId", str, where) != nodes[i + 1]["nodeId"]:
                raise MessageError(f"{where}: must join {nodes[i]['nodeId']} and {nodes[i + 1]['nodeId']}")

    elif kind == "instantActions":
        for j, act in enumerate(_need(msg, "actions", list, kind)):
            _check_action(act, f"instantActions.actions[{j}]")

    elif kind == "state":
        _need(msg, "orderId", str, kind)
        _need(msg, "orderUpdateId", int, kind)
        _need(msg, "lastNodeId", str, kind)
        _need(msg, "driving", bool, kind)
        _need(msg, "paused", bool, kind)
        _need(msg, "nodeStates", list, kind)
        _need(msg, "edgeStates", list, kind)
        _need(msg, "actionStates", list, kind)
        _need(msg, "errors", list, kind)
        _need(msg, "loads", list, kind)
        battery = _need(msg, "batteryState", dict, kind)
        _need(battery, "batteryCharge", (int, float), "state.batteryState")
        _need(battery, "charging", bool, "state.batteryState")
        pos = _need(msg, "agvPosition", dict, kind)
        for key in ("x", "y", "theta"):
            _need(pos, key, (int, float), "state.agvPosition")
        _need(pos, "mapId", str, "state.agvPosition")
        _need(pos, "positionInitialized", bool, "state.agvPosition")

    elif kind == "connection":
        if _need(msg, "connectionState", str, kind) not in CONNECTION_STATES:
            raise MessageError(f"connection: connectionState must be one of {', '.join(CONNECTION_STATES)}")
    return msg
