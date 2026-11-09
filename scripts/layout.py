"""
Load a warehouse from a layout file (JSON), so the warehouse is defined once
and edited as text instead of code.

Usage:
    from scripts.layout import load_layout
    wh = load_layout("layouts/main_warehouse.json")

Map symbols:
    #  wall          .  floor
    C  charger       P  packing station     D  loading dock
    a-z  shelf unit: all cells with the same lowercase letter form one shelf
         (a -> shelf "A", b -> shelf "B", ...)
"""

import json

from scripts.classes import (
    Warehouse, Shelf, Robot,
    WALL, FLOOR, CHARGER, PACKING, DOCK,
)

STATION_SYMBOLS = {WALL, FLOOR, CHARGER, PACKING, DOCK}


class LayoutError(Exception):
    """Raised when a layout file has a mistake. The message says what is wrong."""


def load_layout(path):
    """Read a layout file and return a ready-to-use Warehouse with shelves and robots."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return build_warehouse(data)


def build_warehouse(data):
    """Build a Warehouse from an already-loaded layout dictionary."""
    rows = data.get("map")
    if not rows:
        raise LayoutError("The layout has no 'map'.")

    # --- Check 1: all rows have the same length ---------------------------
    width = len(rows[0])
    for i, row in enumerate(rows):
        if len(row) != width:
            raise LayoutError(
                f"Map row {i} has length {len(row)}, but row 0 has length {width}. "
                "All rows must be the same length."
            )
    height = len(rows)

    wh = Warehouse(height=height, width=width)

    # --- Read the map: stations, walls, and collect shelf cells -------------
    shelf_cells = {}          # "A" -> [(row, col), ...]
    for r, row in enumerate(rows):
        for c, symbol in enumerate(row):
            if symbol.islower():
                shelf_cells.setdefault(symbol.upper(), []).append((r, c))
            elif symbol == WALL:
                wh.add_wall((r, c))
            elif symbol == FLOOR:
                pass            # Warehouse already starts as floor inside
            elif symbol == CHARGER:
                wh.add_charger((r, c))
            elif symbol == PACKING:
                wh.add_packing_station((r, c))
            elif symbol == DOCK:
                wh.add_dock((r, c))
            else:
                raise LayoutError(f"Unknown symbol '{symbol}' at row {r}, column {c}.")

    # --- Check 2: every shelf on the map is described, and the other way ----
    shelf_info = data.get("shelves", {})
    on_map = set(shelf_cells)
    described = set(shelf_info)
    if on_map - described:
        raise LayoutError(f"Shelves on the map but missing in 'shelves': {sorted(on_map - described)}")
    if described - on_map:
        raise LayoutError(f"Shelves in 'shelves' but not on the map: {sorted(described - on_map)}")

    # --- Create the shelves -------------------------------------------------
    for shelf_id in sorted(shelf_cells):
        info = shelf_info[shelf_id]
        items = info.get("items", {})

        # Check 3: quantities are whole numbers, zero or more
        for item, qty in items.items():
            if not isinstance(qty, int) or qty < 0:
                raise LayoutError(f"Shelf {shelf_id}: quantity of '{item}' must be a whole number >= 0, got {qty!r}.")

        shelf = Shelf(shelf_id, shelf_cells[shelf_id], dict(items), name=info.get("name", ""))
        wh.add_shelf(shelf)

        # Check 4: a robot must be able to reach the shelf from somewhere
        if not wh.access_cells(shelf):
            raise LayoutError(f"Shelf {shelf_id} has no free cell next to it, so no robot can reach it.")

    # Check 5: an item should live on only one shelf (keeps orders simple)
    seen = {}
    for shelf in wh.shelves.values():
        for item in shelf.items:
            if item in seen:
                raise LayoutError(f"Item '{item}' is on shelf {seen[item]} and shelf {shelf.shelf_id}. Use one shelf per item.")
            seen[item] = shelf.shelf_id

    # --- Create the robots --------------------------------------------------
    for entry in data.get("robots", []):
        robot_id = entry["id"]
        position = tuple(entry["position"])
        # Check 6: robots start inside, on a drivable cell, one per cell, unique ids
        try:
            wh.add_robot(Robot(robot_id, position))
        except ValueError as error:
            raise LayoutError(str(error)) from None

    return wh


# Quick look at the layout:  python -m scripts.layout
if __name__ == "__main__":
    wh = load_layout("layouts/main_warehouse.json")
    print(f"Warehouse: {wh.height} rows x {wh.width} columns")
    wh.print_map()
    for shelf in wh.shelves.values():
        print(f"{shelf.shelf_id} ({shelf.name}): {len(shelf.cells)} cells, "
              f"{len(wh.access_cells(shelf))} pick spots, items {shelf.items}")
    print("Chargers:", wh.cells_of_type(CHARGER))
    print("Packing: ", wh.cells_of_type(PACKING))
    print("Docks:   ", wh.cells_of_type(DOCK))
    print("Robots:  ", list(wh.robots))