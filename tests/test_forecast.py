"""
Tests for the order forecast (scripts/forecast.py).
"""

import numpy as np
import pandas as pd
import pytest

from scripts.demand import HOURS
from scripts.history import make_history
from scripts.forecast import (
    add_features, seasonal_naive, lightgbm_forecast, forecast_next_day,
    backtest, scores, naive_scale, wape, mase, FEATURES,
)


@pytest.fixture(scope="module")
def history():
    return pd.DataFrame(make_history(days=120, seed=7))


# ---------------------------------------------------------------------------
# Scores
# ---------------------------------------------------------------------------

def test_wape():
    # errors 2 + 0 + 2 = 4, real orders 10 + 10 + 20 = 40  ->  10%
    assert wape([10, 10, 20], [12, 10, 18]) == pytest.approx(0.10)


def test_mase():
    # average error 2, the naive rule's average error is 4  ->  0.5 (twice as good)
    assert mase([10, 10], [12, 8], scale=4) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------

def test_lag_features_look_back_the_right_number_of_days(history):
    f = add_features(history).set_index(["day", "hour"])
    orders = history.set_index(["day", "hour"])["orders"]
    assert f.loc[(30, 17), "lag_1d"] == orders[(29, 17)]
    assert f.loc[(30, 17), "lag_7d"] == orders[(23, 17)]
    assert f.loc[(30, 17), "lag_14d"] == orders[(16, 17)]
    assert f.loc[(30, 17), "mean_7d"] == pytest.approx(np.mean([orders[(d, 17)] for d in range(23, 30)]))
    assert f.loc[(30, 17), "prev_day_total"] == history[history["day"] == 29]["orders"].sum()


def test_no_leakage_features_never_see_the_day_they_predict(history):
    day = 100
    changed = history.copy()
    changed.loc[changed["day"] >= day, "orders"] = 999       # change today and the future
    before = add_features(history)
    after = add_features(changed)
    rows = before["day"] == day
    pd.testing.assert_frame_equal(before.loc[rows, FEATURES], after.loc[rows, FEATURES])


def test_forecast_does_not_use_the_big_delivery_column():
    assert "big_delivery_orders" not in FEATURES       # nobody knows about a big delivery in advance


def test_seasonal_naive_is_the_same_hour_last_week(history):
    f = add_features(history)
    week_ago = history[history["day"] == 93]["orders"].to_numpy()
    assert list(seasonal_naive(f, 100)) == list(week_ago)


# ---------------------------------------------------------------------------
# Forecasts
# ---------------------------------------------------------------------------

def test_lightgbm_gives_one_sensible_number_per_hour(history):
    prediction = lightgbm_forecast(add_features(history), 100)
    assert len(prediction) == len(HOURS)
    assert (prediction >= 0).all()
    assert prediction[HOURS.index(18)] > prediction[HOURS.index(6)]     # it learned the evening peak


def test_forecast_for_a_day_after_the_history(history):
    forecast = forecast_next_day(history, 120)            # the history ends on day 119
    assert list(forecast) == HOURS
    assert 60 < sum(forecast.values()) < 250              # about 138 orders a day on average


def test_lightgbm_forecast_is_repeatable(history):
    f = add_features(history)
    assert list(lightgbm_forecast(f, 100)) == list(lightgbm_forecast(f, 100))


# ---------------------------------------------------------------------------
# Checkpoint: the forecast beats the baseline in the backtest
# ---------------------------------------------------------------------------

def test_checkpoint_lightgbm_beats_the_baseline(history):
    results = backtest(history, first_test_day=90)
    table = scores(results, naive_scale(add_features(history), 90)).set_index("forecast")
    assert table.loc["lightgbm", "WAPE"] < table.loc["baseline", "WAPE"]
    assert table.loc["lightgbm", "MASE"] < 1.0
    # and nothing can beat knowing the true average by much (if it did, something would be leaking)
    assert table.loc["lightgbm", "WAPE"] > table.loc["perfect_average", "WAPE"] - 0.02
