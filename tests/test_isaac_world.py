"""
The Isaac Sim side, tested without Isaac Sim or a GPU:
    isaac/world.py   the warehouse as a USD world (built with Pixar's usd-core)
    isaac/bridge.py  robots whose bodies are prims in that world, driven by the fleet manager
"""

import math
import threading

import pytest

pytest.importorskip("pxr", reason="needs usd-core (pip install usd-core)")

from pxr import Usd  # noqa: E402

from isaac.bridge import SimFleet, robot_ids_in  # noqa: E402
from isaac.world import build_world, cell_centre, cell_of_point  # noqa: E402
from scripts.bus import InMemoryBroker  # noqa: E402
from scripts.fleet_link import FleetLink  # noqa: E402
from scripts.layout import load_layout  # noqa: E402
from scripts.record import build_simulation, snapshot  # noqa: E402

LAYOUT = "layouts/main_warehouse.json"


@pytest.fixture
def stage(tmp_path):
    return build_world(LAYOUT, str(tmp_path / "warehouse.usda"))


def kinds(stage):
    out = {}
    for prim in stage.Traverse():
        attr = prim.GetAttribute("fleetmind:kind")
        if attr and attr.HasValue():
            out.setdefault(attr.Get(), []).append(prim)
    return out


# ----- the world -----

def test_the_world_has_every_part_of_the_layout(stage):
    wh = load_layout(LAYOUT)
    k = kinds(stage)
    assert len(k["shelf"]) == len(wh.shelves) == 40
    assert len(k["charger"]) == 3 and len(k["packing"]) == 2 and len(k["dock"]) == 3
    assert len(k["robot"]) == 5 and robot_ids_in(stage) == ["R1", "R2", "R3", "R4", "R5"]
    assert len(k["wall"]) == sum(1 for r in range(wh.height) for c in range(wh.width) if wh.grid[r][c] == "#")


def test_shelves_know_their_item_and_stand_on_their_cell(stage):
    a1 = stage.GetPrimAtPath("/World/Warehouse/Shelves/A1")
    assert a1.GetAttribute("fleetmind:item").Get() == "apples"
    cell = tuple(a1.GetAttribute("fleetmind:cell").Get())
    assert load_layout(LAYOUT).shelf_at[cell] == "A1"
    x, y, _ = a1.GetAttribute("xformOp:translate").Get()
    assert cell_of_point(x, y) == cell


def test_world_units_are_metres_and_z_is_up(stage):
    from pxr import UsdGeom
    assert UsdGeom.GetStageUpAxis(stage) == "Z"
    assert UsdGeom.GetStageMetersPerUnit(stage) == 1.0
    assert stage.GetPrimAtPath("/World/PhysicsScene").IsValid()


@pytest.mark.parametrize("cell", [(0, 0), (3, 15), (11, 1), (7, 9)])
def test_cells_and_world_points_round_trip(cell):
    x, y, _ = cell_centre(cell)
    assert cell_of_point(x, y) == cell
    assert cell_of_point(x + 0.3, y - 0.3) == cell        # anywhere inside the cell


def test_the_world_file_opens_again(tmp_path):
    path = str(tmp_path / "w.usda")
    build_world(LAYOUT, path)
    again = Usd.Stage.Open(path)
    assert again.GetPrimAtPath("/World/Robots/R3").IsValid()


# ----- the robots in the world, driven by the fleet manager -----

def run_with_frames(link, fleet, ticks):
    """The manager steps in its own thread (like a separate program); this thread plays the frames."""
    for _ in range(ticks):
        done = threading.Event()
        worker = threading.Thread(target=lambda: (link.step(), done.set()))
        worker.start()
        while not done.is_set():
            fleet.step_frame()
        worker.join()


@pytest.fixture
def world_fleet(stage):
    broker = InMemoryBroker()
    sim = build_simulation("normal", LAYOUT)
    fleet = SimFleet(broker, stage, robot_ids_in(stage), frames_per_tick=4).start()
    link = FleetLink(sim, broker, state_timeout=5.0)
    while fleet.busy():
        fleet.step_frame()
    return sim, fleet, link


def test_robots_in_the_world_give_exactly_the_same_run(world_fleet):
    sim, fleet, link = world_fleet
    direct = build_simulation("normal", LAYOUT)
    for _ in range(80):
        direct.step()
        run_with_frames(link, fleet, 1)
        assert snapshot(sim) == snapshot(direct)
    assert link.mismatches == 0
    for rid, robot in fleet.robots.items():                # every body stands in its robot's cell
        x, y, _ = robot.body.get_pose()
        assert cell_of_point(x, y) == sim.warehouse.robots[rid].position


def test_a_driving_robot_faces_the_way_it_drives(world_fleet):
    sim, fleet, link = world_fleet
    seen = False
    for _ in range(40):
        before = {rid: r.position for rid, r in sim.warehouse.robots.items()}
        run_with_frames(link, fleet, 1)
        for rid, robot in fleet.robots.items():
            new = sim.warehouse.robots[rid].position
            if new != before[rid]:
                d_row, d_col = new[0] - before[rid][0], new[1] - before[rid][1]
                expected = math.atan2(-d_row, d_col)          # rows go down = -y
                _, _, yaw = robot.body.get_pose()
                assert abs(math.remainder(yaw - expected, 2 * math.pi)) < 1e-6
                seen = True
    assert seen


def test_a_pushed_robot_is_believed_by_the_manager(world_fleet):
    """Physics (or a person) pushes R1 one cell. The robot reports where it really is."""
    sim, fleet, link = world_fleet
    run_with_frames(link, fleet, 3)
    robot = fleet.robots["R1"]
    row, col = sim.warehouse.robots["R1"].position
    free = next(c for c in ((row, col - 1), (row, col + 1), (row - 1, col))
                if sim.warehouse.can_enter(c)[0])
    x, y, _ = cell_centre(free)
    robot.body.set_pose(x, y, 0.0)
    run_with_frames(link, fleet, 1)
    assert link.mismatches >= 1
    assert any(e[2] == "state_mismatch" and e[1] == "R1" for e in sim.events)
    run_with_frames(link, fleet, 30)                        # and the fleet carries on normally
    assert sim.kpis()["collisions"] == 0
