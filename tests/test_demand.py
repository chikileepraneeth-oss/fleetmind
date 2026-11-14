"""
Tests for the realistic demand model (scripts/demand.py).
"""

import statistics

from scripts.demand import (
    DemandModel, HOURS, MINUTES_PER_DAY, BASE_PER_HOUR, poisson, clock, hour_of,
)
import random


def test_same_seed_gives_the_same_days():
    a, b = DemandModel(seed=7), DemandModel(seed=7)
    for day in (0, 3, 40):
        assert a.arrivals(day) == b.arrivals(day)


def test_a_day_is_the_same_whatever_you_looked_at_first():
    a, b = DemandModel(seed=7), DemandModel(seed=7)
    a.hourly(5)                                   # look at day 5 first
    assert a.arrivals(30) == b.arrivals(30)       # day 30 doesn't change


def test_different_seeds_give_different_days():
    assert DemandModel(seed=7).hourly(0) != DemandModel(seed=8).hourly(0)


def test_a_day_has_one_number_per_minute_and_per_hour():
    model = DemandModel()
    assert len(model.arrivals(0)) == MINUTES_PER_DAY == 960
    assert len(model.hourly(0)) == len(HOURS) == 16
    assert sum(model.hourly(0)) == sum(model.arrivals(0))


def test_clock_and_hours():
    assert clock(0) == "06:00"
    assert clock(690) == "17:30"
    assert hour_of(959) == 21


def test_poisson_has_the_right_average():
    rng = random.Random(1)
    draws = [poisson(rng, 0.3) for _ in range(20000)]
    assert abs(statistics.mean(draws) - 0.3) < 0.02


def test_evening_peak_is_busier_than_lunch_and_early_morning():
    model = DemandModel()
    days = range(0, 70)
    by_hour = {h: statistics.mean(model.hourly(d)[HOURS.index(h)] for d in days) for h in HOURS}
    assert by_hour[18] > by_hour[13] > by_hour[6]
    assert by_hour[10] > by_hour[13]                       # morning peak too


def test_friday_is_busy_and_sunday_is_quiet():
    model = DemandModel()
    totals = {wd: [] for wd in range(7)}
    for day in range(0, 140):
        totals[model.weekday(day)].append(sum(model.hourly(day)))
    friday, sunday, tuesday = (statistics.mean(totals[w]) for w in (4, 6, 1))
    assert friday > tuesday > sunday


def test_real_orders_match_the_expected_average():
    model = DemandModel()
    real = sum(sum(model.hourly(d)) - sum(n for _, n in model.big_deliveries(d)) for d in range(100))
    expected = sum(sum(model.expected_hourly(d)) for d in range(100))
    assert abs(real - expected) / expected < 0.03


def test_big_deliveries_happen_sometimes_and_add_their_orders():
    model = DemandModel()
    days_with_big = [d for d in range(120) if model.big_deliveries(d)]
    assert 4 <= len(days_with_big) <= 25                    # about 1 day in 10
    day = days_with_big[0]
    start, size = model.big_deliveries(day)[0]
    burst = sum(model.arrivals(day)[start:start + 5])
    assert burst >= size                                    # the burst is really there


def test_level_moves_slowly():
    model = DemandModel()
    levels = [model.level(d) for d in range(200)]
    assert all(0.6 < lv < 1.6 for lv in levels)
    jumps = [abs(levels[i + 1] - levels[i]) for i in range(199)]
    assert statistics.mean(jumps) < 0.12


def test_base_demand_has_peaks_above_what_five_robots_can_do():
    assert max(BASE_PER_HOUR.values()) > 11                 # 5 robots deliver about 10-11 per hour
