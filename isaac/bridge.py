"""
Robots that live in a 3D world (Isaac Sim), controlled by the same fleet manager.

The fleet manager (scripts/fleet_link.py) does not change at all. It sends the
same VDA 5050 messages over MQTT. Here, each robot is a RobotAgent whose body
is a prim in the USD world:

    1. a message arrives (paho's network thread) -> it is put in a queue
    2. the simulator's main loop calls fleet.step_frame() every frame:
         - handle the queued messages (on the main thread: Isaac Sim requires that)
         - on a clock tick: the robot does its tick (as before) and its BODY starts
           to drive to the new cell, turning to face the way it goes
         - when the body has arrived, the robot sends its state with the pose the
           simulator MEASURED. If physics pushed it somewhere else, the manager
           sees that and believes the robot.

The body is anything with get_pose() and set_pose(x, y, yaw):
    UsdBody   moves a prim of the USD stage. Works in Isaac Sim AND with plain
              usd-core, so the bridge is tested on a computer without a GPU.
    (later)   a wheeled robot with a drive controller, e.g. Nova Carter
"""

import math
import queue
import time

from pxr import Gf, Sdf, UsdGeom

from isaac.world import cell_centre, cell_of_point
from scripts.robot_agent import RobotAgent

FRAMES_PER_TICK = 12          # frames of animation for one cell (at 60 fps: 0.2 s)
HEARTBEAT_SECONDS = 1.0


class UsdBody:
    """A robot body that is a prim in a USD stage (the robot's Xform from isaac/world.py)."""

    def __init__(self, stage, robot_id):
        self.prim = stage.GetPrimAtPath(f"/World/Robots/{robot_id}")
        if not self.prim or not self.prim.IsValid():
            raise ValueError(f"the world has no robot /World/Robots/{robot_id}")
        self.xform = UsdGeom.XformCommonAPI(self.prim)

    def get_pose(self):
        translate, rotate, _, _, _ = self.xform.GetXformVectors(0)
        return translate[0], translate[1], math.radians(rotate[2])

    def set_pose(self, x, y, yaw):
        self.xform.SetTranslate(Gf.Vec3d(x, y, 0.0))
        self.xform.SetRotate(Gf.Vec3f(0.0, 0.0, math.degrees(yaw)))


class SimRobot(RobotAgent):
    """A RobotAgent with a body in the 3D world."""

    def __init__(self, serial, broker, body, inbox, frames_per_tick=FRAMES_PER_TICK, **kw):
        super().__init__(serial, broker, **kw)
        self.body = body
        self.queue = inbox
        self.frames_per_tick = frames_per_tick
        self.publish_after_tick = False      # report only when the body has arrived
        self.waypoints = []                  # world points still to drive through this tick
        self.frame = 0
        self.arrived = True
        self.last_report = 0.0

    def dispatch(self, handler, *args):
        self.queue.put((handler, args))      # handled later, on the simulator's main thread

    def reported_pose(self):
        if self.position is None:
            return None, 0.0
        x, y, yaw = self.body.get_pose()
        return cell_of_point(x, y), round(yaw, 4)

    def _instant(self, act):
        result = super()._instant(act)
        if act["actionType"] == "initPosition" and self.position is not None:
            x, y, _ = cell_centre(self.position)
            _, _, yaw = self.body.get_pose()
            self.body.set_pose(x, y, yaw)    # put the body where the manager says
            self.waypoints, self.arrived = [], True
        return result

    def on_clock(self, message):
        super().on_clock(message)            # the robot's tick, exactly as before (no report yet)
        self.waypoints = [cell_centre(c) for c in self.moved_through]
        self.frame = 0
        self.arrived = not self.waypoints
        if self.arrived:
            self.publish_state()             # nothing to drive: report at once

    def animate(self):
        """One frame: drive the body a little further. Report when it has arrived."""
        if self.arrived:
            return
        self.frame += 1
        x0, y0, yaw = self.body.get_pose()
        target = self.waypoints[0]
        frames_left = max(1, self.frames_per_tick - self.frame + 1)
        dx, dy = target[0] - x0, target[1] - y0
        if abs(dx) > 1e-6 or abs(dy) > 1e-6:
            yaw = math.atan2(dy, dx)         # face the way we drive
        step = 1.0 / frames_left
        self.body.set_pose(x0 + dx * step, y0 + dy * step, yaw)
        if frames_left == 1:
            self.waypoints.pop(0)
            self.frame = 0
            if not self.waypoints:
                self.arrived = True
                self.publish_state()

    def heartbeat(self, now):
        if now - self.last_report >= HEARTBEAT_SECONDS:
            self.last_report = now
            self.publish_state()

    def publish_state(self):
        # the body is the truth: if something pushed it into another cell, that is where we are now
        if self.position is not None and self.arrived:
            cell, _ = self.reported_pose()
            if cell != self.position:
                self.position = cell
                self.nodes = [n for n in self.nodes if n["sequenceId"] <= self.last_seq]
        self.last_report = time.monotonic()
        super().publish_state()


