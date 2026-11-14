"""
Record scenarios tick by tick, so the browser viewer can replay them.

Usage (from the fleetmind folder):
    python -m scripts.record                      # record every scenario
    python -m scripts.record --scenario rush_hour # record just one

Then open viewer/index.html in your browser.

The recordings are written to viewer/recordings.js as a JavaScript file
(not plain JSON), because browsers refuse to load JSON files from your
disk when you open a page by double-clicking it. A .js file loads fine.
"""

import argparse
import json
import os

from scripts.classes import CHARGER, PACKING, DOCK
from scripts.layout import load_layout, add_robots
from scripts.scenarios import SCENARIOS
from scripts.simulation import Simulation, SimConfig

OUTPUT = os.path.join("viewer", "recordings.js")


def build_simulation(key, layout_path, seed=None):
    """
    Build a fresh Simulation for one scenario: load the layout, apply the
    scenario's robot count, start battery and config. Used by this recorder
    and by the live server (web/), so both run a scenario exactly the same way.
    """
    spec = SCENARIOS[key]
    warehouse = load_layout(layout_path)

    # Optional: keep only the first N robots, or add robots up to N
    keep = spec.get("robots")
    if keep is not None and keep < len(warehouse.robots):
        for robot_id in sorted(warehouse.robots)[keep:]:
            robot = warehouse.robots.pop(robot_id)
            del warehouse.occupied[robot.position]
    elif keep is not None:
        add_robots(warehouse, keep)

    # Optional: change the starting battery
    if "start_battery" in spec:
        for robot in warehouse.robots.values():
            robot.battery = spec["start_battery"]

    config = SimConfig(**spec["config"])
    return Simulation(warehouse, seed=spec["seed"] if seed is None else seed, config=config)


def record_scenario(key, layout_path):
    """Run one scenario and return everything the viewer needs, as plain data."""
    spec = SCENARIOS[key]
    sim = build_simulation(key, layout_path)
    warehouse = sim.warehouse

    frames = [snapshot(sim)]                  # tick 0: the starting state
    realistic = sim.config.demand == "realistic"
    # a realistic day runs until its last order is delivered; other scenarios for spec["ticks"]
    while (not sim.day_over()) if realistic else sim.tick < spec["ticks"]:
        sim.step()
        frames.append(snapshot(sim))

    return {
        "key": key,
        "title": spec["title"],
        "description": spec["description"],
        "seed": spec["seed"],
        "ticks": sim.tick,
        "config": {**SimConfig().__dict__, **spec["config"]},
        "layout": layout_data(warehouse),
        "frames": frames,
        "events": [list(e) for e in sim.events],
        "kpis": sim.kpis(),
    }


def layout_data(warehouse):
    """The fixed parts of the warehouse: size, walls, shelves, stations."""
    walls = [[r, c] for r in range(warehouse.height) for c in range(warehouse.width)
             if warehouse.grid[r][c] == "#"]
    return {
        "height": warehouse.height,
        "width": warehouse.width,
        "walls": walls,
        "shelves": [
            {"id": s.shelf_id, "name": s.name, "cells": [list(c) for c in s.cells],
             "items": list(s.items)}
            for s in sorted(warehouse.shelves.values(), key=lambda s: s.shelf_id)
        ],
        "chargers": [list(c) for c in warehouse.cells_of_type(CHARGER)],
        "packing": [list(c) for c in warehouse.cells_of_type(PACKING)],
        "docks": [list(c) for c in warehouse.cells_of_type(DOCK)],
    }


def snapshot(sim):
    """What every robot is doing at this tick, plus blocked cells and queue size."""
    robots = []
    for robot_id in sorted(sim.warehouse.robots):
        r = sim.warehouse.robots[robot_id]
        robots.append({
            "id": r.robot_id,
            "pos": list(r.position),
            "status": r.status.value,
            "task": r.task or "idle",
            "battery": round(r.battery, 1),
            "carrying": r.carrying,
            "order": r.order.order_id if r.order else None,
            "target": list(r.target) if r.target else None,
            "path": [list(c) for c in r.path],
            "repair": r.repair_left,
        })
    return {
        "tick": sim.tick,
        "robots": robots,
        # cells blocked by a lost robot are shown as the robot, not as a blocked cell
        "blocked": [list(c) for c in sorted(sim.warehouse.blocked)
                    if c not in getattr(sim, "lost_cells", {}).values()],
        "queue": len(sim.queue),
        **(sim.crew.snapshot() if getattr(sim, "crew", None) else {}),   # restocking people / robots
    }


def main():
    parser = argparse.ArgumentParser(description="Record scenarios for the browser viewer.")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), help="record only this scenario")
    parser.add_argument("--layout", default="layouts/main_warehouse.json")
    args = parser.parse_args()

    keys = [args.scenario] if args.scenario else list(SCENARIOS)
    recordings = {}
    for key in keys:
        recordings[key] = record_scenario(key, args.layout)
        k = recordings[key]["kpis"]
        print(f"{key:<16} {k['orders_delivered']:>4} delivered / {k['orders_created']:>4} created, "
              f"stuck {k['stuck_steps']}")

    os.makedirs("viewer", exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write("// Generated by scripts/record.py - do not edit by hand.\n")
        f.write("window.RECORDINGS = ")
        json.dump(recordings, f, separators=(",", ":"))
        f.write(";\n")
    size_kb = os.path.getsize(OUTPUT) / 1024
    print(f"\nSaved {len(recordings)} scenario(s) to {OUTPUT} ({size_kb:.0f} KB)")
    print("Open viewer/index.html in your browser.")


if __name__ == "__main__":
    main()