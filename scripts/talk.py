"""
Why do the robots and the fleet manager talk? Read it as a story.

Runs the scenario "Talking robots" (2 robots, low batteries, breakdowns) over
the message bus, listens to EVERY message, and the first time each kind of
conversation happens, prints it in plain words with the reason.

    python -m scripts.talk                 # the story
    python -m scripts.talk --json          # also show the real JSON of each message
    python -m scripts.talk --scenario normal --robot R3

In the story, two things are done on purpose so you can see them:
    - someone swaps R1's battery by hand (the manager and the robot disagree)
    - R2's computer crashes, and later it is switched on again (lost and found)

To watch the raw messages of the live website instead (Mosquitto running):
    docker exec fleetmind-mosquitto mosquitto_sub -t "#" -v
"""

import argparse
import json

from scripts import vda5050 as v
from scripts.bus import InMemoryBroker
from scripts.demand import clock as clock_text
from scripts.fleet_link import FleetLink, start_robots
from scripts.record import build_simulation
from scripts.robot_agent import RobotAgent

LAYOUT = "layouts/main_warehouse.json"
SECONDS_PER_TICK = 0.1

# The kinds of conversation, in the order they are explained.
WHY = {
    "hello": "The robots were just switched on. They know the map, but not where they stand or "
             "how full their battery is. The manager tells each robot where it is (initPosition), "
             "and the robot answers with its first state.",
    "new_route": "The manager gave the robot a job. It sends the whole planned route, but only "
                 "RELEASES the first step. The rest is the 'horizon': the robot knows where it is "
                 "going, but may not drive there yet, so two robots never plan into each other.",
    "route_update": "Every tick the manager RELEASES the next cell of the route (an order update). "
                    "This is traffic control: the manager decides how far each robot may go. "
                    "The robot drives exactly that far and confirms with its state.",
    "quiet": "Nothing new for this robot: no order, no instant action. It still gets the clock "
             "and answers with its state. That answer is its HEARTBEAT: 'I'm still here'.",
    "pick": "The robot stands next to the right shelf. The manager sends a tiny order with one "
            "node (right here) and a 'pick' action on it. The robot picks and reports the load.",
    "drop": "The robot is on the packing station. A one-node order with a 'drop' action: "
            "the item is delivered, and the robot's state shows no load any more.",
    "start_charging": "The robot reached a charger. 'startCharging' is an INSTANT action: do it now, "
                      "it is not part of a route. Every tick the robot adds 5% by itself.",
    "stop_charging": "The manager wants the robot off the charger (it is full, or it has work). "
                     "'stopCharging' tells it to stop.",
    "breakdown": "The simulation decided the robot breaks down. In real life the robot would notice "
                 "this itself. Here the manager tells it with 'simBreakdown', and the robot then "
                 "reports the error in every state until it is repaired.",
    "mismatch": "Someone swapped the robot's battery by hand. The robot's state says a different "
                "battery than the manager expected. The ROBOT is the truth: the manager logs "
                "'state_mismatch' and believes the robot.",
    "lost": "The robot's computer crashed. It could not say goodbye, but when it connected it had "
            "left a 'last will' with the broker. The broker now sends it for the robot: "
            "CONNECTIONBROKEN. The manager marks the robot LOST, puts its order back in the queue, "
            "and the other robots drive around its cell.",
    "found": "The robot is switched on again. It says ONLINE and sends a state, but it does not "
             "know where it is. The manager takes it back and tells it where it stands "
             "(initPosition). From now on it gets work again.",
}
TITLE = {
    "hello": "The robots start", "new_route": "A new job: the route", "route_update": "Driving: release the next cell",
    "quiet": "A quiet tick: the heartbeat", "pick": "Picking the item", "drop": "Delivering the item",
    "start_charging": "Start charging", "stop_charging": "Stop charging", "breakdown": "A breakdown",
    "mismatch": "The manager and the robot disagree", "lost": "A robot disappears", "found": "The robot comes back",
}


# ---------------------------------------------------------------------------
# Messages in plain words
# ---------------------------------------------------------------------------

def cells(nodes):
    return " ".join(str(v.cell_of(n["nodeId"])) for n in nodes)


