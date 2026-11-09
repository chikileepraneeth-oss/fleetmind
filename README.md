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
- **Shelf:** a shelf unit that covers many cells and holds items.
- **Stations:** chargers, packing stations and loading docks.

### 3. Wrote the main classes (`scripts/classes.py`)
- `Warehouse` – the grid map. It knows what is in every cell and where every robot is.
- `Robot` – can `move()`, `pick()` and `place()`.
- `Order` – knows its delivery time and if it is late.
- `Shelf` – a shelf unit with items.

Every action gives back two things: did it work, and why. For example `(False, "wall")` or `(True, "picked apples")`. This makes it easy to find problems.

### 4. Made one fixed warehouse that is easy to edit
The warehouse is saved in a file: `layouts/main_warehouse.json`.
To change the warehouse, I only edit this text map. I don't need to change any code.

```
# # # # # # # # # # # # # # # # #
# . . . . . . . . . . . . . . C #
# . a a a a a . . b b b b b . C #
# . a a a a a . . b b b b b . C #
# . a a a a a . . b b b b b . . #
# . . . . . . . . . . . . . . P #
# . . . . . . . . . . . . . . P #
# . c c c c c . . d d d d d . . #
# . c c c c c . . d d d d d . . #
# . c c c c c . . d d d d d . . #
# . . . . . . . . . . . . . . . #
# D D D . . . . . . . . . . . . #
# # # # # # # # # # # # # # # # #

#  wall      .  floor      C  charger
P  packing   D  loading dock
a b c d  shelf units
```

- Size: 13 rows × 17 columns
- 4 shelf units, each 3 tall and 5 wide:
  - **A** – Fruit (apples, bananas, oranges)
  - **B** – Grains (rice, pasta, flour)
  - **C** – Dairy (milk, cheese, yogurt)
  - **D** – Snacks (chips, cookies, chocolate)
- The aisles in the middle are 2 cells wide, so two robots can pass.
- 3 chargers, 2 packing stations, 3 loading docks.
- The robots and their start positions are also in the file.

The file `scripts/layout.py` reads this file and builds the warehouse.
If I make a mistake in the file, it tells me clearly what is wrong. It checks that:

- all map rows have the same length
- there are no unknown symbols
- every shelf on the map has its items written down
- item amounts are not negative
- one item is only on one shelf
- every shelf can be reached by a robot
- robots start on a free floor cell, and no two robots start in the same place

### 5. Wrote tests
I have **52 tests** in the `tests/` folder. They check the rules, for example:

- a robot cannot drive into a wall, a shelf or another robot
- moving uses battery, and a robot with no battery cannot move
- a broken robot cannot move
- a robot can only pick when it has an order and stands next to the shelf
- a robot can only deliver when it stands on the packing station
- delivery time and late orders are calculated correctly
- a broken layout file gives a clear error

---

## The rules of the warehouse

**Map**
- The warehouse is a grid. The outer edge is always walls.
- Positions are (row, column). (0, 0) is the top-left corner.

**Moving**
- A robot moves one cell at a time: up, down, left or right. No diagonal moves.
- It cannot drive into walls, shelves or outside the map.
- Only one robot can be in a cell.
- Every step uses 0.5% battery.

**Picking and delivering**
- A robot carries only one item at a time.
- To pick, it must stand next to any side of the shelf unit.
- To deliver, it must stand on the packing station.

---

## Decisions I made and why

**A shelf block is one shelf unit.**
In a 3 × 5 block, the middle cells are closed in on all sides, so no robot could reach them. So I made each block one unit. A robot can pick from it standing next to any of its cells. This is also closer to real warehouse racks.

**The warehouse is in a file, not in code.**
Every test and every run uses the same warehouse, so comparisons are fair. And I can change the layout easily.

---

## Problems I faced and what I learned

- **Same names for different things.** At first I used `row` and `col` for the size of the warehouse and also for a position. That is confusing, so now the size is `height` and `width`.
- **Shelves sharing items by mistake.** If you write `items={}` as a default in Python, all shelves share the same dictionary. Adding apples to one shelf adds them to all. The fix is `items=None` and making a new dictionary inside.
- **The middle of big shelf blocks could not be reached.** I fixed this by making every block one shelf unit.
- **Running the code.** Files must be run from the main project folder with `python -m scripts.layout`, and the folders need an `__init__.py` file.
- **Windows setup.** I had to allow PowerShell scripts to activate the virtual environment. I also found that my tests were running with the global Python instead of the project's Python, so I need to make sure the virtual environment is really active.
- **A test failed after I changed the warehouse.** I changed the number of robots in the layout file and a test still expected the old number. I learned that when I change a design on purpose, I must also update its test.
- **Where the battery check belongs.** The battery check inside `move()` is only a last safety stop. A smarter battery decision should happen before a robot gets an order.

---

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

---

## Project structure

```
fleetmind/
├── layouts/
│   └── main_warehouse.json   # the warehouse map, shelves, items and robots
├── scripts/
│   ├── classes.py            # Warehouse, Robot, Order, Shelf
│   └── layout.py             # reads the layout file and builds the warehouse
├── tests/
│   ├── test_classes.py       # tests for the rules
│   └── test_layout.py        # tests for the layout file and shelf units
├── requirements.txt
└── README.md
```

---

## Author

**Praneeth Chikile** – M.Sc. Data Science and Analytics, SRH University Heidelberg