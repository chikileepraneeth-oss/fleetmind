"""
Cleaners, technicians and work tickets (scripts/shift_lead/staff.py): a spill waits for a cleaner,
a broken robot for a technician. Without staff, every run stays exactly the same as before.
"""

from scripts.bus import InMemoryBroker
from scripts.classes import RobotStatus
from scripts.record import build_simulation
from scripts.shift_lead.staff import CLEAN_MINUTES, NEVER
from web.session import LiveSession

LAYOUT = "layouts/main_warehouse.json"
SPILL_A, SPILL_B = (3, 4), (7, 11)          # aisle cells far apart


def calm_sim(scenario="calm"):
    return build_simulation(scenario, LAYOUT, staff=True)


def spill(sim, cell, kind="spill"):
    """What a random blocked cell looks like when staff are on: blocked, with no timer."""
    sim.warehouse.block_cell(cell)
    sim.blocked_until[cell] = NEVER
    sim.block_reasons[cell] = kind


def run_until(sim, done, limit=300):
    for _ in range(limit):
        sim.step()
        if done():
            return
    raise AssertionError("it did not happen in time")


def test_without_staff_nothing_changes():
    a = build_simulation("realistic_friday", LAYOUT)
    b = build_simulation("realistic_friday", LAYOUT, staff=False)
    for _ in range(400):
        a.step(), b.step()
    assert a.staff is None and a.kpis() == b.kpis() and a.events == b.events


def test_a_spill_opens_a_cleaning_ticket():
    sim = calm_sim()
    spill(sim, SPILL_A)
    sim.step()
    tickets = sim.staff.tickets
    assert len(tickets) == 1
    t = tickets[0]
    assert (t.kind, t.cell, t.what) == ("clean", SPILL_A, "spill")
    assert t.status == "assigned" and t.assigned == "C1" and t.start_point == "desk"


def test_a_cleaner_walks_there_cleans_and_the_cell_opens_again():
    sim = calm_sim()
    spill(sim, SPILL_A)
    run_until(sim, lambda: SPILL_A not in sim.warehouse.blocked)
    t = sim.staff.tickets[0]
    assert t.status == "done" and t.note == "cleaned"
    assert t.done - t.started == CLEAN_MINUTES["spill"]        # 8 minutes of cleaning once there
    assert t.started > t.created                               # first the walk from the desk
    assert "spill" not in sim.block_reasons.values()
    run_until(sim, lambda: all(m.task == "free" for m in sim.staff.members))   # and back to the desk


def test_the_cell_stays_blocked_until_it_is_cleaned_and_robots_drive_around_it():
    sim = calm_sim("normal")
    spill(sim, SPILL_A)
    seen_blocked = 0
    while SPILL_A in sim.warehouse.blocked:
        sim.step()
        seen_blocked += 1
        for robot in sim.warehouse.robots.values():
            assert robot.position != SPILL_A
            if sim.config.movement == "cooperative":
                assert SPILL_A not in robot.path
        assert seen_blocked < 300
    assert seen_blocked > CLEAN_MINUTES["spill"]
    assert sim.kpis()["collisions"] == 0


def test_two_spills_are_cleaned_by_two_cleaners_at_the_same_time():
    sim = calm_sim()
    spill(sim, SPILL_A)
    spill(sim, SPILL_B, "box")
    both_working = False
    for _ in range(300):
        sim.step()
        working = {m.id for m in sim.staff.members if m.task == "working"}
        both_working |= working == {"C1", "C2"}
        if not sim.staff.open_tickets():
            break
    assert both_working
    assert {t.assigned for t in sim.staff.tickets} == {"C1", "C2"}
    assert SPILL_A not in sim.warehouse.blocked and SPILL_B not in sim.warehouse.blocked


def test_a_broken_robot_waits_for_a_technician():
    sim = calm_sim()
    robot = sim.warehouse.robots["R1"]
    robot.break_down(sim.config.repair_ticks)
    sim.step()
    t = sim.staff.active("repair", robot="R1")
    assert t and t.assigned == "T1"
    left_before = robot.repair_left
    while t.status == "assigned":                              # walking: the repair does not run yet
        sim.step()
    assert robot.repair_left == left_before
    run_until(sim, lambda: robot.status != RobotStatus.ERROR)
    assert t.status == "done" and t.note == "repaired"


def test_people_never_stand_on_a_robot_and_robots_never_drive_into_people():
    sim = build_simulation("breakdown_day", LAYOUT, staff=True)
    for _ in range(400):
        sim.step()
        people = {m.position for m in sim.staff.members if m.position}
        assert not people & set(sim.warehouse.occupied)
    assert sim.kpis()["collisions"] == 0
    assert sim.staff.done_count > 0


def test_the_frame_shows_staff_and_tickets():
    sim = calm_sim()
    spill(sim, SPILL_A)
    sim.step()
    s = LiveSession(LAYOUT, scenario="calm", shift_lead=True)
    s.sim = sim
    state = s.state_message()
    assert {p["id"] for p in state["staff"]} == {"C1", "C2", "T1", "T2"}
    assert state["tickets"][0]["kind"] == "clean"
    why = next(b["why"] for b in state["blocked_info"] if tuple(b["cell"]) == SPILL_A)
    assert "C1" in why


def test_a_lost_robot_gets_a_restart_ticket_and_comes_back():
    s = LiveSession(LAYOUT, scenario="calm", broker=InMemoryBroker(), link_options={"state_timeout": 0},
                    shift_lead=True)
    for _ in range(20):
        s.step()
    s.agents["R2"].crash()
    s.step()
    assert "R2" in s.sim.lost
    for _ in range(120):
        s.step()
        if "R2" not in s.sim.lost:
            break
    assert "R2" not in s.sim.lost
    t = next(t for t in s.sim.staff.tickets if t.kind == "restart")
    assert t.robot == "R2" and t.note == "restarted" and t.assigned.startswith("T")
    assert any(e[2] == "technician_restarted" and e[1] == "R2" for e in s.sim.events)
    s.close()
