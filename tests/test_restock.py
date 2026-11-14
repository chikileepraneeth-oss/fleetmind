"""
Restocking by people and by robots (scripts/restock.py).

People: the robots don't know where they are until a robot sees one; then every
robot knows (the spot is booked) and routes through it are planned again.
Restock robots: they book their routes like every robot, so nobody has to find them.
"""

import pytest

from scripts.bus import InMemoryBroker
from scripts.fleet_link import FleetLink, start_robots
from scripts.record import build_simulation, snapshot
from scripts.simulation import Simulation, SimConfig
from scripts.layout import load_layout

LAYOUT = "layouts/main_warehouse.json"


def sim_with(scenario="restock_people", **config):
    sim = build_simulation(scenario, LAYOUT)
    for key, value in config.items():
        setattr(sim.config, key, value)
    return sim


def place(sim, member, cell):
    """Put a restocker on a cell by hand (for a test)."""
    wh = sim.warehouse
    del wh.people[member.position]
    member.position = cell
    wh.people[cell] = member.id
    member.task, member.path, member.timer = "restocking", [], 999     # stands still


def park(sim, rid, cell):
    """Move a robot to a cell by hand."""
    robot = sim.warehouse.robots[rid]
    type(sim.warehouse).move_robot(sim.warehouse, robot, cell)


# ----- people take up space -----

def test_a_robot_cannot_drive_into_a_person():
    sim = sim_with()
    p1 = sim.crew.members[0]
    place(sim, p1, (10, 5))
    park(sim, "R1", (10, 4))
    ok, reason = sim.warehouse.can_enter((10, 5))
    assert not ok and reason.startswith("person") and "P1" in reason
    moved, _ = sim.warehouse.robots["R1"].move("right", sim.warehouse)
    assert not moved


def test_robots_and_people_never_share_a_cell_over_a_day():
    sim = sim_with()
    for _ in range(400):
        sim.step()
        robots = {r.position for r in sim.warehouse.robots.values()}
        people = {m.position for m in sim.crew.members}
        assert not robots & people
    k = sim.kpis()
    assert k["collisions"] == 0 and k["restocks"] > 5


# ----- seeing, telling everyone, rerouting -----

def test_robots_do_not_know_a_person_nobody_has_seen():
    sim = sim_with()
    p1 = sim.crew.members[0]
    place(sim, p1, (1, 4))                       # top aisle, far from every robot
    sim.crew._robots_look()
    assert "P1" not in sim.crew.known


def test_a_sighting_reroutes_the_other_robots_and_books_the_spot():
    sim = sim_with()
    p1 = sim.crew.members[0]
    spot = (3, 8)
    place(sim, p1, spot)
    park(sim, "R2", (3, 10))                     # R2 is 2 cells away in the same aisle: it sees P1
    r1 = sim.warehouse.robots["R1"]
    r1.path = [(9, 8), (8, 8), (7, 8), (6, 8), (5, 8), (4, 8), spot, (2, 8)]   # R1's route goes through it
    sim.crew._robots_look()
    assert sim.crew.known["P1"]["cell"] == spot and sim.crew.known["P1"]["seen_by"] == "R2"
    assert r1.path == []                                                     # told: plans again
    assert any(e[2] == "person_seen" and e[1] == "R2" for e in sim.events)
    assert any(e[2] == "rerouted" and "R1" in e[3] for e in sim.events)
    sim.crew._book_known_people()
    now = sim.tick - 1
    assert sim.table.owner(spot, now + 5) == "P1"                         # every robot plans around it


def test_a_person_seen_somewhere_else_moves_the_spot_for_everyone():
    sim = sim_with()
    p1 = sim.crew.members[0]
    place(sim, p1, (3, 8))
    park(sim, "R2", (3, 10))
    sim.crew._robots_look()
    park(sim, "R2", (9, 13))                     # R2 drives off (so it doesn't look at the old spot)
    place(sim, p1, (5, 8))
    park(sim, "R3", (5, 10))
    sim.crew._robots_look()
    assert sim.crew.known["P1"]["cell"] == (5, 8) and sim.crew.known["P1"]["seen_by"] in ("R2", "R3")
    assert any(e[2] == "person_seen" and "moved from (3, 8) to (5, 8)" in e[3] for e in sim.events)


def test_a_stale_spot_is_cleared_when_a_robot_sees_it_empty():
    sim = sim_with()
    p1 = sim.crew.members[0]
    sim.crew.known["P1"] = {"cell": (3, 8), "seen_by": "R2", "tick": sim.tick, "moving": False}
    place(sim, p1, (1, 2))                       # P1 is really far away
    park(sim, "R3", (3, 9))                      # R3 looks at (3, 8): nobody there
    sim.crew._robots_look()
    assert "P1" not in sim.crew.known
    assert any(e[2] == "person_gone" and e[1] == "R3" and "(3, 8)" in e[3] for e in sim.events)


def test_robots_do_not_see_through_shelves():
    sim = sim_with()
    p1 = sim.crew.members[0]
    place(sim, p1, (1, 4))                       # above shelf row A
    park(sim, "R1", (3, 4))                      # below it: 2 cells, but shelf A3 is in between
    sim.crew._robots_look()
    assert "P1" not in sim.crew.known


def test_with_badges_the_fleet_always_knows_where_people_are():
    sim = sim_with(people_tracking="badges")
    for _ in range(60):
        sim.step()
        assert {k["cell"] for k in sim.crew.known.values()} == {m.position for m in sim.crew.members}


# ----- restock robots -----

def test_restock_robots_book_their_route_like_every_robot():
    sim = sim_with("restock_robots")
    booked = False
    for _ in range(40):
        sim.step()
        for m in sim.crew.members:
            if m.path:
                tick = sim.tick + 1
                booked = booked or sim.table.owner(m.path[0], tick - 1) == m.id or \
                    sim.table.owner(m.path[0], tick) == m.id
    assert booked
    assert sim.crew.known_cells() == {m.position for m in sim.crew.members}
    assert sim.kpis()["people_reports"] == 0                 # nobody has to find them


# ----- the work gets done -----

def test_restocking_fills_the_shelves():
    sim = sim_with()
    sim.run(300)
    restocked = [e[3].split()[0] for e in sim.events if e[2] == "restocked"]
    assert restocked
    assert sim.crew.restocks == len(restocked)
    shelf = sim.warehouse.shelves[restocked[-1]]
    assert all(n <= 20 for n in shelf.items.values())


def test_restocking_needs_cooperative_movement():
    with pytest.raises(ValueError, match="cooperative"):
        Simulation(load_layout(LAYOUT), seed=1, config=SimConfig(restock="people", movement="astar"))


def test_without_restocking_nothing_changes():
    sim = build_simulation("normal", LAYOUT)
    assert sim.crew is None and "people" not in snapshot(sim)


# ----- over MQTT -----

@pytest.mark.parametrize("scenario", ["restock_people", "restock_robots"])
def test_restocking_over_messages_is_exactly_the_same_run(scenario):
    direct = build_simulation(scenario, LAYOUT)
    broker = InMemoryBroker()
    sim = build_simulation(scenario, LAYOUT)
    start_robots(broker, sorted(sim.warehouse.robots), LAYOUT, heartbeat=False)
    link = FleetLink(sim, broker, state_timeout=0, clock=lambda: 0.0)
    for _ in range(200):
        direct.step()
        link.step()
        assert snapshot(sim) == snapshot(direct)
    assert link.mismatches == 0
