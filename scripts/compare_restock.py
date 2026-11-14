"""
Who should restock the shelves, and does it matter if the robots know where people are?

Runs the Normal day over 30 seeds four ways and prints the average ± its standard error:

    no restocking          the fleet alone
    people, sightings      2 people; robots only know where they are after seeing them,
                           and share every sighting with all robots
    people, badges         2 people with location badges: the fleet always knows where they are
    restock robots         2 restocking robots that book their routes like every robot

    python -m scripts.compare_restock
    python -m scripts.compare_restock --seeds 10 --ticks 500

The ± matters: with 10 seeds the differences between the last three looked big,
with 30 seeds they are smaller than the uncertainty.
"""

import argparse
import statistics

from scripts.record import build_simulation

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


def mean_se(values):
    m = statistics.mean(values)
    se = statistics.stdev(values) / len(values) ** 0.5 if len(values) > 1 else 0.0
    return m, se


def compare(seeds, ticks, layout="layouts/main_warehouse.json"):
    table = {}
    for name, changes in VARIANTS:
        runs = [run(changes, seed, ticks, layout) for seed in seeds]
        table[name] = {key: mean_se([r[key] or 0 for r in runs]) for key, _ in COLUMNS}
    return table


def main():
    parser = argparse.ArgumentParser(description="Compare restocking by people and by robots.")
    parser.add_argument("--seeds", type=int, default=30)
    parser.add_argument("--ticks", type=int, default=500)
    args = parser.parse_args()
    seeds = list(range(1, args.seeds + 1))
    table = compare(seeds, args.ticks)
    print(f"Normal day, {args.ticks} ticks, average of {len(seeds)} seeds (+/- standard error)\n")
    print(f"{'':<20}" + "".join(f"{label:>16}" for _, label in COLUMNS))
    for name, values in table.items():
        print(f"{name:<20}" + "".join(f"{f'{m:.1f} +/-{se:.1f}':>16}" for m, se in values.values()))


if __name__ == "__main__":
    main()
