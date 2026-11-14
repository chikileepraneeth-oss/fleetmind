# FleetMind in NVIDIA Isaac Sim

The fleet manager does not change. It already talks to the robots only with
VDA 5050 messages over MQTT. In Isaac Sim, the robots are bodies in a 3D
warehouse, and they speak the same messages.

```
 fleet manager + website           MQTT broker            Isaac Sim
 (python -m web.server --mqtt) <-> (Mosquitto)   <->  isaac/run_isaac.py
   decides, sends orders            topics              5 robot bodies in the
   reads states, finds lost robots  uagv/v2/...         USD warehouse, drive, report
```

| File | What it does | Needs a GPU? |
|---|---|---|
| `isaac/world.py` | builds the warehouse as a USD world from `layouts/main_warehouse.json` | no (usd-core) |
| `isaac/bridge.py` | robots whose bodies are prims in the world; messages in, driving, measured state out | no (tested with usd-core) |
| `isaac/run_isaac.py` | starts Isaac Sim, opens the world, runs the bridge every frame | **yes** |

## 1. On any computer: build and check the world

```powershell
pip install usd-core
python -m isaac.world            # writes isaac/warehouse.usda
python -m pytest tests/test_isaac_world.py
```

The world: 1 cell = 1 metre, Z up, 56 walls, 40 shelves (each knows its item),
3 chargers, 2 packing stations, 3 loading docks, 5 robots. Every prim has
`fleetmind:kind`, `fleetmind:cell`, and for shelves `fleetmind:shelf` and `fleetmind:item`.

## 2. Get a computer that can run Isaac Sim

Isaac Sim needs an **NVIDIA RTX GPU** (NVIDIA lists an RTX 4080 / 16 GB or better
for the current versions). A laptop with Intel graphics can't run it.
Choose one:

- **NVIDIA Brev** (simplest): a cloud GPU machine (the docs use one L40S GPU). See
  [NVIDIA Brev Deployment](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/installation/install_advanced_cloud_setup_brev.html).
- **AWS**: the Isaac Sim Marketplace image on EC2. See
  [AWS Deployment](https://docs.isaacsim.omniverse.nvidia.com/latest/installation/install_advanced_cloud_setup_aws.html).
- **A PC with an RTX card**: install Isaac Sim locally
  ([Download Isaac Sim](https://docs.isaacsim.omniverse.nvidia.com/6.1.0/installation/download.html)).

Cloud GPUs cost money per hour: **stop the machine when you are done.**

## 3. On the GPU machine: run everything

Put everything on the GPU machine, so the broker never has to be open to the internet:

```bash
git clone https://github.com/chikileepraneeth-oss/fleetmind.git
cd fleetmind
git checkout worktree-mqtt-messaging     # until this branch is merged

# the broker
docker compose up -d

# the fleet manager and the website (normal Python 3.12 with requirements.txt)
python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
python -m isaac.world
python -m web.server --mqtt external --no-browser --port 8001 --state-timeout 3 &

# the robots in Isaac Sim (with Isaac Sim's own Python, which needs paho-mqtt)
<isaac-python> -m pip install paho-mqtt
<isaac-python> isaac/run_isaac.py --headless --livestream
```

`<isaac-python>` is the Python of your Isaac Sim install: `./python.sh` in the
Isaac Sim folder, or the Python where you installed the `isaacsim` pip package.

`--state-timeout 3`: robots in Isaac Sim drive to the next cell before they
answer, so the manager waits up to 3 s for their states (instead of 0.5 s).

## 4. On your laptop: watch it

- **The website**: forward its port over SSH, then open http://127.0.0.1:8001
  ```powershell
  ssh -L 8001:127.0.0.1:8001 <user>@<gpu-machine>
  ```
- **The 3D view**: the Isaac Sim WebRTC streaming client (or the web viewer of
  the Isaac Sim container), as in NVIDIA's cloud guide. It needs TCP 49100 and
  UDP 47998 open on the machine. Hosts that only forward TCP (e.g. RunPod) can't
  stream over WebRTC.

Press **Start** on the website. The robots in Isaac Sim drive the same routes
as on the website. Close Isaac Sim and the website shows them as **lost**.

## What to test in the simulator

1. **Same run**: a run with Isaac Sim robots delivers the same orders as without (0 state mismatches).
2. **A robot is pushed** (move its prim in the viewport while it waits): the manager logs `state_mismatch` and believes the robot.
3. **Isaac Sim is closed**: all robots are lost after about 3 s, and found again when it starts.
4. **Next steps**: replace the box robots with a real wheeled robot (e.g. Nova Carter) driven by a
   wheel controller, and let a lidar see the boxes on the floor (the blocked cells).
