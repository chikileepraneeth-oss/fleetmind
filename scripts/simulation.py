"""
The FleetMind simulation: the world moves forward one tick at a time.

Every tick, in this fixed order:
    1. blocked cells that have expired are cleared
    2. broken robots count down their repair time
    3. maybe a new order arrives (order generator)
    4. random events: breakdowns, dropped items, blocked cells
    5. idle robots with low battery are sent to a charger
    6. waiting orders are given to free robots (first free robot, oldest order)
    7. every robot does one action: move, pick, deliver or charge

Everything random uses ONE random generator made from the seed,
and robots are always handled in the same order (sorted by id).
So the same seed always gives exactly the same run.
"""

import csv
import random
import statistics
from collections import deque
from dataclasses import dataclass

from scripts.classes import (
    RobotStatus, OrderStatus,
    FLOOR, CHARGER, PACKING,
)
from scripts.orders import OrderGenerator
from scripts.movement import greedy_step, nearest, astar, direction_to


@dataclass
class SimConfig:
    """All the numbers that control the simulation, in one place."""
    order_rate: float = 0.2          # chance of a new order each tick
    breakdown_chance: float = 0.001  # per robot, per tick
    repair_ticks: int = 30           # how long a repair takes
    drop_chance: float = 0.002       # per carrying robot, per tick
    block_chance: float = 0.005      # chance a floor cell gets blocked, per tick
    block_ticks: int = 20            # how long a cell stays blocked
    charge_threshold: float = 25.0   # idle robots below this battery go to charge
    movement: str = "astar"          # "astar" (plan a route) or "greedy" (old week-3 way, for comparison)
    sidestep_chance: float = 0.5     # greedy only: when stuck, chance to try a random sidestep
    replan_after: int = 2            # astar: after waiting this many ticks for a robot, plan around it
    sidestep_after: int = 6          # astar: after waiting this long, take a random sidestep (breaks deadlocks)


