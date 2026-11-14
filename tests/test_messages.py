"""
Tests for the VDA 5050-style messages (scripts/vda5050.py) and the message bus (scripts/bus.py).
"""

import pytest

from scripts import vda5050 as v
from scripts.bus import InMemoryBroker, topic_matches


# ----- topics, nodes, time -----

def test_topics_follow_the_vda5050_pattern():
    assert v.topic("R1", "state") == "uagv/v2/FleetMind/R1/state"
    assert v.parse_topic("uagv/v2/FleetMind/R3/order") == ("R3", "order")
    assert v.parse_topic("fleetmind/sim/clock") is None
    with pytest.raises(ValueError):
        v.topic("R1", "gossip")


def test_node_ids_and_positions_round_trip():
    assert v.node_id((3, 4)) == "n3_4"
    assert v.cell_of("n3_4") == (3, 4)
    pos = v.position((3, 4))
    assert (pos["x"], pos["y"]) == (4.0, 3.0)          # x = column, y = row, in metres
    assert v.cell_at(pos["x"], pos["y"]) == (3, 4)
    with pytest.raises(v.MessageError, match="bad nodeId"):
        v.cell_of("shelf-A")


def test_timestamps_are_simulation_minutes():
    assert v.timestamp(0) == "2026-06-01T06:00:00.000Z"
    assert v.timestamp(61) == "2026-06-01T07:01:00.000Z"
    assert v.tick_of(v.timestamp(1234)) == 1234


def test_header_ids_count_up_per_topic():
    h = v.Header("R1")
    assert h.make("order", 0)["headerId"] == 0
    assert h.make("order", 0)["headerId"] == 1
    assert h.make("state", 0)["headerId"] == 0


# ----- orders -----

def test_order_releases_the_first_nodes_and_keeps_the_rest_as_horizon():
    msg = v.order(v.Header("R1"), 5, "R1-1", 0, [(1, 1), (1, 2), (1, 3), (1, 4)], released=2)
    v.validate("order", msg)
    assert [n["sequenceId"] for n in msg["nodes"]] == [0, 2, 4, 6]
    assert [e["sequenceId"] for e in msg["edges"]] == [1, 3, 5]
    assert [n["released"] for n in msg["nodes"]] == [True, True, False, False]
    assert [e["released"] for e in msg["edges"]] == [True, False, False]
    assert msg["edges"][0]["startNodeId"] == "n1_1" and msg["edges"][0]["endNodeId"] == "n1_2"


def test_an_order_update_continues_the_sequence_ids():
    msg = v.order(v.Header("R1"), 5, "R1-1", 3, [(1, 2), (1, 3)], released=2, first_sequence=2)
    v.validate("order", msg)
    assert [n["sequenceId"] for n in msg["nodes"]] == [2, 4]


def test_node_actions_are_attached_to_their_node():
    pick = v.action("pick", "a1", {"item": "apples"})
    msg = v.order(v.Header("R1"), 0, "R1-2", 0, [(1, 1)], released=1, node_actions={0: [pick]})
    v.validate("order", msg)
    assert msg["nodes"][0]["actions"][0]["actionType"] == "pick"
    assert v.params_of(msg["nodes"][0]["actions"][0]) == {"item": "apples"}


# ----- clear errors for wrong messages -----

def good_order():
    return v.order(v.Header("R1"), 0, "R1-1", 0, [(1, 1), (1, 2)], released=2)


@pytest.mark.parametrize("change, error", [
    (lambda m: m.pop("orderId"), "order: missing 'orderId'"),
    (lambda m: m.update(orderUpdateId="1"), "'orderUpdateId' must be an integer"),
    (lambda m: m.update(timestamp="yesterday"), "bad timestamp"),
    (lambda m: m.update(nodes=[]), "needs at least one node"),
    (lambda m: m["edges"].pop(), "2 nodes need 1 edges, got 0"),
    (lambda m: m["nodes"][1].update(sequenceId=7), "sequenceId 7 should be 2"),
    (lambda m: m["nodes"][0].update(released=False), "a released node can't come after the horizon"),
    (lambda m: m["nodes"][1].update(nodeId="here"), "bad nodeId 'here'"),
    (lambda m: m["edges"][0].update(endNodeId="n9_9"), "must join n1_1 and n1_2"),
    (lambda m: m["nodes"][0]["actions"].append({"actionType": "pick", "actionId": "a", "blockingType": "MAYBE"}),
     "blockingType must be one of"),
])
def test_a_wrong_order_gives_a_clear_error(change, error):
    msg = good_order()
    change(msg)
    with pytest.raises(v.MessageError, match=error.replace("(", r"\(").replace(")", r"\)")):
        v.validate("order", msg)


def test_wrong_connection_state_is_rejected():
    msg = v.connection(v.Header("R1"), 0, "ONLINE")
    v.validate("connection", msg)
    msg["connectionState"] = "SLEEPY"
    with pytest.raises(v.MessageError, match="connectionState must be one of"):
        v.validate("connection", msg)


# ----- the bus -----

@pytest.mark.parametrize("pattern, topic, match", [
    ("uagv/v2/FleetMind/+/state", "uagv/v2/FleetMind/R1/state", True),
    ("uagv/v2/FleetMind/+/state", "uagv/v2/FleetMind/R1/order", False),
    ("uagv/#", "uagv/v2/FleetMind/R1/state", True),
    ("uagv/v2/FleetMind/R1/state", "uagv/v2/FleetMind/R1/state", True),
    ("uagv/v2/+", "uagv/v2/FleetMind/R1", False),
])
def test_topic_wildcards(pattern, topic, match):
    assert topic_matches(pattern, topic) is match


def test_messages_reach_every_subscriber_as_json_copies():
    broker = InMemoryBroker()
    a, b = broker.connect("a"), broker.connect("b")
    got = []
    b.subscribe("x/+", lambda t, m: got.append((t, m)))
    msg = {"n": 1, "cell": (1, 2)}
    a.publish("x/y", msg)
    assert got == [("x/y", {"n": 1, "cell": [1, 2]})]     # tuples become lists, like real JSON


def test_retained_messages_go_to_late_subscribers():
    broker = InMemoryBroker()
    broker.connect("robot").publish("robots/R1/connection", {"state": "ONLINE"}, retain=True)
    got = []
    broker.connect("manager").subscribe("robots/+/connection", lambda t, m: got.append(m))
    assert got == [{"state": "ONLINE"}]


def test_the_will_is_sent_only_when_a_client_crashes():
    broker = InMemoryBroker()
    got = []
    broker.connect("manager").subscribe("will/#", lambda t, m: got.append(m))
    broker.connect("r1", will=("will/r1", {"bye": "broken"}, True)).close()
    assert got == []                                    # a clean goodbye: no will
    broker.connect("r2", will=("will/r2", {"bye": "broken"}, True)).crash()
    assert got == [{"bye": "broken"}]


def test_a_closed_connection_cannot_publish():
    conn = InMemoryBroker().connect("a")
    conn.close()
    with pytest.raises(ConnectionError):
        conn.publish("x", {})
