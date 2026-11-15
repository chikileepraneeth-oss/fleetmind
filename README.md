# FleetMind

Live demo: https://fleetmind-demo.onrender.com/ (the 3D view is at [/3d](https://fleetmind-demo.onrender.com/3d)). It runs on Render's free plan, so the first visit can take about a minute while it wakes up.

FleetMind is a warehouse full of robots that I simulate in Python, with one central brain that decides which robot takes which order, which way it drives and when it charges. The robots talk to the brain with the same VDA 5050 messages that real warehouse robots use. An AI supervisor handles incidents with tools and asks a person before it does anything risky.

## Why I built this

In a warehouse with many robots, a lot goes wrong. Two robots go for the same order while others do nothing, robots block each other in narrow aisles, many of them run out of battery at the same time, and urgent orders wait behind normal ones. I wanted to build the brain that prevents this, step by step, to learn how robotics companies build real systems.

It also connects to my Master's thesis, which was about giving resources to tasks when the demand is uncertain. Here the resources are robots, and the uncertain demand is the orders.

## Architecture

```
   browser (2D map, 3D view)           AI supervisor
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

## Results

| What I compared | Result |
|---|---|
| A* with bookings vs plain A* (10 seeds) | 80 vs 56 orders delivered, 54 vs 112 ticks per delivery, 0 collisions |
| Forecast on real M5 data, whole days | 6.2% daily WAPE, against 10.7% for "same as last week" |
| The fleet over real MQTT | exactly the same run as without MQTT, 0 state mismatches |
| A robot dies (90 times, at different moments) | the fleet always recovered; before my fixes it got stuck in about half the cases |
| Restocking by people, badges or robots (30 seeds) | costs about 10 minutes per delivery; the difference between them is within the noise |

All tables and the details behind them are in [docs/details.md](docs/details.md).

## How I built it

I built it in this order. Every new version is a setting, and the old one stays, so I can compare them on the same seeds.

### 1. The warehouse
I started with the main classes in `scripts/classes.py`: the warehouse grid, robots, orders and shelves. Every action returns whether it worked and why, like `(False, "wall")`, which made bugs easy to find. The warehouse is a text map in `layouts/main_warehouse.json`: 13 × 17 cells, 40 shelves in 8 rows, 3 chargers, 2 packing stations and 3 loading docks. At first I had big 3 × 5 shelf blocks, but no robot could reach the middle cells, so I changed to rows that are 1 cell tall with an aisle on both sides.

### 2. Charging and breakdowns
Robots charge on a charger, 5% per tick, and they can break down. A broken robot can't move or charge until it is repaired, and if it was carrying an item, it keeps it and finishes the job afterwards.

### 3. The simulation
At first I moved every robot by hand. Then I made the warehouse run by itself in ticks: orders arrive at random, robots break down, drop items or find a cell blocked, and idle robots go to charge or back home. Everything random comes from one seed, so the same seed always gives the same day. This is what makes every comparison in this project fair.

### 4. Route planning
First the robots moved greedily, always one step closer to the goal. When a shelf row was between a robot and its goal, it wobbled left and right forever and never got there. Now every robot plans its whole route with A* before it moves.

### 5. The replay viewer
To see what the robots do, I built a replay viewer (`viewer/index.html`) that plays a recorded day in the browser. It has 16 scenarios, like a realistic Friday, rush hour, breakdown day and a 20-robot stress test.

### 6. Smarter assignment
Before, the first free robot took the oldest order, even if it was far away. Now the most urgent order goes first, to the robot that can reach the shelf fastest, and only if its battery is enough for the trip and the drive to a charger afterwards. A test checks that over 10 seeds this gives a shorter average delivery time.

### 7. Traffic
With plain A*, every robot planned its route alone, as if it was the only robot, so robots met in the narrow aisles and waited. I made each robot book the cells it will use, tick by tick, so the next robot plans around those bookings or plans to wait. If robots wait on each other in a circle, the least important one gives way. Later the wait report showed a robot kept waiting even after the way was free, and fixing that took on-time orders on "Realistic Friday" from 84% to 93% (`scripts/traffic.py`).

### 8. A live website
The replay could only play old runs, so I put the simulation on a FastAPI server that sends the state to the browser over a WebSocket after every tick. All open tabs see and control the same warehouse, like a screen in a control room. Realistic days show a clock (1 tick = 1 minute from 06:00) and start behind a roller shutter that rolls up when I press Start.

### 9. Opportunity charging
Before, a robot on a charger could not take orders, so idle robots only topped up below 70%. Now an idle robot under 90% tops up when no orders are waiting, and it can still leave the charger for an order if it has at least 60%. It helps most on a low-battery day: 94.5% on time instead of 87.4%.

### 10. Demand and a forecast
Orders used to arrive at the same rate all day, so I made demand with daily peaks, busy weekdays and surprise big deliveries (`scripts/demand.py`). Then I forecast every hour of the next day with LightGBM, using only days before the one it predicts. Made-up demand only has the patterns I put in, so I also tested on real Walmart data from M5 (store CA_1, FOODS, 1,941 days). There the daily error went from 10.7% for "same as last week" to 6.2%.

### 11. A fair on-time rate
My on-time rate only counted delivered orders, so a fleet that left its hardest orders waiting looked better than it was. Now an order still waiting after its deadline counts as late. In "Rush hour" this changed the on-time rate from about 30% to 11%.

### 12. Robots that talk over MQTT
Real robots are separate machines, so the manager and the robots now only talk with VDA 5050 messages over MQTT. A robot that is silent for 3 seconds is marked lost, the others drive around its cell, and its order goes back to the queue. When I killed robots at 18 different moments of a normal day, other robots waited forever behind the lost robot in about half the cases. After the fixes the fleet always recovered.

### 13. The day ends when the work is done
A realistic day used to stop at 23:00, even with orders left. Now orders stop at 22:00, but the day only ends when every order is delivered, with a safety stop at 06:00 the next morning. With 5 robots, Realistic Friday closes at 22:24 with all 159 orders delivered.

### 14. Getting ready for Isaac Sim
I turned the layout into a 3D USD world for Isaac Sim (1 cell = 1 metre) with `usd-core`, so it doesn't need a GPU. Robot bodies in this world speak the same messages and give exactly the same run. My laptop has Intel graphics, so running it inside Isaac Sim itself still needs an NVIDIA RTX GPU. See [isaac/README.md](isaac/README.md).

### 15. Tools for an AI agent
An AI should not change the simulation directly, so it gets tools, like a person gets buttons. The MCP server at `/mcp` has 8 tools for the fleet status, robots, orders, pausing robots, closing aisles and the forecast, and the risky ones are marked. The errors are written so an AI can fix its own call, for example "There is no item 'banana' in the warehouse. Did you mean: bananas?". The live test also found a bug: robots stayed "lost" forever after a reconnect, because the broker still held their last will.

### 16. A livelier 3D view
I added a "New random day" button, a reason for every blocked cell (a spill, a fallen box, a closed aisle and more), and technicians who walk to broken robots in the 3D view. Reset still replays the same day.

### 17. Restocking
People restock the shelves all day and stand in the robots' way. I tried three versions: the robots must find the people and tell each other, the people wear location badges, or restocking robots book their route like every robot. With 10 seeds the badges looked much slower, and I found a real bug behind it (robots switched pick spots every time a person walked past). But with 30 seeds most of the other differences disappeared into the noise, so now I show the +/- and use enough seeds before I believe a difference.

### 18. A public demo
I wanted anyone to open a link and see the warehouse, without using my tools or my keys. `--public` runs one shared warehouse with no `/mcp`, limits on clicks and speed, and a new random day after each day ends. It runs as one Docker container on Render.

### 19. Lost robots are rescued
Before, a lost robot stayed lost until someone restarted it by hand. Now a technician walks to where it was last seen and restarts it. In the "Connection trouble" scenario, 8 robots were lost in one day and all 8 came back.

### 20. The AI supervisor
The supervisor (`scripts/agent.py`) handles incidents with the MCP tools. It plans with read-only tools, acts, and then a separate check looks at whether the problem is solved. A risky action, like closing an aisle, only runs after a person clicks Approve on the website. The tests use a scripted model, so they need no key and cost nothing.

### 21. A free AI option
Claude costs money for every incident, and I don't know how many people will open the demo. So the supervisor also works with free AI services (Groq, Gemini, OpenRouter) and with Ollama on my own computer. In the public demo, visitors press a button like "A spill blocks an aisle" and watch the supervisor handle it, with a daily limit and one task every 2 minutes per visitor. Sending tool results as compact JSON brought one incident from about 15,000 to about 12,000 input tokens.

### 22. Tests on every push
GitHub Actions runs all tests on every push, and checks that the demo container starts without the tools.

## How much can I trust this simulator?

Real robots fail in many more ways than my simulator, so I don't use it to predict exact numbers. Robots move one cell per tick on a grid, a breakdown is a random chance with a fixed 30-minute repair, the battery drains linearly, and there are no network delays.

I use it for two things. First, to compare ideas fairly: the same seeds give the same days, and I compare over many seeds, so a better idea here is a good candidate, not a proven win. Second, to test software: the fleet manager, the messages, the lost-robot handling and the AI supervisor are real software, and the simulator throws problems at them. To trust it more, I need real failure logs and driving speeds, and a check against Isaac Sim with physics and then a small real robot.

## Setup

```powershell
git clone https://github.com/chikileepraneeth-oss/fleetmind.git
cd fleetmind
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

