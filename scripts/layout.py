"""
Load a warehouse from a layout file (JSON), so the warehouse is defined once
and edited as text instead of code.

Usage:
    from scripts.layout import load_layout
    wh = load_layout("layouts/main_warehouse.json")

Map symbols:
    #  wall          .  floor
    C  charger       P  packing station     D  loading dock
    a-z  shelf row. Every cell with the same letter belongs to that row.
         Cells are numbered left to right (then top to bottom):
         the first "a" is shelf A1, the next is A2, and so on.
         Each cell is its own shelf and holds ONE item.

In the "shelves" section, each row lists its items in the same order as its
cells: the first item goes on A1, the second on A2, ...
"stock_per_shelf" is how many units every shelf starts with.
"""

import json

from scripts.classes import (
    Warehouse, Shelf, Robot,
    WALL, FLOOR, CHARGER, PACKING, DOCK,
)


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
    shelf_cells = {}          # "A" -> [(row, col), ...] in reading order (left to right, top to bottom)
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

    # --- Check 2: every shelf row on the map is described, and the other way
    shelf_info = data.get("shelves", {})
    on_map = set(shelf_cells)
    described = set(shelf_info)
    if on_map - described:
        raise LayoutError(f"Shelf rows on the map but missing in 'shelves': {sorted(on_map - described)}")
    if described - on_map:
        raise LayoutError(f"Shelf rows in 'shelves' but not on the map: {sorted(described - on_map)}")

    # --- Check 3: stock is a whole number, zero or more ---------------------
    stock = data.get("stock_per_shelf", 20)
    if not isinstance(stock, int) or stock < 0:
        raise LayoutError(f"'stock_per_shelf' must be a whole number >= 0, got {stock!r}.")

    # --- Create one shelf per cell: A1, A2, ... -----------------------------
    seen_items = {}
    for row_id in sorted(shelf_cells):
        cells = shelf_cells[row_id]
        info = shelf_info[row_id]
        items = info.get("items", [])

        # Check 4: one item per cell
        if len(items) != len(cells):
            raise LayoutError(
                f"Shelf row {row_id} has {len(cells)} cells on the map but {len(items)} items. "
                "Give exactly one item per cell."
            )

        for number, (cell, item) in enumerate(zip(cells, items), start=1):
            shelf_id = f"{row_id}{number}"

            # Check 5: every item is on exactly one shelf
            if item in seen_items:
                raise LayoutError(
                    f"Item '{item}' is on shelf {seen_items[item]} and shelf {shelf_id}. "
                    "Each item must be on one shelf only."
                )
            seen_items[item] = shelf_id

            shelf = Shelf(shelf_id, [cell], {item: stock}, name=info.get("name", ""))
            wh.add_shelf(shelf)

            # Check 6: a robot must be able to reach every shelf
            if not wh.access_cells(shelf):
                raise LayoutError(f"Shelf {shelf_id} has no free cell next to it, so no robot can reach it.")

    # --- Create the robots --------------------------------------------------
    for entry in data.get("robots", []):
        robot_id = entry["id"]
        position = tuple(entry["position"])
        # Check 7: robots start inside, on a drivable cell, one per cell, unique ids
        try:
            wh.add_robot(Robot(robot_id, position))
        except ValueError as error:
            raise LayoutError(str(error)) from None

    return wh


# Quick look at the layout:  python -m scripts.layout
if __name__ == "__main__":
    wh = load_layout("layouts/main_warehouse.json")
    print(f"Warehouse: {wh.height} rows x {wh.width} columns, {len(wh.shelves)} shelves")
    wh.print_map()
    current_row = None
    for shelf_id in sorted(wh.shelves, key=lambda s: (s[0], int(s[1:]))):
        shelf = wh.shelves[shelf_id]
        if shelf_id[0] != current_row:
            current_row = shelf_id[0]
            print(f"\n{current_row} ({shelf.name}):", end="")
        item, qty = next(iter(shelf.items.items()))
        print(f"  {shelf_id}={item}({qty})", end="")
    print("\n")
    print("Chargers:", wh.cells_of_type(CHARGER))
    print("Packing: ", wh.cells_of_type(PACKING))
    print("Docks:   ", wh.cells_of_type(DOCK))
    print("Robots:  ", list(wh.robots))


def add_robots(warehouse, total):
    """
    Add robots until the warehouse has `total` robots (R1, R2, ...).

    New robots start (and later park) on floor cells that block nobody:
    not next to a shelf (those are pick spots in the aisles) and not next to
    a charger or packing station. Bottom rows first, spread out.
    """
    from scripts.classes import FLOOR, SHELF, CHARGER, PACKING, DIRECTIONS

    def next_to(cell, kinds):
        for d_row, d_col in DIRECTIONS.values():
            n = (cell[0] + d_row, cell[1] + d_col)
            if warehouse.is_inside(n) and warehouse.cell_type(n) in kinds:
                return True
        return False

    floor = [(r, c) for r in range(warehouse.height - 1, -1, -1) for c in range(warehouse.width)
             if warehouse.cell_type((r, c)) == FLOOR and (r, c) not in warehouse.occupied]
    parking = [cell for cell in floor if not next_to(cell, {SHELF, CHARGER, PACKING})]
    others = [cell for cell in floor if cell not in parking]
    spread = parking[::2] + parking[1::2] + others

    number = 1
    while len(warehouse.robots) < total:
        robot_id = f"R{number}"
        number += 1
        if robot_id in warehouse.robots:
            continue
        if not spread:
            raise LayoutError(f"No free floor left for {total} robots.")
        warehouse.add_robot(Robot(robot_id, spread.pop(0)))
    return warehouse