"""
Realistic order demand: how many orders arrive in each minute of a working day.

Time: 1 tick = 1 minute. The warehouse is open 06:00-22:00, so a working day
is 960 minutes (ticks). Minute 0 is 06:00, minute 959 is 21:59.

Expected orders in an hour =
        base orders for that hour       (daily peaks: morning and evening)
      x weekday factor                  (busy Monday and Friday, quiet Sunday)
      x level of the day                (some weeks are a bit busier than others)

The level moves slowly from day to day (each day it keeps 70% of yesterday's
difference from normal, plus a little noise), so yesterday really tells you
something about today, like in a real warehouse.

The real number of orders in each minute is random around the expected number
(a Poisson draw: the usual model for "how many arrivals in a minute").
On some days a big delivery comes on top: 15-30 orders within 5 minutes.
A forecast can't know when those come - that is realistic too.

Every day has its own random generator made from (seed, day), so day 57 is
always exactly the same, no matter which other days you looked at first.

Two sources for how many orders a day gets:
    "synthetic"  the made-up model above (weekday factor x level, plus big deliveries)
    "m5"         REAL daily demand: the units a Walmart store (CA_1, FOODS) sold that
                 day, from data/m5_ca1_foods_daily.csv (made by scripts/m5.py), scaled
                 so the average day has ORDERS_PER_DAY orders. Day 0 = 2011-01-29.
                 The daily curve above still spreads the day's orders over the hours,
                 because M5 only has daily numbers.
"""

import math
import random
from datetime import date, timedelta

OPEN_HOUR, CLOSE_HOUR = 6, 22
HOURS = list(range(OPEN_HOUR, CLOSE_HOUR))         # 6, 7, ..., 21
MINUTES_PER_DAY = len(HOURS) * 60                   # 960

# Expected orders per hour on a normal Tuesday at normal level.
# Morning peak around 10:00, quieter at lunch, bigger evening peak around 18:00.
# (5 robots deliver about 10-11 orders an hour, so the peaks are more than they can do.)
BASE_PER_HOUR = {
    6: 2, 7: 4, 8: 7, 9: 11, 10: 13, 11: 11, 12: 7, 13: 6,
    14: 7, 15: 9, 16: 12, 17: 15, 18: 16, 19: 12, 20: 7, 21: 4,
}

# Monday = 0 ... Sunday = 6
WEEKDAY_FACTOR = [1.15, 1.00, 0.95, 1.05, 1.20, 0.70, 0.45]
WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

LEVEL_MEMORY = 0.7        # how much of yesterday's level stays
LEVEL_NOISE = 0.08        # how much the level changes from day to day
BIG_DELIVERY_CHANCE = 0.1 # chance of a big delivery on a day
BIG_DELIVERY_SIZE = (15, 30)
BIG_DELIVERY_MINUTES = 5  # a big delivery arrives over this many minutes

START_DATE = date(2026, 6, 1)   # a Monday; day 0 of the synthetic history
ORDERS_PER_DAY = 140            # m5: the real units are scaled so an average day has this many orders
EVENT_TYPES = {"": 0, "Cultural": 1, "National": 2, "Religious": 3, "Sporting": 4}


def poisson(rng, mean):
    """A random whole number of arrivals with this mean (Knuth's method, fine for small means)."""
    if mean <= 0:
        return 0
    limit = math.exp(-mean)
    k, p = 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def hour_of(minute):
    """The clock hour (6-21) of a minute of the working day (0-959)."""
    return OPEN_HOUR + minute // 60


def clock(minute):
    """'06:00' for minute 0, '17:30' for minute 690, ..."""
    return f"{OPEN_HOUR + minute // 60:02d}:{minute % 60:02d}"


