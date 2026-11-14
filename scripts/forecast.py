"""
Forecast how many orders will arrive in each hour of tomorrow.

Usage (from the fleetmind folder):
    python -m scripts.forecast                 # synthetic history (120 days)
    python -m scripts.forecast --source m5     # REAL M5 demand (1941 days, last 365 days tested)

The question: every evening, predict the orders for each hour (06-21) of the
NEXT day. That is when a warehouse plans charging and robot positions.

Two forecasts:
    baseline (seasonal naive)  "tomorrow at 17:00 = the same weekday last week at 17:00"
    LightGBM                   a gradient-boosting model that learns from these features

Features. Lags are values from the past, always at least 1 day back:
    lag_1d, lag_7d, lag_14d   same hour yesterday, 1 week ago, 2 weeks ago
    lag_364                   same hour, same weekday, one year ago (52 weeks)
    mean_7d                   average of this hour over the last 7 days
    mean_weekday_4w           average of this hour on the same weekday, last 4 weeks
    prev_day_total            all orders yesterday
Calendar features. A calendar is known in advance, so these may be about the day itself:
    hour, weekday
    event, event_type_code    is it a holiday/event, and what kind (M5 calendar)
    event_next_day            is TOMORROW a holiday/event (people shop the day before)
    christmas                 25 December (the store is closed)
    snap                      SNAP day: food stamps are paid out, food sales go up
Using anything from the ORDERS of the day we predict would be cheating ("leakage"):
the forecast would look great in the test and fail in real life.

Backtest ("walk forward"): train on the days before the test day, predict the test
day, move one day on, ... Always the past predicts the future. To save time the
model can be retrained every N days (it still uses yesterday's numbers as features).

Scores:
    WAPE   total absolute error / total real orders   (0.15 = off by 15% of all orders)
    MASE   average error / average error of "same as last week" on the training days
           (below 1.0 = better than the simple rule)
    daily WAPE   the same as WAPE, but for whole days. With M5 this is the most honest
           number: the daily totals are real, the split into hours is our own curve.
"""

import argparse
import os

import numpy as np
import pandas as pd

from scripts.demand import DemandModel, HOURS, EVENT_TYPES
from scripts.history import OUTPUT as HISTORY, OUTPUT_M5 as HISTORY_M5, make_history, write_history

RESULTS = os.path.join("data", "forecast_backtest.csv")
RESULTS_M5 = os.path.join("data", "forecast_backtest_m5.csv")
FIRST_TEST_DAY = 90
FEATURES = ["hour", "weekday", "lag_1d", "lag_7d", "lag_14d", "lag_364",
            "mean_7d", "mean_weekday_4w", "prev_day_total",
            "event", "event_type_code", "event_next_day", "christmas", "snap"]
LGBM_PARAMS = dict(
    objective="poisson",       # orders are counts (0, 1, 2, ...): the Poisson objective fits counts
    learning_rate=0.03, num_leaves=15, min_data_in_leaf=20,
    seed=0, num_threads=1, deterministic=True, force_row_wise=True, verbose=-1,
)
LGBM_ROUNDS = 300              # how many small trees the model adds up


# ---------------------------------------------------------------------------
# Data and features
# ---------------------------------------------------------------------------

def load_history(path=HISTORY, source="synthetic"):
    """Read the history CSV; make it first if it doesn't exist yet."""
    if not os.path.exists(path):
        write_history(make_history(days=None if source == "m5" else 120, source=source), path)
    return pd.read_csv(path, keep_default_na=False)


