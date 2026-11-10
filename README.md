# FleetMind

FleetMind is my project to build a warehouse where many robots work together, with one central "brain" that decides which robot does which order.

I am building it step by step, to learn how robotics and AI companies build real systems.

---

## Why I am building this

In a warehouse with many robots, a lot can go wrong:

- two robots go for the same order, while other robots do nothing
- robots get stuck in narrow aisles or block each other
- many robots run out of battery at the same time
- urgent orders wait behind normal orders

FleetMind should fix this. One central brain looks at all robots and all orders and decides which robot takes which order, which way it drives, and when it goes to charge.

This connects to my Master's thesis, which was about giving resources to tasks when the demand is uncertain. Here the resources are robots.

---

## What I have done so far

### 1. Set up the project
- GitHub repository, Python virtual environment, VS Code and pytest.

### 2. Designed the warehouse
I decided what a cell, a robot, an order and a station are.

- **Robot:** id, position, battery, status, what it is carrying, its order, its path, speed.
- **Order:** id, item, pickup shelf, drop-off station, priority, created time, deadline, status, delivered time.
- **Shelf:** one cell that holds one item. Shelves are in rows, like A1, A2, A3, A4, A5.
- **Stations:** chargers, packing stations and loading docks.

### 3. Wrote the main classes (`scripts/classes.py`)
- `Warehouse` – the grid map. It knows what is in every cell and where every robot is.
- `Robot` – can `move()`, `pick()`, `place()`, `charge()`, `break_down()` and `tick_repair()`.
- `Order` – knows its delivery time and if it is late.
- `Shelf` – a shelf with its item.

Every action gives back two things: did it work, and why. For example `(False, "wall")` or `(True, "picked apples")`. This makes it easy to find problems.

### 4. Made one fixed warehouse that is easy to edit
The warehouse is saved in a file: `layouts/main_warehouse.json`.
To change the warehouse, I only edit this text map. I don't need to change any code.

```
# # # # # # # # # # # # # # # # #
# . . . . . . . . . . . . . . C #
# . a a a a a . . e e e e e . C #
# . . . . . . . . . . . . . . C #
# . b b b b b . . f f f f f . . #
# . . . . . . . . . . . . . . P #
# . c c c c c . . g g g g g . P #
# . . . . . . . . . . . . . . . #
# . d d d d d . . h h h h h . . #
# . . . . . . . . . . . . . . . #
# . . . . . . . . . . . . . . . #
# D D D . . . . . . . . . . . . #
# # # # # # # # # # # # # # # # #

#  wall      .  floor      C  charger
P  packing   D  loading dock
a–h  shelf rows
```

- Size: 13 rows × 17 columns
- 8 shelf rows (A to H). Each row is 1 cell tall and 5 cells wide.
- There is an aisle above and below every shelf row, so robots can walk past every shelf.
- Every cell is its own shelf: A1, A2, A3, A4, A5, and so on. That makes 40 shelves.
- Each shelf holds **one item**, and every shelf starts with 20 of it.

| Row | Type | Items (shelf 1 to 5) |
|---|---|---|
| A | Fruit | apples, bananas, oranges, grapes, pears |
| B | Vegetables | carrots, potatoes, onions, tomatoes, peppers |
| C | Grains | rice, pasta, flour, oats, lentils |
| D | Dairy | milk, cheese, yogurt, butter, cream |
| E | Snacks | chips, cookies, chocolate, crackers, nuts |
| F | Drinks | water, juice, soda, coffee, tea |
| G | Bakery | bread, croissants, bagels, muffins, cake |
| H | Household | soap, shampoo, toothpaste, tissues, detergent |

- 3 chargers, 2 packing stations, 3 loading docks.
- The robots and their start positions are also in the file.

The file `scripts/layout.py` reads this file and builds the warehouse.
If I make a mistake in the file, it tells me clearly what is wrong. It checks that:

- all map rows have the same length
- there are no unknown symbols
- every shelf row on the map has its items written down
- every row has exactly one item per shelf
- the stock number is not negative
- one item is only on one shelf
- every shelf can be reached by a robot
- robots start on a free floor cell, and no two robots start in the same place

### 5. Wrote tests
I have **127 tests** in the `tests/` folder. They check the rules, for example:

- a robot cannot drive into a wall, a shelf, a blocked cell or another robot
- moving uses battery, and a robot with no battery cannot move
- a broken robot cannot move or charge, and works again after the repair
- a robot can only pick when it has an order and stands next to the right shelf
- a robot can only deliver when it stands on the packing station
- delivery time and late orders are calculated correctly
- a broken layout file gives a clear error
- the same seed always gives exactly the same simulation
- A* finds the shortest route and goes around shelves and blocked cells

### 6. Charging, breakdowns and repairs
- A robot can **charge** when it stands on a charger. It gets 5% per tick and stops at 100%.
- A robot can **break down**. Then it cannot move or charge until it is repaired.
- A repair takes a number of ticks. If the robot was carrying an item, it keeps it and continues after the repair.

### 7. The warehouse runs by itself (simulation)
Before, I had to move every robot by hand. Now the warehouse runs on its own.

