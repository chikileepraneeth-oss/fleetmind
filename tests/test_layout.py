"""
Tests for the layout loader (scripts/layout.py) and the shelf rows.

Layout format: each lowercase letter on the map is a shelf row. Every cell of
a row is its own shelf (A1, A2, ...) holding exactly one item.

Run from the fleetmind folder with:
    python -m pytest
"""

import copy
import json

import pytest

from scripts.classes import Order, Priority, CHARGER, PACKING, DOCK
from scripts.layout import load_layout, build_warehouse, LayoutError

MAIN_LAYOUT = "layouts/main_warehouse.json"


# A small valid layout used as a starting point for "broken file" tests.
#   One shelf row "a" with 3 shelves (A1, A2, A3), one packing station, one robot.
#
#   row 0:  # # # # # # #
#   row 1:  # . . . . . #
#   row 2:  # . a a a . #      A1=(2,2) apples, A2=(2,3) rice, A3=(2,4) milk
#   row 3:  # . . . . . #
#   row 4:  # . . . . P #
#   row 5:  # # # # # # #
SMALL = {
    "map": [
        "#######",
        "#.....#",
        "#.aaa.#",
        "#.....#",
        "#....P#",
        "#######",
    ],
    "stock_per_shelf": 5,
    "shelves": {"A": {"name": "Food", "items": ["apples", "rice", "milk"]}},
    "robots": [{"id": "R1", "position": [3, 2]}],
}


def small_layout():
    """A fresh copy, so one test's changes never leak into another."""
    return copy.deepcopy(SMALL)


