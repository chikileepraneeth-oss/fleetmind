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
    Robot, RobotStatus, OrderStatus,
    FLOOR, CHARGER, PACKING, DIRECTIONS,
)
from scripts.orders import OrderGenerator
from scripts.demand import DemandModel
from scripts.movement import greedy_step, nearest, astar, direction_to, distance_table
from scripts import assignment
from scripts.traffic import ReservationTable, cooperative_astar, find_wait_cycles


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

    # assignment and charging
    assignment: str = "smart"        # "first_free" (simple) or "smart"
    charging: str = "opportunity"    # "simple", "smart" or "opportunity"
    critical_battery: float = 15.0   # smart: always charge below this
    top_up_below: float = 70.0       # smart: top up below this when nothing is waiting
    min_available_share: float = 0.5 # smart: keep at least this share of robots ready for orders
    leave_charger_at: float = 60.0   # smart: stop charging early above this if orders pile up
    battery_margin: float = 10.0     # smart: extra battery kept for safety on every trip
    safety_buffer: float = 8.0       # smart: go charge when the battery left would be less than this
    movement: str = "cooperative"    # "cooperative" (A* with reservations), "astar" (plain A*), "greedy"
    sidestep_chance: float = 0.5     # greedy only: when stuck, chance to try a random sidestep
    replan_after: int = 2            # astar: after waiting this many ticks for a robot, plan around it
    sidestep_after: int = 6          # astar: after waiting this long, take a random sidestep (breaks deadlocks)
    # traffic
    window: int = 16                 # cooperative: how many ticks ahead robots plan and book cells
    deadlock_after: int = 5          # cooperative: robots waiting on each other this long = deadlock
    station_admit: int = 2           # waiting zones: robots allowed to approach one packing station at once
    rescue_after: int = 30           # an empty robot gets a battery swap from staff after this many ticks
    rescue_battery: float = 50.0     # battery after the swap
    # opportunity charging
    top_up_full_below: float = 90.0  # opportunity: idle robots below this go top up when no orders wait
    # demand
    demand: str = "flat"             # "flat" (order_rate every tick) or "realistic" (scripts/demand.py, 1 tick = 1 minute from 06:00)
    demand_seed: int = 7             # realistic: seed of the demand model (the same seed as the order history)
    demand_day: int = 0              # realistic: which day of the demand model to simulate (day 0 = 1 June 2026)
    demand_source: str = "synthetic" # realistic: "synthetic" (made-up days) or "m5" (real Walmart days, day 0 = 2011-01-29)