The commands I use most:

```powershell
python -m pytest                                       # all tests
python -m scripts.run --seed 42 --ticks 500 --show-map # one simulation in the terminal
python -m web.server                                   # the live website at http://127.0.0.1:8000
docker compose up -d                                   # start the MQTT broker (needs Docker Desktop)
python -m web.server --mqtt                            # the website, with the robots over MQTT
python -m web.server --public --mqtt memory            # the public demo on my computer
```

Everything else (the AI supervisor with a free or paid AI, the forecast and the M5 data, Isaac Sim, putting the demo online on Hugging Face, Render or Netlify) is in [docs/how-to-run.md](docs/how-to-run.md).

## Folders

```
layouts/   the warehouse map, shelves, items and robots
scripts/   the simulation, planning, forecast, MQTT robots and manager, MCP tools, AI supervisor
web/       the live website, the WebSocket, the MCP server and the supervisor's desk
viewer/    the replay viewer and the drawing code shared with the website
isaac/     the 3D world for Isaac Sim and the robots in it
tests/     all tests
data/      the order history and forecasts
deploy/    Render, Netlify and self-hosting
docs/      how to run it, details, design decisions, the full file tree
```

The full file tree is in [docs/project-structure.md](docs/project-structure.md), and why I made the main choices is in [docs/design-decisions.md](docs/design-decisions.md).

## Tests

There are 411 tests, and GitHub Actions runs them on every push.

## What's next

- Record a short demo video.
- Evaluate the supervisor on 10 to 50 incident scenarios with the expected outcome: how often it succeeds, how many steps it needs and what it costs.
- Simulated camera images of an incident for a vision model, and a small classifier for damaged boxes.
- Run `isaac/run_isaac.py` in Isaac Sim on a GPU machine, then a real wheeled robot model with a lidar.

## Author

Praneeth Chikile, M.Sc. Data Science and Analytics, SRH University Heidelberg
