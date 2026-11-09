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
    """Each tick, maybe creates one new order for an item that is in stock."""

    def __init__(self, warehouse, rng, order_rate=0.2):
        self.warehouse = warehouse
        self.rng = rng
        self.order_rate = order_rate          # chance of a new order each tick (0.2 = about 1 every 5 ticks)
        self.next_number = 1
        self.packing_stations = warehouse.cells_of_type(PACKING)
        if not self.packing_stations:
            raise ValueError("The warehouse has no packing station, so orders have nowhere to go.")

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