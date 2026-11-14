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
- robots start on a free cell they can drive on, and no two robots start in the same place

### 5. Wrote tests
I have **249 tests** in the `tests/` folder. They check the rules, for example:

- a robot cannot drive into a wall, a shelf, a blocked cell or another robot
- moving uses battery, and a robot with no battery cannot move
- a broken robot cannot move or charge, and works again after the repair
- a robot can only pick when it has an order and stands next to the right shelf
- a robot can only deliver when it stands on the packing station
- delivery time and late orders are calculated correctly
- a broken layout file gives a clear error
- the same seed always gives exactly the same simulation
- A* finds the shortest route and goes around shelves and blocked cells
- the nearest robot with enough battery gets the order, and urgent orders go first
- robots book their cells, never crash, and get out of deadlocks
- idle robots top up their battery, and a robot on a charger can still take an order
- the live server starts, pauses, steps and resets, and sends the right messages
- the demand has daily peaks and busy weekdays, and the same seed gives the same days
- the forecast never sees the day it predicts, and LightGBM beats the baseline
- the real M5 data is added up correctly, and calendar features are known in advance

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

- **Replay viewer** (`viewer/index.html`): plays back a whole day in the warehouse. I can pause, jump to any tick, follow one robot, and see its planned route, battery and what it carries. There are 13 scenarios, like a realistic Friday, rush hour, breakdown day, blocked aisles, low battery, single robot and a 20-robot stress test, plus "old version" scenarios to compare with earlier versions.
- **Test viewer** (`viewer/tests.html`): shows every test step by step on a small map, next to the test code.

### 10. Smarter assignment and charging
Before, the first free robot took the oldest order, even if it was far away. Now (`SimConfig(assignment="smart")`):

- **Most urgent first:** the order with the earliest deadline is served first. High-priority orders have shorter deadlines, so they come first, but old normal orders are not forgotten.
- **Nearest robot:** each order goes to the robot that can reach the shelf fastest (real walking distance around shelves, not a straight line).
- **Enough battery:** a robot only takes an order if its battery is enough for the whole trip **and** the drive to a charger afterwards.
- **Fair work:** if two robots are equally good, the one that delivered less today gets it.
- **Least busy packing station:** after picking, the robot delivers to the packing station with the shortest line.
- **Battery safety:** every tick, each working robot checks if it can still finish its job and reach a charger. If not, it goes to charge now. If it was carrying an item, it keeps it and finishes the delivery after charging.

A test checks the goal: over 10 seeds, smart assignment has a shorter average delivery time than first-free assignment.

### 11. Traffic control: bookings and deadlocks
With A*, every robot planned its route alone, as if it were the only robot. Robots met in narrow aisles and waited, replanned or stepped aside. Now (`SimConfig(movement="cooperative")`, `scripts/traffic.py`):

- **Bookings (cooperative A\*):** every robot books the cells it will use, tick by tick: "cell (3, 4) at tick 51 is mine". The next robot plans around these bookings. It can also plan to **wait**, like "wait 2 ticks here, then go". Robots plan 16 ticks ahead, then plan again.
- **Moving together:** all robots move at the same time each tick. A robot can only enter a cell after the robot in it has left.
- **Deadlocks:** if robots wait on each other in a circle (R1 waits for R2, R2 for R3, R3 for R1), the least important robot gives way and everyone plans again. A robot that is only waiting (idle or going home) moves aside for a working robot.
- **Waiting zones:** only 2 robots may drive up to one packing station at a time. The others wait in open cells nearby, so they don't block the aisles.
- **Battery rescue:** if a robot's battery is empty away from a charger, staff swap the battery after 30 ticks.
- **Robots that can't move:** a broken robot books its cell until it is repaired (a robot with an empty battery until the battery swap). Before, it booked its cell for only 2 ticks, so the robots behind it planned through it and waited in a line until the repair was done. Now they drive around it. Over 10 seeds of "Breakdown day", the time robots spent stuck behind broken robots fell from 298 to 90 ticks, and 10 more orders were delivered per run.
- **A planned wait is checked every tick:** a robot that plans to wait for another robot now checks every tick whether the way got free earlier. Before, a robot kept waiting until its old plan ran out, even when the cell had emptied long ago. The wait report (`python -m scripts.waits`) showed that most long waits were exactly this, in front of the packing stations. Over 10 seeds of "Realistic Friday", long waits (8+ ticks) fell from 78.5 to 16.8 robot-ticks, the average delivery time from 73.3 to 60.4 ticks, and late orders from 25.5 to 11.3 per day (on time: 84% → 93%).

