"""
The FleetMind live website.

Start it from the fleetmind folder:
    python -m web.server               # opens http://127.0.0.1:8000 in your browser
    python -m web.server --no-browser  # just start the server
    python -m web.server --port 8080
    python -m web.server --mqtt                 # manager and robots talk over MQTT (Mosquitto)
    python -m web.server --mqtt external        # ... and the robots run in python -m scripts.robot_agent
    python -m web.server --mqtt memory          # over MQTT messages, but without a broker

How it works:
    - One LiveSession (web/session.py) holds the running simulation.
    - A background task (the "ticker") calls step() while the session is running,
      then waits 1/speed seconds, and sends the new state to every open tab.
    - Each tab talks to the server over ONE WebSocket at /ws. A WebSocket is a
      connection that stays open, so the server can send a message every tick
      without the browser asking each time.
    - Commands from a tab ({"type": "start"} etc.) go to LiveSession.handle(),
      and the answer is sent to ALL tabs, so they always show the same warehouse.
    - With --mqtt the session steps a FleetLink instead of the simulation. One step
      then waits for the robots' answers, so it runs in a worker thread
      (asyncio.to_thread) and the website stays responsive. While paused, the
      ticker still checks the heartbeats, so a robot that dies is shown as lost.
"""

import argparse
import asyncio
import json
import threading
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from web.session import LiveSession

ROOT = Path(__file__).resolve().parent.parent       # the fleetmind folder
STATIC = ROOT / "web" / "static"
VIEWER = ROOT / "viewer"                             # draw.js is shared with the replay viewer
LAYOUT = ROOT / "layouts" / "main_warehouse.json"
IDLE_POLL = 0.05                                     # while paused, check 20x a second if we should run
HEARTBEAT_POLL = 0.5                                 # while paused over MQTT: check heartbeats this often


class Hub:
    """The one session plus every open browser tab."""

    def __init__(self, session):
        self.session = session
        self.clients = set()
        self.lock = asyncio.Lock()       # only one thing changes the simulation at a time

    async def send(self, ws, message):
        await ws.send_text(json.dumps(message))

    async def broadcast(self, messages):
        for ws in list(self.clients):
            try:
                for message in messages:
                    await self.send(ws, message)
            except Exception:
                self.clients.discard(ws)            # tab was closed: forget it

    async def ticker(self):
        """Background loop: one step every 1/speed seconds while running."""
        loop = asyncio.get_running_loop()
        last_check = 0.0
        while True:
            if not self.session.running:
                await asyncio.sleep(IDLE_POLL)
                if self.session.link and loop.time() - last_check >= HEARTBEAT_POLL:
                    last_check = loop.time()
                    async with self.lock:
                        changed = self.session.check_link()
                        message = self.session.state_message() if changed else None
                    if message:
                        await self.broadcast([message])
                continue
            started = loop.time()
            async with self.lock:
                if self.session.running:          # could have been paused while we waited
                    await asyncio.to_thread(self.session.step)
                    message = self.session.state_message()
                else:
                    message = None
            if message:
                await self.broadcast([message])
            # wait the rest of this tick's time slot (stepping itself took some of it)
            await asyncio.sleep(max(0.0, 1 / self.session.speed - (loop.time() - started)))


def make_broker(mqtt, broker="127.0.0.1:1883"):
    """--mqtt memory -> in-memory broker; inproc/external -> a real MQTT broker; None -> no MQTT."""
    from scripts.bus import InMemoryBroker, MqttBroker
    if mqtt is None:
        return None, ""
    if mqtt == "memory":
        return InMemoryBroker(), "in-memory"
    host, _, port = broker.partition(":")
    return MqttBroker(host, int(port or 1883)), broker


def create_app(layout_path=LAYOUT, scenario="normal", mqtt=None, broker="127.0.0.1:1883", state_timeout=0.5):
    """Build a fresh app with its own session (tests use this to start clean)."""
    bus, name = make_broker(mqtt, broker)
    robots = "external" if mqtt == "external" else "inproc"
    hub = Hub(LiveSession(str(layout_path), scenario=scenario, broker=bus, robots=robots, broker_name=name,
                          link_options={"state_timeout": state_timeout}))

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(hub.ticker())   # start the ticker when the server starts
        yield
        task.cancel()                              # stop it when the server stops
        hub.session.close()

    app = FastAPI(title="FleetMind live", lifespan=lifespan)
    app.state.hub = hub

    @app.get("/")
    def live_page():
        return FileResponse(STATIC / "live.html")

    @app.websocket("/ws")
    async def websocket(ws: WebSocket):
        await ws.accept()
        async with hub.lock:
            hello = hub.session.hello_message()
            state = hub.session.state_message(new_events=False)   # new tab: past events come in hello
        await hub.send(ws, hello)
        await hub.send(ws, state)
        hub.clients.add(ws)
        try:
            while True:
                text = await ws.receive_text()
                try:
                    command = json.loads(text)
                    async with hub.lock:
                        messages = await asyncio.to_thread(hub.session.handle, command)
                except (ValueError, json.JSONDecodeError) as error:
                    await hub.send(ws, {"type": "error", "message": str(error)})
                    continue
                await hub.broadcast(messages)
        except WebSocketDisconnect:
            pass
        finally:
            hub.clients.discard(ws)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    app.mount("/viewer", StaticFiles(directory=VIEWER), name="viewer")
    return app


app = create_app()          # for: uvicorn web.server:app (no MQTT)


def main():
    import uvicorn

    parser = argparse.ArgumentParser(description="Run the FleetMind live website.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true", help="don't open the browser")
    parser.add_argument("--mqtt", nargs="?", const="inproc", choices=("inproc", "external", "memory"),
                        help="talk to the robots over MQTT. inproc (default): start simulated robots here; "
                             "external: robots run in python -m scripts.robot_agent; memory: no broker needed")
    parser.add_argument("--broker", default="127.0.0.1:1883", help="host:port of the MQTT broker")
    parser.add_argument("--scenario", default="normal")
    parser.add_argument("--state-timeout", type=float, default=0.5,
                        help="seconds to wait for the robots' states each tick (Isaac Sim robots drive "
                             "for a moment before they answer: use 3)")
    args = parser.parse_args()
    app = create_app(scenario=args.scenario, mqtt=args.mqtt, broker=args.broker, state_timeout=args.state_timeout)

    url = f"http://127.0.0.1:{args.port}"
    mode = f"over MQTT ({args.mqtt}, broker {args.broker})" if args.mqtt else "local"
    print(f"FleetMind live: {url}   {mode}   (press Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=[url]).start()   # give the server a second to start
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