class Simulation:

    def __init__(self, warehouse, seed, config=None):
        self.warehouse = warehouse
        self.seed = seed
        self.config = config or SimConfig()
        self.rng = random.Random(seed)                 # the ONE random generator

        self.demand = None
        if self.config.demand == "realistic":
            self.demand = DemandModel(seed=self.config.demand_seed, source=self.config.demand_source)
        self.generator = OrderGenerator(warehouse, self.rng, self.config.order_rate,
                                        demand=self.demand, day=self.config.demand_day)
        self.queue = deque()                           # waiting orders, oldest first
        self.orders = []                               # every order ever created
        self.tick = 0
        self.events = []                               # the log: (tick, robot, event, details)

        self.chargers = warehouse.cells_of_type(CHARGER)
        self.dist = distance_table(warehouse)          # walking distance between any two cells
        self.deliveries_by = {}                        # robot_id -> orders delivered (its "load")
        self.resume = {}                               # robot_id -> (task, target) to continue after charging

        # traffic
        self.table = ReservationTable()                # who booked which cell at which tick
        self.intents = {}                              # robot_id -> the cell it wants to be in this tick
        self.no_progress = {}                          # robot_id -> ticks in a row it didn't get closer
        self.collisions = 0
        self.deadlocks = 0
        self.max_wait = 0
        self._last_position = {}
        self._empty_since = {}
        self.rescues = 0

        # waiting zones: a queue per packing station, and open cells to wait in
        self.packing = warehouse.cells_of_type(PACKING)
        self.station_queue = {station: [] for station in self.packing}
        self.admitted = {station: set() for station in self.packing}
        self.holding_cells = self._find_holding_cells()
        self._plan_goal = {}
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
        assignment.battery_safety(self)
        self._send_low_battery_to_charge()
        self._assign_orders()
        if self.config.movement == "cooperative":
            self._prepare_traffic()
        self._robots_act()
        if self.config.movement == "cooperative":
            self._resolve_moves()
            self._handle_deadlocks()
        self._check_traffic()

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
        # robots with an empty battery: staff come and swap the battery after a while
        for robot in self._robots():
            rid = robot.robot_id
            if robot.battery < Robot.BATTERY_PER_STEP and self.warehouse.cell_type(robot.position) != CHARGER:
                self._empty_since.setdefault(rid, self.tick)
                if self.tick - self._empty_since[rid] >= self.config.rescue_after:
                    robot.battery = self.config.rescue_battery
                    del self._empty_since[rid]
                    self.rescues += 1
                    self.log("battery_rescue", rid, f"staff swapped the battery at {robot.position}")
            else:
                self._empty_since.pop(rid, None)
        for robot in self._robots():
            if robot.status == RobotStatus.ERROR:
                repaired, _ = robot.tick_repair()
                if repaired:
                    self.log("repaired", robot.robot_id)

    # ------------------------------------------------------------------
    # 3. New orders
    # ------------------------------------------------------------------

    def _new_orders(self):
        for order in self.generator.orders_for(self.tick):
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
                robot.path = []
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
        self.resume.pop(robot.robot_id, None)
        self._leave_station_queues(robot.robot_id)
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
        if self.config.charging == "smart":
            assignment.charge_smart(self)
        elif self.config.charging == "opportunity":
            assignment.charge_opportunity(self)
        else:
            assignment.charge_simple(self)

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
        if self.config.assignment == "smart":
            assignment.assign_smart(self)
        else:
            assignment.assign_first_free(self)

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
            elif robot.task == "wait_charger":
                self._act_wait_charger(robot)
            else:
                self._act_idle(robot)

    def _act_pickup(self, robot):
        order = robot.order
        shelf = self.warehouse.get_shelf(order.pickup)

        if self.warehouse.is_next_to_shelf(robot.position, shelf):
            picked, reason = robot.pick(self.warehouse)
            if picked:
                if self.config.assignment == "smart":
                    order.dropoff = self._least_busy_station(robot)
                robot.task = "to_dropoff"
                robot.target = tuple(order.dropoff) if self.config.movement == "greedy" else None
                robot.path = []
                self.station_queue.setdefault(tuple(order.dropoff), []).append(robot.robot_id)
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
        station = tuple(order.dropoff)
        if robot.position != station and self.config.movement != "greedy" \
                and not self._admitted(robot, station):
            self._go_wait(robot, station)
            return
        if robot.target != station:
            robot.target = station
            robot.path = []
        if robot.position == station:
            delivered, _ = robot.place(self.warehouse, self.tick)
            if delivered:
                self._leave_station_queues(robot.robot_id)
                robot.task = None
                robot.target = None
                robot.path = []
                self.deliveries_by[robot.robot_id] = self.deliveries_by.get(robot.robot_id, 0) + 1
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
        if assignment.should_leave_charger_early(self, robot):
            self._finish_charging(robot)
            self.log("left_charger_early", robot.robot_id, f"battery {robot.battery:.1f}%, orders waiting")
            return
        charged, reason = robot.charge(self.warehouse)
        if reason in ("fully charged", "battery already full"):
            self._finish_charging(robot)
            self.log("charged", robot.robot_id, "battery 100%")
        elif not charged:
            # not on a charger any more (should not happen) - give up charging
            robot.task = None
            robot.target = None

    def _act_wait_charger(self, robot):
        """
        Waiting until a charger is free. If the battery allows, wait in the robot's
        parking spot (so it doesn't block an aisle); otherwise stand still right here.
        """
        robot.task = None
        if assignment.send_to_charger(self, robot):
            self._act_to_charger(robot)
            return
        robot.task = "wait_charger"
        home = self.home.get(robot.robot_id)
        if self.config.movement == "greedy" or home is None or robot.position == home:
            robot.target = None
            return
        here = self.dist.get(robot.position, {})
        to_home = here.get(home)
        home_to_charger = min((self.dist.get(home, {}).get(c, 10**6) for c in self.chargers), default=10**6)
        if to_home is not None and robot.battery >= (to_home + home_to_charger) * Robot.BATTERY_PER_STEP + 2:
            if robot.target != home:
                robot.target = home
                robot.path = []
            self._drive(robot)
        else:
            robot.target = None

    # ----- Waiting zones at the packing stations -----

    def _find_holding_cells(self):
        """
        Open floor cells where a robot can wait without blocking anyone:
        no shelf in the 3x3 square around it (so it's not in an aisle or at an
        aisle crossing), and at least 3 cells from any charger or packing station.
        """
        from scripts.classes import SHELF
        wh = self.warehouse
        stations = wh.cells_of_type(CHARGER) + wh.cells_of_type(PACKING)
        cells = []
        for r in range(wh.height):
            for c in range(wh.width):
                if wh.cell_type((r, c)) != FLOOR:
                    continue
                around = [(r + dr, c + dc) for dr in (-1, 0, 1) for dc in (-1, 0, 1)]
                if any(wh.is_inside(p) and wh.cell_type(p) == SHELF for p in around):
                    continue
                if any(max(abs(r - sr), abs(c - sc)) <= 2 for sr, sc in stations):
                    continue
                cells.append((r, c))
        return cells

    def _admitted(self, robot, station):
        """Is this robot among the first few allowed to drive up to the station?"""
        queue = self.station_queue.setdefault(station, [])
        if robot.robot_id not in queue:
            queue.append(robot.robot_id)
        admitted = self.admitted.setdefault(station, set())
        # robots that are no longer delivering here give up their turn
        for rid in list(admitted):
            if rid not in queue or self.warehouse.robots[rid].task != "to_dropoff":
                admitted.discard(rid)
        if robot.robot_id in admitted:
            return True                          # once it's your turn, you keep it
        if len(admitted) >= self.config.station_admit:
            return False
        waiting = [rid for rid in queue if rid not in admitted
                   and self.warehouse.robots[rid].task == "to_dropoff"
                   and self.warehouse.robots[rid].status != RobotStatus.ERROR]
        # the robots closest to the station get the free turns, so one far-away robot can't hold everyone up
        to_station = self.dist.get(station, {})
        waiting.sort(key=lambda rid: (to_station.get(self.warehouse.robots[rid].position, 10**6), queue.index(rid)))
        for rid in waiting[:self.config.station_admit - len(admitted)]:
            admitted.add(rid)
        return robot.robot_id in admitted

    def _go_wait(self, robot, station):
        """Not our turn yet: wait in the nearest free holding cell (or right here if we're in one)."""
        wh = self.warehouse
        if robot.position in self.holding_cells and robot.target in (None, robot.position, station):
            robot.target = robot.position
            robot.path = []
            return
        taken = {r.target for r in self._robots() if r is not robot} | set(wh.occupied)
        here = self.dist.get(robot.position, {})
        options = [c for c in self.holding_cells
                   if c not in taken and c not in wh.blocked and c in here]
        if not options:
            return                                   # nowhere to wait: stay put
        to_station = self.dist.get(station, {})
        spot = min(options, key=lambda c: (to_station.get(c, 10**6) + here[c], c))
        if robot.target != spot:
            robot.target = spot
            robot.path = []
        self._drive(robot)

    def _least_busy_station(self, robot):
        here = self.dist.get(robot.position, {})
        def busy(station):
            waiting = sum(1 for rid in self.station_queue.get(station, [])
                          if self.warehouse.robots[rid].task == "to_dropoff")
            return (waiting, here.get(station, 10**6), station)
        return min(self.packing, key=busy)

    def _leave_station_queues(self, robot_id):
        for queue in self.station_queue.values():
            if robot_id in queue:
                queue.remove(robot_id)
        for admitted in self.admitted.values():
            admitted.discard(robot_id)

    def _finish_charging(self, robot):
        """Done charging: continue a paused delivery, or become free."""
        robot.path = []
        if robot.robot_id in self.resume and robot.order is not None:
            robot.task, robot.target = self.resume.pop(robot.robot_id)
            if robot.task == "to_dropoff" and self.config.movement != "greedy":
                robot.target = None              # wait for our turn at the station again
            robot.status = RobotStatus.CARRYING
            self.log("resumed_delivery", robot.robot_id, robot.order.order_id)
        else:
            self.resume.pop(robot.robot_id, None)
            robot.task = None
            robot.target = None
            robot.status = RobotStatus.IDLE

    def _act_idle(self, robot):
        """
        An idle robot drives back to its home spot, so it doesn't block aisles,
        packing stations or chargers while it waits for the next order.
        """
        home = self.home.get(robot.robot_id)
        if robot.battery < self.config.charge_threshold:
            return      # low battery and no charger free: wait here, don't waste battery driving home
        if self.config.movement != "greedy" and home is not None:
            if robot.position != home and home not in self.warehouse.blocked:
                robot.task = "to_home"
                robot.target = home
                robot.path = []
                self._act_to_home(robot)
            return

        # greedy: only step off stations
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
        elif self.config.movement == "astar":
            self._drive_astar(robot)
        else:
            self._drive_cooperative(robot)

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

    # ----- Cooperative: plan with bookings, move together -----

    def _prepare_traffic(self):
        """Forget old bookings. Robots without a plan book the cell they stand on."""
        now = self.tick - 1                      # robots are where they were at the end of last tick
        self.table.prune(now)
        self.intents = {}
        for robot in self._robots():
            if robot.path:
                continue
            self.table.release(robot.robot_id)
            # parked/working robots hold their cell for the whole window; robots about
            # to drive somewhere only for a moment (they will plan in a second)
            hold = 2 if robot.target and robot.position != robot.target else self.config.window
            # a robot that CAN'T move (broken, or battery empty) holds its cell until it
            # can move again, so the others plan a way around it instead of waiting behind it
            hold = max(hold, self._ticks_stuck(robot))
            self.table.reserve_stay(robot.robot_id, robot.position, now, now + hold)

    def _ticks_stuck(self, robot):
        """For how many more ticks this robot certainly can't move (0 = it can move)."""
        if robot.status == RobotStatus.ERROR:
            return robot.repair_left + 1
        if robot.battery < Robot.BATTERY_PER_STEP and self.warehouse.cell_type(robot.position) != CHARGER:
            since = self._empty_since.get(robot.robot_id, self.tick)
            return max(1, self.config.rescue_after - (self.tick - since)) + 1
        return 0

    def _drive_cooperative(self, robot):
        if robot.position == robot.target:
            robot.path = []
            return
        if not robot.path or self._plan_goal.get(robot.robot_id) != robot.target:
            self._plan_cooperative(robot)
            if not robot.path:
                self._count_stuck(robot, "no route")
                return
        elif robot.path[0] == robot.position:
            # The plan says "wait here". The robot it waits for may have left early,
            # so plan again: A* finds the earliest arrival, so this is never slower.
            old = robot.path
            self._plan_cooperative(robot, log=False)
            if not robot.path:
                self._count_stuck(robot, "no route")
                return
            if robot.path != old:
                self.log("route_planned", robot.robot_id,
                         f"{robot.position} -> {robot.target}, {len(robot.path)} ticks (the way got free)")
        self.intents[robot.robot_id] = robot.path[0]

    def _plan_cooperative(self, robot, log=True):
        rid = robot.robot_id
        now = self.tick - 1
        self.table.release(rid)
        path = cooperative_astar(self.warehouse, robot.position, robot.target, now,
                                 self.table, rid, self.dist, window=self.config.window)
        if not path:
            robot.path = []
            self.table.reserve_stay(rid, robot.position, now, now + 2)
            return
        robot.path = path
        self._plan_goal[rid] = robot.target
        self.table.reserve_path(rid, robot.position, now, path)
        if log:
            self.log("route_planned", rid, f"{robot.position} -> {robot.target}, {len(path)} ticks")

    def _resolve_moves(self):
        """
        Move every robot that wants to move. A robot can only enter a cell after the
        robot standing there has left, so we try in rounds until nobody else can move.
        """
        pending = {}
        for rid, cell in self.intents.items():
            robot = self.warehouse.robots[rid]
            if cell == robot.position:
                robot.path.pop(0)                # a planned wait
            else:
                pending[rid] = cell

        changed = True
        while pending and changed:
            changed = False
            for rid in sorted(pending):
                robot = self.warehouse.robots[rid]
                ok, _ = self.warehouse.can_enter(pending[rid])
                if not ok:
                    continue
                moved, reason = robot.move(direction_to(robot.position, pending[rid]), self.warehouse)
                if moved:
                    robot.path.pop(0)
                    del pending[rid]
                    changed = True

        for rid in sorted(pending):              # could not move this tick: plan again next tick
            robot = self.warehouse.robots[rid]
            _, reason = robot.move(direction_to(robot.position, pending[rid]), self.warehouse)
            self._count_stuck(robot, reason)
            robot.path = []
            self.table.release(rid)

    def _handle_deadlocks(self):
        """Find robots waiting on each other in a circle, and make one of them back off."""
        wh = self.warehouse
        waits_for = {}
        for robot in self._robots():
            rid = robot.robot_id
            if self.no_progress.get(rid, 0) < self.config.deadlock_after:
                continue
            blocker = self._blocker(robot)
            if blocker is not None:
                waits_for[rid] = blocker

        for cycle in find_wait_cycles(waits_for):
            robots = sorted((wh.robots[r] for r in cycle), key=self._yield_order, reverse=True)
            victim = robots[-1]
            # Everyone in the circle plans again, most important first. The robot that
            # gives way plans LAST, so its new plan must respect the others' bookings:
            # it backs out of their way instead of meeting them again.
            for robot in robots:
                robot.path = []
                self.table.release(robot.robot_id)
            for robot in robots:
                if robot.target:
                    self._plan_cooperative(robot)
            self.no_progress[victim.robot_id] = 0
            self.deadlocks += 1
            self.log("deadlock_resolved", victim.robot_id, f"circle {' -> '.join(cycle)}, {victim.robot_id} gives way")

        # a robot stuck behind one that is only waiting (idle, going home, or waiting
        # for a charger): the waiting one moves aside
        for rid, blocker in sorted(waits_for.items()):
            other = wh.robots[blocker]
            just_waiting = other.task in (None, "to_home", "wait_charger") and other.order is None \
                or other.task == "wait_charger"
            if just_waiting and not other.path and other.status != RobotStatus.ERROR:
                if self._back_off(other, [rid, blocker]):
                    self.log("made_way", blocker, f"moved aside for {rid}")

    def _blocker(self, robot):
        """The robot standing on the next cell of this robot's shortest way to its target."""
        if not robot.target or robot.position == robot.target or robot.status == RobotStatus.ERROR:
            return None
        to_goal = self.dist.get(robot.target, {})
        here = to_goal.get(robot.position)
        if here is None:
            return None
        for d_row, d_col in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nxt = (robot.position[0] + d_row, robot.position[1] + d_col)
            if to_goal.get(nxt) == here - 1 and nxt in self.warehouse.occupied:
                return self.warehouse.occupied[nxt]
        return None

    def _yield_order(self, robot):
        """Who gives way first: robots without an order, then low priority, then newest id."""
        priority = robot.order.priority.value if robot.order else 0
        carrying = 1 if robot.carrying else 0
        return (priority, carrying, [-ord(ch) for ch in robot.robot_id])

    def _back_off(self, robot, cycle):
        """Step into a free neighbour cell that nobody in the circle needs, then plan again."""
        if robot.status == RobotStatus.ERROR or robot.battery < Robot.BATTERY_PER_STEP:
            return False
        others_next = set()
        for rid in cycle:
            other = self.warehouse.robots[rid]
            if other is not robot and other.path:
                others_next.add(other.path[0])
        options = []
        for name in ("up", "down", "left", "right"):
            d_row, d_col = DIRECTIONS[name]
            cell = (robot.position[0] + d_row, robot.position[1] + d_col)
            ok, _ = self.warehouse.can_enter(cell)
            if ok and self.warehouse.cell_type(cell) == FLOOR:
                options.append((cell in others_next, name))
        if not options:
            return False
        options.sort()
        self.table.release(robot.robot_id)
        moved, _ = robot.move(options[0][1], self.warehouse)
        for rid in cycle:
            self.warehouse.robots[rid].path = []
            self.table.release(rid)
        return moved

    # ----- Traffic checks for every movement mode -----

    def _check_traffic(self):
        positions = [r.position for r in self.warehouse.robots.values()]
        if len(positions) != len(set(positions)):
            self.collisions += 1
        for robot in self._robots():
            rid = robot.robot_id
            last = self._last_position.get(rid)
            driving = robot.target is not None and robot.position != robot.target \
                and robot.status != RobotStatus.ERROR and robot.battery >= Robot.BATTERY_PER_STEP
            if driving and last == robot.position:
                self.no_progress[rid] = self.no_progress.get(rid, 0) + 1
                self.max_wait = max(self.max_wait, self.no_progress[rid])
            else:
                self.no_progress[rid] = 0
            self._last_position[rid] = robot.position

    # ----- Greedy (the first version), kept so we can compare -----

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
        # orders that are not delivered yet but already past their deadline: they are late too
        overdue = [o for o in self.orders
                   if o.status in (OrderStatus.WAITING, OrderStatus.ASSIGNED, OrderStatus.PICKED_UP)
                   and o.is_late(self.tick)]
        counted = len(delivered) + len(overdue)
        return {
            "ticks": self.tick,
            "orders_created": len(self.orders),
            "orders_delivered": len(delivered),
            "orders_waiting": len(self.queue),
            "orders_in_progress": sum(1 for o in self.orders
                                      if o.status in (OrderStatus.ASSIGNED, OrderStatus.PICKED_UP)),
            "orders_failed": sum(1 for o in self.orders if o.status == OrderStatus.FAILED),
            "avg_delivery_time": round(statistics.mean(times), 1) if times else None,
            # on time = delivered on time / (delivered + still waiting after the deadline)
            "on_time_rate": round(len(on_time) / counted, 3) if counted else None,
            "late_orders": len(delivered) - len(on_time) + len(overdue),
            "overdue_waiting": len(overdue),
            "breakdowns": self.breakdowns,
            "dropped_items": self.drops,
            "blocked_cells": self.blocks,
            "stuck_steps": self.stuck_steps,
            "collisions": self.collisions,
            "deadlocks_resolved": self.deadlocks,
            "longest_wait": self.max_wait,
            "battery_empty": len(self.battery_empty_logged),
            "battery_rescues": self.rescues,
        }