Plain A* compared with A* with bookings, over 10 seeds (500 ticks each):

| | Plain A* | A* with bookings |
|---|---|---|
| Orders delivered | 56.2 | **80.0** |
| Average delivery (ticks) | 111.8 | **53.7** |
| Stuck steps | 383.0 | **34.2** |
| Longest wait in one place (ticks) | **5.1** | 24.8 |
| Collisions | 0 | 0 |

The longest wait got longer, because with bookings a robot sometimes decides to wait for another robot on purpose, instead of trying to push through.

### 12. A live website
The replay viewer only plays a run that was recorded before. Now the simulation runs on a **server**, and the browser shows it **while it runs**.

- **Server** (`web/server.py`, FastAPI): builds the simulation for a scenario and runs it tick by tick in the background. After every tick it sends the new state to the browser.
- **WebSocket:** the browser and the server keep one connection open (`/ws`), so the server can send a message every tick without the browser asking each time. All open tabs see the same warehouse.
- **Controls:** start, pause, step one tick, reset, speed (1–60 ticks per second) and scenario. The buttons only send a command. The screen changes when the server answers.
- **KPI bar:** throughput (deliveries per 100 ticks), average delivery time, on-time rate, orders waiting, and how many robots are busy, idle, charging or broken.
- **Events and following:** the latest events, and click a robot to follow it (its route, its details and only its events).
- **Same seed, same run:** the server only calls `sim.step()`, so a live run is exactly the same as a recorded one. A test checks this over the WebSocket.
- The website did not change the simulation: the server only drives it from outside. The drawing code is now shared in `viewer/draw.js`, so the live page and the replay viewer look the same.
- The logic (`web/session.py`) is kept apart from the web code (`web/server.py`), so most of it can be tested without a server or a browser.
- **A real working day:** scenarios with realistic demand show the **clock** (1 tick = 1 minute from 06:00) and the date. The day starts behind a closed **roller shutter** that rolls up when I press Start. Orders stop at 22:00, the robots finish their work until 23:00, then the simulation stops by itself, the shutter rolls down and shows the day's summary (delivered, late, not done, on time). Reset opens the day again. Other scenarios run forever. The replay viewer shows the clock too.

**The messages.** Every message is JSON with a `"type"`.

Browser → server:
```json
{"type": "start"}   {"type": "pause"}   {"type": "step"}   {"type": "reset"}
{"type": "speed", "value": 10}
{"type": "scenario", "key": "rush_hour"}
```

Server → browser:
- `hello`: the map, the list of scenarios and the latest events. Sent when a tab connects, and after a reset or a scenario change.
- `state`: sent after every tick. It holds the tick, running or paused, the speed, every robot (position, task, battery, what it carries, its order, target and route), blocked cells, the queue, **only the new events**, and the KPIs.
- `error`: a command was wrong, for example `{"type": "error", "message": "unknown scenario 'xyz'"}`.

```json
{"type": "state", "tick": 51, "running": true, "speed": 6,
 "robots": [{"id": "R1", "pos": [3, 4], "status": "moving", "task": "to_pickup", "battery": 87.5,
             "carrying": null, "order": "O12", "target": [3, 6], "path": [[3, 5], [3, 6]], "repair": 0}],
 "blocked": [[7, 9]], "queue": 3,
 "events": [[51, "R2", "picked", "O9 apples from A1"]],
 "kpis": {"throughput": 4.1, "avg_delivery_time": 31.2, "on_time_rate": 0.95, "orders_waiting": 3,
          "robots_busy": 3, "robots_idle": 1, "robots_charging": 1, "robots_broken": 0, "...": "..."}}
```

