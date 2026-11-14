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

## FleetMind at a glance

```
   browser (2D map, 3D view)           AI agent (next step)
          |  WebSocket                        |  MCP tools: status, robots, orders,
          v                                   v  pause, aisles, forecast
   +--------------------------------------------------+
   |  live website + MCP server   (web/)              |
   |  fleet manager: the simulation's brain           |
   |  assignment, A* routes with bookings, charging,  |
   |  lost robots, restocking, forecast               |
   +--------------------------------------------------+
          |  VDA 5050 messages over MQTT (Mosquitto in Docker)
          v
   robots: simulated robot programs (scripts/robot_agent.py)
           or robot bodies in the 3D world for Isaac Sim (isaac/)
```

**What is built:**
- **A warehouse that runs by itself:** 40 shelves, chargers, packing stations, 5 robots (up to 20), orders, breakdowns, dropped items, blocked cells. The same seed always gives the same day, so versions can be compared fairly.
- **A fleet brain:** smart order assignment, A* routes where robots book their cells (no collisions, deadlocks solved), opportunity charging, battery safety.
- **Realistic demand and a forecast:** daily peaks and busy weekdays, real Walmart data (M5), and a LightGBM forecast that is 42% more accurate than "same as last week" on whole days.
- **Robots that talk like real robots:** VDA 5050 messages over MQTT. The manager notices a robot that stops answering (lost), plans around it, and takes it back when it returns.
- **A live website:** 2D map and a 3D view of the same warehouse, start/pause, random days, technicians who walk to broken robots, the reason for every blocked cell.
- **Ready for Isaac Sim:** the warehouse as a 3D USD world, and robots whose bodies live in it, using the same messages.
- **Tools for an AI agent:** an MCP server with 8 tools on the live warehouse, with clear errors and risky tools marked.
- **Restocking:** people the robots must find and tell each other about, or restocking robots that book their way.
- **A public demo mode and a container:** one shared warehouse anyone can open, without any tools, AI or keys.
- **An AI supervisor (Claude):** handles incidents with the MCP tools (plan, act, check) and asks a person before risky actions.
- **Lost robots are rescued** by a technician, and **tests run on every push** (GitHub Actions).
- **387 tests**, and every result compared over many seeds.

**Some results:**

| What | Result |
|---|---|
| A* with bookings vs plain A* (10 seeds) | 80 vs 56 orders delivered, 54 vs 112 ticks per delivery, 0 collisions |
| Forecast on real M5 data (whole days) | 6.2% error vs 10.7% for "same as last week" |
| Fleet over real MQTT | exactly the same run as without MQTT, 0 state mismatches |
| A robot dies (killed 90 times at different moments) | the fleet always recovered; before the fixes it got stuck in about half the cases |
| Restocking (30 seeds) | costs about 10 minutes per delivery; people, badges or robots: the difference is within the noise |