- **Clock:** time moves in small steps called ticks. In every tick, the same things happen in the same order.
- **Orders:** new orders arrive at random times (about 1 every 5 ticks). Each order has an item, a packing station, a priority (high, normal or low) and a deadline.
- **Queue:** waiting orders stand in a line. The first free robot takes the oldest order.
- **Accidents:** robots can break down, drop the item they carry, or find a floor cell blocked for a while.
- **Charging:** when a robot has nothing to do and its battery is under 25%, it goes to a free charger.
- **Parking:** when a robot has nothing to do, it drives back to its home spot, so it does not block aisles or packing stations.
- **Seed:** all random things come from one seed number. The same seed always gives exactly the same run, so I can compare versions fairly.
- **Log:** every event is saved in a CSV file (tick, robot, event, details).
- **KPIs:** at the end I get numbers like orders delivered, average delivery time, on-time rate, breakdowns and how often robots got stuck.

### 8. Robots plan their route with A*
First, robots moved in a simple "greedy" way: always one step closer to the goal.
With shelf rows, this failed badly. When a shelf row was between the robot and its goal, the robot wobbled left and right forever and never got there.

Now every robot uses **A\*** (A-star). It plans the whole route from start to end before moving, going around walls, shelves and blocked cells. Then it follows the route step by step.

- If another robot is in the way, it waits a little, then plans a route around it.
- If it is still stuck after a while, it takes a random step to the side to break the jam.
- If a blocked cell appears on its route, it plans a new route right away.

### 9. I can see everything in my browser
I built two viewers that open in the browser:

- **Replay viewer** (`viewer/index.html`): plays back a whole day in the warehouse. I can pause, jump to any tick, follow one robot, and see its planned route, battery and what it carries. There are 9 scenarios, like rush hour, breakdown day, blocked aisles, low battery and single robot.
- **Test viewer** (`viewer/tests.html`): shows every test step by step on a small map, next to the test code.

---

## The rules of the warehouse

**Map**
- The warehouse is a grid. The outer edge is always walls.
- Positions are (row, column). (0, 0) is the top-left corner.

**Moving**
- A robot moves one cell at a time: up, down, left or right. No diagonal moves.
- It cannot drive into walls, shelves, blocked cells or outside the map.
- Only one robot can be in a cell.
- Every step uses 0.5% battery.

**Picking and delivering**
- A robot carries only one item at a time.
- To pick, it must stand right next to the exact shelf (in the aisle above or below it).
- To deliver, it must stand on the packing station.

**Charging and repairs**
- A robot charges only on a charger, 5% per tick, up to 100%.
- A broken robot does nothing until its repair time is over.

---

## Decisions I made and why

**Shelves are in rows with aisles in between.**
At first I had big 3 × 5 shelf blocks, but the middle cells were closed in on all sides and no robot could reach them. So I changed to rows that are only 1 cell tall, with an aisle above and below each row. Now every shelf can be reached, and each shelf holds one item, like in a real warehouse.

**The warehouse is in a file, not in code.**
Every test and every run uses the same warehouse, so comparisons are fair. And I can change the layout easily.

**Robots plan the whole route instead of only stepping closer.**
Greedy movement wobbles forever when a shelf row is in the way. A* plans the full route around it first.

**Idle robots go back home.**
Robots that finished a job used to stay where they were, often in a narrow aisle or next to a packing station, and blocked other robots.

**A robot charges on any charger it is standing on.**
Once, a robot ran out of battery while standing on a charger, because it was going to a different one.


## How to run it

### Setup (Windows)

```powershell
git clone https://github.com/chikileepraneeth-oss/fleetmind.git
cd fleetmind
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### See the warehouse

```powershell
python -m scripts.layout
```

### Run a small demo (one robot delivers apples)

```powershell
python -m scripts.classes
```

### Run the tests

```powershell
python -m pytest
```

### Run one simulation

```powershell
python -m scripts.run --seed 42 --ticks 500 --show-map
```

### Watch the scenarios in the browser

```powershell
python -m scripts.record
start viewer\index.html
```

### Watch the tests in the browser

```powershell
python -m scripts.record_tests
start viewer\tests.html
```

---

## Project structure

```
fleetmind/
├── layouts/
│   └── main_warehouse.json   # the warehouse map, shelves, items and robots
├── scripts/
│   ├── classes.py            # Warehouse, Robot, Order, Shelf
│   ├── layout.py             # reads the layout file and builds the warehouse
│   ├── orders.py             # makes new orders
│   ├── movement.py           # greedy movement and A* route planning
│   ├── simulation.py         # the clock, queue, accidents, charging and KPIs
│   ├── run.py                # run one simulation from the command line
│   ├── scenarios.py          # different situations to test (rush hour, breakdowns, ...)
│   ├── record.py             # records the scenarios for the replay viewer
│   └── record_tests.py       # records the tests for the test viewer
├── tests/
│   ├── test_classes.py       # rules for robots, orders, shelves, charging, breakdowns
│   ├── test_layout.py        # the layout file and the shelf rows
│   ├── test_simulation.py    # the running simulation and the order generator
│   ├── test_astar.py         # A* route planning
│   └── conftest.py           # records tests for the test viewer (only when asked)
├── viewer/
│   ├── index.html            # replay viewer
│   └── tests.html            # test viewer
├── requirements.txt
└── README.md
```

---

## Author

**Praneeth Chikile** – M.Sc. Data Science and Analytics, SRH University Heidelberg