### 13. Opportunity charging
Before (smart charging), an idle robot only topped up below 70%, and never more than half the fleet at once, because **a robot on a charger could not take orders**.

Now (`SimConfig(charging="opportunity")`, the new default):
- An idle robot that is **not (nearly) full** (under 90%) goes to top up while no orders are waiting, so it is ready for the busy hours.
- A **full** robot waits at its parking spot. If all 3 chargers are taken, the robots with the lowest battery get them first, and the others wait at their parking spot.
- A robot on a charger **can still take an order**, if it has at least 60% battery and is not in the middle of a delivery. So topping up never keeps a robot away from work.

I compared it with smart charging over 10 seeds (with all later fixes and the fair on-time rate from section 15):

| Scenario | Smart charging (old) | Opportunity charging |
|---|---|---|
| Normal day | 44.7 ticks avg, 97.4% on time, 2.3 late | 41.6 ticks avg, 96.1% on time, 3.4 late |
| Rush hour | 167.6 ticks avg, 11.2% on time | 167.9 ticks avg, 11.2% on time |
| Low battery | 58.9 ticks avg, 87.4% on time, 10.5 late | **53.6 ticks avg, 94.5% on time, 4.9 late** |

It helps most when batteries are low. The 20-robot stress test got a little worse (more waiting in the corner where the chargers and packing stations are), but I don't think a warehouse this size would buy 20 robots, so it did not decide the default. The old behaviour is still there as the scenario "Normal day – smart charging (old)".

### 14. Realistic demand and an order forecast
Until now, orders arrived at the same rate all day. Real warehouses have busy hours, busy weekdays and surprises. To prepare the fleet for busy times, I first need demand that looks real, and then a forecast of it.

**Time:** 1 tick = 1 minute. The warehouse is open 06:00–22:00, so a working day is 960 ticks.

**Realistic demand** (`scripts/demand.py`):
- **Daily peaks:** a morning peak around 10:00, quieter at lunch, a bigger evening peak around 18:00. The peaks are more than 5 robots can deliver (about 10–11 orders an hour).
- **Weekdays:** Monday and Friday are busy, Saturday is lighter, Sunday is quiet.
- **Busy and quiet weeks:** the level of demand moves slowly from day to day, so yesterday tells you something about today.
- **Big deliveries:** on about 1 day in 10, 15–30 extra orders arrive within 5 minutes. Nobody can predict these.
- **Chance:** the real number of orders in each minute is random around the expected number (a Poisson draw).

The simulation uses it with `SimConfig(demand="realistic", demand_seed=7, demand_day=N)`. Without it (`demand="flat"`, the default), every old scenario runs exactly as before. The new scenario "Realistic Friday" shows one full day.

**Order history** (`scripts/history.py`): 120 days (1 June – 28 September 2026), about 138 orders a day, saved as `data/orders_hourly.csv` (date, weekday, hour, orders).

**The forecast** (`scripts/forecast.py`): every evening, predict the orders for each hour of the next day.
- **Baseline (seasonal naive):** "tomorrow at 17:00 = the same weekday last week at 17:00".
- **LightGBM:** a gradient-boosting model. It learns from lag features: the same hour yesterday, a week ago and two weeks ago, the average of the last 7 days, the average of the same weekday over 4 weeks, and yesterday's total. Every feature only uses days **before** the day it predicts, so the forecast can't cheat. A test checks this.
- **Backtest:** train on days 0–89 and predict day 90, then train on days 0–90 and predict day 91, and so on up to day 119. Always the past predicts the future, like in real life.

Results over 30 test days:

| Forecast | WAPE | MASE |
|---|---|---|
| Baseline (same as last week) | 39.9% | 0.93 |
| **LightGBM** | **30.7%** | **0.71** |
| Perfect average (not possible in real life) | 28.0% | 0.65 |

- **WAPE** = total error ÷ total real orders. **MASE** below 1.0 = better than "same as last week".
- **LightGBM is 23% more accurate than the baseline.**
- Why are all the numbers so high? One hour has only about 5–16 orders, so chance moves them a lot. Even someone who knew the **true average** demand would be off by 28%. LightGBM (30.7%) is close to that limit.

