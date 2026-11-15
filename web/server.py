"""
The FleetMind live website.

Start it from the fleetmind folder:
    python -m web.server               # opens http://127.0.0.1:8000 in your browser
    python -m web.server --no-browser  # just start the server
    python -m web.server --port 8080
    python -m web.server --mqtt                 # manager and robots talk over MQTT (Mosquitto)
    python -m web.server --mqtt external        # ... and the robots run in python -m scripts.robot_agent
    python -m web.server --mqtt memory          # over MQTT messages, but without a broker
    python -m web.server --public --mqtt memory --host 0.0.0.0   # the public demo (see PUBLIC below)

PUBLIC mode (for a link anyone can open):
    - ONE shared warehouse: everyone sees the same day and can press Start, Pause,
      change the scenario or start a random day
    - NO tools, NO AI, NO keys: the MCP server (/mcp) is not there at all, and nothing
      in public mode reads an API key, so visitors can't use or spend anything of yours
    - limits: at most PUBLIC_MAX_CLIENTS tabs, PUBLIC_COMMANDS_PER_SECOND per tab,
      speed at most PUBLIC_MAX_SPEED
    - it runs by itself: it starts when the server starts, and a finished day is followed
      by a new random day; while nobody is watching, nothing is computed

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
import os
import threading
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from web.agent_desk import AgentDesk
from web.mcp_server import build_mcp
from web.session import LiveSession

ROOT = Path(__file__).resolve().parent.parent       # the fleetmind folder
STATIC = ROOT / "web" / "static"
VIEWER = ROOT / "viewer"                             # draw.js is shared with the replay viewer
LAYOUT = ROOT / "layouts" / "main_warehouse.json"
IDLE_POLL = 0.05                                     # while paused, check 20x a second if we should run
PUBLIC_MAX_CLIENTS = 100                             # public demo: open tabs at the same time
PUBLIC_COMMANDS_PER_SECOND = 3                       # public demo: commands per tab (buttons, not spam)
PUBLIC_MAX_SPEED = 30                                # public demo: ticks per second
PUBLIC_NEXT_DAY_AFTER = 15                           # public demo: seconds the closed shutter shows before a new day
PUBLIC_AI_COOLDOWN = 120                             # public demo: seconds between AI tasks from one tab
HEARTBEAT_POLL = 0.5                                 # while paused over MQTT: check heartbeats this often


class Hub:
    """The one session plus every open browser tab."""

    def __init__(self, session, public=False):
        self.session = session
        self.public = public
        self.desk = None                 # the AI supervisor (web/agent_desk.py), if switched on
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
        finished_at = None
        while True:
            if self.public:
                if not self.clients:                  # nobody watching: compute nothing
                    await asyncio.sleep(IDLE_POLL)
                    continue
                if self.session.finished:             # the day is over: show the summary, then a new day
                    finished_at = finished_at or loop.time()
                    if loop.time() - finished_at >= PUBLIC_NEXT_DAY_AFTER:
                        finished_at = None
                        async with self.lock:
                            await asyncio.to_thread(self.session.new_day)
                            self.session.start()
                            messages = [self.session.hello_message(), self.session.state_message()]
                        await self.broadcast(messages)
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
                if self.desk:
                    await self.desk.watch()           # an incident for the AI supervisor?
            await asyncio.sleep(max(0.0, 1 / self.session.speed - (loop.time() - started)))


def make_broker(mqtt, broker="127.0.0.1:1883"):
    """--mqtt memory -> in-memory broker; inproc/external -> a real MQTT broker; None -> no MQTT."""
    from scripts.bus import InMemoryBroker, MqttBroker
    if mqtt is None:
        return None, ""
    if mqtt == "memory":
        return InMemoryBroker(keep_log=False), "in-memory"   # a website runs for days: no message log
    host, _, port = broker.partition(":")
    return MqttBroker(host, int(port or 1883)), broker


def create_app(layout_path=LAYOUT, scenario="normal", mqtt=None, broker="127.0.0.1:1883", state_timeout=0.5,
               public=False, agent_model=None, incidents=True, public_ai=False):
    """
    Build a fresh app with its own session (tests use this to start clean). public=True: the public demo.
    agent_model: the AI supervisor's model (scripts/agent.py); None = no agent.
    public_ai: the public demo may use the supervisor too (with a free AI: a daily limit, incident
    buttons, one task per tab every PUBLIC_AI_COOLDOWN seconds). /mcp stays closed to the public.
    """
    bus, name = make_broker(mqtt, broker)
    robots = "external" if mqtt == "external" else "inproc"
    session = LiveSession(str(layout_path), scenario=scenario, broker=bus, robots=robots, broker_name=name,
                          link_options={"state_timeout": state_timeout})
    session.public = public
    hub = Hub(session, public=public)
    if public:
        session.start()                            # the demo runs by itself

    use_ai = agent_model is not None and (not public or public_ai)
    mcp = build_mcp(hub) if (not public or use_ai) else None          # the supervisor's tools, in the server
    mcp_app = mcp.streamable_http_app(streamable_http_path="/mcp") if mcp and not public else None
    hub.desk = AgentDesk(hub, mcp, agent_model if use_ai else None,
                         incidents=incidents and not public, public=public,
                         daily_tasks=int(os.environ.get("FLEETMIND_AI_TASKS_PER_DAY", "20")))

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(hub.ticker())   # start the ticker when the server starts
        hub.desk.start()
        if mcp_app:
            async with mcp.session_manager.run():  # and the MCP server's sessions
                yield
        else:
            yield
        task.cancel()                              # stop it when the server stops
        hub.desk.stop()
        hub.session.close()

    app = FastAPI(title="FleetMind live", lifespan=lifespan,
                  docs_url=None if public else "/docs", redoc_url=None, openapi_url=None if public else "/openapi.json")
    app.state.mcp = mcp
    if mcp_app:
        app.router.routes.extend(mcp_app.routes)   # POST/GET /mcp
    app.state.hub = hub

    embed_from = " ".join(os.environ.get("EMBED_FROM", "").split())
    if embed_from:
        @app.middleware("http")
        async def only_my_website_may_embed(request, call_next):
            """EMBED_FROM=https://you.netlify.app: only that website may show the demo in an iframe."""
            response = await call_next(request)
            response.headers["Content-Security-Policy"] = f"frame-ancestors 'self' {embed_from}"
            return response

    @app.get("/health")
    def health():
        """For the hosting service: is the server alive?"""
        return {"ok": True, "tick": hub.session.sim.tick, "watching": len(hub.clients), "public": public}

    @app.get("/")
    def live_page():
        return FileResponse(STATIC / "live.html")

    @app.get("/3d")
    def world_page():
        """The same live warehouse, drawn in 3D from the Isaac Sim world (isaac/warehouse.usda)."""
        return FileResponse(STATIC / "world3d.html")

    @app.get("/world.json")
    def world_json():
        try:
            from isaac.export import world_data
        except ImportError:
            return JSONResponse({"error": "the 3D world needs usd-core: pip install usd-core"}, status_code=503)
        return world_data(str(ROOT / "isaac" / "warehouse.usda"))

    @app.websocket("/ws")
    async def websocket(ws: WebSocket):
        if public and len(hub.clients) >= PUBLIC_MAX_CLIENTS:
            await ws.close(code=1013, reason="The demo is full. Please try again in a minute.")
            return
        await ws.accept()
        recent = []                                  # times of this tab's last commands (public limit)
        last_ai = -10 ** 9                           # when this tab last started an AI task
        async with hub.lock:
            hello = hub.session.hello_message()
            hello["agent"] = hub.desk.hello()
            state = hub.session.state_message(new_events=False)   # new tab: past events come in hello
        await hub.send(ws, hello)
        await hub.send(ws, state)
        hub.clients.add(ws)
        try:
            while True:
                text = await ws.receive_text()
                try:
                    command = json.loads(text)
                    if public:
                        now = asyncio.get_running_loop().time()
                        recent[:] = [t for t in recent if now - t < 1.0]
                        if len(recent) >= PUBLIC_COMMANDS_PER_SECOND:
                            raise ValueError("Slow down a little: a few clicks per second is enough.")
                        recent.append(now)
                        if isinstance(command, dict) and command.get("type") == "speed":
                            value = command.get("value")
                            if isinstance(value, (int, float)) and not isinstance(value, bool):
                                command["value"] = min(value, PUBLIC_MAX_SPEED)
                    kind = command.get("type") if isinstance(command, dict) else None
                    if kind in ("agent_ask", "agent_incident"):
                        now = asyncio.get_running_loop().time()
                        if public and now - last_ai < PUBLIC_AI_COOLDOWN:
                            wait = int(PUBLIC_AI_COOLDOWN - (now - last_ai))
                            raise ValueError(f"One AI task every {PUBLIC_AI_COOLDOWN // 60} minutes per visitor: "
                                             f"try again in {wait} s.")
                        if kind == "agent_ask":
                            await hub.desk.submit(command.get("text", ""))
                        else:
                            hub.desk.check_can_take()
                            async with hub.lock:
                                task = hub.desk.make_incident(command.get("kind"))
                                state = hub.session.state_message()
                            await hub.broadcast([state])
                            await hub.desk.submit(task, source="visitor")
                        last_ai = now
                        continue
                    if isinstance(command, dict) and command.get("type") == "agent_answer":
                        hub.desk.answer(command.get("id"), command.get("ok") is True, command.get("note", ""))
                        continue
                    async with hub.lock:
                        messages = await asyncio.to_thread(hub.session.handle, command)
                    if isinstance(command, dict) and command.get("type") in ("reset", "scenario", "new_day"):
                        for message in messages:
                            if message["type"] == "hello":
                                message["agent"] = hub.desk.hello()
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