class SimFleet:
    """
    All robots of the world. The simulator calls step_frame() once per rendered frame.

    Robots can JOIN: when the fleet manager sends initPosition to a robot this world
    doesn't have yet (e.g. R6..R20 in the 20-robot stress test), a copy of an existing
    robot's body is added to the world at that cell and connected as a new robot.
    """

    def __init__(self, broker, stage, robot_ids, frames_per_tick=FRAMES_PER_TICK, layout_path=None):
        self.broker = broker
        self.stage = stage
        self.inbox = queue.Queue()
        self.frames_per_tick = frames_per_tick
        self.kw = {"layout_path": layout_path} if layout_path else {}
        self.robots = {rid: SimRobot(rid, broker, UsdBody(stage, rid), self.inbox, frames_per_tick, **self.kw)
                       for rid in robot_ids}
        self.watcher = None

    def start(self):
        for robot in self.robots.values():
            robot.start(heartbeat=False)     # the heartbeat comes from step_frame (main thread)
        # listen for robots the manager wants that this world doesn't have yet
        self.watcher = self.broker.connect("fleetmind-world-spawner")
        self.watcher.subscribe("uagv/v2/FleetMind/+/instantActions",
                               lambda topic, msg: self.inbox.put((self._maybe_spawn, (topic, msg))))
        return self

    def _maybe_spawn(self, topic, msg):
        serial = topic.split("/")[3]
        if serial in self.robots:
            return
        init = [a for a in msg.get("actions", []) if a.get("actionType") == "initPosition"]
        if not init or not self.robots:
            return
        template = next(iter(sorted(self.robots)))
        layer = self.stage.GetRootLayer()
        if not Sdf.CopySpec(layer, f"/World/Robots/{template}", layer, f"/World/Robots/{serial}"):
            return
        prim = self.stage.GetPrimAtPath(f"/World/Robots/{serial}")
        attr = prim.GetAttribute("fleetmind:robot_id")
        if attr:
            attr.Set(serial)
        robot = SimRobot(serial, self.broker, UsdBody(self.stage, serial), self.inbox, self.frames_per_tick, **self.kw)
        self.robots[serial] = robot
        robot.start(heartbeat=False)
        robot._receive("instantActions", msg)          # the initPosition it was sent before it existed
        print(f"[fleetmind] {serial} joined the world")

    def step_frame(self, now=None):
        while True:
            try:
                handler, args = self.inbox.get_nowait()
            except queue.Empty:
                break
            handler(*args)
        for robot in self.robots.values():
            robot.animate()
        now = time.monotonic() if now is None else now
        for robot in self.robots.values():
            if robot.arrived:
                robot.heartbeat(now)

    def busy(self):
        return not self.inbox.empty() or not all(r.arrived for r in self.robots.values())

    def stop(self):
        for robot in self.robots.values():
            robot.stop()
        if self.watcher:
            self.watcher.close()


def robot_ids_in(stage):
    robots = stage.GetPrimAtPath("/World/Robots")
    return sorted(child.GetName() for child in robots.GetChildren()) if robots else []
