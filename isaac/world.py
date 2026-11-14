"""
Build the FleetMind warehouse as a 3D world (a USD file) for NVIDIA Isaac Sim.

USD ("Universal Scene Description", from Pixar) is the file format Isaac Sim
uses for its worlds. This script reads the same layout file as the simulation
(layouts/main_warehouse.json) and turns every cell into a real object:

    #  wall            1 m x 1 m x 2 m block, with a collider
    a-h  shelf         a rack 0.8 m x 0.8 m x 1.8 m, coloured per row, with its item
    C  charger         a yellow pad on the floor
    P  packing station a purple pad with a table
    D  loading dock    a grey pad
    robots             a simple box robot (0.6 m x 0.5 m) at its home cell

It does NOT need Isaac Sim or a GPU: it uses Pixar's usd-core package
(pip install usd-core), so the world can be built and checked on any computer
and then opened in Isaac Sim.

    python -m isaac.world                          # writes isaac/warehouse.usda
    python -m isaac.world --out my_world.usda

Coordinates: 1 cell = 1 metre, Z is up. Cell (row, col) has its centre at
x = col + 0.5, y = -(row + 0.5), so the warehouse looks the same from above as
on the website (row 0 at the top). Every prim has fleetmind:* attributes
(cell, shelf, item, kind), so the bridge (isaac/bridge.py) can find things by meaning.
"""

import argparse

from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdPhysics, UsdShade, Vt

from scripts.classes import CHARGER, DOCK, PACKING, SHELF, WALL
from scripts.layout import load_layout

DEFAULT_LAYOUT = "layouts/main_warehouse.json"
DEFAULT_OUT = "isaac/warehouse.usda"

WALL_HEIGHT = 2.0
SHELF_SIZE, SHELF_HEIGHT = 0.8, 1.8
ROBOT_SIZE = (0.6, 0.5, 0.3)          # length, width, height in metres
ROW_COLOURS = [(0.55, 0.65, 0.90), (0.55, 0.80, 0.60), (0.95, 0.80, 0.50), (0.80, 0.65, 0.90),
               (0.95, 0.60, 0.60), (0.55, 0.80, 0.85), (0.80, 0.75, 0.55), (0.70, 0.70, 0.70)]
COLOURS = {
    "floor": (0.82, 0.83, 0.85), "wall": (0.45, 0.47, 0.50), "charger": (0.95, 0.75, 0.10),
    "packing": (0.55, 0.35, 0.80), "dock": (0.35, 0.37, 0.40), "robot": (0.18, 0.44, 0.87),
}


def cell_centre(cell, z=0.0):
    """Cell (row, col) -> world point (x, y, z) in metres."""
    row, col = cell
    return Gf.Vec3d(col + 0.5, -(row + 0.5), z)


def cell_of_point(x, y):
    """World point -> the cell it is in."""
    return int(round(-y - 0.5)), int(round(x - 0.5))


