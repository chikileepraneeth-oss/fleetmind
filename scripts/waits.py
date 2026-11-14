"""
Wait report: every time a robot wanted to drive but stood still for a while, and why.

Usage (from the fleetmind folder):
    python -m scripts.waits                              # Realistic Friday
    python -m scripts.waits --scenario breakdown_day --min-wait 5

Every tick, for every robot that has somewhere to go but did not move, we write
down one reason. Ticks in a row for the same robot become one "wait". Waits of
--min-wait ticks or more are listed, and the reasons are added up.

Reasons:
    behind a broken robot     the next cell on its way holds a broken robot
    behind an empty robot     ... a robot whose battery is empty
    behind a parked robot     ... a robot that is idle, going home or waiting for a charger
    behind a working robot    ... a robot standing at its own target (picking, delivering, charging)
    behind a driving robot    ... a robot that is on its way somewhere too
    planned wait              nobody is in the next cell, but its plan says "wait here"
                              (another robot has booked a cell it needs)
    no route / blocked cell   no robot in the way: a blocked cell, or no route was found
"""

import argparse
from collections import Counter
from dataclasses import dataclass, field

from scripts.classes import Robot, RobotStatus
from scripts.demand import clock
from scripts.record import build_simulation
from scripts.scenarios import SCENARIOS

REASONS = [
    "behind a broken robot", "behind an empty robot", "behind a parked robot",
    "behind a working robot", "behind a driving robot", "planned wait", "no route / blocked cell",
]


@dataclass
class Wait:
    robot: str
    start: int                      # first tick it stood still
    cell: tuple
    task: str
    ticks: int = 0
    reasons: Counter = field(default_factory=Counter)
    blockers: Counter = field(default_factory=Counter)

    @property
    def reason(self):
        return self.reasons.most_common(1)[0][0]

    @property
    def blocker(self):
        return self.blockers.most_common(1)[0][0] if self.blockers else ""


def why_waiting(sim, robot):
    """One reason (and the robot in the way, if any) for a robot that didn't move this tick."""
    blocker_id = sim._blocker(robot)
    if blocker_id is None:
        if robot.path and robot.path[0] == robot.position:
            return "planned wait", ""
        return "no route / blocked cell", ""
    other = sim.warehouse.robots[blocker_id]
    if other.status == RobotStatus.ERROR:
        return "behind a broken robot", blocker_id
    if other.battery < Robot.BATTERY_PER_STEP:
        return "behind an empty robot", blocker_id
    if other.task in (None, "to_home", "wait_charger") and not other.path:
        return "behind a parked robot", blocker_id
    if other.target is None or other.position == other.target or other.task == "charging":
        return "behind a working robot", blocker_id
    return "behind a driving robot", blocker_id


def wait_report(sim, ticks):
    """Run the simulation for `ticks` ticks and return every wait (a list of Wait)."""
    open_waits, done = {}, []
    for _ in range(ticks):
        sim.step()
        for robot in sim._robots():
            rid = robot.robot_id
            if sim.no_progress.get(rid, 0) > 0:          # wanted to drive, but stood still
                wait = open_waits.get(rid)
                if wait is None:
                    wait = open_waits[rid] = Wait(rid, sim.tick, robot.position, robot.task or "idle")
                reason, blocker = why_waiting(sim, robot)
                wait.ticks += 1
                wait.reasons[reason] += 1
                if blocker:
                    wait.blockers[blocker] += 1
            elif rid in open_waits:
                done.append(open_waits.pop(rid))
    return done + list(open_waits.values())


def summary(waits, min_wait):
    """Ticks spent waiting per reason, for all waits and for the long ones."""
    all_ticks, long_ticks = Counter(), Counter()
    for w in waits:
        for reason, n in w.reasons.items():
            all_ticks[reason] += n
            if w.ticks >= min_wait:
                long_ticks[reason] += n
    return all_ticks, long_ticks


def main():
    parser = argparse.ArgumentParser(description="List the long waits of a scenario and their reasons.")
    parser.add_argument("--scenario", default="realistic_friday", choices=sorted(SCENARIOS))
    parser.add_argument("--min-wait", type=int, default=8, help="list waits of at least this many ticks")
    parser.add_argument("--layout", default="layouts/main_warehouse.json")
    args = parser.parse_args()

    spec = SCENARIOS[args.scenario]
    sim = build_simulation(args.scenario, args.layout)
    waits = wait_report(sim, spec["ticks"])
    long = sorted((w for w in waits if w.ticks >= args.min_wait), key=lambda w: -w.ticks)
    realistic = spec["config"].get("demand") == "realistic"

    print(f"{spec['title']}: {len(waits)} waits, {sum(w.ticks for w in waits)} robot-ticks spent waiting, "
          f"{len(long)} waits of {args.min_wait}+ ticks\n")
    print(f"{'robot':<6}{'from':>7}{'ticks':>7}  {'cell':<10}{'doing':<13}{'main reason':<26}{'in the way'}")
    for w in long:
        when = clock(w.start - 1) if realistic else w.start
        print(f"{w.robot:<6}{when:>7}{w.ticks:>7}  {str(w.cell):<10}{w.task:<13}{w.reason:<26}{w.blocker}")

    all_ticks, long_ticks = summary(waits, args.min_wait)
    print(f"\n{'reason':<26}{'all waits':>11}{f'waits {args.min_wait}+':>11}   (robot-ticks)")
    for reason in REASONS:
        print(f"{reason:<26}{all_ticks[reason]:>11}{long_ticks[reason]:>11}")


if __name__ == "__main__":
    main()
