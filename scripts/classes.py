"""
FleetMind - core classes for the warehouse simulation.

Contains:
    Shelf      - a storage unit covering one or more cells, holding items
    Order      - a job: carry an item from a shelf to a drop-off station
    Robot      - an autonomous robot that moves, picks and places
    Warehouse  - the grid map that knows what is in every cell

Positions are always (row, col) tuples. (0, 0) is the top-left corner.
"""

from enum import Enum


# ---------------------------------------------------------------------------
# Status values
# ---------------------------------------------------------------------------
# An Enum is a fixed list of allowed values. Using RobotStatus.IDLE instead of
# the string "idle" means a typo like "idel" becomes an error you see at once,
# instead of a silent bug.

class RobotStatus(Enum):
    IDLE = "idle"
    MOVING = "moving"
    CARRYING = "carrying"
    CHARGING = "charging"
    ERROR = "error"


class OrderStatus(Enum):
    WAITING = "waiting"        # in the queue, no robot yet
    ASSIGNED = "assigned"      # a robot is on its way to pick it up
    PICKED_UP = "picked_up"    # the robot is carrying the item
    DELIVERED = "delivered"    # done
    FAILED = "failed"          # something went wrong (dropped item, breakdown)


class Priority(Enum):
    LOW = 1
    NORMAL = 2
    HIGH = 3


# Map symbols
FLOOR = "."
WALL = "#"
SHELF = "S"
CHARGER = "C"
PACKING = "P"
DOCK = "D"

# Cells a robot is allowed to drive on
DRIVABLE = {FLOOR, CHARGER, PACKING, DOCK}

# How each direction changes a (row, col) position
DIRECTIONS = {
    "up": (-1, 0),
    "down": (1, 0),
    "left": (0, -1),
    "right": (0, 1),
}


# ---------------------------------------------------------------------------
# Shelf
# ---------------------------------------------------------------------------

class Shelf:
    """
    A storage unit (a rack) that can cover many cells, e.g. a 3x5 block.

    The items belong to the whole unit. A robot can pick from it while
    standing next to ANY of its cells. That way the middle cells of a big
    block don't need to be reachable on their own.
    """

    def __init__(self, shelf_id, cells, items=None, name=""):
        self.shelf_id = shelf_id          # e.g. "A"
        self.name = name                  # e.g. "Fruit"

        # Convenience: a single (row, col) also works for a one-cell shelf.
        if len(cells) == 2 and all(isinstance(v, int) for v in cells):
            cells = [cells]
        self.cells = [tuple(cell) for cell in cells]

        # items maps item name -> quantity, e.g. {"apples": 20}
        # (None as the default, then a new dict, avoids a classic Python bug
        #  where all shelves would share one dictionary.)
        self.items = items if items is not None else {}

    def has_item(self, item):
        return self.items.get(item, 0) > 0

    def take_item(self, item):
        """Remove one unit of an item. Returns True if it worked."""
        if not self.has_item(item):
            return False
        self.items[item] -= 1
        return True

    def __repr__(self):
        return f"Shelf({self.shelf_id}, {len(self.cells)} cells, items={self.items})"


# ---------------------------------------------------------------------------
# Order
# ---------------------------------------------------------------------------

class Order:
    """A job: carry one item from a shelf to a drop-off station."""

    def __init__(self, order_id, item, pickup, dropoff,
                 priority=Priority.NORMAL, created_at=0, deadline=None):
        self.order_id = order_id          # e.g. "O-0001"
        self.item = item                  # e.g. "apples"
        self.pickup = pickup              # shelf id, e.g. "A"
        self.dropoff = dropoff            # (row, col) of the packing station
        self.priority = priority          # Priority.LOW / NORMAL / HIGH
        self.created_at = created_at      # simulation tick when it was created
        self.deadline = deadline          # tick by which it should be delivered
        self.status = OrderStatus.WAITING
        self.delivered_at = None          # filled in when delivered

    def mark_delivered(self, tick):
        self.status = OrderStatus.DELIVERED
        self.delivered_at = tick

    def delivery_time(self):
        """Ticks from creation to delivery, or None if not delivered yet."""
        if self.delivered_at is None:
            return None
        return self.delivered_at - self.created_at

    def is_late(self, current_tick):
        """True if the deadline has passed and it is not delivered on time."""
        if self.deadline is None:
            return False
        if self.delivered_at is not None:
            return self.delivered_at > self.deadline
        return current_tick > self.deadline

    def __repr__(self):
        return (f"Order({self.order_id}, {self.item}, {self.pickup} -> "
                f"{self.dropoff}, {self.priority.name}, {self.status.value})")