class DemandModel:

    def __init__(self, seed=7, start=START_DATE, source="synthetic", m5_path=None,
                 orders_per_day=ORDERS_PER_DAY):
        self.seed = seed
        self.start = start
        self.source = source
        self.days = None                       # how many days exist (None = no end)
        if source == "m5":
            from scripts.m5 import read_daily, OUTPUT
            self._m5 = read_daily(m5_path or OUTPUT)
            mean = sum(r["units"] for r in self._m5) / len(self._m5)
            self.scale = orders_per_day / mean
            self.start = date.fromisoformat(self._m5[0]["date"])
            self.days = len(self._m5)
        elif source != "synthetic":
            raise ValueError(f"unknown demand source '{source}'")
        self._levels = []          # level of day 0, 1, 2, ... (worked out as needed)
        self._x = 0.0              # today's level as a number around 0 (level = e^x)
        self._rng_level = random.Random(f"{seed}:level")
        self._cache = {}           # day -> (arrivals per minute, big deliveries)

    # ----- calendar -----

    def date(self, day):
        return self.start + timedelta(days=day)

    def weekday(self, day):
        return self.date(day).weekday()

    def calendar(self, day):
        """What a calendar says about this day (known in advance): event and SNAP day."""
        if self.source != "m5":
            return {"event": "", "event_type": "", "snap": 0}
        row = self._m5_row(day)
        return {"event": row["event"], "event_type": row["event_type"], "snap": row["snap"]}

    def _m5_row(self, day):
        if not 0 <= day < self.days:
            raise IndexError(f"M5 has days 0-{self.days - 1}, not day {day}")
        return self._m5[day]

    def daily_total(self, day):
        """m5: the real orders of this day (the store's units, scaled)."""
        return round(self._m5_row(day)["units"] * self.scale)

    # ----- the expected (true average) demand -----

    def level(self, day):
        """How busy this day is compared with normal (1.0 = normal)."""
        while len(self._levels) <= day:
            self._x = LEVEL_MEMORY * self._x + self._rng_level.gauss(0, LEVEL_NOISE)
            self._levels.append(math.exp(self._x))
        return self._levels[day]

    def expected_hourly(self, day):
        """
        The true expected orders for each hour (6-21) of this day, without big deliveries.
        m5: the day's real total, spread over the hours by the daily curve.
        """
        if self.source == "m5":
            total = self.daily_total(day)
            base = sum(BASE_PER_HOUR.values())
            return [total * BASE_PER_HOUR[h] / base for h in HOURS]
        factor = WEEKDAY_FACTOR[self.weekday(day)] * self.level(day)
        return [BASE_PER_HOUR[h] * factor for h in HOURS]

    # ----- what really happens -----

    def _make_day(self, day):
        if day in self._cache:
            return self._cache[day]
        if self.source == "m5":
            return self._make_m5_day(day)
        rng = random.Random(f"{self.seed}:{day}")
        expected = self.expected_hourly(day)

        big = []
        if rng.random() < BIG_DELIVERY_CHANCE:
            size = rng.randint(*BIG_DELIVERY_SIZE)
            start = rng.randrange(60, MINUTES_PER_DAY - 60)   # not in the first or last hour
            big.append((start, size))

        arrivals = []
        for minute in range(MINUTES_PER_DAY):
            arrivals.append(poisson(rng, expected[minute // 60] / 60))
        for start, size in big:
            for i in range(size):
                arrivals[start + i % BIG_DELIVERY_MINUTES] += 1

        self._cache[day] = (arrivals, big)
        return self._cache[day]

    def _make_m5_day(self, day):
        """m5: exactly the day's real number of orders, each at a random minute (busier hours more likely)."""
        rng = random.Random(f"{self.seed}:m5:{day}")
        weights = [BASE_PER_HOUR[hour_of(m)] for m in range(MINUTES_PER_DAY)]
        arrivals = [0] * MINUTES_PER_DAY
        for minute in rng.choices(range(MINUTES_PER_DAY), weights=weights, k=self.daily_total(day)):
            arrivals[minute] += 1
        self._cache[day] = (arrivals, [])
        return self._cache[day]

    def arrivals(self, day):
        """How many orders arrive in each minute (0-959) of this day."""
        return self._make_day(day)[0]

    def big_deliveries(self, day):
        """[(start minute, number of orders), ...] for this day (usually empty)."""
        return self._make_day(day)[1]

    def hourly(self, day):
        """How many orders arrived in each hour (6-21) of this day."""
        arrivals = self.arrivals(day)
        return [sum(arrivals[i * 60:(i + 1) * 60]) for i in range(len(HOURS))]
