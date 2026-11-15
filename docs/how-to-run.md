# How to run FleetMind

Back to the [README](../README.md).

## Setup (Windows)

```powershell
git clone https://github.com/chikileepraneeth-oss/fleetmind.git
cd fleetmind
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## See the warehouse

```powershell
python -m scripts.layout
```

## Run a small demo (one robot delivers apples)

```powershell
python -m scripts.classes
```

## Run the tests

```powershell
python -m pytest
```

## Run one simulation

```powershell
python -m scripts.run --seed 42 --ticks 500 --show-map
```

## Watch the fleet live

```powershell
python -m web.server
```

This starts the server and opens http://127.0.0.1:8000 in your browser. Press Start. Stop the server with `Ctrl+C`.
Use `--no-browser` to not open the browser, or `--port 8080` for another port.

## Run the fleet over MQTT

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

## Build the 3D world for Isaac Sim

```powershell
python -m isaac.world                        # writes isaac/warehouse.usda
python isaac/run_isaac.py --no-isaac         # robots in the 3D world, without Isaac Sim (with the website: --mqtt external --state-timeout 3)
```

To run it in Isaac Sim itself (needs an NVIDIA RTX GPU), follow [isaac/README.md](../isaac/README.md).

## Try the AI agent's tools (MCP)

```powershell
python -m web.server                                   # the website, with the tools at /mcp
python -m scripts.mcp_try                              # in another terminal: every tool once
python -m scripts.mcp_try list
python -m scripts.mcp_try call close_aisle aisle=BC "reason=a box fell"
```

## Run the public demo

```powershell
python -m web.server --public --mqtt memory          # the public demo on your computer
docker build -t fleetmind-demo .                      # or as a container
docker run --rm -p 8000:8000 fleetmind-demo           # then open http://127.0.0.1:8000
```

To put it online, any host that runs a Docker image works, e.g. Hugging Face Spaces (Docker), Google Cloud Run or Fly.io. The host sets `PORT`; the container reads it. Set a budget alert first.

## Run the AI supervisor with Claude (paid)

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

## Run the AI supervisor for free

The supervisor also works with free AI services. They speak the OpenAI chat format, and `scripts/agent.py` has a small adapter for it (`OpenAICompatibleModel`). I measured one incident: 7 model calls and about 12,000 input tokens, so a free daily quota is enough for a few incidents a day.

| `AI_PROVIDER` | Where to get it | Key |
|---|---|---|
| `groq` | console.groq.com, free tier | `AI_API_KEY` |
| `gemini` | aistudio.google.com, free tier | `AI_API_KEY` |
| `openrouter` | openrouter.ai, free models end in `:free` | `AI_API_KEY` |
| `ollama` | ollama.com, runs on your own computer (slow on a laptop) | none |
| `openai` | any other OpenAI-compatible service, with `AI_BASE_URL` | `AI_API_KEY` |
| `claude` | console.anthropic.com, paid | `ANTHROPIC_API_KEY` |

1. Copy `.env.example` to `.env` and fill in `AI_PROVIDER`, `AI_MODEL` (a model that supports tool use) and `AI_API_KEY`. Git and Docker ignore `.env`.
2. Start the website with the supervisor:
   ```powershell
   python -m web.server --mqtt memory --agent --scenario connection_trouble
   ```
3. The "AI supervisor" card shows which AI it uses and that it is free (the cost shows $0).

If the free quota is used up, the supervisor waits and tries again (up to 30 seconds each time, at most 4 tries), then says "The free AI quota is used up for now. Try again later." A wrong key gives "The AI key is missing or wrong (check AI_API_KEY)."

## Let visitors use the supervisor in the public demo

With a free AI, the public demo can show the supervisor too:

```powershell
$env:FLEETMIND_PUBLIC_AI = "on"
python -m web.server --public --mqtt memory
```

Visitors don't type requests. They press one of three buttons ("A spill blocks an aisle", "Two robots break down", "A big delivery arrives"), the incident happens in the warehouse, and everyone watches the supervisor handle it. The limits:

- `FLEETMIND_AI_TASKS_PER_DAY` tasks a day for everyone together (20 if it is not set; `render.yaml` sets 10).
- One AI task every 2 minutes per visitor.
- `/mcp` stays closed (404), and the key is only in `.env` or in the host's secret settings, never in the container.

## Put the public demo online (Hugging Face Spaces, free)

1. Make an account at huggingface.co.
2. Create a new Space: Docker, template Blank, visibility Public, name `fleetmind`.
3. Create an access token with write access (Settings, Access Tokens). Keep it secret.
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

## Put the demo on your own website

On Render, shown on a Netlify website: see [deploy/render-netlify.md](../deploy/render-netlify.md). Render runs the demo from `render.yaml` (free plan; measured with its limits: about 65 MB and 5 ticks per second), and your Netlify page shows it with an `<iframe>`. `EMBED_FROM` makes sure only your website can show it.

Other ways, see [deploy/selfhost/README.md](../deploy/selfhost/README.md):
- Static website or website builder: run the demo on Hugging Face (above) and show it in a page with an `<iframe>`.
- Your own server: `deploy/selfhost/` runs the demo with Caddy on your domain (e.g. `demo.yoursite.com`) with HTTPS, in one `docker compose up -d --build`.

## Compare restocking by people and by robots

```powershell
python -m scripts.compare_restock               # 30 seeds, a few minutes
```

## Make the order history and test the forecast

```powershell
python -m scripts.history
python -m scripts.forecast
```

The first command writes 120 days of made-up orders to `data/orders_hourly.csv`. The second runs the backtest, prints the results table and saves every forecast to `data/forecast_backtest.csv`.

## Use the real M5 data

Download M5 from Kaggle ("M5 Forecasting - Accuracy"). The M5 files are not part of this project. Then:

```powershell
python -m scripts.m5 --raw "C:\path\to\m5"
python -m scripts.history --source m5
python -m scripts.forecast --source m5
```

The first command reads the big sales file once and saves the daily totals of CA_1 FOODS in `data/m5_ca1_foods_daily.csv`. The files made from M5 are in `.gitignore`, so they are not uploaded to GitHub (check the Kaggle rules before sharing them).

## Find out why robots wait

```powershell
python -m scripts.waits
python -m scripts.waits --scenario breakdown_day --min-wait 5
```

Lists every time a robot stood still for 8+ ticks, with the reason (behind a broken robot, behind a parked robot, planned wait, ...), and adds up the waiting time per reason.

## Watch the scenarios in the browser (replay)

```powershell
python -m scripts.record
start viewer\index.html
```