**Real demand from the M5 dataset.** The demand above is made up: I chose the numbers. To test the forecast on real demand, I use M5 (Walmart sales 2011–2016, from a Kaggle competition): every product of store CA_1 in category FOODS, added up per day (`scripts/m5.py`). That is 1,941 real days.

- Each day's real units are scaled so an average day has about 140 orders. The pattern stays real, only the size changes.
- M5 only has **daily** numbers, so my daily curve still spreads each day's orders over the hours.
- The real data looks different from what I had made up: **weekends are the busiest days** (Saturday ×1.25, Sunday ×1.29), the store is **closed on Christmas**, and sales grew from about 2,240 units a day in 2011 to about 3,000 from 2013.
- The forecast gets calendar features, which are known in advance: holidays and events (and their type), "tomorrow is a holiday", Christmas, SNAP days (food stamps are paid out, food sales go up), and the same weekday one year ago.

Backtest on the last 365 real days (24 May 2015 – 22 May 2016, retrained every 7 days):

| Forecast | Hourly WAPE | MASE | **Daily WAPE** |
|---|---|---|---|
| Baseline (same as last week) | 36.7% | 1.00 | 10.7% |
| **LightGBM** | **26.4%** | **0.72** | **6.2%** |
| Perfect daily total (not possible in real life) | 24.6% | 0.67 | 0.0% |

- **On real demand, LightGBM is 42% more accurate than the baseline for whole days** (10.7% → 6.2%).
- **On the 30 holiday and event days** the difference is even bigger: 17.5% → 9.4%. That is what the calendar features are for.
- The daily WAPE is the most honest number here: the daily totals are real, the split into hours is my own curve.

### 15. A fair on-time rate
Before, the on-time rate only looked at **delivered** orders. An order still waiting long after its deadline did not count at all, so a fleet that left its hardest orders undelivered looked better than it was.

Now:
- **on-time rate** = delivered on time ÷ (delivered + orders still waiting after their deadline)
- **late orders** = delivered late + still waiting after their deadline. This is the main number to compare versions.
- Orders that failed (the shelf was empty) are not counted: they are not late, they can't be done.

The difference is big when the fleet can't keep up. In "Rush hour" the old rate said about 30% on time. The fair rate says 11%, because most orders are still waiting when the day ends.

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
- A robot with an empty battery away from a charger gets a battery swap from staff after 30 ticks (to 50%).

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

**Robots book their cells before they drive.**
When every robot planned alone, robots met in the aisles and lost time waiting and replanning. Booking cells tick by tick lets robots plan around each other before they meet.

**Every new version is a setting, and the old one stays.**
`movement`, `assignment` and `charging` in `SimConfig` keep the old versions, and there are scenarios for them. So I can always compare the new version with the old one on the same seeds.

**I compare over 10 seeds, not one.**
One seed can be lucky or unlucky. For example, on seed 42 opportunity charging delivers fewer orders than smart charging, but over 10 seeds it delivers more.

**One shared simulation for the live website.**
All browser tabs show and control the same warehouse, like a control room screen. The buttons only send commands, and every tab shows what the server really has.

**Opportunity charging is the default, even though the 20-robot test got a little worse.**
A warehouse this size would not buy 20 robots, because robots are expensive. So the normal 5-robot days decided.

**1 tick = 1 minute, open 06:00–22:00.**
A forecast of "orders per hour" needs real clock time. With 1 minute per tick, the deadlines become realistic too: 1 hour for urgent orders.

**The forecast is made the evening before, for every hour of the next day.**
That is when a warehouse plans charging and robot positions. It also means every feature must be at least 1 day old.

**I test the forecast on real data too.**
Made-up demand contains exactly the patterns I put in, so a forecast can look better than it would in real life. The real M5 data has holidays, growth and surprises I did not design.

**I compare the forecast with the perfect average.**
Without it, a WAPE of 30% looks bad. With it, I can see that most of the error is pure chance that no forecast can remove.


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

### Watch the fleet live

```powershell
python -m web.server
```

This starts the server and opens http://127.0.0.1:8000 in your browser. Press **Start**. Stop the server with `Ctrl+C`.
Use `--no-browser` to not open the browser, or `--port 8080` for another port.

