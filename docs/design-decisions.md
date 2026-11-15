# Decisions I made and why

Back to the [README](../README.md).

Shelves are in rows with aisles in between.
At first I had big 3 × 5 shelf blocks, but the middle cells were closed in on all sides and no robot could reach them. So I changed to rows that are only 1 cell tall, with an aisle above and below each row. Now every shelf can be reached, and each shelf holds one item, like in a real warehouse.

The warehouse is in a file, not in code.
Every test and every run uses the same warehouse, so comparisons are fair. And I can change the layout easily.

Robots plan the whole route instead of only stepping closer.
Greedy movement wobbles forever when a shelf row is in the way. A* plans the full route around it first.

Idle robots go back home.
Robots that finished a job used to stay where they were, often in a narrow aisle or next to a packing station, and blocked other robots.

A robot charges on any charger it is standing on.
Once, a robot ran out of battery while standing on a charger, because it was going to a different one.

Robots book their cells before they drive.
When every robot planned alone, robots met in the aisles and lost time waiting and replanning. Booking cells tick by tick lets robots plan around each other before they meet.

Every new version is a setting, and the old one stays.
`movement`, `assignment` and `charging` in `SimConfig` keep the old versions, and there are scenarios for them. So I can always compare the new version with the old one on the same seeds.

I compare over 10 seeds, not one.
One seed can be lucky or unlucky. For example, on seed 42 opportunity charging delivers fewer orders than smart charging, but over 10 seeds it delivers more.

One shared simulation for the live website.
All browser tabs show and control the same warehouse, like a control room screen. The buttons only send commands, and every tab shows what the server really has.

Opportunity charging is the default, even though the 20-robot test got a little worse.
A warehouse this size would not buy 20 robots, because robots are expensive. So the normal 5-robot days decided.

1 tick = 1 minute, open 06:00–22:00.
A forecast of "orders per hour" needs real clock time. With 1 minute per tick, the deadlines become realistic too: 1 hour for urgent orders.

The forecast is made the evening before, for every hour of the next day.
That is when a warehouse plans charging and robot positions. It also means every feature must be at least 1 day old.

I test the forecast on real data too.
Made-up demand contains exactly the patterns I put in, so a forecast can look better than it would in real life. The real M5 data has holidays, growth and surprises I did not design.

I compare the forecast with the perfect average.
Without it, a WAPE of 30% looks bad. With it, I can see that most of the error is pure chance that no forecast can remove.

The manager predicts, the robot confirms.
The manager still runs the whole simulation and sends each robot what to do. The robot does it and reports back. If they disagree, the robot wins. So a healthy run stays exactly the same as before, and a robot that does something else (later: physics in Isaac Sim) is still handled.

The simulation has a clock topic.
In a simulation all robots must do one tick together, so the manager sends `fleetmind/sim/clock` and waits for the states. A real robot or Isaac Sim has its own time, so this is the only message that is not VDA 5050.

Accidents are messages too.
Breakdowns, dropped items and battery swaps are made up by the simulation, so the manager tells the robot about them with instant actions that start with `sim` (`simBreakdown`, ...). In Isaac Sim, physics or staff would cause them, and these messages would go away.

A day ends when the last order is delivered, not at a fixed time.
A real warehouse doesn't send customers' orders home undelivered at 23:00. The safety stop at 06:00 the next morning is only there so a broken day can't run forever.

The robot bodies move by setting their position, not with physics yet.
That uses only plain USD, so the same code runs in Isaac Sim and in my tests without a GPU. A real wheeled robot (with wheels, a controller and a lidar) is the next step once Isaac Sim runs.

The AI agent only gets tools, never direct access.
Every change goes through the same checks as a person's: a robot that is lost can't be paused, a closed day can't get orders, a wrong name gets a helpful error. And the browser shows every change, so a person always sees what the agent did.

Tests use an in-memory broker.
It has the same interface as the real one, so 55 tests run in seconds without Docker. Two more tests use the real Mosquitto, and are skipped when it is not running.