def public_model_from_env(env=None):
    """
    The AI for the public demo (FLEETMIND_PUBLIC_AI=on). A missing or wrong AI setting never stops
    the demo: it prints why and runs without the supervisor.
    """
    from scripts.agent import model_from_env
    env = os.environ if env is None else env
    provider = (env.get("AI_PROVIDER") or "").strip().lower()
    try:
        model = model_from_env(env)
    except ValueError as error:
        print(f"{error} The public demo runs without the supervisor.")
        return None
    if model is None:
        print("FLEETMIND_PUBLIC_AI=on but no AI_PROVIDER is set: the public demo runs without the supervisor.")
        return None
    if provider not in ("claude", "ollama") and not (env.get("AI_API_KEY") or "").strip():
        print("AI_API_KEY is not set: the public demo runs without the supervisor.")
        return None
    return model


def main():
    import uvicorn

    parser = argparse.ArgumentParser(description="Run the FleetMind live website.")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    parser.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to be reachable from outside (a container)")
    parser.add_argument("--agent", action="store_true",
                        help="switch on the AI supervisor. The AI comes from AI_PROVIDER / AI_MODEL / AI_API_KEY "
                             "(environment or .env); without AI_PROVIDER: Claude with ANTHROPIC_API_KEY")
    parser.add_argument("--public", action="store_true",
                        help="the public demo: one shared warehouse, no MCP tools, no API keys, limits")
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
    agent_model = None
    public_ai = args.public and os.environ.get("FLEETMIND_PUBLIC_AI", "off").lower() == "on"
    if (args.agent and not args.public) or public_ai:
        from dotenv import load_dotenv
        from scripts.agent import ClaudeModel, model_from_env
        load_dotenv()                                  # the AI settings from .env (git and Docker ignore .env)
        agent_model = public_model_from_env() if args.public else model_from_env()
        if agent_model is None and not args.public:
            if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
                print("The AI supervisor needs an AI: set AI_PROVIDER (e.g. groq, a free tier) with AI_MODEL and "
                      "AI_API_KEY, or ANTHROPIC_API_KEY for Claude, in a .env file. Tasks fail until then.")
            agent_model = ClaudeModel()
    app = create_app(scenario=args.scenario, mqtt=args.mqtt, broker=args.broker, state_timeout=args.state_timeout,
                     public=args.public, agent_model=agent_model, public_ai=public_ai)

    url = f"http://127.0.0.1:{args.port}"
    if args.mqtt == "memory":
        mode = "VDA 5050 messages over the in-memory bus"
    else:
        mode = f"over MQTT ({args.mqtt}, broker {args.broker})" if args.mqtt else "local"
    mode += ", PUBLIC demo (no tools, no keys)" if args.public else ""
    mode += f", AI supervisor ON ({getattr(agent_model, 'label', '')})" if agent_model else ""
    print(f"FleetMind live: {url}   {mode}   (press Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=[url]).start()   # give the server a second to start
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