# ---------------------------------------------------------------------------
# Robot
# ---------------------------------------------------------------------------

class Robot:
    """An autonomous mobile robot that carries one item at a time."""

    BATTERY_PER_STEP = 0.5   # percent used per move (a design choice: change it)
    CHARGE_PER_TICK = 5.0    # percent added per charge

    def __init__(self, robot_id, position, battery=100.0, speed=1):
        self.robot_id = robot_id          # e.g. "R1"
        self.position = tuple(position)   # (row, col)
        self.battery = battery            # 0-100 percent
        self.status = RobotStatus.IDLE
        self.carrying = None              # item name, or None
        self.order = None                 # the Order it is working on, or None
        self.path = []                    # planned list of (row, col) steps (used from week 4)
        self.speed = speed                # cells per tick (keep 1 for now)
        self.repair_left = 0              # ticks until repaired (0 = not broken)
        self.task = None                  # what it is doing: "to_pickup", "to_dropoff", "to_charger", "charging"
        self.target = None                # (row, col) it is driving to, or None

    # Every action returns (success, reason) so the caller always knows WHY
    # something failed. This makes debugging and logging much easier.

    def move(self, direction, warehouse):
        """Move one cell up/down/left/right if the rules allow it."""
        if self.status == RobotStatus.ERROR:
            return False, "robot is broken"
        if direction not in DIRECTIONS:
            return False, f"unknown direction '{direction}'"
        if self.battery < self.BATTERY_PER_STEP:
            return False, "battery empty"

        d_row, d_col = DIRECTIONS[direction]
        new_position = (self.position[0] + d_row, self.position[1] + d_col)

        ok, reason = warehouse.can_enter(new_position)
        if not ok:
            return False, reason

        warehouse.move_robot(self, new_position)
        self.battery -= self.BATTERY_PER_STEP
        if self.status == RobotStatus.IDLE:
            self.status = RobotStatus.MOVING
        return True, "moved"

    def pick(self, warehouse):
        """Pick the current order's item while standing next to its shelf unit."""
        if self.order is None:
            return False, "no order assigned"
        if self.carrying is not None:
            return False, f"already carrying {self.carrying}"

        shelf = warehouse.get_shelf(self.order.pickup)
        if shelf is None:
            return False, f"shelf {self.order.pickup} does not exist"
        if not warehouse.is_next_to_shelf(self.position, shelf):
            return False, f"not next to shelf {shelf.shelf_id}"
        if not shelf.take_item(self.order.item):
            return False, f"shelf {shelf.shelf_id} has no {self.order.item}"

        self.carrying = self.order.item
        self.status = RobotStatus.CARRYING
        self.order.status = OrderStatus.PICKED_UP
        return True, f"picked {self.carrying}"

    def place(self, warehouse, tick):
        """Deliver the carried item while standing ON the drop-off station."""
        if self.carrying is None:
            return False, "not carrying anything"
        if self.position != tuple(self.order.dropoff):
            return False, "not at the drop-off station"

        delivered_item = self.carrying
        self.order.mark_delivered(tick)
        self.carrying = None
        self.order = None
        self.status = RobotStatus.IDLE
        return True, f"delivered {delivered_item}"

    def charge(self, warehouse):
        """Charge while standing ON a charger."""
        if self.status == RobotStatus.ERROR:
            return False, "robot is broken"
        if warehouse.cell_type(self.position) != CHARGER:
            return False, "not on a charger"
        if self.battery >= 100:
            self.status = RobotStatus.IDLE
            return False, "battery already full"

        self.battery = min(100.0, self.battery + self.CHARGE_PER_TICK)

        if self.battery >= 100:
            self.status = RobotStatus.IDLE
            return True, "fully charged"
        self.status = RobotStatus.CHARGING
        return True, f"charging ({self.battery:.1f}%)"

    def break_down(self, repair_ticks):
        """The robot breaks and needs repair_ticks ticks to be fixed."""
        self.status = RobotStatus.ERROR
        self.repair_left = repair_ticks
        return True, f"broke down, repair in {repair_ticks} ticks"

    def tick_repair(self):
        """Call once per tick. Counts down the repair time."""
        if self.status != RobotStatus.ERROR:
            return False, "not broken"

        self.repair_left -= 1

        if self.repair_left > 0:
            return False, f"still broken ({self.repair_left} ticks left)"

        self.repair_left = 0
        if self.carrying is not None:
            self.status = RobotStatus.CARRYING
        else:
            self.status = RobotStatus.IDLE
        return True, "repaired"

    def __repr__(self):
        return (f"Robot({self.robot_id}, pos={self.position}, "
                f"battery={self.battery:.1f}%, {self.status.value}, "
                f"carrying={self.carrying})")


