"""
Optional test recorder for the visual test viewer (viewer/tests.html).

Normally this file does NOTHING: your tests run exactly as before.

Only when you run:
    python -m scripts.record_tests
it watches every test while it runs, takes a snapshot of the warehouse after
each action (move, pick, charge, ...), and saves everything to
viewer/test_recordings.js so you can replay each test in the browser.

How it works: it wraps ("patches") some methods of Robot, Warehouse,
Simulation and the layout loader. The wrapper calls the real method, then
writes down what happened. The real behaviour is never changed.
"""

import functools
import inspect
import json
import os

import pytest

RECORDING = os.environ.get("FLEETMIND_RECORD_TESTS") == "1"
OUTPUT = os.path.join("viewer", "test_recordings.js")
MAX_FRAMES = 250            # per test, so long simulations don't get huge
SIM_TICKS_RECORDED = 120    # record a simulation tick by tick only for this long


class Recorder:
    def __init__(self):
        self.tests = []
        self.current = None
        self.quiet = 0          # > 0 while inside a bigger action (don't record the small steps)
        self.warehouses = []    # warehouses seen in the current test, newest last
        self.sim_ticks = 0

    # ----- per test -----

    def start(self, item):
        try:
            source = inspect.getsource(item.obj)
        except (OSError, TypeError):
            source = ""
        self.current = {
            "id": item.nodeid,
            "file": item.nodeid.split("::")[0].split("/")[-1],
            "name": item.name,
            "doc": inspect.getdoc(item.obj) or "",
            "source": source,
            "outcome": "passed",
            "message": "",
            "frames": [],
            "layout_error": None,
        }
        self.warehouses = []
        self.sim_ticks = 0
        self.quiet = 0

    def finish(self):
        if self.current is None:
            return
        # a last picture of the final state
        if self.warehouses or self._robots_without_map:
            self.frame("End of test: final state", final=True)
        self.tests.append(self.current)
        self.current = None
        self._robots_without_map = []

    _robots_without_map = []

    # ----- snapshots -----

    def frame(self, label, ok=None, final=False):
        if self.current is None or self.quiet:
            return
        frames = self.current["frames"]
        if len(frames) >= MAX_FRAMES and not final:
            return
        wh = self.warehouses[-1] if self.warehouses else None
        frames.append({
            "label": label,
            "ok": ok,
            "map": snapshot_map(wh) if wh else None,
            "robots": snapshot_robots(wh, self._robots_without_map),
            "shelves": snapshot_shelves(wh) if wh else [],
        })

    def remember_warehouse(self, wh):
        if wh not in self.warehouses:
            self.warehouses.append(wh)

    def remember_robot(self, robot):
        if robot not in self._robots_without_map:
            self._robots_without_map.append(robot)


REC = Recorder()


# ---------------------------------------------------------------------------
# Snapshots: turn objects into plain data the browser can draw
# ---------------------------------------------------------------------------

def snapshot_map(wh):
    rows = []
    for r in range(wh.height):
        line = []
        for c in range(wh.width):
            pos = (r, c)
            if pos in wh.shelf_at:
                line.append(wh.shelf_at[pos][0].lower())
            elif pos in getattr(wh, "blocked", set()):
                line.append("X")
            else:
                line.append(wh.grid[r][c])
        rows.append("".join(line))
    return rows


def snapshot_robots(wh, extra):
    robots = list(wh.robots.values()) if wh else []
    for robot in extra:
        if robot not in robots:
            robots.append(robot)
    out = []
    for r in sorted(robots, key=lambda x: x.robot_id):
        out.append({
            "id": r.robot_id,
            "pos": list(r.position),
            "status": r.status.value,
            "task": getattr(r, "task", None) or "idle",
            "battery": round(r.battery, 1),
            "carrying": r.carrying,
            "order": r.order.order_id if r.order else None,
            "target": list(r.target) if getattr(r, "target", None) else None,
            "repair": getattr(r, "repair_left", 0),
            "on_map": wh is not None and r.robot_id in wh.robots and wh.robots[r.robot_id] is r,
        })
    return out


def snapshot_shelves(wh):
    return [{"id": s.shelf_id, "items": dict(s.items)}
            for s in sorted(wh.shelves.values(), key=lambda s: s.shelf_id)]


def short(value):
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], bool):
        return f"{value[0]}, '{value[1]}'"
    return repr(value)


# ---------------------------------------------------------------------------
# Patching: wrap methods so they also record a snapshot
# ---------------------------------------------------------------------------

