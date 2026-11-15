"""
The case log (scripts/shift_lead/caselog.py): every handled incident is saved, and the next
decision of the same kind uses the option with the best result so far.
"""

import csv

from scripts.shift_lead.caselog import COLUMNS, MIN_CASES, CaseLog
from scripts.shift_lead.lead import Incident
from scripts.shift_lead.playbooks import run_playbook
from scripts.shift_lead.staff import NEVER
from web.session import LiveSession

LAYOUT = "layouts/main_warehouse.json"


def solved(kind, option, minutes, late=0, number=1):
    i = Incident(f"I{number}", kind, 100, cell=(3, 4), decision="send a person", handled_by="rules",
                 actions=[{"tool": "open_ticket"}], option=option, ticket="W1")
    i.status, i.solved, i.late_caused = "solved", 100 + minutes, late
    return i


def test_every_case_is_written_to_the_csv_and_read_back(tmp_path):
    path = tmp_path / "incidents.csv"
    log = CaseLog(str(path))
    log.add(solved("spill", "nearest", 12), scenario="normal", seed=42)
    log.add(solved("robot_broken", "give away", 40, late=2, number=2), scenario="normal", seed=42)
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert list(rows[0]) == COLUMNS
    assert [(r["kind"], r["option"], r["minutes"], r["late_caused"]) for r in rows] == [
        ("spill", "nearest", "12", "0"), ("robot_broken", "give away", "40", "2")]
    assert rows[0]["actions"] == "open_ticket" and rows[0]["scenario"] == "normal"
    again = CaseLog(str(path))                            # the next start of the website remembers them
    assert len(again.cases) == 2


def test_the_lookup_picks_the_option_with_the_better_result():
    log = CaseLog()
    for n in range(MIN_CASES):
        log.add(solved("spill", "nearest", 20, number=n))
        log.add(solved("box", "from desk", 12, number=10 + n))          # boxes and spills are learned together
    best = log.best("pallet")
    assert best["option"] == "from desk"
    assert "12.0" in best["why"] and "20.0" in best["why"]


def test_late_orders_count_against_an_option():
    log = CaseLog()
    for n in range(MIN_CASES):
        log.add(solved("robot_broken", "give away", 40, late=0, number=n))
        log.add(solved("robot_broken", "keep", 35, late=2, number=10 + n))   # faster, but 2 late orders each time
    assert log.best("robot_broken")["option"] == "give away"


def test_an_option_without_enough_cases_is_tried_first():
    log = CaseLog()
    assert log.best("spill")["option"] == "nearest"                   # nothing known: the first option
    for n in range(MIN_CASES):
        log.add(solved("spill", "nearest", 5, number=n))
    advice = log.best("spill")
    assert advice["option"] == "from desk" and "to learn" in advice["why"]
    assert log.best("robot_lost") is None                             # only one way to do it: nothing to choose


def test_the_playbook_follows_the_advice():
    from tests.test_playbooks import FakeTools
    tools = FakeTools()
    incident = Incident("I1", "robot_broken", 10, cell=(5, 5), robot="R2")
    _, reason, _ = run_playbook(tools, incident, {"option": "keep", "why": "Earlier cases: 'keep' scored best."})
    assert incident.option == "keep" and "reassign_order" not in [n for n, _ in tools.calls]
    assert "keeps its order O-0007" in reason and "Earlier cases" in reason


def test_the_live_shift_lead_saves_cases_and_uses_them(tmp_path):
    path = tmp_path / "incidents.csv"
    s = LiveSession(LAYOUT, scenario="calm", shift_lead=True, case_log_path=str(path))
    sim = s.sim
    for n, cell in enumerate([(3, 4), (7, 11), (9, 3), (3, 12)]):
        sim.warehouse.block_cell(cell)
        sim.blocked_until[cell], sim.block_reasons[cell] = NEVER, "spill"
        for _ in range(200):
            s.step()
            if cell not in sim.warehouse.blocked:
                break
        for _ in range(60):                               # let the cleaner walk back
            s.step()
    cases = s.lead.caselog.cases
    assert len(cases) == 4 and all(c["kind"] == "spill" and int(c["minutes"]) > 8 for c in cases)
    assert [c["option"] for c in cases] == ["nearest", "from desk", "nearest", "from desk"]   # each gets measured
    assert path.exists()
    assert s.lead.incidents[-1].advice.startswith("Trying 'from desk'")