### Make the order history and test the forecast

```powershell
python -m scripts.history
python -m scripts.forecast
```

The first command writes 120 days of made-up orders to `data/orders_hourly.csv`. The second runs the backtest, prints the results table and saves every forecast to `data/forecast_backtest.csv`.

### Use the real M5 data

Download M5 from Kaggle ("M5 Forecasting - Accuracy"). The M5 files are not part of this project. Then:

```powershell
python -m scripts.m5 --raw "C:\path\to\m5"
python -m scripts.history --source m5
python -m scripts.forecast --source m5
```

The first command reads the big sales file once and saves the daily totals of CA_1 FOODS in `data/m5_ca1_foods_daily.csv`. The files made from M5 are in `.gitignore`, so they are not uploaded to GitHub (check the Kaggle rules before sharing them).

### Find out why robots wait

```powershell
python -m scripts.waits
python -m scripts.waits --scenario breakdown_day --min-wait 5
```

Lists every time a robot stood still for 8+ ticks, with the reason (behind a broken robot, behind a parked robot, planned wait, ...), and adds up the waiting time per reason.

### Watch the scenarios in the browser (replay)

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
├── data/
│   ├── orders_hourly.csv     # 120 days of order history
│   └── forecast_backtest.csv # every forecast from the backtest
├── layouts/
│   └── main_warehouse.json   # the warehouse map, shelves, items and robots
├── scripts/
│   ├── classes.py            # Warehouse, Robot, Order, Shelf
│   ├── layout.py             # reads the layout file and builds the warehouse
│   ├── orders.py             # makes new orders
│   ├── demand.py             # realistic demand: daily peaks, weekdays, big deliveries
│   ├── history.py            # makes the order history for forecasting
│   ├── forecast.py           # baseline and LightGBM forecasts, backtest, WAPE and MASE
│   ├── m5.py                 # real daily demand from the M5 dataset (Walmart CA_1 FOODS)
│   ├── movement.py           # greedy movement, A* route planning and walking distances
│   ├── simulation.py         # the clock, queue, accidents, charging and KPIs
│   ├── run.py                # run one simulation from the command line
│   ├── scenarios.py          # different situations to test (rush hour, breakdowns, ...)
│   ├── assignment.py         # smart order assignment and charging decisions
│   ├── traffic.py            # cell bookings (reservations) and deadlock detection
│   ├── record.py             # builds scenarios and records them for the replay viewer
│   ├── waits.py              # wait report: every long wait and its reason
│   └── record_tests.py       # records the tests for the test viewer
├── web/
│   ├── session.py            # LiveSession: one running simulation, its commands and messages
│   ├── server.py             # FastAPI server: the page, the WebSocket and the background ticker
│   └── static/
│       └── live.html         # the live website
├── tests/
│   ├── test_classes.py       # rules for robots, orders, shelves, charging, breakdowns
│   ├── test_layout.py        # the layout file and the shelf rows
│   ├── test_simulation.py    # the running simulation and the order generator
│   ├── test_astar.py         # A* route planning
│   ├── test_assignment.py    # smart assignment and charging
│   ├── test_charging.py      # opportunity charging
│   ├── test_traffic.py       # bookings and deadlocks
│   ├── test_session.py       # the live session: commands, messages, same seed = same run
│   ├── test_server.py        # the server and WebSocket (FastAPI TestClient)
│   ├── test_demand.py        # realistic demand
│   ├── test_history.py       # the order history and realistic demand in the simulation
│   ├── test_forecast.py      # features without leakage, scores, the forecast beats the baseline
│   ├── test_waits.py         # the wait report
│   ├── test_m5.py            # M5 data and real demand (with a tiny fake M5 folder)
│   └── conftest.py           # records tests for the test viewer (only when asked)
├── viewer/
│   ├── draw.js               # drawing code shared by the replay viewer and the live website
│   ├── index.html            # replay viewer
│   └── tests.html            # test viewer
├── requirements.txt
└── README.md
```

---

## Author

**Praneeth Chikile** – M.Sc. Data Science and Analytics, SRH University Heidelberg