def install():
    from scripts import classes, layout, simulation

    Robot, Warehouse = classes.Robot, classes.Warehouse

    def wrap_robot_action(name, describe):
        original = getattr(Robot, name)

        @functools.wraps(original)
        def wrapper(self, *args, **kwargs):
            for arg in args:
                if isinstance(arg, Warehouse):
                    REC.remember_warehouse(arg)
            if not any(self in wh.robots.values() for wh in REC.warehouses):
                REC.remember_robot(self)
            result = original(self, *args, **kwargs)
            ok = result[0] if isinstance(result, tuple) and result and isinstance(result[0], bool) else None
            REC.frame(f"{self.robot_id}.{name}({describe(args, kwargs)}) → {short(result)}", ok)
            return result
        setattr(Robot, name, wrapper)

    no_args = lambda a, k: ""
    wrap_robot_action("move", lambda a, k: repr(a[0]) if a else "")
    wrap_robot_action("pick", no_args)
    wrap_robot_action("place", lambda a, k: f"tick={a[1] if len(a) > 1 else k.get('tick')}")
    wrap_robot_action("charge", no_args)
    wrap_robot_action("break_down", lambda a, k: f"repair_ticks={a[0] if a else k.get('repair_ticks')}")
    wrap_robot_action("tick_repair", no_args)

    def wrap_setup(name, describe):
        original = getattr(Warehouse, name)

        @functools.wraps(original)
        def wrapper(self, *args, **kwargs):
            REC.remember_warehouse(self)
            try:
                result = original(self, *args, **kwargs)
            except Exception as error:
                # e.g. a test checking that a robot can't be placed on a shelf
                REC.frame(f"setup: {name}({describe(args)}) → raised {type(error).__name__}: {error}", ok=False)
                raise
            REC.frame(f"setup: {name}({describe(args)})")
            return result
        setattr(Warehouse, name, wrapper)

    wrap_setup("add_robot", lambda a: f"{a[0].robot_id} at {a[0].position}")
    wrap_setup("add_shelf", lambda a: f"{a[0].shelf_id}, {len(a[0].cells)} cell(s), items {a[0].items}")
    wrap_setup("add_charger", lambda a: repr(a[0]))
    wrap_setup("add_packing_station", lambda a: repr(a[0]))
    wrap_setup("block_cell", lambda a: repr(a[0]))
    wrap_setup("unblock_cell", lambda a: repr(a[0]))

    # Loading a layout: record ONE frame, not every shelf/robot added inside it
    original_build = layout.build_warehouse

    @functools.wraps(original_build)
    def build_wrapper(data):
        REC.quiet += 1
        try:
            wh = original_build(data)
        except layout.LayoutError as error:
            REC.quiet -= 1
            if REC.current is not None:
                REC.current["layout_error"] = {"message": str(error), "map": list(data.get("map") or [])}
            raise
        REC.quiet -= 1
        REC.remember_warehouse(wh)
        REC.frame(f"Layout loaded: {wh.height} x {wh.width}, {len(wh.shelves)} shelves, {len(wh.robots)} robots")
        return wh
    layout.build_warehouse = build_wrapper

    # Simulation: one frame per tick (for the first ticks), not per robot move
    original_step = simulation.Simulation.step

    @functools.wraps(original_step)
    def step_wrapper(self):
        REC.remember_warehouse(self.warehouse)
        REC.quiet += 1
        try:
            original_step(self)
        finally:
            REC.quiet -= 1
        REC.sim_ticks += 1
        if REC.sim_ticks <= SIM_TICKS_RECORDED:
            events = [e for e in self.events if e[0] == self.tick]
            text = "; ".join(f"{e[1] + ' ' if e[1] else ''}{e[2]}" for e in events[:4])
            if len(events) > 4:
                text += f"; +{len(events) - 4} more"
            REC.frame(f"Simulation tick {self.tick}" + (f": {text}" if text else ""))
        elif REC.sim_ticks == SIM_TICKS_RECORDED + 1:
            REC.frame(f"… recording paused after {SIM_TICKS_RECORDED} ticks (the test keeps running)")
    simulation.Simulation.step = step_wrapper


# ---------------------------------------------------------------------------
# pytest hooks (only active when recording)
# ---------------------------------------------------------------------------

if RECORDING:
    install()

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_call(item):
        REC.start(item)
        yield

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(item, call):
        outcome = yield
        report = outcome.get_result()
        if report.when == "call" and REC.current is not None:
            REC.current["outcome"] = report.outcome
            if report.failed:
                REC.current["message"] = str(report.longrepr)[-1500:]
            REC.finish()

    def pytest_sessionfinish(session, exitstatus):
        os.makedirs("viewer", exist_ok=True)
        with open(OUTPUT, "w", encoding="utf-8") as f:
            f.write("// Generated by scripts/record_tests.py - do not edit by hand.\n")
            f.write("window.TEST_RECORDINGS = ")
            json.dump(REC.tests, f, separators=(",", ":"))
            f.write(";\n")
        size_kb = os.path.getsize(OUTPUT) / 1024
        print(f"\nRecorded {len(REC.tests)} tests to {OUTPUT} ({size_kb:.0f} KB). Open viewer/tests.html")