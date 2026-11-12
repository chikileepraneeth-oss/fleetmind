"""
Deciding which robot does which order, and when robots charge.

v1 (the simple baseline): the first free robot takes the oldest order.
v2 ("smart"):
    - the most urgent order is served first (earliest deadline first)
    - each order goes to the robot that can reach it fastest
    - a robot only takes an order if its battery is enough for the whole trip
      AND the drive to a charger afterwards
    - when two robots are equally good, the one with less work today wins

Both work on the same Simulation object, so we can switch between them with
SimConfig(assignment="first_free") or SimConfig(assignment="smart").

charging="opportunity" (opportunity charging):
    - an idle robot that is not (nearly) full goes to top up while there is no work,
      so it is ready for the busy hours; a full robot waits at its parking spot
    - a robot on a charger can still take an order (if its battery is good enough),
      so topping up never keeps a robot away from work
"""

from scripts.classes import Robot, OrderStatus, RobotStatus


def can_take_order(sim, robot):
    """
    Can this robot be given a new order right now?
    Always: robots that are free (idle or driving home).
    With opportunity charging also: robots topping up (going to or on a charger),
    if they have enough battery and are not in the middle of a delivery.
    """
    if sim._is_free(robot):
        return True
    return (sim.config.charging == "opportunity"
            and robot.task in ("to_charger", "charging")
            and robot.order is None
            and robot.robot_id not in sim.resume
            and robot.status != RobotStatus.ERROR
            and robot.battery >= sim.config.leave_charger_at)


# ---------------------------------------------------------------------------
# v1: first free robot, oldest order
# ---------------------------------------------------------------------------

def assign_first_free(sim):
    free = [r for r in sim._robots()
            if can_take_order(sim, r) and r.battery >= sim.config.charge_threshold]
    while sim.queue and free:
        order = sim.queue.popleft()
        robot = free.pop(0)
        give_order(sim, robot, order, spot=None)


# ---------------------------------------------------------------------------
# v2: scored assignment
# ---------------------------------------------------------------------------

def assign_smart(sim):
    cfg = sim.config
    free = [r for r in sim._robots()
            if can_take_order(sim, r) and r.battery >= cfg.critical_battery]
    if not free or not sim.queue:
        return

    # Earliest deadline first. High-priority orders get shorter deadlines,
    # so they naturally come first, but old normal orders are not forgotten.
    orders = sorted(sim.queue, key=lambda o: (o.deadline, -o.priority.value, o.created_at, o.order_id))

    for order in orders:
        if not free:
            break
        best = None
        for robot in free:
            plan = trip_plan(sim, robot, order)
            if plan is None:
                continue                         # can't reach, or not enough battery
            spot, steps = plan
            # lower is better: distance first, then more battery, then less work today
            score = (steps, -robot.battery, sim.deliveries_by.get(robot.robot_id, 0), robot.robot_id)
            if best is None or score < best[0]:
                best = (score, robot, spot)
        if best is None:
            continue
        _, robot, spot = best
        sim.queue.remove(order)
        free.remove(robot)
        give_order(sim, robot, order, spot)


def trip_plan(sim, robot, order):
    """
    Where the robot should stand to pick this order, and how many steps it
    takes to get there. None if it can't be done (no route, or the battery
    would run out before the robot reaches a charger afterwards).
    """
    wh = sim.warehouse
    shelf = wh.get_shelf(order.pickup)
    dist = sim.dist
    here = dist.get(robot.position)
    if here is None:
        return None

    spots = [s for s in wh.access_cells(shelf) if s in here and s not in wh.blocked]
    if not spots:
        return None
    spot = min(spots, key=lambda s: (here[s], s))
    to_shelf = here[spot]

    dropoff = tuple(order.dropoff)
    to_packing = dist[spot].get(dropoff)
    if to_packing is None:
        return None
    to_charger = min((dist[dropoff].get(c, 10**6) for c in sim.chargers), default=0)

    battery_needed = (to_shelf + to_packing + to_charger) * Robot.BATTERY_PER_STEP \
        + sim.config.battery_margin
    if robot.battery < battery_needed:
        return None
    return spot, to_shelf


def give_order(sim, robot, order, spot):
    if robot.task in ("to_charger", "charging"):
        # opportunity charging: unplug (or stop driving to the charger) and go to work
        sim.log("left_charger_for_order", robot.robot_id, f"battery {robot.battery:.1f}%")
        if robot.status == RobotStatus.CHARGING:
            robot.status = RobotStatus.IDLE
    robot.order = order
    robot.task = "to_pickup"
    robot.target = spot
    robot.path = []
    order.status = OrderStatus.ASSIGNED
    sim.log("order_assigned", robot.robot_id, order.order_id)


# ---------------------------------------------------------------------------
# Charging decisions
# ---------------------------------------------------------------------------

def charge_simple(sim):
    """v1: idle robots below the threshold go to a free charger."""
    for robot in sim._robots():
        if not sim._is_free(robot) or robot.battery >= sim.config.charge_threshold:
            continue
        send_to_charger(sim, robot)


