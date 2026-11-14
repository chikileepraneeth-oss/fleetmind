"""
Tests for the M5 data (scripts/m5.py) and real demand in the demand model.

The real M5 files are big and not part of this project, so these tests build a
tiny fake M5 folder with the same columns.
"""

import pandas as pd
import pytest

from scripts.demand import DemandModel, MINUTES_PER_DAY
from scripts.m5 import extract, write_daily, read_daily
from scripts.forecast import add_features


def fake_m5(folder):
    (folder / "sales_train_evaluation.csv").write_text(
        "id,item_id,dept_id,cat_id,store_id,state_id,d_1,d_2,d_3\n"
        "A,FOODS_1_001,FOODS_1,FOODS,CA_1,CA,10,20,0\n"
        "B,FOODS_1_002,FOODS_1,FOODS,CA_1,CA,5,5,0\n"
        "C,HOBBIES_1_001,HOBBIES_1,HOBBIES,CA_1,CA,99,99,99\n"      # other category
        "D,FOODS_1_001,FOODS_1,FOODS,TX_1,TX,77,77,77\n",           # other store
        encoding="utf-8")
    (folder / "calendar.csv").write_text(
        "date,wm_yr_wk,weekday,wday,month,year,d,event_name_1,event_type_1,event_name_2,event_type_2,snap_CA,snap_TX,snap_WI\n"
        "2015-12-23,1,Wednesday,5,12,2015,d_1,,,,,1,0,0\n"
        "2015-12-24,1,Thursday,6,12,2015,d_2,,,,,0,0,0\n"
        "2015-12-25,1,Friday,7,12,2015,d_3,Christmas,National,,,0,0,0\n",
        encoding="utf-8")
    return folder


@pytest.fixture
def daily_file(tmp_path):
    rows = extract(str(fake_m5(tmp_path)))
    path = tmp_path / "daily.csv"
    write_daily(rows, str(path))
    return str(path)


def test_only_foods_of_the_store_are_added_up(tmp_path):
    rows = extract(str(fake_m5(tmp_path)))
    assert [r["units"] for r in rows] == [15, 25, 0]


def test_calendar_is_joined(tmp_path):
    rows = extract(str(fake_m5(tmp_path)))
    assert rows[0]["date"] == "2015-12-23" and rows[0]["weekday"] == 2 and rows[0]["snap"] == 1
    assert rows[2]["event"] == "Christmas" and rows[2]["event_type"] == "National"


def test_daily_file_reads_back(daily_file):
    rows = read_daily(daily_file)
    assert [r["units"] for r in rows] == [15, 25, 0]


def test_missing_daily_file_gives_a_clear_message(tmp_path):
    with pytest.raises(FileNotFoundError, match="python -m scripts.m5"):
        read_daily(str(tmp_path / "nope.csv"))


def test_m5_demand_scales_the_real_days(daily_file):
    model = DemandModel(source="m5", m5_path=daily_file, orders_per_day=140)
    # average units = 40/3; 140 orders a day on average -> day 0 = 15 * 140 / (40/3) = 157.5 -> 158
    assert model.daily_total(0) == round(15 * 140 / (40 / 3))
    assert model.daily_total(2) == 0                          # closed on Christmas
    assert model.days == 3
    assert model.date(0).isoformat() == "2015-12-23"


def test_m5_day_has_exactly_its_real_orders(daily_file):
    model = DemandModel(source="m5", m5_path=daily_file)
    arrivals = model.arrivals(1)
    assert len(arrivals) == MINUTES_PER_DAY
    assert sum(arrivals) == model.daily_total(1)
    assert sum(model.hourly(1)) == model.daily_total(1)
    assert DemandModel(source="m5", m5_path=daily_file).arrivals(1) == arrivals    # repeatable


def test_m5_calendar_and_end_of_data(daily_file):
    model = DemandModel(source="m5", m5_path=daily_file)
    assert model.calendar(2) == {"event": "Christmas", "event_type": "National", "snap": 0}
    with pytest.raises(IndexError):
        model.arrivals(3)


def test_calendar_features():
    df = pd.DataFrame({
        "day": [0, 1, 2], "hour": [6, 6, 6], "orders": [5, 6, 0],
        "date": ["2015-12-23", "2015-12-24", "2015-12-25"], "weekday": [2, 3, 4],
        "big_delivery_orders": 0,
        "event": ["", "", "Christmas"], "event_type": ["", "", "National"], "snap": [1, 0, 0],
    })
    f = add_features(df)
    assert list(f["event"]) == [0, 0, 1]
    assert list(f["event_type_code"]) == [0, 0, 2]
    assert list(f["event_next_day"]) == [0, 1, 0]        # the calendar knows tomorrow is Christmas
    assert list(f["christmas"]) == [0, 0, 1]
    assert list(f["snap"]) == [1, 0, 0]
