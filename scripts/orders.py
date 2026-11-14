"""
Order generator: creates new customer orders at random times.

All randomness comes from the rng (random generator) passed in, which is
created from a seed. Same seed -> exactly the same orders every run.
"""

from scripts.classes import Order, Priority, PACKING


# How likely each priority is, and how many ticks each one gets until its deadline.
PRIORITY_WEIGHTS = {Priority.HIGH: 0.2, Priority.NORMAL: 0.6, Priority.LOW: 0.2}
DEADLINE_TICKS = {Priority.HIGH: 60, Priority.NORMAL: 100, Priority.LOW: 200}


class OrderGenerator:
    """
    Makes new orders for items that are in stock.

    Two ways to decide WHEN orders arrive:
      - flat (no demand model): each tick, one order with chance order_rate
      - realistic (a DemandModel from scripts/demand.py): the number of orders in
        each minute of one working day comes from the demand model (tick 1 = 06:00)
    WHAT is ordered (item, packing station, priority) always comes from rng.
    """

    def __init__(self, warehouse, rng, order_rate=0.2, demand=None, day=0):
        self.warehouse = warehouse
        self.rng = rng
        self.order_rate = order_rate          # chance of a new order each tick (0.2 = about 1 every 5 ticks)
        self.demand = demand                  # None = flat demand
        self.day = day                        # which day of the demand model is simulated
        self.next_number = 1
        self.packing_stations = warehouse.cells_of_type(PACKING)
        if not self.packing_stations:
            raise ValueError("The warehouse has no packing station, so orders have nowhere to go.")

    def orders_for(self, tick):
        """All new orders that arrive in this tick (often none, sometimes several)."""
        if self.demand is None:
            order = self.maybe_create(tick)
            return [order] if order else []
        arrivals = self.demand.arrivals(self.day)
        minute = tick - 1                     # tick 1 is the first minute of the day (06:00)
        if not 0 <= minute < len(arrivals):
            return []                         # the warehouse is closed: no new orders
        orders = [self.create(tick) for _ in range(arrivals[minute])]
        return [o for o in orders if o]

    def maybe_create(self, tick):
        """Return a new Order, or None if no order arrives this tick."""
        if self.rng.random() >= self.order_rate:
            return None
        return self.create(tick)

    def create(self, tick):
        """Create one order right now (or None if nothing is in stock)."""
        in_stock = self._items_in_stock()
        if not in_stock:
            return None

        item = self.rng.choice(in_stock)
        shelf = self.warehouse.find_shelf_with(item)
        dropoff = self.rng.choice(self.packing_stations)

        priorities = list(PRIORITY_WEIGHTS)
        weights = [PRIORITY_WEIGHTS[p] for p in priorities]
        priority = self.rng.choices(priorities, weights=weights)[0]

        order = Order(
            order_id=f"O-{self.next_number:04d}",      # O-0001, O-0002, ...
            item=item,
            pickup=shelf.shelf_id,
            dropoff=dropoff,
            priority=priority,
            created_at=tick,
            deadline=tick + DEADLINE_TICKS[priority],
        )
        self.next_number += 1
        return order

    def _items_in_stock(self):
        """All items with at least one unit, in a fixed (sorted) order so runs repeat."""
        items = []
        for shelf_id in sorted(self.warehouse.shelves):
            shelf = self.warehouse.shelves[shelf_id]
            for item in sorted(shelf.items):
                if shelf.items[item] > 0:
                    items.append(item)
        return items