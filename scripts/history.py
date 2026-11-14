"""
Make a history of past orders: how many orders arrived in every hour of every day.
This is the data the forecast learns from.

Usage (from the fleetmind folder):
    python -m scripts.history                 # synthetic: 120 days, seed 7 -> data/orders_hourly.csv
    python -m scripts.history --days 200 --seed 3
    python -m scripts.history --source m5     # REAL daily demand from M5 (all 1941 days)
                                              #   -> data/orders_hourly_m5.csv
                                              #   (run python -m scripts.m5 --raw ... first)

Only the demand model runs (no robots), so this takes a second.
The days are the same days the simulation uses with SimConfig(demand="realistic",
demand_seed=7, demand_day=N), so a forecast for day 120 can be tested by
simulating day 120.

Columns:
    day       0, 1, 2, ... (day 0 = 1 June 2026)
    date      2026-06-01
    weekday   0 = Monday ... 6 = Sunday
    hour      6 ... 21 (the hour that starts at this time: 6 = 06:00-06:59)
    orders    orders that arrived in that hour
    big_delivery_orders   how many of them came from a big delivery
              (for checking results only; a forecast must NOT use this column,
               because nobody knows about a big delivery in advance)
    event, event_type, snap   what the calendar says about the day (m5 only;
              empty / 0 for synthetic). A calendar is known in advance, so a
              forecast may use these.
"""

import argparse
import csv
import os

from scripts.demand import DemandModel, HOURS, hour_of

OUTPUT = os.path.join("data", "orders_hourly.csv")
OUTPUT_M5 = os.path.join("data", "orders_hourly_m5.csv")
COLUMNS = ["day", "date", "weekday", "hour", "orders", "big_delivery_orders", "event", "event_type", "snap"]


def make_history(days=120, seed=7, source="synthetic"):
    """Return one row (a dict) per day and hour. For m5, days=None means all M5 days."""
    model = DemandModel(seed=seed, source=source)
    if days is None:
        days = model.days
    rows = []
    for day in range(days):
        calendar = model.calendar(day)
        big_per_hour = {h: 0 for h in HOURS}
        for start, size in model.big_deliveries(day):
            big_per_hour[hour_of(start)] += size      # a big delivery takes 5 minutes, so it stays in one hour
        for hour, orders in zip(HOURS, model.hourly(day)):
            rows.append({
                "day": day,
                "date": model.date(day).isoformat(),
                "weekday": model.weekday(day),
                "hour": hour,
                "orders": orders,
                "big_delivery_orders": big_per_hour[hour],
                **calendar,
            })
    return rows


def write_history(rows, path=OUTPUT):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description="Make a history of hourly orders for forecasting.")
    parser.add_argument("--source", choices=["synthetic", "m5"], default="synthetic")
    parser.add_argument("--days", type=int, default=None, help="default: 120 (synthetic) or all days (m5)")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    days = args.days if args.days is not None else (120 if args.source == "synthetic" else None)
    out = args.out or (OUTPUT if args.source == "synthetic" else OUTPUT_M5)
    rows = make_history(days, args.seed, args.source)
    write_history(rows, out)
    n_days = rows[-1]["day"] + 1
    total = sum(r["orders"] for r in rows)
    big = len({r["day"] for r in rows if r["big_delivery_orders"]})
    print(f"{args.source}: {n_days} days ({rows[0]['date']} to {rows[-1]['date']}), {total} orders "
          f"({total / n_days:.0f} per day), {big} days with a big delivery")
    print(f"Saved to {out}")


if __name__ == "__main__":
    main()