def add_features(df):
    """Add the features. Lags only use days BEFORE the row's own day; the rest is calendar."""
    df = df.sort_values(["day", "hour"]).reset_index(drop=True).copy()
    for col, default in (("event", ""), ("event_type", ""), ("snap", 0)):
        if col not in df:
            df[col] = default
    by_hour = df.groupby("hour")["orders"]           # inside one hour, rows go day by day
    df["lag_1d"] = by_hour.shift(1)
    df["lag_7d"] = by_hour.shift(7)
    df["lag_14d"] = by_hour.shift(14)
    df["lag_364"] = by_hour.shift(364)
    df["mean_7d"] = by_hour.transform(lambda s: s.shift(1).rolling(7).mean())
    df["mean_weekday_4w"] = sum(by_hour.shift(7 * k) for k in (1, 2, 3, 4)) / 4
    totals = df.groupby("day")["orders"].sum().shift(1)
    df["prev_day_total"] = df["day"].map(totals)

    # calendar
    df["event"] = (df["event"].astype(str) != "").astype(int)
    df["event_type_code"] = df["event_type"].astype(str).map(EVENT_TYPES).fillna(0).astype(int)
    event_by_day = df.groupby("day")["event"].max()
    df["event_next_day"] = df["day"].map(event_by_day.shift(-1)).fillna(0).astype(int)
    df["christmas"] = df["date"].astype(str).str.endswith("-12-25").astype(int)
    df["snap"] = df["snap"].astype(int)
    return df


# ---------------------------------------------------------------------------
# Forecasts
# ---------------------------------------------------------------------------

def seasonal_naive(features, day):
    """Baseline: each hour of `day` = the same hour 7 days earlier."""
    return features.loc[features["day"] == day, "lag_7d"].to_numpy()


def train_lightgbm(features, until_day):
    """Train on every day before `until_day` that has a week of history."""
    import lightgbm as lgb
    train = features[(features["day"] < until_day) & features["lag_7d"].notna()]
    data = lgb.Dataset(train[FEATURES], label=train["orders"])
    return lgb.train(LGBM_PARAMS, data, num_boost_round=LGBM_ROUNDS)


def predict(model, features, day):
    rows = features[features["day"] == day]
    return np.clip(model.predict(rows[FEATURES]), 0, None)


def lightgbm_forecast(features, day):
    """LightGBM forecast for each hour of `day`, trained only on earlier days."""
    return predict(train_lightgbm(features, day), features, day)


def forecast_next_day(history, day, demand=None):
    """
    Forecast for one future day (e.g. the day after the history ends).
    Returns {hour: expected orders}. Used by the fleet and the website.
    `demand` gives the date and the calendar of that day (default: synthetic model).
    """
    demand = demand or DemandModel()
    calendar = demand.calendar(day)
    future = pd.DataFrame({"day": day, "hour": HOURS, "orders": np.nan,
                           "date": demand.date(day).isoformat(), "weekday": demand.weekday(day),
                           "event": calendar["event"], "event_type": calendar["event_type"],
                           "snap": calendar["snap"]})
    extended = pd.concat([history[history["day"] < day], future], ignore_index=True)
    features = add_features(extended)
    predictions = lightgbm_forecast(features, day)
    return dict(zip(HOURS, (float(p) for p in predictions)))


# ---------------------------------------------------------------------------
# Scores
# ---------------------------------------------------------------------------

def wape(actual, predicted):
    actual, predicted = np.asarray(actual, float), np.asarray(predicted, float)
    return float(np.abs(actual - predicted).sum() / actual.sum())


def mase(actual, predicted, scale):
    """Mean absolute error divided by `scale` (the naive rule's error on the training days)."""
    actual, predicted = np.asarray(actual, float), np.asarray(predicted, float)
    return float(np.abs(actual - predicted).mean() / scale)


def naive_scale(features, until_day):
    """How wrong 'same as last week' was, on average, on the training days."""
    train = features[(features["day"] < until_day) & features["lag_7d"].notna()]
    return float((train["orders"] - train["lag_7d"]).abs().mean())


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------