class Simulation:

    def __init__(self, warehouse, seed, config=None):
        self.warehouse = warehouse
        self.seed = seed
        self.config = config or SimConfig()
        self.rng = random.Random(seed)                 # the ONE random generator

        self.generator = OrderGenerator(warehouse, self.rng, self.config.order_rate)
        self.queue = deque()                           # waiting orders, oldest first
        self.orders = []                               # every order ever created
        self.tick = 0
        self.events = []                               # the log: (tick, robot, event, details)

        self.chargers = warehouse.cells_of_type(CHARGER)
        self.blocked_until = {}                        # (row, col) -> tick when it is cleared
        self.battery_empty_logged = set()
        self.waiting = {}                              # robot_id -> ticks waited in a row
        # where each robot parks when it has nothing to do (its start position)
        self.home = {rid: r.position for rid, r in warehouse.robots.items()}

        # counters for KPIs
        self.breakdowns = 0
        self.drops = 0
        self.blocks = 0
        self.stuck_steps = 0

    # ------------------------------------------------------------------
    # Running
    # ------------------------------------------------------------------

    def run(self, ticks):
        for _ in range(ticks):
            self.step()

    def step(self):
        """Advance the world by one tick."""
        self.tick += 1
        self._clear_expired_blocks()
        self._repair_robots()
        self._new_orders()
        self._random_events()
        self._send_low_battery_to_charge()
        self._assign_orders()
        self._robots_act()

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def log(self, event, robot_id="", details=""):
        self.events.append((self.tick, robot_id, event, details))

    def write_log(self, path):
        """Save every event to a CSV file (opens in Excel or pandas)."""
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["tick", "robot", "event", "details"])
            writer.writerows(self.events)

    # ------------------------------------------------------------------
    # 1. Blocked cells
    # ------------------------------------------------------------------

    def _clear_expired_blocks(self):
        for cell in sorted(self.blocked_until):
            if self.tick >= self.blocked_until[cell]:
                self.warehouse.unblock_cell(cell)
                del self.blocked_until[cell]
                self.log("cell_cleared", details=str(cell))

    # ------------------------------------------------------------------
    # 2. Repairs
    # ------------------------------------------------------------------

    def _repair_robots(self):
        for robot in self._robots():
            if robot.status == RobotStatus.ERROR:
                repaired, _ = robot.tick_repair()
                if repaired:
                    self.log("repaired", robot.robot_id)

    # ------------------------------------------------------------------
    # 3. New orders
    # ------------------------------------------------------------------

    def _new_orders(self):
        order = self.generator.maybe_create(self.tick)
        if order is None:
            return
        self.orders.append(order)
        self.queue.append(order)
        self.log("order_created", details=(
            f"{order.order_id} {order.item} {order.pickup}->{order.dropoff} {order.priority.name}"
        ))

    # ------------------------------------------------------------------
    # 4. Random events
    # ------------------------------------------------------------------

    def _random_events(self):
        cfg = self.config
        for robot in self._robots():
            if robot.status == RobotStatus.ERROR:
                continue
            if self.rng.random() < cfg.breakdown_chance:
                robot.break_down(cfg.repair_ticks)
                self.breakdowns += 1
                self.log("breakdown", robot.robot_id, f"repair in {cfg.repair_ticks} ticks")
                continue
            if robot.carrying is not None and self.rng.random() < cfg.drop_chance:
                self._drop_item(robot)

        if self.rng.random() < cfg.block_chance:
            self._block_random_cell()

    def _drop_item(self, robot):
        """The robot drops its item. The item is lost; the order goes back to the front of the queue."""
        order = robot.order
        self.log("dropped_item", robot.robot_id, f"{order.order_id} {robot.carrying}")
        robot.carrying = None
        robot.order = None
        robot.task = None
        robot.target = None
        robot.path = []
        robot.status = RobotStatus.IDLE
        order.status = OrderStatus.WAITING
        self.queue.appendleft(order)        # it was waiting longest, so it goes first
        self.drops += 1

    def _block_random_cell(self):
        wh = self.warehouse
        free_floor = [cell for cell in wh.cells_of_type(FLOOR)
                      if cell not in wh.occupied and cell not in wh.blocked]
        if not free_floor:
            return
        cell = self.rng.choice(free_floor)
        wh.block_cell(cell)
        self.blocked_until[cell] = self.tick + self.config.block_ticks
        self.blocks += 1
        self.log("cell_blocked", details=f"{cell} for {self.config.block_ticks} ticks")

    # ------------------------------------------------------------------
    # 5. Charging decisions
    # ------------------------------------------------------------------

    def _send_low_battery_to_charge(self):
        for robot in self._robots():
            if not self._is_free(robot) or robot.battery >= self.config.charge_threshold:
                continue
            charger = self._free_charger_for(robot)
            if charger is None:
                continue                    # all chargers busy: wait
            robot.task = "to_charger"
            robot.target = charger
            robot.path = []
            self.log("going_to_charge", robot.robot_id, f"battery {robot.battery:.1f}% -> {charger}")

    def _free_charger_for(self, robot):
        taken = {r.target for r in self._robots()
                 if r is not robot and r.task in ("to_charger", "charging")}
        free = [c for c in self.chargers
                if c not in taken and self.warehouse.occupied.get(c, robot.robot_id) == robot.robot_id]
        return nearest(robot.position, free)

    # ------------------------------------------------------------------
    # 6. Assigning orders (the baseline rule)
    # ------------------------------------------------------------------

    def _assign_orders(self):
        """Baseline: the first free robot (by id) takes the oldest waiting order."""
        free_robots = [r for r in self._robots()
                       if self._is_free(r) and r.battery >= self.config.charge_threshold]
        while self.queue and free_robots:
            order = self.queue.popleft()
            robot = free_robots.pop(0)
            robot.order = order
            robot.task = "to_pickup"
            robot.target = None
            robot.path = []
            order.status = OrderStatus.ASSIGNED
            self.log("order_assigned", robot.robot_id, order.order_id)

    # ------------------------------------------------------------------
    # 7. Robots act
    # ------------------------------------------------------------------

    def _robots_act(self):
        for robot in self._robots():
            if robot.status == RobotStatus.ERROR:
                continue
            if robot.task == "to_pickup":
                self._act_pickup(robot)
            elif robot.task == "to_dropoff":
                self._act_dropoff(robot)
            elif robot.task == "to_charger":
                self._act_to_charger(robot)
            elif robot.task == "charging":
                self._act_charging(robot)
            elif robot.task == "to_home":
                self._act_to_home(robot)
            else:
                self._act_idle(robot)

    def _act_pickup(self, robot):
        order = robot.order
        shelf = self.warehouse.get_shelf(order.pickup)

        if self.warehouse.is_next_to_shelf(robot.position, shelf):
            picked, reason = robot.pick(self.warehouse)
            if picked:
                robot.task = "to_dropoff"
                robot.target = tuple(order.dropoff)
                robot.path = []
                self.log("picked", robot.robot_id, f"{order.order_id} {order.item} from {shelf.shelf_id}")
            else:
                # e.g. the shelf ran out: this order can't be done
                order.status = OrderStatus.FAILED
                robot.order = None
                robot.task = None
                robot.target = None
                self.log("order_failed", robot.robot_id, f"{order.order_id}: {reason}")
            return

        # choose where to stand once; only change it if that spot became unusable
        if robot.target is None or robot.target not in self.warehouse.access_cells(shelf) \
                or robot.target in self.warehouse.blocked:
            robot.target = self._pick_spot(robot, shelf)
            robot.path = []
        self._drive(robot)

    def _act_dropoff(self, robot):
        order = robot.order
        if robot.position == tuple(order.dropoff):
            delivered, _ = robot.place(self.warehouse, self.tick)
            if delivered:
                robot.task = None
                robot.target = None
                robot.path = []
                late = " LATE" if order.is_late(self.tick) else ""
                self.log("delivered", robot.robot_id,
                         f"{order.order_id} in {order.delivery_time()} ticks{late}")
            return
        self._drive(robot)

    def _act_to_charger(self, robot):
        # standing on ANY charger already? plug in right here
        if robot.position == robot.target or self.warehouse.cell_type(robot.position) == CHARGER:
            robot.target = robot.position
            robot.task = "charging"
            self._act_charging(robot)
            return
        self._drive(robot)

    def _act_charging(self, robot):
        charged, reason = robot.charge(self.warehouse)
        if reason in ("fully charged", "battery already full"):
            robot.task = None
            robot.target = None
            self.log("charged", robot.robot_id, "battery 100%")
        elif not charged:
            # not on a charger any more (should not happen) - give up charging
            robot.task = None
            robot.target = None

    def _act_idle(self, robot):
        """
        An idle robot drives back to its home spot, so it doesn't block aisles,
        packing stations or chargers while it waits for the next order.
        """
        home = self.home.get(robot.robot_id)
        if self.config.movement == "astar" and home is not None:
            if robot.position != home and home not in self.warehouse.blocked:
                robot.task = "to_home"
                robot.target = home
                robot.path = []
                self._act_to_home(robot)
            return

        # greedy (week 3): only step off stations
        cell = self.warehouse.cell_type(robot.position)
        if cell not in (PACKING, CHARGER):
            return
        for direction in ("left", "up", "down", "right"):
            moved, _ = robot.move(direction, self.warehouse)
            if moved and self.warehouse.cell_type(robot.position) == FLOOR:
                return

    def _act_to_home(self, robot):
        if robot.position == robot.target:
            robot.task = None
            robot.target = None
            robot.path = []
            return
        self._drive(robot)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _robots(self):
        """All robots, always in the same order (sorted by id)."""
        return [self.warehouse.robots[rid] for rid in sorted(self.warehouse.robots)]

    def _is_free(self, robot):
        """Free = no order and nothing important to do (driving home doesn't count)."""
        return (robot.order is None and robot.task in (None, "to_home")
                and robot.status != RobotStatus.ERROR)

    def _pick_spot(self, robot, shelf):
        """The nearest free cell next to the shelf. If all are taken, the nearest one anyway."""
        wh = self.warehouse
        spots = wh.access_cells(shelf)
        free = [s for s in spots
                if s not in wh.blocked and wh.occupied.get(s, robot.robot_id) == robot.robot_id]
        return nearest(robot.position, free or spots)

    def _drive(self, robot):
        """Move the robot one step towards robot.target."""
        if self.config.movement == "greedy":
            self._drive_greedy(robot)
        else:
            self._drive_astar(robot)

    # ----- A*: plan the whole route, then follow it step by step -----

    def _drive_astar(self, robot):
        wh = self.warehouse
        if robot.position == robot.target:
            robot.path = []
            return

        # (re)plan if there is no route yet, or it leads somewhere else
        if not robot.path or robot.path[-1] != robot.target:
            self._plan(robot)
            if not robot.path:
                self._count_stuck(robot, "no route")
                return

        next_cell = robot.path[0]
        moved, reason = robot.move(direction_to(robot.position, next_cell), wh)
        if moved:
            robot.path.pop(0)
            self.waiting[robot.robot_id] = 0
            return

        self._count_stuck(robot, reason)
        if reason == "battery empty" or robot.status == RobotStatus.ERROR:
            return

        waited = self.waiting.get(robot.robot_id, 0)
        if reason.startswith("occupied"):
            # Another robot is in the way. It may move on its own, so wait a little,
            # then plan a route around it, and if still stuck, sidestep randomly.
            if waited >= self.config.sidestep_after:
                self._random_sidestep(robot)
                self.waiting[robot.robot_id] = 0
                robot.path = []
            elif waited >= self.config.replan_after:
                if robot.task == "to_pickup" and next_cell == robot.target:
                    # our pick spot is taken: try another spot at the same shelf
                    shelf = wh.get_shelf(robot.order.pickup)
                    robot.target = self._pick_spot(robot, shelf)
                self._plan(robot, avoid_robots=True)
        else:
            # a blocked cell appeared on the route: plan a new one right away
            self._plan(robot)

    def _plan(self, robot, avoid_robots=False):
        avoid = None
        if avoid_robots:
            avoid = {pos for pos, rid in self.warehouse.occupied.items() if rid != robot.robot_id}
        path = astar(self.warehouse, robot.position, robot.target, avoid)
        if path is None and avoid_robots:
            path = astar(self.warehouse, robot.position, robot.target)   # no way around: keep the normal route
        robot.path = path or []
        if path:
            self.log("route_planned", robot.robot_id, f"{robot.position} -> {robot.target}, {len(path)} steps")

    def _count_stuck(self, robot, reason):
        self.stuck_steps += 1
        self.waiting[robot.robot_id] = self.waiting.get(robot.robot_id, 0) + 1
        if reason == "battery empty" and robot.robot_id not in self.battery_empty_logged:
            self.battery_empty_logged.add(robot.robot_id)
            self.log("battery_empty", robot.robot_id, f"stuck at {robot.position}")

    def _random_sidestep(self, robot):
        directions = ["up", "down", "left", "right"]
        self.rng.shuffle(directions)
        for direction in directions:
            stepped, _ = robot.move(direction, self.warehouse)
            if stepped:
                return True
        return False

    # ----- Greedy (week 3), kept so we can compare -----

    def _drive_greedy(self, robot):
        moved, reason = greedy_step(robot, robot.target, self.warehouse)
        if moved:
            return
        self._count_stuck(robot, reason)
        if reason == "battery empty":
            return
        # Blocked: take a random sidestep to break the jam.
        if self.rng.random() < self.config.sidestep_chance:
            self._random_sidestep(robot)

    # ------------------------------------------------------------------
    # KPIs
    # ------------------------------------------------------------------

    def kpis(self):
        delivered = [o for o in self.orders if o.status == OrderStatus.DELIVERED]
        times = [o.delivery_time() for o in delivered]
        on_time = [o for o in delivered if not o.is_late(self.tick)]
        return {
            "ticks": self.tick,
            "orders_created": len(self.orders),
            "orders_delivered": len(delivered),
            "orders_waiting": len(self.queue),
            "orders_in_progress": sum(1 for o in self.orders
                                      if o.status in (OrderStatus.ASSIGNED, OrderStatus.PICKED_UP)),
            "orders_failed": sum(1 for o in self.orders if o.status == OrderStatus.FAILED),
            "avg_delivery_time": round(statistics.mean(times), 1) if times else None,
            "on_time_rate": round(len(on_time) / len(delivered), 3) if delivered else None,
            "breakdowns": self.breakdowns,
            "dropped_items": self.drops,
            "blocked_cells": self.blocks,
            "stuck_steps": self.stuck_steps,
        }