def say(topic, msg):
    """One message as one plain sentence."""
    if topic == v.CLOCK_TOPIC:
        return f"Manager -> all robots   CLOCK      tick {msg['tick']} has passed: do your tick and report"
    serial, kind = v.parse_topic(topic)
    if kind == "order":
        released = [n for n in msg["nodes"] if n["released"]]
        horizon = [n for n in msg["nodes"] if not n["released"]]
        actions = [a for n in msg["nodes"] for a in n["actions"]]
        text = f"Manager -> {serial:<3}   ORDER      {msg['orderId']} (update {msg['orderUpdateId']}): "
        if len(released) > 1:
            text += f"you may drive to {cells(released[1:])}"
        else:
            text += f"stay at {cells(released)}"
        for a in actions:
            p = v.params_of(a)
            text += f" and {a['actionType'].upper()} " + " ".join(f"{k}={val}" for k, val in p.items())
        if horizon:
            more = " ..." if len(horizon) > 4 else ""
            text += f". Planned after that (not allowed yet): {cells(horizon[:4])}{more}"
        return text
    if kind == "instantActions":
        parts = []
        for a in msg["actions"]:
            p = v.params_of(a)
            if a["actionType"] == "initPosition":
                parts.append(f"initPosition: you are at {v.cell_at(p['x'], p['y'])}, battery {p.get('battery')}%")
            else:
                extra = " ".join(f"{k}={val}" for k, val in p.items())
                parts.append(f"{a['actionType']} {extra}".strip())
        return f"Manager -> {serial:<3}   INSTANT    " + "; ".join(parts)
    if kind == "state":
        pos = msg["agvPosition"]
        if not pos["positionInitialized"]:
            where = "I don't know where I am"
        else:
            where = f"I am at {v.cell_at(pos['x'], pos['y'])}"
        text = f"{serial:<3} -> Manager   STATE      {where}, battery {msg['batteryState']['batteryCharge']:.1f}%"
        if msg["batteryState"]["charging"]:
            text += " (charging)"
        if msg["loads"]:
            text += f", carrying {msg['loads'][0]['loadId']}"
        if msg["driving"]:
            text += ", I just drove"
        ahead = [n for n in msg["nodeStates"] if not n["released"]]
        if ahead:
            text += f", {len(ahead)} planned cells still to come"
        for e in msg["errors"]:
            text += f", ERROR {e['errorType']}: {e['errorDescription']}"
        return text
    if kind == "connection":
        who = "the broker, for " + serial if msg["connectionState"] == "CONNECTIONBROKEN" else serial
        return f"{who} -> Manager   CONNECTION {msg['connectionState']}"
    return f"{topic}: {msg}"


def kind_of(robot_msgs, events):
    """What kind of conversation was this, for one robot in one tick?"""
    names = {e[2] for e in events}
    actions = {a["actionType"] for _, m in robot_msgs if "actions" in m for a in m["actions"]}
    node_actions = {a["actionType"] for _, m in robot_msgs if "nodes" in m for n in m["nodes"] for a in n["actions"]}
    orders = [m for t, m in robot_msgs if t.endswith("/order")]
    if "state_mismatch" in names:
        return "mismatch"
    if "simBreakdown" in actions:
        return "breakdown"
    if "pick" in node_actions:
        return "pick"
    if "drop" in node_actions:
        return "drop"
    if "startCharging" in actions:
        return "start_charging"
    if "stopCharging" in actions:
        return "stop_charging"
    if any(m["orderUpdateId"] == 0 for m in orders):
        return "new_route"
    if orders:
        return "route_update"
    if not robot_msgs:
        return "quiet"
    return None


# ---------------------------------------------------------------------------
# The story
# ---------------------------------------------------------------------------

