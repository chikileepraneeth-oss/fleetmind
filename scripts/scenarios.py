"""
Scenarios: the same warehouse under different conditions.

Each scenario is a seed, a number of ticks, and some changes to SimConfig.
Because the seed is fixed, every scenario replays exactly the same way.

To add your own scenario, copy one entry and change the numbers.
"""

SCENARIOS = {
    "normal": {
        "title": "Normal day",
        "description": "Default settings, robots plan routes with A*.",
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
        "description": "Same as Normal day, but robots use week 3 greedy movement. Compare with Normal day.",
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
}