# ---------------------------------------------------------------------------
# Warehouse
# ---------------------------------------------------------------------------

class Warehouse:
    """The grid map. Knows what is in every cell and where every robot is."""

    def __init__(self, height, width):
        # height/width are the SIZE of the grid (not a position).
        self.height = height
        self.width = width
        # A 2D grid: grid[row][col] holds a symbol. Checking a cell is then
        # instant - no need to search through lists every time a robot moves.
        self.grid = [[FLOOR for _ in range(width)] for _ in range(height)]
        self.shelves = {}            # shelf_id -> Shelf
        self.shelf_at = {}           # (row, col) -> shelf_id, for every shelf cell
        self.robots = {}             # robot_id -> Robot
        self.occupied = {}           # (row, col) -> robot_id, for fast "is this cell taken?"
        self.blocked = set()         # floor cells temporarily blocked (e.g. a fallen box)

        # Surround the warehouse with walls
        for row in range(height):
            for col in range(width):
                if row in (0, height - 1) or col in (0, width - 1):
                    self.grid[row][col] = WALL

    # ----- building the map -----

    def add_wall(self, position):
        row, col = position
        self.grid[row][col] = WALL

    def add_shelf(self, shelf):
        """Place a shelf unit on all of its cells."""
        if shelf.shelf_id in self.shelves:
            raise ValueError(f"Shelf id {shelf.shelf_id} is used twice")
        for cell in shelf.cells:
            if not self.is_inside(cell):
                raise ValueError(f"Shelf {shelf.shelf_id}: cell {cell} is outside the warehouse")
            if self.cell_type(cell) != FLOOR or cell in self.shelf_at:
                raise ValueError(f"Shelf {shelf.shelf_id}: cell {cell} is not free floor")
        for row, col in shelf.cells:
            self.grid[row][col] = SHELF
            self.shelf_at[(row, col)] = shelf.shelf_id
        self.shelves[shelf.shelf_id] = shelf

    def add_charger(self, position):
        row, col = position
        self.grid[row][col] = CHARGER

    def add_packing_station(self, position):
        row, col = position
        self.grid[row][col] = PACKING

    def add_dock(self, position):
        row, col = position
        self.grid[row][col] = DOCK

    def add_robot(self, robot):
        ok, reason = self.can_enter(robot.position)
        if not ok:
            raise ValueError(f"Cannot place {robot.robot_id} at {robot.position}: {reason}")
        if robot.robot_id in self.robots:
            raise ValueError(f"Robot id {robot.robot_id} is used twice")
        self.robots[robot.robot_id] = robot
        self.occupied[robot.position] = robot.robot_id

    # ----- questions about the map -----

    def is_inside(self, position):
        row, col = position
        return 0 <= row < self.height and 0 <= col < self.width

    def cell_type(self, position):
        row, col = position
        return self.grid[row][col]

    def can_enter(self, position):
        """Can a robot move into this cell? Returns (bool, reason)."""
        if not self.is_inside(position):
            return False, "outside the warehouse"
        cell = self.cell_type(position)
        if cell == WALL:
            return False, "wall"
        if cell == SHELF:
            return False, "shelf (robots stand next to shelves)"
        if position in self.blocked:
            return False, "blocked"
        if position in self.occupied:
            return False, f"occupied by {self.occupied[position]}"
        return True, "free"

    def is_adjacent(self, pos_a, pos_b):
        """True if the two cells touch up/down/left/right (not diagonally)."""
        return abs(pos_a[0] - pos_b[0]) + abs(pos_a[1] - pos_b[1]) == 1

    def is_next_to_shelf(self, position, shelf):
        """True if the position touches ANY cell of the shelf unit."""
        return any(self.is_adjacent(position, cell) for cell in shelf.cells)

    def access_cells(self, shelf):
        """
        All drivable cells next to a shelf unit: the places a robot can stand
        to pick from it. Useful as movement targets in week 3.
        """
        result = []
        for row, col in shelf.cells:
            for d_row, d_col in DIRECTIONS.values():
                neighbour = (row + d_row, col + d_col)
                if (self.is_inside(neighbour)
                        and self.cell_type(neighbour) in DRIVABLE
                        and neighbour not in result):
                    result.append(neighbour)
        return sorted(result)

    def cells_of_type(self, symbol):
        """All positions with a given symbol, e.g. all chargers."""
        return [(row, col)
                for row in range(self.height)
                for col in range(self.width)
                if self.grid[row][col] == symbol]

    def get_shelf(self, shelf_id):
        return self.shelves.get(shelf_id)

    def find_shelf_with(self, item):
        """The shelf unit that stocks an item, or None."""
        for shelf in self.shelves.values():
            if item in shelf.items:
                return shelf
        return None

    # ----- changing the map -----

    def block_cell(self, position):
        """Temporarily block a floor cell (e.g. something fell on the floor)."""
        self.blocked.add(position)

    def unblock_cell(self, position):
        self.blocked.discard(position)

    def move_robot(self, robot, new_position):
        """Update the occupancy record. Call only after can_enter() said yes."""
        del self.occupied[robot.position]
        robot.position = new_position
        self.occupied[new_position] = robot.robot_id

    # ----- display -----

    def print_map(self):
        """
        Print the grid as text.
        Shelf cells show their shelf letter in lowercase (a, b, c ...),
        robots show their number, blocked cells show X.
        """
        for row in range(self.height):
            line = []
            for col in range(self.width):
                position = (row, col)
                if position in self.occupied:
                    line.append(self.occupied[position][-1])     # "R1" -> "1"
                elif position in self.blocked:
                    line.append("X")
                elif position in self.shelf_at:
                    line.append(self.shelf_at[position][0].lower())
                else:
                    line.append(self.grid[row][col])
            print(" ".join(line))
        print()


