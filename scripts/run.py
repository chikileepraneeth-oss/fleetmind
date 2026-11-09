"""
Run the FleetMind simulation from the command line.

Examples (from the fleetmind folder):
    python -m scripts.run
    python -m scripts.run --seed 42 --ticks 500
    python -m scripts.run --seed 7 --ticks 1000 --show-map
"""

import argparse
import os

from scripts.layout import load_layout
from scripts.simulation import Simulation


def main():
    parser = argparse.ArgumentParser(description="Run the FleetMind warehouse simulation.")
    parser.add_argument("--seed", type=int, default=42, help="random seed (same seed = same run)")
    parser.add_argument("--ticks", type=int, default=500, help="how many ticks to simulate")
    parser.add_argument("--layout", default="layouts/main_warehouse.json", help="layout file")
    parser.add_argument("--show-map", action="store_true", help="print the map at the end")
    args = parser.parse_args()

    warehouse = load_layout(args.layout)
    sim = Simulation(warehouse, seed=args.seed)
    sim.run(args.ticks)

    os.makedirs("logs", exist_ok=True)
    log_path = f"logs/run_seed{args.seed}_ticks{args.ticks}.csv"
    sim.write_log(log_path)

    if args.show_map:
        print("Warehouse at the end:")
        warehouse.print_map()

    print(f"FleetMind run  |  seed {args.seed}  |  {args.ticks} ticks  |  {len(warehouse.robots)} robots")
    print("-" * 50)
    for name, value in sim.kpis().items():
        print(f"{name:<20} {value}")
    print("-" * 50)
    print(f"Event log saved to {log_path} ({len(sim.events)} events)")


if __name__ == "__main__":
    main()