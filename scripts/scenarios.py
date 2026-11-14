"""
Scenarios: the same warehouse under different conditions.

Each scenario is a seed, a number of ticks, and some changes to SimConfig.
Because the seed is fixed, every scenario replays exactly the same way.

To add your own scenario, copy one entry and change the numbers.
"""

SCENARIOS = {
    "normal": {
        "title": "Normal day",
        "description": "Default settings: smart assignment, opportunity charging, A* routes with bookings.",
        "seed": 42,
        "ticks": 500,
        "config": {},
    },
    "calm": {
        "title": "Calm",
        "description": "No breakdowns, no dropped items, no blocked cells. How the fleet moves in a perfect world.",
        "seed": 42,
        "ticks": 500,
        "config": {"breakdown_chance": 0, "drop_chance": 0, "block_chance": 0},
    },
    "rush_hour": {
        "title": "Rush hour",
        "description": "Orders arrive about 3 times faster. Watch the queue grow and the packing stations jam.",
        "seed": 42,
        "ticks": 500,
        "config": {"order_rate": 0.6},
    },
    "breakdown_day": {
        "title": "Breakdown day",
        "description": "Robots break down 20 times more often. Orders wait while robots are repaired.",
        "seed": 42,
        "ticks": 500,
        "config": {"breakdown_chance": 0.02},
    },
    "blocked_aisles": {
        "title": "Blocked aisles",
        "description": "Many floor cells get blocked for a while. Robots get stuck and sidestep.",
        "seed": 42,
        "ticks": 500,
        "config": {"block_chance": 0.15, "block_ticks": 40},
    },
    "low_battery": {
        "title": "Low battery",
        "description": "Every robot starts at 30% battery. Watch the queue at the 3 chargers.",
        "seed": 42,
        "ticks": 500,
        "config": {},
        "start_battery": 30.0,
    },
    "single_robot": {
        "title": "Single robot",
        "description": "Only one robot, slow orders. Easy to follow its exact route.",
        "seed": 42,
        "ticks": 300,
        "config": {"order_rate": 0.05, "breakdown_chance": 0, "drop_chance": 0},
        "robots": 1,
    },
    "normal_greedy": {
        "title": "Normal day - old greedy movement",
        "description": "Same as Normal day, but robots use the old greedy movement. Compare with Normal day.",
        "seed": 42,
        "ticks": 500,
        "config": {"movement": "greedy"},
    },
    "single_robot_greedy": {
        "title": "Single robot - old greedy movement",
        "description": "Same as Single robot, but with greedy movement. Watch it wobble next to a shelf row.",
        "seed": 42,
        "ticks": 300,
        "config": {"order_rate": 0.05, "breakdown_chance": 0, "drop_chance": 0, "movement": "greedy"},
        "robots": 1,
    },
    "astar_only": {
        "title": "Normal day - plain A* (no bookings)",
        "description": "Same as Normal day, but with plain A* movement (no bookings), first-free assignment and simple charging. Compare with Normal day.",
        "seed": 42,
        "ticks": 500,
        "config": {"movement": "astar", "assignment": "first_free", "charging": "simple"},
    },
    "smart_charging": {
        "title": "Normal day - smart charging (old)",
        "description": "Same as Normal day, but with the older smart charging: robots on a charger can't take orders, and only top up below 70%. Compare with Normal day.",
        "seed": 42,
        "ticks": 500,
        "config": {"charging": "smart"},
    },
    "realistic_friday": {
        "title": "Realistic Friday (06:00-22:00)",
        "description": "Real-looking demand: 1 tick = 1 minute, a morning and a bigger evening peak. Friday 2 October 2026, the day after the order history ends.",
        "seed": 42,
        "ticks": 1020,
        "config": {"demand": "realistic", "demand_seed": 7, "demand_day": 123},
    },
    "restock_people": {
        "title": "Restocking by people",
        "description": "2 people carry goods from the docks to the shelves. The robots don't know where they are: "
                       "a robot that sees a person tells every robot, and routes through that spot are planned again.",
        "seed": 42,
        "ticks": 500,
        "config": {"restock": "people"},
    },
    "restock_robots": {
        "title": "Restocking by robots",
        "description": "The same restock jobs, done by 2 restocking robots. They book their route like every robot, "
                       "so nobody has to find them. Compare with Restocking by people.",
        "seed": 42,
        "ticks": 500,
        "config": {"restock": "robots"},
    },
    "connection_trouble": {
        "title": "Connection trouble",
        "description": "Now and then a robot loses its connection and is lost. A technician walks over and "
                       "restarts it; then it rejoins the fleet. (Needs MQTT, like the public demo.)",
        "seed": 42,
        "ticks": 500,
        "config": {},
        "lost_chance": 0.003,
    },
    "talking_robots": {
        "title": "Talking robots (MQTT story)",
        "description": "2 robots, low batteries and breakdowns, so every kind of message happens early. "
                       "Run python -m scripts.talk to read what they say to each other, and why.",
        "seed": 42,
        "ticks": 300,
        "config": {"order_rate": 0.08, "breakdown_chance": 0.01, "drop_chance": 0, "block_chance": 0},
        "robots": 2,
        "start_battery": 30.0,
    },
    "stress_20": {
        "title": "Stress test - 20 robots",
        "description": "20 robots and an order every 2 ticks. Watch the waiting zones, bookings and deadlock handling.",
        "seed": 1,
        "ticks": 600,
        "config": {"order_rate": 0.5},
        "robots": 20,
    },
}