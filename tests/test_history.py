"""
Tests for the order history (scripts/history.py) and for realistic demand
inside the simulation.
"""

import csv
from collections import Counter

from scripts.demand import DemandModel, HOURS
from scripts.history import make_history, write_history, COLUMNS
from scripts.layout import load_layout
from scripts.simulation import Simulation, SimConfig

MAIN_LAYOUT = "layouts/main_warehouse.json"


def test_history_has_one_row_per_day_and_hour():
    rows = make_history(days=10, seed=7)
    assert len(rows) == 10 * len(HOURS)
    assert rows[0]["date"] == "2026-06-01" and rows[0]["weekday"] == 0 and rows[0]["hour"] == 6
    assert rows[-1]["day"] == 9 and rows[-1]["hour"] == 21


def test_history_matches_the_demand_model():
    rows = make_history(days=5, seed=7)
    model = DemandModel(seed=7)
    for day in range(5):
        assert [r["orders"] for r in rows if r["day"] == day] == model.hourly(day)


def test_big_delivery_orders_are_part_of_the_orders():
    rows = make_history(days=120, seed=7)
    assert any(r["big_delivery_orders"] for r in rows)
    assert all(r["big_delivery_orders"] <= r["orders"] for r in rows)


def test_history_is_saved_as_csv(tmp_path):
    path = tmp_path / "history.csv"
    write_history(make_history(days=2), path=str(path))
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == COLUMNS
        assert len(list(reader)) == 2 * len(HOURS)


def realistic_sim(day=123, seed=42):
    config = SimConfig(demand="realistic", demand_seed=7, demand_day=day)
    return Simulation(load_layout(MAIN_LAYOUT), seed=seed, config=config)


def test_simulation_gets_exactly_the_orders_of_that_day():
    sim = realistic_sim()
    sim.run(960)                                     # the whole working day
    per_hour = Counter(6 + (o.created_at - 1) // 60 for o in sim.orders)
    assert [per_hour[h] for h in HOURS] == DemandModel(seed=7).hourly(123)


def test_no_new_orders_after_closing_time():
    sim = realistic_sim()
    sim.run(1020)
    assert max(o.created_at for o in sim.orders) <= 960


def test_realistic_demand_is_repeatable():
    a, b = realistic_sim(), realistic_sim()
    a.run(300)
    b.run(300)
    assert a.events == b.events