def charge_smart(sim):
    """
    v2:
      - battery critical  -> always go (if a charger is free)
      - battery low       -> go
      - battery could use a top-up, and nothing is waiting -> go, but only while
        enough robots stay available for new orders (never all at once)
    """
    cfg = sim.config
    robots = sim._robots()
    free = [r for r in robots if sim._is_free(r)]
    # lowest battery first, so the robots that need it most get the chargers
    free.sort(key=lambda r: (r.battery, r.robot_id))

    min_available = max(1, round(len(robots) * cfg.min_available_share))
    available = len(free)

    for robot in free:
        if robot.battery < cfg.critical_battery or robot.battery < cfg.charge_threshold:
            if send_to_charger(sim, robot):
                available -= 1
        elif robot.battery < cfg.top_up_below and not sim.queue and available - 1 >= min_available:
            if send_to_charger(sim, robot):
                available -= 1


def charge_opportunity(sim):
    """
    Opportunity charging:
      - battery low -> always go (as before)
      - not (nearly) full and no orders waiting -> go top up, so the robot is
        ready for the busy hours. No "keep half the fleet available" limit is
        needed, because robots on a charger can still take orders (can_take_order).
      - (nearly) full -> stay free; it waits at its parking spot
    Lowest battery first, so if there are not enough chargers, the robots that
    need it most get them; the others wait at their parking spot.
    """
    cfg = sim.config
    free = [r for r in sim._robots() if sim._is_free(r)]
    free.sort(key=lambda r: (r.battery, r.robot_id))
    for robot in free:
        if robot.battery < cfg.charge_threshold:
            send_to_charger(sim, robot)
        elif robot.battery < cfg.top_up_full_below and not sim.queue:
            send_to_charger(sim, robot)


def should_leave_charger_early(sim, robot):
    """v2: stop charging early when orders are piling up and the battery is good enough."""
    cfg = sim.config
    if cfg.charging != "smart" or robot.battery < cfg.leave_charger_at:
        return False
    free_robots = sum(1 for r in sim._robots() if sim._is_free(r))
    return len(sim.queue) > free_robots


def send_to_charger(sim, robot):
    charger = sim._free_charger_for(robot)
    if charger is None:
        return False                             # all chargers busy: wait
    robot.task = "to_charger"
    robot.target = charger
    robot.path = []
    sim.log("going_to_charge", robot.robot_id, f"battery {robot.battery:.1f}% -> {charger}")
    return True


# ---------------------------------------------------------------------------
# Battery safety (v2): check every tick, not only when an order is given
# ---------------------------------------------------------------------------

def battery_safety(sim):
    """
    Waiting, detours and replanning can use more battery than the trip
    estimate. So every tick: if a robot can no longer finish its job AND
    still reach a charger, send it to charge NOW, while it still can.

    - going to pick up: the order goes back to the front of the queue
    - carrying an item: it keeps the item and finishes the delivery after charging
    """
    if sim.config.charging not in ("smart", "opportunity"):
        return
    step = Robot.BATTERY_PER_STEP
    for robot in sim._robots():
        if robot.task not in ("to_pickup", "to_dropoff", "to_home") or robot.status.value == "error":
            continue
        here = sim.dist.get(robot.position, {})
        to_charger = min((here.get(c, 10**6) for c in sim.chargers), default=0)

        if robot.task == "to_pickup":
            shelf_spot = robot.target or robot.position
            remaining = here.get(shelf_spot, 0) + sim.dist.get(shelf_spot, {}).get(tuple(robot.order.dropoff), 0)
            after = min((sim.dist.get(tuple(robot.order.dropoff), {}).get(c, 10**6) for c in sim.chargers), default=0)
        elif robot.task == "to_dropoff":
            drop = tuple(robot.order.dropoff)
            remaining = here.get(drop, 0)
            after = min((sim.dist.get(drop, {}).get(c, 10**6) for c in sim.chargers), default=0)
        else:  # to_home
            remaining, after = 0, 0

        if robot.battery >= (remaining + after) * step + sim.config.safety_buffer:
            continue                                 # still fine

        # not enough to finish safely: charge first
        if robot.task == "to_pickup":
            order = robot.order
            order.status = OrderStatus.WAITING
            sim.queue.appendleft(order)
            robot.order = None
            sim.log("order_returned", robot.robot_id, f"{order.order_id}: battery too low ({robot.battery:.1f}%)")
        elif robot.task == "to_dropoff":
            sim.resume[robot.robot_id] = ("to_dropoff", tuple(robot.order.dropoff))
            sim.log("charge_before_delivery", robot.robot_id, f"{robot.order.order_id}, battery {robot.battery:.1f}%")

        robot.task = None
        if not send_to_charger(sim, robot):
            # every charger is busy: stand still (uses no battery) until one is free
            robot.task = "wait_charger"
            robot.target = None
            robot.path = []
            sim.log("waiting_for_charger", robot.robot_id, f"battery {robot.battery:.1f}%")