**What's next:** put the demo online, try the supervisor on many incidents (an evaluation), cameras and a damaged-box detector. See [What's next](#whats-next).

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
I have **387 tests** in the `tests/` folder. They check the rules, for example:

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
- every message is valid VDA 5050, and a wrong message gives a clear error
- a run over MQTT is exactly the same run, and a robot that dies or goes silent is marked lost
- nobody waits forever for a lost robot, and a lost robot that comes back works again
- the 3D world has every part of the layout, and robots in it give exactly the same run
- every MCP tool works through a real MCP client and gives a clear error for a wrong request
- a paused robot stays put, nobody drives into a closed aisle, a created order gets delivered
- a new random day is different, and Reset repeats it
- robots can't drive into a person, a sighting reroutes the other robots, an old spot is cleared
- the public demo has no tools (404), limits clicks and speed, and runs by itself
- a lost robot is restarted by a technician and works again
- the AI supervisor plans before it acts, never runs a rejected action, and stops at its limits

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

- **Replay viewer** (`viewer/index.html`): plays back a whole day in the warehouse. I can pause, jump to any tick, follow one robot, and see its planned route, battery and what it carries. There are 16 scenarios, like a realistic Friday, rush hour, breakdown day, blocked aisles, low battery, single robot and a 20-robot stress test, plus "old version" scenarios to compare with earlier versions.
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
- **A real working day:** scenarios with realistic demand show the **clock** (1 tick = 1 minute from 06:00) and the date. The day starts behind a closed **roller shutter** that rolls up when I press Start. Orders stop at 22:00, the robots finish every order of the day (see section 17), then the simulation stops by itself, the shutter rolls down and shows the day's summary (delivered, late, not done, on time). Reset opens the day again. Other scenarios run forever. The replay viewer shows the clock too.

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

### 16. The robots talk to the fleet manager over MQTT
Until now the fleet manager moved the robots itself: one Python program changed `robot.position` directly. Real robots are separate machines, so now the manager and the robots only talk with **messages**, over **MQTT**, in the format of **VDA 5050** (the standard many warehouse robots use). Later, a robot in Isaac Sim can speak the same messages, and the manager does not need to change.

**MQTT** (`scripts/bus.py`): nobody talks to anyone directly. Everyone publishes messages to *topics* on a **broker** (Mosquitto, in Docker), and the broker passes each message to whoever subscribed to that topic.

**The messages** (`scripts/vda5050.py`), on topics like `uagv/v2/FleetMind/R1/state`:

| Topic | From → to | What it says |
|---|---|---|
| `order` | manager → robot | a route: nodes (cells) joined by edges, with actions like `pick` or `drop` on the nodes |
| `instantActions` | manager → robot | do it now: `startCharging`, `stopCharging`, `initPosition`, `cancelOrder`, `startPause` |
| `state` | robot → manager | where I am, battery, what I carry, how far I got with my order, errors |
| `connection` | robot → manager | `ONLINE`, `OFFLINE`, or `CONNECTIONBROKEN` (sent by the broker, see below) |
| `fleetmind/sim/clock` | manager → all robots | one tick has passed (only in the simulation) |

**How a route works:** the manager sends the whole planned route, but only **releases** the cells a robot may drive to now. The rest is the **horizon**: the robot knows where it is going, but may not go there yet. Every tick the manager sends an **order update** that releases the next cell. This is how the cell bookings from section 11 become messages.

**The robot** (`scripts/robot_agent.py`) only knows the map and itself: its position, battery, load and order. It does not know the other robots or the queue. Each tick it repairs, does its instant actions, drives the released cells, does the node actions and charges, then sends its `state`.

**The manager** (`scripts/fleet_link.py`) still makes every decision with the same simulation code. After each tick it turns what each robot should do into messages, sends the clock, waits for the states and **compares** them with what it expected. The robot is the truth: if a robot says it is somewhere else or has a different battery, the manager believes it and logs `state_mismatch`.

**Lost robots:**
- Every robot sends its state every tick, and every second when the day is paused. That is its **heartbeat**.
- If the manager hears nothing from a robot for 3 seconds, the robot is marked **lost**.
- If a robot's program dies, it can't say goodbye. So when it connects, it leaves a **last will** with the broker: "CONNECTIONBROKEN". The broker sends it for the robot when the connection breaks.
- A lost robot gets no new work. Its order goes back to the front of the queue. Its cell counts as **blocked**, so other robots drive around it, pick from another spot, or deliver to the other packing station.
- When a lost robot answers again, the manager takes it back. It believes the robot's position and battery and sends `initPosition`, and the robot starts fresh.

**Results:**
- **Same seed, same run:** a healthy robot does exactly what it was told, so a run over MQTT is **exactly** the same as before. A test checks this for 4 scenarios. All 13 scenarios gave 0 state mismatches over 400 ticks.
- Over the real Mosquitto broker, "Normal day" delivers 88 of 90 orders, 0 late, the same as without MQTT. That is 2,222 messages to the robots and 2,630 back.
- If I kill one robot's program, the website shows it as lost after about 3 seconds, and the other 4 robots keep working with 0 collisions.

**Bugs I found by killing robots:**
- A robot whose pick spot or packing station was the lost robot's cell waited there **for the rest of the day**. A broken robot never caused this, because a repair ends. Fix: the lost robot's cell counts as blocked.
- A robot next to the chargers was trapped: its only free neighbour was the lost robot, and robots never drive through chargers or packing stations. Fix: only when a lost robot has closed every other way out, a robot may drive through a station.
- I lost every robot (R1 to R5) at 18 different moments of a normal day. Before the fixes, robots got stuck forever in about half the cases. After the fixes, the fleet always recovered. The worst case is still a wait of 133 ticks, when a robot dies in the narrow corner next to the packing stations.

**Speed:** one tick over Mosquitto takes about 50 ms, so the website can run about 20 ticks per second. Two things made it slow:
- The first version took about 90 ms per tick. VDA 5050 says to use QoS 0 for orders and states (QoS 1 only for `connection`), and QoS 0 is also much faster. I also turned off Nagle's algorithm, which holds back small messages.
- The remaining ~46 ms come from Docker Desktop on Windows: it forwards the port from Windows into its Linux machine. Inside Docker's own network, the same message goes there and back in 0.8 ms.

**Read the conversation as a story** (`python -m scripts.talk`): the scenario "Talking robots" has 2 robots with low batteries and breakdowns, so every kind of message happens early. The script listens to every message and, the first time each kind happens, prints it in plain words with the reason. On purpose, someone swaps R1's battery by hand, and R2's computer crashes and is switched on again. For example:

```
EXAMPLE 7: Picking the item  (tick 41)
  Manager -> R1    ORDER      R1-3 (update 0): stay at (5, 4) and PICK item=flour orderId=O-0001 shelf=C3
  Manager -> all robots   CLOCK      tick 41 has passed: do your tick and report
  R1  -> Manager   STATE      I am at (5, 4), battery 54.5%, carrying flour
  What the manager decided in this tick:
    - R1: picked O-0001 flour from C3
  WHY: The robot stands next to the right shelf. The manager sends a tiny order with one node
       (right here) and a 'pick' action on it. The robot picks and reports the load.
```

The 12 examples: the robots start, a new job (the route), driving (release the next cell), a quiet tick (the heartbeat), start charging, stop charging, picking, delivering, the manager and the robot disagree, a breakdown, a robot disappears, the robot comes back. In 200 ticks that is 333 messages to the robots and 393 back.

### 17. The day ends when the work is done
Before, a realistic day always stopped at 23:00. On a quiet day the robots stood idle until then, and on a busy day orders that came before 22:00 were never delivered.

Now: no new orders after 22:00, but **every order of the day is delivered before the warehouse closes** (`Simulation.day_over`). Only if the robots can't finish by 06:00 the next morning (for example every robot is lost) does the day stop anyway, so it never runs forever. The shutter says when the last order was delivered.

Realistic Friday (159 orders):

| Robots | The day closes at | Delivered |
|---|---|---|
| 5 | **22:24** (before: idle until 23:00) | 159 / 159 |
| 4 | **00:49** (before: stopped at 23:00 with orders left) | 159 / 159 |
| 3 | **04:22** | 159 / 159 |

### 18. Getting ready for Isaac Sim
Isaac Sim (NVIDIA's robot simulator) shows the warehouse in 3D with real physics. The fleet manager does not change: in Isaac Sim the robots are bodies in a 3D world, and they speak the same VDA 5050 messages over MQTT. Everything is explained in [isaac/README.md](isaac/README.md).

- **The 3D world** (`isaac/world.py`): the layout file becomes a USD world (the file format of Isaac Sim): 1 cell = 1 metre, 56 walls, 40 shelves that know their item, chargers, packing stations, docks, lights, a physics scene and 5 robots. It is made with Pixar's `usd-core`, so it does not need Isaac Sim or a GPU.
- **The robots in the world** (`isaac/bridge.py`): a robot does its tick exactly as before, then its body drives to the next cell (turning to face the way it goes), and only when it has arrived does it report, with the position the simulator **measured**. If something pushes it into another cell, it reports that, and the manager believes it.
- **Starting Isaac Sim** (`isaac/run_isaac.py`): opens the world and runs the bridge every frame. `--no-isaac` runs the same bridge without Isaac Sim, so the whole chain (website, manager, MQTT, robots in the world) runs on my laptop too.

**Tests:** robots in the world give exactly the same run (80 ticks, 0 state mismatches), every body ends in its robot's cell, a driving robot faces the way it drives, and a pushed robot is believed.

**What I can't do on my laptop:** it has Intel graphics, and Isaac Sim needs an NVIDIA RTX GPU. So the last step, running `isaac/run_isaac.py` in Isaac Sim itself, needs a cloud GPU machine (NVIDIA Brev or AWS) or a PC with an RTX card.

### 19. Tools for an AI agent (MCP)
Next, an AI agent will help run the fleet. It must not change the simulation's code or variables directly: it gets **tools**, like a person gets buttons. The tools use **MCP** (Model Context Protocol), the standard way for an AI to find tools, read what they do, and call them.

The tools run inside the live website at `/mcp` (`web/mcp_server.py`), so they see and change the **same** warehouse as the browser. A change by a tool appears at once in every open tab (2D and 3D). The logic is in `scripts/fleet_tools.py`, without MCP code, so each tool is easy to test.

| Tool | What it does | Changes the warehouse? |
|---|---|---|
| `get_fleet_status` | time, every robot, orders (waiting, late, delivered), problems (lost, paused, broken robots, blocked cells, closed aisles), the aisle names | no |
| `get_robot` | one robot: cell, task, battery, load, its order and deadline, route, connection, last events | no |
| `create_order` | a new customer order for an item, with a priority and a deadline | yes |
| `pause_robot` / `resume_robot` | stop a robot where it is; an order it has not picked up goes back to the queue, an item it carries stays on it | yes, **risky** |
| `close_aisle` / `reopen_aisle` | close an aisle (e.g. `BC` = between shelf rows B and C); says which shelves can no longer be reached | yes, **risky** |
| `get_forecast` | expected orders per hour for a day (LightGBM), the peak hour, and the hours above the fleet's capacity | no |

**Clear errors**, because an AI reads them and has to fix its own call:
- `get_robot("R9")`: "There is no robot 'R9'. The robots are: R1, R2, R3, R4, R5."
- `create_order("banana")`: "There is no item 'banana' in the warehouse. Did you mean: bananas?"
- `close_aisle("XY")`: the list of all real aisles and the shelves next to them.
- After closing time: "The working day is over and the warehouse is closed."

**Risky tools are marked** (MCP `destructiveHint`), so the agent can later ask a person before it uses them.

**Over MQTT**, a paused robot also gets the real VDA 5050 message `startPause` (and `stopPause` when resumed).

**Tested by hand first:** `python -m scripts.mcp_try` calls every tool on the running website (also with wrong inputs) and prints exactly what an AI would get back. 28 tests call the tools through a real MCP client and then let the simulation run on: a paused robot really stays where it is and its order is delivered by another robot, nobody drives into a closed aisle, a created order gets delivered.

**A bug the live test found:** the robots stayed "lost" for ever after a network problem. The broker had thrown them off (a test was using the same robot names on the same broker), they reconnected by themselves and kept working, but the broker still held their last will "CONNECTIONBROKEN". Now a robot says ONLINE again after every reconnect, and the manager treats any fresh state message as proof that a robot is alive. The broker tests also refuse to run on a broker where a fleet is already running.

### 20. A livelier 3D view, and random days
- **New random day** (button on both pages): the same scenario with a new random seed, so breakdowns, fallen boxes, ordered items and routes are different every time. **Reset** still replays the same day, which is what I need to compare versions fairly.
- **Why is this cell blocked?** Every blocked cell has a reason: a fallen box, a spill, a pallet left in the aisle, a person restocking, a closed aisle (with the reason the agent or a person gave), or a lost robot. The 3D view shows each one as a different object with a label ("Spill, until 07:12"). The reasons come from their own random generator, so the runs did not change (a test checks this).
- **Technicians:** when a robot breaks down, a technician in an orange vest walks from the staff desk (in the loading-dock corner, where robots never drive) through the aisles to the robot, repairs it, and walks back when it works again. The walk takes the first 30% of the repair time.
- **20 robots in the 3D world:** the 3D world has 5 robots. When the manager asks for robots the world doesn't have (R6 to R20 in the stress test), the bridge copies a robot into the world and connects it. Before, they were shown as lost, because nobody was answering for them.

### 21. Restocking: people the robots must find, or robots that book their way
Every day goods arrive at the loading docks and must go onto the shelves. Two people or two restocking robots do this all day: fetch goods at a dock (3 minutes), carry them to the shelf with the lowest stock, restock it (8 minutes), next job. While they work they stand in the aisles, often on the robots' pick spots.

**Restocking by people** (scenario "Restocking by people", `scripts/restock.py`):
- The robots **don't know where the people are**. A person takes up a cell, and a robot can't drive into it, but the planner doesn't know it is there.
- A robot **sees** a person up to 2 cells away (not through a shelf). It **tells every robot**: the person's cell is booked in the shared reservation table, and every robot whose route goes through it plans a new one at once.
- When a robot sees the person **somewhere else**, the spot moves for everyone ("P2 moved from (6, 14) to (7, 14): told every robot"). When a robot looks at an old spot and **nobody is there**, it clears it ("P2 is no longer at (8, 14)").
- A person seen **walking** is avoided for 2 minutes (they will have gone by then), a person **standing** (restocking) for the whole planning window.
- The 2D and 3D views show where the people **really** are, and a red ring "P1?" where the **robots believe** they are. When the two differ, nobody has seen the person since they walked on.

**People with location badges** (`people_tracking="badges"`): the same, but people wear a location tag (like UWB badges in real warehouses), so the fleet always knows where they are.

**Restocking by robots** (scenario "Restocking by robots"): two restocking robots do the same jobs. They are part of the fleet: they **book their route** like every robot, so nobody has to find them.

**Results** (`python -m scripts.compare_restock`): Normal day, 500 ticks, 30 seeds, average +/- standard error:

| | Delivered | Avg delivery (ticks) | Late | Reroutes for people | Surprise stops | Collisions |
|---|---|---|---|---|---|---|
| No restocking | 87.6 +/-0.9 | 38.6 +/-2.9 | 3.9 +/-1.6 | – | – | 0 |
| People, robots find them | 82.1 +/-0.9 | 48.0 +/-3.1 | 5.3 +/-1.5 | 28.5 | 1.8 | 0 |
| People with badges | 83.3 +/-0.9 | 46.5 +/-3.4 | 5.8 +/-2.5 | 34.0 | 5.9 | 0 |
| Restocking robots | 84.3 +/-0.9 | 48.1 +/-3.4 | 5.5 +/-2.2 | – | 4.0 | 0 |

- **Restocking costs the fleet about 10 minutes per delivery and 3–5 orders a day**, because someone stands in the aisles.
- **Who restocks matters much less than I expected:** the differences between people, badges and robots are smaller than the uncertainty (+/-3 ticks). The cost comes from the space they use, not from how the robots learn where they are.
- **Robots find people in time:** under 2 surprise stops per day (a person stepped right in front of a robot), and never a collision.

**What I got wrong first:**
- With 10 seeds, people with badges looked much **slower** than people the robots had to find (56 vs 50 ticks). I first guessed it was the 16-minute booking of a walking person's cell. I tested that: it was not it.
- The real cause: a robot **chose a new pick spot** whenever a known person stood on its spot. With badges the robots know every step of every person, so a person walking past a pick spot made the robot switch spots again and again. Now only a person who is **standing** makes a robot pick from another side. Badges went from 52.4 to 46.5 ticks over 30 seeds.
- And with 30 seeds instead of 10, most of the remaining "differences" disappeared into the noise. The lesson: show the +/-, and use enough seeds before believing a difference.

### 22. A public demo anyone can open
The goal: anyone opens a link and sees the warehouse working, in 2D and 3D. But they only **see my work**: they can't use my tools, my AI or my keys.

`python -m web.server --public` (and the `Dockerfile`, which runs exactly that):
- **One shared warehouse.** Everyone who opens the link sees the same day and can press Start, Pause, pick a scenario or start a random day. A banner says it is a public demo, with a link to this repository.
- **No tools, no AI, no keys.** The MCP server (`/mcp`) is not there at all in public mode (it answers 404), the API docs are hidden, and nothing in public mode reads an API key. The container runs as a normal user and never contains `.env` files.
- **Limits:** at most 100 open tabs, 3 commands per second per tab ("Slow down a little"), speed at most 30 ticks per second.
- **It runs by itself:** it starts with Realistic Friday, and when a day is over the summary shows for 15 seconds, then a new random day starts. While nobody is watching, nothing is computed, so it costs almost nothing.
- **Tested:** `/mcp` gives 404, the demo starts by itself, the click limit and the speed cap work, and the normal website still has its tools.
- **Checked in the container:** `docker build` and `docker run`, then a visitor's connection: the banner is on, the day runs by itself, speed 60 becomes 30, `/mcp` is 404.

The robots in the demo still talk VDA 5050 messages, over the in-memory bus, because most free hosts allow only one container (no separate broker).

### 23. Lost robots are rescued
Before, a lost robot stayed lost until someone restarted its program by hand. Now a **technician walks to where it was last seen and restarts it** (25 minutes: walking and restarting), and the robot rejoins the fleet. The 3D view shows the technician ("restarts R3").
- New scenario **Connection trouble**: now and then a robot's connection drops. Over a day, 8 robots were lost, 8 were restarted and 8 came back, with 0 collisions.
- A robot program that runs in someone's own terminal can't be restarted from here: the technician reports it ("restart its robot program by hand").

### 24. The AI supervisor
An AI agent (Claude) that handles incidents with the fleet's MCP tools, and asks a person before anything risky.

- **What starts it:** a request typed in the chat on the website ("Prepare for a big delivery at 2 pm"), or an **incident** the fleet finds by itself: a robot lost, two or more robots broken at the same time, or 5 or more orders waiting past their deadline. The same incident doesn't start it again for 120 minutes.
- **How it works** (`scripts/agent.py`):
  1. **Plan:** it looks at the fleet with the read-only tools and writes a short plan. In this step it can't change anything.
  2. **Act:** it carries out the plan with the tools. A **risky tool** (pause a robot, close an aisle) is only run after a person clicks **Approve** on the website. If the person clicks **Reject**, the agent is told why and must find another way.
  3. **Check:** a separate reviewer looks at the fleet afterwards: is the problem solved? If not, the agent tries again (at most 2 more rounds).
- **Limits:** at most 12 tool calls per task, it stops after 3 failed tool calls, and the cost of every task is counted and shown.
- **On the website** (`web/agent_desk.py`): an "AI supervisor" card shows every step (task, plan, tool calls, check, cost), a box to type requests, and the Approve / Reject buttons with the agent's reason.
- **The model:** Claude (`claude-opus-5-5`) through the official Anthropic SDK, with server-side fallbacks (if the model declines a request, another one continues). Tests use a **scripted model** instead: fixed answers, no API key, no cost. They check the agent's rules: it plans before it acts, a rejected action never runs, the planning step can't change anything, the limits stop it, a failed check makes it try again, and an incident starts it.
- **Never in the public demo:** there is no agent and no `/mcp` in `--public`, so nobody else can use my API key.

### 25. Tests on every push, and the demo ready to go online
- **GitHub Actions** (`.github/workflows/tests.yml`) runs every test on every push, builds the public demo container and checks that it starts without tools. No secrets are needed.
- **Hugging Face Spaces:** `python -m scripts.make_space` writes `dist/hf-space/`, everything a free Docker Space needs (checked: it builds and runs). The steps are below in "Put the public demo online".

### How much can I trust this simulator?
Real robots fail in many more ways than my simulation has, so I don't use it to predict exact numbers. I use it to compare ideas and to test software.

**What it leaves out:**
- Robots move one cell per tick on a grid. No speeds, acceleration, turning or wheel slip.
- Breakdowns are one random chance per tick with a fixed 30-minute repair. Real robots have many kinds of faults (sensors, lost localisation, motors, software) that happen more after hard use, and repairs take different times.
- The battery goes down linearly. Real batteries age and drop faster near empty.
- No people walking around (except as blocked cells), no network delays, and robots always know exactly where they are.
- The demand is made up, or real M5 daily totals spread over my own hourly curve.

**Why it is still useful:**
- **Comparing versions:** the same seeds give the same days, so "A* with bookings vs plain A*" or "opportunity vs smart charging" is a fair test. I compare over 10 seeds, not one, because one seed can be lucky. A better idea in the simulator is a good candidate, not a proven win.
- **Testing software, not robots:** the fleet manager, the MQTT messages, the lost-robot handling, the MCP tools and later the AI agent are real software. The simulator throws problems at them (breakdowns, blocked aisles, robots that disappear) and the tests check that the software reacts correctly. That part transfers to real robots, because real robots speak the same VDA 5050 messages.
- **Finding bugs before a real warehouse does:** killing robots in the simulator found robots that waited for ever behind a lost robot, and the live test found robots that stayed "lost" after a reconnect.

**How to make it more trustworthy:**
1. Use real numbers where they exist: failure logs (time between failures, repair times), measured driving speeds, real order data.
2. Check it against something more real: Isaac Sim with physics, then a small real robot.
3. Measure how much a result depends on the settings: if smart charging only wins when breakdowns are rare, say so.
4. Add the failure types that matter most: localisation loss, slow robots, network delays, partial breakdowns.

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

**The manager predicts, the robot confirms.**
The manager still runs the whole simulation and sends each robot what to do. The robot does it and reports back. If they disagree, the robot wins. So a healthy run stays exactly the same as before, and a robot that does something else (later: physics in Isaac Sim) is still handled.

**The simulation has a clock topic.**
In a simulation all robots must do one tick together, so the manager sends `fleetmind/sim/clock` and waits for the states. A real robot or Isaac Sim has its own time, so this is the only message that is not VDA 5050.

**Accidents are messages too.**
Breakdowns, dropped items and battery swaps are made up by the simulation, so the manager tells the robot about them with instant actions that start with `sim` (`simBreakdown`, ...). In Isaac Sim, physics or staff would cause them, and these messages would go away.

**A day ends when the last order is delivered, not at a fixed time.**
A real warehouse doesn't send customers' orders home undelivered at 23:00. The safety stop at 06:00 the next morning is only there so a broken day can't run forever.

**The robot bodies move by setting their position, not with physics yet.**
That uses only plain USD, so the same code runs in Isaac Sim and in my tests without a GPU. A real wheeled robot (with wheels, a controller and a lidar) is the next step once Isaac Sim runs.

**The AI agent only gets tools, never direct access.**
Every change goes through the same checks as a person's: a robot that is lost can't be paused, a closed day can't get orders, a wrong name gets a helpful error. And the browser shows every change, so a person always sees what the agent did.

**Tests use an in-memory broker.**
It has the same interface as the real one, so 55 tests run in seconds without Docker. Two more tests use the real Mosquitto, and are skipped when it is not running.


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

### Run the fleet over MQTT

Start the broker once (needs Docker Desktop):

```powershell
docker compose up -d
```

Then:

```powershell
python -m web.server --mqtt                            # the website; the 5 robots run inside the server
python -m scripts.fleet_link --broker 127.0.0.1:1883   # a whole scenario without the website
python -m scripts.fleet_link                           # the same over the in-memory bus (no Docker needed)
```

To read what the manager and the robots say to each other, and why:

```powershell
python -m scripts.talk            # add --json to see the real messages
```

To watch the raw messages of the running website: `docker exec fleetmind-mosquitto mosquitto_sub -t "#" -v`

To kill a robot and watch it get lost, run the robots in their own programs:

```powershell
python -m web.server --mqtt external              # terminal 1: the manager and the website
python -m scripts.robot_agent R1 R2 R3 R4         # terminal 2
python -m scripts.robot_agent R5                  # terminal 3: press Ctrl+C (goes OFFLINE) or close the window
```

Only run one fleet on a broker at a time: every robot connects with its own id (R1, R2, ...), and a second R1 throws the first one out.
`docker compose down` stops the broker.

### Build the 3D world for Isaac Sim

```powershell
python -m isaac.world                        # writes isaac/warehouse.usda
python isaac/run_isaac.py --no-isaac         # robots in the 3D world, without Isaac Sim (with the website: --mqtt external --state-timeout 3)
```

To run it in Isaac Sim itself (needs an NVIDIA RTX GPU), follow [isaac/README.md](isaac/README.md).

### Try the AI agent's tools (MCP)

```powershell
python -m web.server                                   # the website, with the tools at /mcp
python -m scripts.mcp_try                              # in another terminal: every tool once
python -m scripts.mcp_try list
python -m scripts.mcp_try call close_aisle aisle=BC "reason=a box fell"
```

### Run the public demo

```powershell
python -m web.server --public --mqtt memory          # the public demo on your computer
docker build -t fleetmind-demo .                      # or as a container
docker run --rm -p 8000:8000 fleetmind-demo           # then open http://127.0.0.1:8000
```

To put it online, any host that runs a Docker image works, e.g. Hugging Face Spaces (Docker), Google Cloud Run or Fly.io. The host sets `PORT`; the container reads it. Set a budget alert first.

### Run the AI supervisor (needs an Anthropic API key)

1. Get a key at console.anthropic.com (API keys). Set a monthly spending limit there first.
2. Put it in a file called `.env` in the fleetmind folder (git and Docker ignore this file):
   ```
   ANTHROPIC_API_KEY=sk-ant-...
   ```
3. Start the website with the supervisor:
   ```powershell
   python -m web.server --mqtt memory --agent --scenario connection_trouble
   ```
4. Press Start. When a robot is lost, the supervisor starts by itself; or type a request in the "AI supervisor" card. Approve or reject what it asks.

### Put the public demo online (Hugging Face Spaces, free)

1. Make an account at huggingface.co.
2. Create a new Space: **Docker**, template **Blank**, visibility **Public**, name `fleetmind`.
3. Create an access token with **write** access (Settings, Access Tokens). Keep it secret.
4. Prepare the files and push them (use your Hugging Face user name; the password is the token):
   ```powershell
   python -m scripts.make_space
   cd dist/hf-space
   git init
   git add .
   git commit -m "FleetMind public demo"
   git remote add origin https://huggingface.co/spaces/<your-user-name>/fleetmind
   git push -u origin main --force
   ```
5. Wait for the build (a few minutes), then open `https://<your-user-name>-fleetmind.hf.space` (the 3D view is at `/3d`).

The free Space sleeps when nobody visits for a while and wakes up on the next visit. It has no API key and no AI.

### Compare restocking by people and by robots

```powershell
python -m scripts.compare_restock               # 30 seeds, a few minutes
```

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
│   ├── vda5050.py            # VDA 5050-style messages: build them and check them
│   ├── bus.py                # MQTT: an in-memory broker for tests and the real one (paho-mqtt)
│   ├── robot_agent.py        # a robot that only follows messages and reports its state
│   ├── fleet_link.py         # the manager over MQTT: orders out, states in, lost robots
│   ├── talk.py               # the message story: what they say to each other, and why
│   ├── fleet_tools.py        # the AI agent's tools: status, robots, orders, pause, aisles, forecast
│   ├── mcp_try.py            # try the tools by hand from an MCP client
│   ├── restock.py            # restocking by people (robots find them and tell each other) or robots
│   ├── compare_restock.py    # who should restock: 4 ways over 30 seeds
│   ├── agent.py              # the AI supervisor: plan, act (with approval), check
│   ├── make_space.py         # prepare the public demo for Hugging Face Spaces
│   └── record_tests.py       # records the tests for the test viewer
├── isaac/
│   ├── world.py              # the warehouse as a 3D USD world for Isaac Sim
│   ├── bridge.py             # robots whose bodies are in the 3D world
│   ├── run_isaac.py          # starts Isaac Sim with the world and the robots
│   └── README.md             # how to run it on a GPU machine
├── web/
│   ├── session.py            # LiveSession: one running simulation, its commands and messages
│   ├── mcp_server.py         # the MCP server at /mcp (the tools for an AI agent)
│   ├── agent_desk.py         # the AI supervisor on the website: chat, Approve/Reject, incidents
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
│   ├── test_messages.py      # VDA 5050 messages and the in-memory broker
│   ├── test_fleet_link.py    # robots and manager over messages, lost and found robots
│   ├── test_mqtt_broker.py   # the same over a real Mosquitto (skipped without it)
│   ├── test_isaac_world.py   # the 3D world and the robots in it (no GPU needed)
│   ├── test_mcp_tools.py     # every MCP tool through a real MCP client
│   ├── test_restock.py       # people block robots, sightings reroute, stale spots, same run over MQTT
│   ├── test_public.py        # the public demo: no tools, limits, runs by itself
│   ├── test_rescue.py        # lost robots are restarted by a technician
│   ├── test_agent.py         # the AI supervisor with a scripted model (no API key)
│   └── conftest.py           # records tests for the test viewer (only when asked)
├── viewer/
│   ├── draw.js               # drawing code shared by the replay viewer and the live website
│   ├── index.html            # replay viewer
│   └── tests.html            # test viewer
├── mosquitto/
│   └── mosquitto.conf        # the MQTT broker's settings
├── docker-compose.yml        # starts Mosquitto
├── Dockerfile                # the public demo as one container
├── .github/workflows/tests.yml  # all tests and the demo container on every push
├── requirements.txt
└── README.md
```

---

## What's next

1. **Put the demo online** (the steps are above) and record a 2–3 minute demo video.
2. **Evaluate the supervisor:** 10 to 50 incident scenarios with the expected outcome; measure how often it succeeds, how many steps and how much it costs. Done with my own API key, never in the public demo.
3. **Eyes:** simulated camera images of an incident for a vision model, and a small classifier for damaged boxes.
4. **Production:** tracing of every agent step (e.g. Langfuse), MLflow for runs and results. The AI agent stays OFF in the public demo, so nobody can use my API key.
5. **Isaac Sim on a GPU machine:** run `isaac/run_isaac.py` in Isaac Sim, then a real wheeled robot model with a lidar.

---

## Author

**Praneeth Chikile** – M.Sc. Data Science and Analytics, SRH University Heidelberg