class WorldBuilder:

    def __init__(self, warehouse, path):
        self.wh = warehouse
        self.stage = Usd.Stage.CreateNew(path)
        UsdGeom.SetStageUpAxis(self.stage, UsdGeom.Tokens.z)
        UsdGeom.SetStageMetersPerUnit(self.stage, 1.0)
        self.world = UsdGeom.Xform.Define(self.stage, "/World")
        self.stage.SetDefaultPrim(self.world.GetPrim())
        self.materials = {}

    # ----- helpers -----

    def material(self, name, rgb):
        if name not in self.materials:
            mat = UsdShade.Material.Define(self.stage, f"/World/Looks/{name}")
            shader = UsdShade.Shader.Define(self.stage, f"/World/Looks/{name}/Shader")
            shader.CreateIdAttr("UsdPreviewSurface")
            shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*rgb))
            shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.7)
            mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
            self.materials[name] = mat
        return self.materials[name]

    def box(self, path, centre, size, colour_name, rgb, collider=False):
        """A box of size (sx, sy, sz) metres, centred at `centre`."""
        cube = UsdGeom.Cube.Define(self.stage, path)
        cube.CreateSizeAttr(1.0)
        xf = UsdGeom.XformCommonAPI(cube)
        xf.SetTranslate(centre)
        xf.SetScale(Gf.Vec3f(*size))
        cube.CreateDisplayColorAttr(Vt.Vec3fArray([Gf.Vec3f(*rgb)]))
        UsdShade.MaterialBindingAPI.Apply(cube.GetPrim()).Bind(self.material(colour_name, rgb))
        if collider:
            UsdPhysics.CollisionAPI.Apply(cube.GetPrim())
        return cube.GetPrim()

    @staticmethod
    def tag(prim, kind, cell=None, **extra):
        prim.CreateAttribute("fleetmind:kind", Sdf.ValueTypeNames.String).Set(kind)
        if cell is not None:
            prim.CreateAttribute("fleetmind:cell", Sdf.ValueTypeNames.Int2).Set(Gf.Vec2i(*cell))
        for key, value in extra.items():
            prim.CreateAttribute(f"fleetmind:{key}", Sdf.ValueTypeNames.String).Set(str(value))

    # ----- the warehouse -----

    def build(self):
        wh = self.wh
        UsdPhysics.Scene.Define(self.stage, "/World/PhysicsScene").CreateGravityMagnitudeAttr(9.81)
        dome = UsdLux.DomeLight.Define(self.stage, "/World/Lights/Dome")
        dome.CreateIntensityAttr(600)
        sun = UsdLux.DistantLight.Define(self.stage, "/World/Lights/Sun")
        sun.CreateIntensityAttr(2500)
        UsdGeom.XformCommonAPI(sun).SetRotate(Gf.Vec3f(-40, 20, 0))

        floor = self.box("/World/Warehouse/Floor", Gf.Vec3d(wh.width / 2, -wh.height / 2, -0.01),
                         (wh.width, wh.height, 0.02), "floor", COLOURS["floor"], collider=True)
        self.tag(floor, "floor")

        rows = sorted({sid[0] for sid in wh.shelves})
        for r in range(wh.height):
            for c in range(wh.width):
                kind = wh.cell_type((r, c))
                name = f"r{r}_c{c}"
                if kind == WALL:
                    prim = self.box(f"/World/Warehouse/Walls/{name}", cell_centre((r, c), WALL_HEIGHT / 2),
                                    (1, 1, WALL_HEIGHT), "wall", COLOURS["wall"], collider=True)
                    self.tag(prim, "wall", (r, c))
                elif kind == SHELF:
                    sid = wh.shelf_at[(r, c)]
                    shelf = wh.shelves[sid]
                    rgb = ROW_COLOURS[rows.index(sid[0]) % len(ROW_COLOURS)]
                    prim = self.box(f"/World/Warehouse/Shelves/{sid}", cell_centre((r, c), SHELF_HEIGHT / 2),
                                    (SHELF_SIZE, SHELF_SIZE, SHELF_HEIGHT), f"shelf_{sid[0]}", rgb, collider=True)
                    item = next(iter(shelf.items), "")
                    self.tag(prim, "shelf", (r, c), shelf=sid, item=item, row_name=shelf.name)
                elif kind == CHARGER:
                    prim = self.box(f"/World/Warehouse/Chargers/{name}", cell_centre((r, c), 0.01),
                                    (0.9, 0.9, 0.02), "charger", COLOURS["charger"])
                    self.tag(prim, "charger", (r, c))
                    self.box(f"/World/Warehouse/Chargers/{name}_post", cell_centre((r, c), 0.4) + Gf.Vec3d(0.4, 0, 0),
                             (0.1, 0.3, 0.8), "charger", COLOURS["charger"], collider=True)
                elif kind == PACKING:
                    prim = self.box(f"/World/Warehouse/Packing/{name}", cell_centre((r, c), 0.01),
                                    (0.9, 0.9, 0.02), "packing", COLOURS["packing"])
                    self.tag(prim, "packing", (r, c))
                    self.box(f"/World/Warehouse/Packing/{name}_table", cell_centre((r, c), 0.45) + Gf.Vec3d(0.42, 0, 0),
                             (0.15, 0.9, 0.9), "packing", COLOURS["packing"], collider=True)
                elif kind == DOCK:
                    prim = self.box(f"/World/Warehouse/Docks/{name}", cell_centre((r, c), 0.01),
                                    (0.95, 0.95, 0.02), "dock", COLOURS["dock"])
                    self.tag(prim, "dock", (r, c))

        self.staff_desk()
        for rid in sorted(wh.robots):
            self.robot(rid, wh.robots[rid].position)
        return self.stage

    def staff_desk(self):
        """Where the technicians wait, in the loading-dock corner (robots never drive through docks)."""
        docks = self.wh.cells_of_type(DOCK)
        if not docks:
            return
        cell = min(docks)                                    # the dock in the top-left of the dock row
        centre = cell_centre(cell)
        desk = self.box("/World/Staff/Desk", centre + Gf.Vec3d(0, 0, 0.375), (0.8, 0.45, 0.75), "desk", (0.55, 0.40, 0.28))
        self.tag(desk, "desk", cell)
        self.box("/World/Staff/Chair", centre + Gf.Vec3d(0, 0.38, 0.22), (0.4, 0.35, 0.44), "wall", COLOURS["wall"])

    def robot(self, rid, cell):
        """A simple box robot. Isaac Sim can later swap it for a real robot model (e.g. Nova Carter)."""
        root = UsdGeom.Xform.Define(self.stage, f"/World/Robots/{rid}")
        UsdGeom.XformCommonAPI(root).SetTranslate(cell_centre(cell))
        self.tag(root.GetPrim(), "robot", tuple(cell), robot_id=rid)
        length, width, height = ROBOT_SIZE
        self.box(f"/World/Robots/{rid}/Body", Gf.Vec3d(0, 0, 0.05 + height / 2), (length, width, height),
                 "robot", COLOURS["robot"])
        # a small "front" block, so you can see which way the robot faces
        self.box(f"/World/Robots/{rid}/Front", Gf.Vec3d(length / 2 - 0.05, 0, 0.05 + height + 0.04),
                 (0.1, 0.3, 0.08), "wall", COLOURS["wall"])


def build_world(layout_path=DEFAULT_LAYOUT, out=DEFAULT_OUT):
    stage = WorldBuilder(load_layout(layout_path), out).build()
    stage.GetRootLayer().Save()
    return stage


def main():
    parser = argparse.ArgumentParser(description="Build the warehouse as a USD world for Isaac Sim.")
    parser.add_argument("--layout", default=DEFAULT_LAYOUT)
    parser.add_argument("--out", default=DEFAULT_OUT)
    args = parser.parse_args()
    stage = build_world(args.layout, args.out)
    kinds = {}
    for prim in stage.Traverse():
        attr = prim.GetAttribute("fleetmind:kind")
        if attr and attr.HasValue():
            kinds[attr.Get()] = kinds.get(attr.Get(), 0) + 1
    print(f"Wrote {args.out}: " + ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())))
    print("Open it in Isaac Sim: File > Open, or run python isaac/run_isaac.py on the GPU machine.")


if __name__ == "__main__":
    main()