def backtest(history, first_test_day=FIRST_TEST_DAY, last_test_day=None, demand=None, retrain_every=1):
    """
    Walk forward over the test days. Returns one row per test day and hour with
    the real orders and each forecast. "perfect_average" is the true expected
    demand from the demand model (synthetic: the true average; m5: the day's real
    total spread over the hours). No forecast can know it in real life, but it
    shows how good a forecast could possibly be: the rest is pure chance.
    """
    features = add_features(history)
    last = int(history["day"].max()) if last_test_day is None else last_test_day
    truth = demand or DemandModel()
    parts, model = [], None
    for day in range(first_test_day, last + 1):
        if model is None or (day - first_test_day) % retrain_every == 0:
            model = train_lightgbm(features, day)        # only days before `day`
        rows = features[features["day"] == day]
        parts.append(pd.DataFrame({
            "day": day,
            "date": rows["date"].to_numpy(),
            "weekday": rows["weekday"].to_numpy(),
            "hour": rows["hour"].to_numpy(),
            "orders": rows["orders"].to_numpy(),
            "big_delivery_orders": rows["big_delivery_orders"].to_numpy(),
            "event": rows["event"].to_numpy(),
            "baseline": seasonal_naive(features, day),
            "lightgbm": predict(model, features, day),
            "perfect_average": truth.expected_hourly(day),
        }))
    return pd.concat(parts, ignore_index=True)


def scores(results, scale):
    """WAPE and MASE of every forecast (hourly), WAPE without big-delivery hours, and daily WAPE."""
    normal = results[results["big_delivery_orders"] == 0]
    daily = results.groupby("day")[["orders", "baseline", "lightgbm", "perfect_average"]].sum()
    table = []
    for name in ("baseline", "lightgbm", "perfect_average"):
        table.append({
            "forecast": name,
            "WAPE": wape(results["orders"], results[name]),
            "MASE": mase(results["orders"], results[name], scale),
            "WAPE without big deliveries": wape(normal["orders"], normal[name]),
            "daily WAPE": wape(daily["orders"], daily[name]),
        })
    return pd.DataFrame(table)


def main():
    parser = argparse.ArgumentParser(description="Backtest the order forecasts.")
    parser.add_argument("--source", choices=["synthetic", "m5"], default="synthetic")
    args = parser.parse_args()

    if args.source == "m5":
        demand = DemandModel(source="m5")
        history = load_history(HISTORY_M5, "m5")
        first = int(history["day"].max()) + 1 - 365      # test the last year
        retrain_every, out = 7, RESULTS_M5
        perfect = "Perfect daily total (not possible in real life)"
    else:
        demand = DemandModel()
        history = load_history(HISTORY, "synthetic")
        first, retrain_every, out = FIRST_TEST_DAY, 1, RESULTS
        perfect = "Perfect average (not possible in real life)"

    results = backtest(history, first, demand=demand, retrain_every=retrain_every)
    scale = naive_scale(add_features(history), first)
    table = scores(results, scale)

    print(f"Backtest ({args.source}): {results['day'].nunique()} days "
          f"({results['date'].iloc[0]} to {results['date'].iloc[-1]}), each forecast made the evening before, "
          f"trained only on earlier days (retrained every {retrain_every} day(s)).\n")
    labels = {"baseline": "Baseline (same as last week)", "lightgbm": "LightGBM", "perfect_average": perfect}
    print(f"{'forecast':<50} {'WAPE':>7} {'MASE':>6} {'daily WAPE':>11}")
    for _, row in table.iterrows():
        print(f"{labels[row['forecast']]:<50} {row['WAPE']:>7.1%} {row['MASE']:>6.2f} {row['daily WAPE']:>11.1%}")

    t = table.set_index("forecast")
    for col, name in (("WAPE", "hourly"), ("daily WAPE", "daily")):
        base, lgbm = t.loc["baseline", col], t.loc["lightgbm", col]
        print(f"{name:>7}: LightGBM is {(1 - lgbm / base):.0%} more accurate than the baseline ({base:.1%} -> {lgbm:.1%})")

    if args.source == "m5":
        ev = results[results["event"] == 1].groupby("day")[["orders", "baseline", "lightgbm"]].sum()
        if len(ev):
            print(f"\nOn the {len(ev)} holiday/event days: baseline daily WAPE {wape(ev['orders'], ev['baseline']):.1%}, "
                  f"LightGBM {wape(ev['orders'], ev['lightgbm']):.1%}")

    os.makedirs(os.path.dirname(out), exist_ok=True)
    results.to_csv(out, index=False)
    print(f"Every forecast is saved in {out}")


if __name__ == "__main__":
    main()
