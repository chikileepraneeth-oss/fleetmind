"""
Real demand from the M5 dataset (Walmart sales, 2011-2016).

M5 has the units sold per product, per store, per day, plus a calendar with
holidays, sports and cultural events, and SNAP days (days when food stamps are
paid out in a state, so food sales go up).

This script reads the big sales file ONCE, line by line (so it needs very little
memory), adds up all products of one store and category per day, and saves a
small file that the rest of FleetMind uses:

    python -m scripts.m5 --raw "C:\\path\\to\\m5\\data\\raw"
    -> data/m5_ca1_foods_daily.csv   (one row per day)

The raw folder must contain sales_train_evaluation.csv (or sales_train_validation.csv)
and calendar.csv. By default it uses store CA_1 and category FOODS: a grocery
store, closest to our grocery warehouse.

Columns of the daily file:
    day         0, 1, 2, ... (day 0 = 2011-01-29, the first M5 day)
    date        2011-01-29
    weekday     0 = Monday ... 6 = Sunday
    units       units sold that day (all products of the store and category)
    event       name of the holiday or event that day ("" if none)
    event_type  Cultural, National, Religious, Sporting ("" if none)
    snap        1 if it is a SNAP day in the store's state, else 0
"""

import argparse
import csv
import os
from datetime import date

OUTPUT = os.path.join("data", "m5_ca1_foods_daily.csv")
COLUMNS = ["day", "date", "weekday", "units", "event", "event_type", "snap"]


def sales_file(raw_dir):
    for name in ("sales_train_evaluation.csv", "sales_train_validation.csv"):
        path = os.path.join(raw_dir, name)
        if os.path.exists(path):
            return path
    raise FileNotFoundError(f"No sales_train_evaluation.csv or sales_train_validation.csv in {raw_dir}")


def daily_totals(raw_dir, store="CA_1", category="FOODS"):
    """Units sold per day, summed over every product of this store and category."""
    totals = None
    with open(sales_file(raw_dir), newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        cat_col, store_col = header.index("cat_id"), header.index("store_id")
        first_day = header.index("d_1")
        for row in reader:                                   # one product per line
            if row[cat_col] != category or row[store_col] != store:
                continue
            units = [int(x) for x in row[first_day:]]
            totals = units if totals is None else [a + b for a, b in zip(totals, units)]
    if totals is None:
        raise ValueError(f"No products of category {category} in store {store}")
    return totals


def extract(raw_dir, store="CA_1", category="FOODS"):
    """One row (dict) per day: the daily totals joined with the calendar."""
    totals = daily_totals(raw_dir, store, category)
    state = store.split("_")[0]                              # CA_1 -> CA
    with open(os.path.join(raw_dir, "calendar.csv"), newline="", encoding="utf-8") as f:
        calendar = list(csv.DictReader(f))
    rows = []
    for day, (units, cal) in enumerate(zip(totals, calendar)):
        rows.append({
            "day": day,
            "date": cal["date"],
            "weekday": date.fromisoformat(cal["date"]).weekday(),
            "units": units,
            "event": cal["event_name_1"],
            "event_type": cal["event_type_1"],
            "snap": int(cal.get(f"snap_{state}", 0) or 0),
        })
    return rows


def write_daily(rows, path=OUTPUT):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def read_daily(path=OUTPUT):
    """Read the small daily file made by this script."""
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} not found. Make it first: python -m scripts.m5 --raw <folder with the M5 csv files>")
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        for key in ("day", "weekday", "units", "snap"):
            row[key] = int(row[key])
    return rows


def main():
    parser = argparse.ArgumentParser(description="Make the daily M5 demand file for FleetMind.")
    parser.add_argument("--raw", required=True, help="folder with sales_train_evaluation.csv and calendar.csv")
    parser.add_argument("--store", default="CA_1")
    parser.add_argument("--category", default="FOODS")
    parser.add_argument("--out", default=OUTPUT)
    args = parser.parse_args()

    rows = extract(args.raw, args.store, args.category)
    write_daily(rows, args.out)
    units = [r["units"] for r in rows]
    print(f"{args.store} {args.category}: {len(rows)} days ({rows[0]['date']} to {rows[-1]['date']}), "
          f"{sum(units) / len(units):.0f} units per day on average")
    print(f"Saved to {args.out}")


if __name__ == "__main__":
    main()
