# Project structure

Back to the [README](../README.md).

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
│   ├── agent.py              # the AI manager: plan, act (with approval), check
│   ├── compare_shift_lead.py # no Shift Lead vs rules vs rules + LLM, 30 seeds
│   ├── shift_lead/
│   │   ├── staff.py          # cleaners, technicians and work tickets
│   │   ├── playbooks.py      # one rule per known incident, only through the tools
│   │   ├── lead.py           # finds incidents every tick, decides, escalates to the LLM
│   │   ├── caselog.py        # every incident and how well each option worked
│   │   ├── plan.py           # the morning shift plan from the forecast
│   │   └── report.py         # the end-of-day report
│   └── make_space.py         # prepare the public demo for Hugging Face Spaces
├── isaac/
│   ├── world.py              # the warehouse as a 3D USD world for Isaac Sim
│   ├── bridge.py             # robots whose bodies are in the 3D world
│   ├── run_isaac.py          # starts Isaac Sim with the world and the robots
│   ├── export.py             # reads the 3D world back as plain data for the browser
│   └── README.md             # how to run it on a GPU machine
├── web/
│   ├── session.py            # LiveSession: one running simulation, its commands and messages
│   ├── mcp_server.py         # the MCP server at /mcp (the tools for an AI agent)
│   ├── agent_desk.py         # the AI supervisor on the website: chat, Approve/Reject, incidents
│   ├── server.py             # FastAPI server: the page, the WebSocket and the background ticker
│   └── static/
│       ├── live.html         # the dashboard: KPIs, the 2D map, the Shift Lead panel
│       └── world3d.html      # the 3D view (/3d)
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
│   ├── test_free_ai.py       # the free AI adapter, with a fake chat server
│   ├── test_staff.py         # cleaners, technicians, tickets
│   ├── test_playbooks.py     # each playbook, only through the tools
│   ├── test_escalation.py    # when the LLM is asked (ScriptedModel)
│   ├── test_caselog.py       # the case log and the lookup
│   ├── test_shift_plan.py    # the morning plan and its one Approve
│   ├── test_day_report.py    # the end-of-day report
│   └── test_public_shift_lead.py  # the public demo: rules only, no LLM, no key
├── viewer/
│   ├── draw.js               # drawing code shared by the replay viewer and the live website
│   └── index.html            # replay viewer
├── deploy/
│   ├── render-netlify.md     # the demo on Render, shown on a Netlify website
│   └── selfhost/             # the demo on your own server with Caddy
├── docs/                     # how to run it, details, decisions, this file
├── mosquitto/
│   └── mosquitto.conf        # the MQTT broker's settings
├── docker-compose.yml        # starts Mosquitto
├── Dockerfile                # the public demo as one container
├── render.yaml               # the Render Blueprint for the demo
├── .env.example              # the AI settings to copy into .env
├── .github/workflows/tests.yml  # all tests and the demo container on every push
├── requirements.txt
└── README.md
```
