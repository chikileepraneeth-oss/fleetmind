"""
Who should restock the shelves, and does it matter if the robots know where people are?

Runs the Normal day over 10 seeds four ways and prints the averages:

    no restocking          the fleet alone
    people, sightings      2 people; robots only know where they are after seeing them,
                           and share every sighting with all robots
    people, badges         2 people with location badges: the fleet always knows where they are
    restock robots         2 restocking robots that book their routes like every robot

    python -m scripts.compare_restock
    python -m scripts.compare_restock --seeds 20 --ticks 500
"""

import argparse
import statistics

from scripts.record import build_simulation
from scripts.simulation import SimConfig

VARIANTS = [
    ("no restocking", {}),
    ("people, sightings", {"restock": "people", "people_tracking": "sightings"}),
    ("people, badges", {"restock": "people", "people_tracking": "badges"}),
    ("restock robots", {"restock": "robots"}),
]
COLUMNS = [
    ("orders_delivered", "delivered"),
    ("avg_delivery_time", "avg delivery"),
    ("late_orders", "late"),
    ("restocks", "restocks"),
    ("reroutes_for_people", "reroutes"),
    ("stops_for_people", "surprise stops"),
    ("stuck_steps", "stuck steps"),
    ("collisions", "collisions"),
]


def run(changes, seed, ticks, layout):
    sim = build_simulation("normal", layout, seed=seed)
    for key, value in changes.items():
        setattr(sim.config, key, value)
    if changes.get("restock"):
        from scripts.restock import RestockCrew
        sim.crew = RestockCrew(sim)
    sim.run(ticks)
    return sim.kpis()


def compare(seeds, ticks, layout="layouts/main_warehouse.json"):
    table = {}
    for name, changes in VARIANTS:
        runs = [run(changes, seed, ticks, layout) for seed in seeds]
        table[name] = {key: round(statistics.mean(r[key] or 0 for r in runs), 1) for key, _ in COLUMNS}
    return table


def main():
    parser = argparse.ArgumentParser(description="Compare restocking by people and by robots.")
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--ticks", type=int, default=500)
    args = parser.parse_args()
    seeds = list(range(1, args.seeds + 1))
    table = compare(seeds, args.ticks)
    header = f"{'':<20}" + "".join(f"{label:>15}" for _, label in COLUMNS)
    print(f"Normal day, {args.ticks} ticks, average of {len(seeds)} seeds\n")
    print(header)
    for name, values in table.items():
        print(f"{name:<20}" + "".join(f"{values[key]:>15}" for key, _ in COLUMNS))


if __name__ == "__main__":
    main()
