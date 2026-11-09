"""
Tests for multi-cell shelf units and the layout loader (scripts/layout.py).

Run from the fleetmind folder with:
    pytest
"""

import copy

import pytest

from scripts.classes import Robot, Order, Priority, CHARGER, PACKING, DOCK
from scripts.layout import load_layout, build_warehouse, LayoutError

MAIN_LAYOUT = "layouts/main_warehouse.json"


# A small valid layout used as a starting point for "broken file" tests.
#   One 2x3 shelf unit "a", one packing station, one robot.
SMALL = {
    "map": [
        "#######",
        "#.....#",
        "#.aaa.#",
        "#.aaa.#",
        "#....P#",
        "#######",
    ],
    "shelves": {"A": {"name": "Food", "items": {"apples": 5}}},
    "robots": [{"id": "R1", "position": [4, 2]}],
}


def small_layout():
    """A fresh copy, so one test's changes never leak into another."""
    return copy.deepcopy(SMALL)


# ---------------------------------------------------------------------------
# The main warehouse file
# ---------------------------------------------------------------------------

def test_main_layout_loads():
    wh = load_layout(MAIN_LAYOUT)
    assert wh.height == 13
    assert wh.width == 17


def test_main_layout_has_four_shelves_of_3x5():
    wh = load_layout(MAIN_LAYOUT)
    assert sorted(wh.shelves) == ["A", "B", "C", "D"]
    for shelf in wh.shelves.values():
        assert len(shelf.cells) == 15
        rows = {r for r, _ in shelf.cells}
        cols = {c for _, c in shelf.cells}
        assert len(rows) == 3          # 3 tall
        assert len(cols) == 5          # 5 wide


def test_main_layout_stations_and_robots():
    wh = load_layout(MAIN_LAYOUT)
    assert len(wh.cells_of_type(CHARGER)) == 3
    assert len(wh.cells_of_type(PACKING)) == 2
    assert len(wh.cells_of_type(DOCK)) == 3
    assert sorted(wh.robots) == ["R1", "R2", "R3"]


def test_every_shelf_in_main_layout_is_reachable():
    wh = load_layout(MAIN_LAYOUT)
    for shelf in wh.shelves.values():
        assert len(wh.access_cells(shelf)) > 0


def test_find_shelf_with_item():
    wh = load_layout(MAIN_LAYOUT)
    assert wh.find_shelf_with("apples").shelf_id == "A"
    assert wh.find_shelf_with("milk").shelf_id == "C"
    assert wh.find_shelf_with("unicorns") is None


# ---------------------------------------------------------------------------
# Multi-cell shelf units
# ---------------------------------------------------------------------------

def test_robot_cannot_drive_into_any_shelf_cell():
    wh = build_warehouse(small_layout())
    for cell in wh.get_shelf("A").cells:
        ok, _ = wh.can_enter(cell)
        assert ok is False


def test_robot_can_pick_next_to_any_side_of_the_unit():
    # Robot below the middle of the bottom row of shelf A
    wh = build_warehouse(small_layout())
    robot = wh.robots["R1"]                      # at (4, 2), below cell (3, 2)
    robot.order = Order("O-1", "apples", "A", (4, 5), Priority.NORMAL)

    success, _ = robot.pick(wh)

    assert success is True
    assert wh.get_shelf("A").items["apples"] == 4


def test_robot_diagonal_to_unit_cannot_pick():
    data = small_layout()
    data["robots"] = [{"id": "R1", "position": [1, 1]}]    # diagonal to corner (2, 2)
    wh = build_warehouse(data)
    robot = wh.robots["R1"]
    robot.order = Order("O-1", "apples", "A", (4, 5), Priority.NORMAL)

    success, _ = robot.pick(wh)

    assert success is False


def test_access_cells_surround_the_unit():
    wh = build_warehouse(small_layout())
    spots = wh.access_cells(wh.get_shelf("A"))
    # 3 above, 3 below, 2 left, 2 right of a 2x3 block
    assert len(spots) == 10
    assert (1, 2) in spots and (4, 4) in spots and (2, 1) in spots and (3, 5) in spots


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


def test_shelf_on_map_without_description_fails():
    data = small_layout()
    data["shelves"] = {}
    with pytest.raises(LayoutError, match="missing in 'shelves'"):
        build_warehouse(data)


def test_described_shelf_not_on_map_fails():
    data = small_layout()
    data["shelves"]["B"] = {"items": {"rice": 5}}
    with pytest.raises(LayoutError, match="not on the map"):
        build_warehouse(data)


def test_negative_quantity_fails():
    data = small_layout()
    data["shelves"]["A"]["items"]["apples"] = -3
    with pytest.raises(LayoutError, match="whole number"):
        build_warehouse(data)


def test_same_item_on_two_shelves_fails():
    data = small_layout()
    data["map"][1] = "#.....#"
    data["map"][4] = "#b...P#"
    data["shelves"]["B"] = {"items": {"apples": 2}}
    with pytest.raises(LayoutError, match="one shelf per item"):
        build_warehouse(data)


def test_robot_starting_on_shelf_fails():
    data = small_layout()
    data["robots"] = [{"id": "R1", "position": [2, 2]}]
    with pytest.raises(LayoutError):
        build_warehouse(data)


def test_two_robots_same_cell_fails():
    data = small_layout()
    data["robots"] = [{"id": "R1", "position": [4, 2]},
                      {"id": "R2", "position": [4, 2]}]
    with pytest.raises(LayoutError):
        build_warehouse(data)


def test_duplicate_robot_id_fails():
    data = small_layout()
    data["robots"] = [{"id": "R1", "position": [4, 2]},
                      {"id": "R1", "position": [4, 3]}]
    with pytest.raises(LayoutError):
        build_warehouse(data)


def test_unreachable_shelf_fails():
    data = small_layout()
    # Shelf "b" in a cell surrounded by walls on all sides is impossible here,
    # so wall in shelf A completely instead.
    data["map"] = [
        "#######",
        "#.###.#",
        "##aaa##",
        "##aaa##",
        "#.###P#",
        "#######",
    ]
    data["robots"] = [{"id": "R1", "position": [1, 1]}]
    with pytest.raises(LayoutError, match="no free cell"):
        build_warehouse(data)