# ---------------------------------------------------------------------------
# Quick demo - runs only when you execute this file directly:
#     python -m scripts.classes
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    wh = Warehouse(height=8, width=12)

    # One shelf unit covering a 2x3 block
    block_a = [(2, 3), (2, 4), (2, 5), (3, 3), (3, 4), (3, 5)]
    wh.add_shelf(Shelf("A", block_a, {"apples": 5, "rice": 3}, name="Food"))
    wh.add_charger((1, 10))
    wh.add_packing_station((5, 10))

    robot = Robot("R1", position=(4, 4))          # below the middle of block A
    wh.add_robot(robot)

    order = Order("O-0001", item="apples", pickup="A", dropoff=(5, 10),
                  priority=Priority.HIGH, created_at=0, deadline=50)
    robot.order = order
    order.status = OrderStatus.ASSIGNED

    print("Start:")
    wh.print_map()
    print("Robot can pick from block A at:", wh.access_cells(wh.get_shelf("A")))

    tick = 0
    print(robot.pick(wh))

    # Walk to the packing station at (5, 10): 1 down, 6 right
    for direction in ["down"] + ["right"] * 6:
        tick += 1
        print(tick, direction, robot.move(direction, wh))

    print(robot.place(wh, tick))
    print()
    print("End:")
    wh.print_map()
    print(robot)
    print(order, "| delivery time:", order.delivery_time(), "ticks | late:", order.is_late(tick))
    print(wh.get_shelf("A"))