class Story:

    def __init__(self, scenario, robot, show_json):
        self.broker = InMemoryBroker()
        self.sim = build_simulation(scenario, LAYOUT)
        self.realistic = self.sim.config.demand == "realistic"
        self.robot = robot
        self.show_json = show_json
        self.agents = {a.serial: a for a in start_robots(self.broker, sorted(self.sim.warehouse.robots), LAYOUT, False)}
        self.now = 0.0
        self.shown = set()
        self.number = 0
        mark = len(self.broker.log)
        self.link = FleetLink(self.sim, self.broker, state_timeout=0, clock=lambda: self.now)
        self.tell("hello", self.broker.log[mark:], [], tick=0)
        self.shown.add("hello")

    def step(self):
        self.now += SECONDS_PER_TICK
        mark, first_event = len(self.broker.log), len(self.sim.events)
        self.link.step()
        return self.broker.log[mark:], self.sim.events[first_event:]

    def tell(self, kind, log, events, tick, robot=None):
        self.number += 1
        when = f"tick {tick}" + (f", {clock_text(tick)}" if self.realistic else "")
        print(f"\n{'=' * 100}\nEXAMPLE {self.number}: {TITLE[kind]}  ({when})\n{'=' * 100}")
        for sender, topic, msg in log:
            parsed = v.parse_topic(topic)
            if robot and parsed and parsed[0] != robot:
                continue                      # only this robot's conversation (and the clock)
            print("  " + say(topic, msg))
            if self.show_json and topic != v.CLOCK_TOPIC:
                text = json.dumps(msg)
                print("      " + (text if len(text) < 400 else text[:400] + " ..."))
        decided = [e for e in events if not robot or e[1] in (robot, "")]
        decided = [e for e in decided if e[2] != "route_planned" or kind in ("new_route", "route_update")]
        if decided:
            print("  What the manager decided in this tick:")
            for _, rid, event, details in decided[:6]:
                print(f"    - {rid or 'warehouse'}: {event.replace('_', ' ')} {details}")
        print(f"  WHY: {WHY[kind]}")

    def run(self, ticks, crash_at, back_after, tamper_at):
        crashed = None
        for _ in range(ticks):
            tick = self.sim.tick + 1
            if tick == tamper_at:
                self.agents[self.robot].battery -= 7.5          # a battery swapped by hand
            if tick == crash_at and len(self.agents) > 1:
                crashed = sorted(self.agents)[1]
                mark = len(self.broker.log)
                self.agents[crashed].crash()
                will = self.broker.log[mark:]
                log, events = self.step()
                self.tell("lost", will + [x for x in log if v.parse_topic(x[1]) and v.parse_topic(x[1])[0] == crashed],
                          [e for e in events if e[2] == "robot_lost" or e[1] == crashed], tick)
                continue
            if crashed and tick == crash_at + back_after:
                mark = len(self.broker.log)
                self.agents[crashed] = RobotAgent(crashed, self.broker, LAYOUT).start()
                hello = self.broker.log[mark:]
                log, events = self.step()
                ours = [x for x in log if v.parse_topic(x[1]) and v.parse_topic(x[1])[0] == crashed]
                self.tell("found", hello + ours[:3], [e for e in events if e[1] == crashed][:3], tick)
                crashed = None
                continue

            log, events = self.step()
            mine = [(t, m) for _, t, m in log if v.parse_topic(t) and v.parse_topic(t)[0] == self.robot
                    and v.parse_topic(t)[1] in ("order", "instantActions")]
            kind = kind_of(mine, [e for e in events if e[1] == self.robot])
            if kind and kind not in self.shown and self.robot not in self.sim.lost:
                self.shown.add(kind)
                self.tell(kind, log, events, tick, robot=self.robot)

        missing = [TITLE[k] for k in WHY if k not in self.shown and k not in ("lost", "found")]
        k = self.sim.kpis()
        print(f"\n{'=' * 100}\nIn {self.sim.tick} ticks: {self.link.sent} messages to the robots, "
              f"{self.link.received} from the robots. {k['orders_delivered']} orders delivered.")
        if missing:
            print(f"Not seen for {self.robot} in this run: {', '.join(missing)}. Try more --ticks or another --robot.")


def main():
    parser = argparse.ArgumentParser(description="Read what the manager and the robots say to each other, and why.")
    parser.add_argument("--scenario", default="talking_robots")
    parser.add_argument("--robot", default="R1", help="whose conversation to follow")
    parser.add_argument("--ticks", type=int, default=200)
    parser.add_argument("--json", action="store_true", help="also print the real JSON messages")
    parser.add_argument("--crash-at", type=int, default=120, help="tick when the second robot crashes (0 = never)")
    parser.add_argument("--tamper-at", type=int, default=60, help="tick when the battery is swapped by hand (0 = never)")
    args = parser.parse_args()

    print("FleetMind: what the fleet manager and the robots say to each other over MQTT")
    print(f"Scenario '{args.scenario}', following {args.robot}. Every robot talks only through the broker.")
    Story(args.scenario, args.robot, args.json).run(args.ticks, args.crash_at, back_after=15, tamper_at=args.tamper_at)


if __name__ == "__main__":
    main()
