"""
Run the FleetMind robots inside NVIDIA Isaac Sim.

This is the ONLY file that needs Isaac Sim (and so an NVIDIA RTX GPU). It:
    1. starts Isaac Sim
    2. opens the warehouse world (isaac/warehouse.usda, made by python -m isaac.world)
    3. connects every robot in the world to the MQTT broker as a VDA 5050 robot
    4. every frame: handles the messages, drives the robot bodies, renders

The fleet manager runs as usual somewhere else, e.g. on the same machine:
    docker compose up -d                                       # the broker
    python -m web.server --mqtt external --state-timeout 3     # manager + website
and then, with Isaac Sim's Python (it needs paho-mqtt: pip install paho-mqtt):
    python isaac/run_isaac.py                                  # a window (a PC with a screen)
    python isaac/run_isaac.py --headless --livestream          # a cloud machine: watch it in the streaming client
    python isaac/run_isaac.py --no-isaac                       # no GPU: the same bridge on the USD world,
                                                               # 60 frames a second, without Isaac Sim

See isaac/README.md for the whole setup.

Written for Isaac Sim 5.x/6.x. It only uses SimulationApp, omni.usd and plain USD
(no version-specific Isaac APIs), but it has not been run on a GPU machine yet.
"""

import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # the fleetmind folder
sys.path.insert(0, ROOT)


def main():
    parser = argparse.ArgumentParser(description="FleetMind robots in Isaac Sim")
    parser.add_argument("--world", default=os.path.join(ROOT, "isaac", "warehouse.usda"))
    parser.add_argument("--broker", default="127.0.0.1:1883")
    parser.add_argument("--headless", action="store_true", help="no window (cloud machines)")
    parser.add_argument("--livestream", action="store_true", help="stream the viewport (WebRTC)")
    parser.add_argument("--frames-per-tick", type=int, default=12, help="animation frames for one cell")
    parser.add_argument("--no-isaac", action="store_true",
                        help="run the same bridge on the USD world with plain usd-core (no GPU, no rendering)")
    args = parser.parse_args()
    if args.no_isaac:
        return run_without_isaac(args)

    # Isaac Sim must be started before anything else from Omniverse (and pxr) is imported.
    from isaacsim import SimulationApp
    app = SimulationApp({"headless": args.headless, "width": 1600, "height": 900})

    import omni.kit.app
    import omni.usd

    if args.livestream:
        manager = omni.kit.app.get_app().get_extension_manager()
        manager.set_extension_enabled_immediate("omni.kit.livestream.webrtc", True)

    if not os.path.exists(args.world):
        os.chdir(ROOT)
        from isaac.world import build_world
        build_world(out=args.world)
        print(f"[fleetmind] built the world: {args.world}")

    context = omni.usd.get_context()
    context.open_stage(args.world)
    for _ in range(30):                      # let the stage finish loading
        app.update()
    stage = context.get_stage()

    os.chdir(ROOT)                           # the robots read layouts/main_warehouse.json
    from isaac.bridge import SimFleet, robot_ids_in
    from scripts.bus import MqttBroker

    host, _, port = args.broker.partition(":")
    robots = robot_ids_in(stage)
    fleet = SimFleet(MqttBroker(host, int(port or 1883)), stage, robots, args.frames_per_tick).start()
    print(f"[fleetmind] robots {', '.join(robots)} are online at {args.broker}. "
          f"Start the fleet manager and press Start on the website.")

    try:
        while app.is_running():
            fleet.step_frame()
            app.update()
    except KeyboardInterrupt:
        pass
    finally:
        fleet.stop()
        time.sleep(0.2)
        app.close()


def run_without_isaac(args):
    """Everything except Isaac Sim itself: the USD world, the bridge, the robots' bodies, 60 frames a second."""
    os.chdir(ROOT)
    from pxr import Usd
    from isaac.bridge import SimFleet, robot_ids_in
    from isaac.world import build_world
    from scripts.bus import MqttBroker

    if not os.path.exists(args.world):
        build_world(out=args.world)
    stage = Usd.Stage.Open(args.world)
    host, _, port = args.broker.partition(":")
    robots = robot_ids_in(stage)
    fleet = SimFleet(MqttBroker(host, int(port or 1883)), stage, robots, args.frames_per_tick).start()
    print(f"[fleetmind] (no Isaac Sim) robots {', '.join(robots)} of {os.path.basename(args.world)} "
          f"are online at {args.broker}. Press Ctrl+C to stop.")
    try:
        while True:
            fleet.step_frame()
            time.sleep(1 / 60)
    except KeyboardInterrupt:
        pass
    finally:
        fleet.stop()


if __name__ == "__main__":
    main()
