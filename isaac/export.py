"""
Read the 3D world (isaac/warehouse.usda) back as plain data, so the browser can draw it.

Every box in the USD world becomes {"kind", "name", "centre": [x, y, z], "size": [sx, sy, sz],
"colour": [r, g, b]} in metres (Z up), plus its fleetmind:* facts (cell, shelf, item).
The website's 3D view (web/static/world3d.html) draws exactly these boxes, so what
you see in the browser is the same world Isaac Sim opens.
"""

import os

from pxr import Usd, UsdGeom

from isaac.world import DEFAULT_OUT, build_world


def _facts(prim):
    """The fleetmind:* attributes of a prim or its nearest tagged parent."""
    while prim and prim.IsValid():
        attrs = {a.GetName().split(":", 1)[1]: a.Get() for a in prim.GetAttributes()
                 if a.GetName().startswith("fleetmind:") and a.HasValue()}
        if attrs:
            return {k: (list(v) if hasattr(v, "__len__") and not isinstance(v, str) else v) for k, v in attrs.items()}
        prim = prim.GetParent()
    return {}


def world_data(path=DEFAULT_OUT):
    if not os.path.exists(path):
        build_world(out=path)
    stage = Usd.Stage.Open(path)
    cache = UsdGeom.XformCache()
    boxes, robots = [], {}
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Cube):
            continue
        parts = str(prim.GetPath()).split("/")
        local = UsdGeom.Xformable(prim).GetLocalTransformation()
        scale = [local.GetRow(i).GetLength() for i in range(3)]
        colour = UsdGeom.Cube(prim).GetDisplayColorAttr().Get()
        colour = list(colour[0]) if colour else [0.7, 0.7, 0.7]
        if parts[2] == "Robots":
            # robot parts are given relative to the robot, because the robot moves
            robots.setdefault(parts[3], []).append({
                "name": parts[-1], "centre": list(local.ExtractTranslation()), "size": scale, "colour": colour})
            continue
        world = cache.GetLocalToWorldTransform(prim)
        boxes.append({"name": parts[-1], "centre": list(world.ExtractTranslation()), "size": scale,
                      "colour": colour, **_facts(prim)})
    floor = next(b for b in boxes if b.get("kind") == "floor")
    return {"source": os.path.basename(path), "width": floor["size"][0], "depth": floor["size"][1],
            "boxes": boxes, "robot_parts": robots}