def main_layout_file():
    with open(MAIN_LAYOUT, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# The main warehouse file
# ---------------------------------------------------------------------------

def test_main_layout_loads():
    wh = load_layout(MAIN_LAYOUT)
    data = main_layout_file()
    assert wh.height == len(data["map"])
    assert wh.width == len(data["map"][0])


def test_main_layout_has_8_rows_of_5_shelves():
    wh = load_layout(MAIN_LAYOUT)
    assert len(wh.shelves) == 40
    for row in "ABCDEFGH":
        assert sorted(s for s in wh.shelves if s[0] == row) == [f"{row}{n}" for n in range(1, 6)]


def test_every_shelf_is_one_cell_with_one_item():
    wh = load_layout(MAIN_LAYOUT)
    for shelf in wh.shelves.values():
        assert len(shelf.cells) == 1
        assert len(shelf.items) == 1


def test_shelves_are_numbered_left_to_right():
    wh = load_layout(MAIN_LAYOUT)
    columns = [wh.get_shelf(f"A{n}").cells[0][1] for n in range(1, 6)]
    assert columns == sorted(columns)
    rows = {wh.get_shelf(f"A{n}").cells[0][0] for n in range(1, 6)}
    assert len(rows) == 1                      # all of row A is on one map row


def test_shelf_rows_have_an_aisle_above_and_below():
    wh = load_layout(MAIN_LAYOUT)
    for shelf in wh.shelves.values():
        row, col = shelf.cells[0]
        assert wh.cell_type((row - 1, col)) != "S"
        assert wh.cell_type((row + 1, col)) != "S"


def test_main_layout_stations_and_robots():
    wh = load_layout(MAIN_LAYOUT)
    data = main_layout_file()
    assert len(wh.cells_of_type(CHARGER)) == 3
    assert len(wh.cells_of_type(PACKING)) == 2
    assert len(wh.cells_of_type(DOCK)) == 3
    # robots: whatever the file says, so this test follows your edits
    assert sorted(wh.robots) == sorted(r["id"] for r in data["robots"])


def test_every_shelf_in_main_layout_is_reachable():
    wh = load_layout(MAIN_LAYOUT)
    for shelf in wh.shelves.values():
        assert len(wh.access_cells(shelf)) > 0


def test_find_shelf_with_item():
    wh = load_layout(MAIN_LAYOUT)
    assert wh.find_shelf_with("apples").shelf_id == "A1"
    assert wh.find_shelf_with("milk").shelf_id == "D1"
    assert wh.find_shelf_with("detergent").shelf_id == "H5"
    assert wh.find_shelf_with("unicorns") is None


def test_stock_per_shelf_is_used():
    wh = load_layout(MAIN_LAYOUT)
    stock = main_layout_file()["stock_per_shelf"]
    assert wh.get_shelf("A1").items == {"apples": stock}


# ---------------------------------------------------------------------------
# Picking from single-cell shelves
# ---------------------------------------------------------------------------

def test_robot_cannot_drive_into_a_shelf():
    wh = build_warehouse(small_layout())
    for shelf in wh.shelves.values():
        ok, _ = wh.can_enter(shelf.cells[0])
        assert ok is False


def test_robot_picks_from_the_shelf_it_stands_next_to():
    wh = build_warehouse(small_layout())
    robot = wh.robots["R1"]                      # at (3, 2), directly below A1
    robot.order = Order("O-1", "apples", "A1", (4, 5), Priority.NORMAL)

    success, _ = robot.pick(wh)

    assert success is True
    assert wh.get_shelf("A1").items["apples"] == 4


def test_robot_next_to_a_different_shelf_cannot_pick():
    wh = build_warehouse(small_layout())
    robot = wh.robots["R1"]                      # below A1, but the order is for A3
    robot.order = Order("O-1", "milk", "A3", (4, 5), Priority.NORMAL)

    success, reason = robot.pick(wh)

    assert success is False
    assert "not next to shelf A3" in reason


def test_robot_diagonal_to_shelf_cannot_pick():
    data = small_layout()
    data["robots"] = [{"id": "R1", "position": [1, 1]}]    # diagonal to A1 at (2, 2)
    wh = build_warehouse(data)
    robot = wh.robots["R1"]
    robot.order = Order("O-1", "apples", "A1", (4, 5), Priority.NORMAL)

    success, _ = robot.pick(wh)

    assert success is False


def test_access_cells_of_a_middle_shelf_are_above_and_below():
    wh = build_warehouse(small_layout())
    spots = wh.access_cells(wh.get_shelf("A2"))   # A2 at (2, 3), between A1 and A3
    assert spots == [(1, 3), (3, 3)]


def test_access_cells_of_an_end_shelf_include_the_side():
    wh = build_warehouse(small_layout())
    spots = wh.access_cells(wh.get_shelf("A1"))   # A1 at (2, 2), floor on its left
    assert spots == [(1, 2), (2, 1), (3, 2)]


# ---------------------------------------------------------------------------
# Broken layout files must give clear errors
# ---------------------------------------------------------------------------

def test_rows_of_different_length_fail():
    data = small_layout()
    data["map"][1] = "#....#"                    # one character short
    with pytest.raises(LayoutError, match="same length"):
        build_warehouse(data)


def test_unknown_symbol_fails():
    data = small_layout()
    data["map"][1] = "#..X..#"
    with pytest.raises(LayoutError, match="Unknown symbol"):
        build_warehouse(data)


def test_shelf_row_on_map_without_description_fails():
    data = small_layout()
    data["shelves"] = {}
    with pytest.raises(LayoutError, match="missing in 'shelves'"):
        build_warehouse(data)


def test_described_shelf_row_not_on_map_fails():
    data = small_layout()
    data["shelves"]["B"] = {"items": ["bread"]}
    with pytest.raises(LayoutError, match="not on the map"):
        build_warehouse(data)


def test_too_few_items_for_the_cells_fails():
    data = small_layout()
    data["shelves"]["A"]["items"] = ["apples", "rice"]     # 3 cells, 2 items
    with pytest.raises(LayoutError, match="one item per cell"):
        build_warehouse(data)


def test_too_many_items_for_the_cells_fails():
    data = small_layout()
    data["shelves"]["A"]["items"].append("bread")         # 3 cells, 4 items
    with pytest.raises(LayoutError, match="one item per cell"):
        build_warehouse(data)


def test_negative_stock_fails():
    data = small_layout()
    data["stock_per_shelf"] = -3
    with pytest.raises(LayoutError, match="whole number"):
        build_warehouse(data)


def test_same_item_on_two_shelves_fails():
    data = small_layout()
    data["shelves"]["A"]["items"] = ["apples", "rice", "apples"]
    with pytest.raises(LayoutError, match="one shelf only"):
        build_warehouse(data)


def test_robot_starting_on_shelf_fails():
    data = small_layout()
    data["robots"] = [{"id": "R1", "position": [2, 2]}]
    with pytest.raises(LayoutError):
        build_warehouse(data)


def test_two_robots_same_cell_fails():
    data = small_layout()
    data["robots"] = [{"id": "R1", "position": [3, 2]},
                      {"id": "R2", "position": [3, 2]}]
    with pytest.raises(LayoutError):
        build_warehouse(data)


def test_duplicate_robot_id_fails():
    data = small_layout()
    data["robots"] = [{"id": "R1", "position": [3, 2]},
                      {"id": "R1", "position": [3, 3]}]
    with pytest.raises(LayoutError):
        build_warehouse(data)


def test_unreachable_shelf_fails():
    data = small_layout()
    # shelf row walled in on every side
    data["map"] = [
        "#######",
        "#.###.#",
        "##aaa##",
        "#.###P#",
        "#.....#",
        "#######",
    ]
    data["robots"] = [{"id": "R1", "position": [4, 1]}]
    with pytest.raises(LayoutError, match="no free cell"):
        build_warehouse(data)