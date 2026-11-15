"""
The end-of-day report (scripts/shift_lead/report.py): when a realistic day closes, a short report
from a template (no LLM), saved to a file; with an LLM, the same facts in plain language.
"""

import asyncio

from scripts.agent import ScriptedModel
from scripts.shift_lead.lead import Incident
from scripts.shift_lead.report import top_incidents
from web.server import create_app
from web.session import LiveSession

LAYOUT = "layouts/main_warehouse.json"


def finish(session):
    while not session.finished:
        session.step()


def test_the_three_incidents_that_cost_the_most_come_first():
    def solved(n, minutes, late):
        i = Incident(f"I{n}", "spill", 10 * n, cell=(3, 4))
        i.solved, i.late_caused = 10 * n + minutes, late
        return i
    incidents = [solved(1, 10, 0), solved(2, 50, 0), solved(3, 20, 4), solved(4, 5, 0), Incident("I5", "box", 1)]
    assert [i.id for i in top_incidents(incidents)] == ["I3", "I2", "I1"]     # 20 + 40, 50, 10


def test_when_the_day_ends_there_is_a_report_and_it_is_saved(tmp_path):
    s = LiveSession(LAYOUT, scenario="realistic_friday", shift_lead=True, reports_dir=str(tmp_path))
    finish(s)
    report = s.lead.report
    k = s.sim.kpis()
    assert report and report["made_by"] == "template"
    text = report["text"]
    assert f"{k['orders_delivered']} delivered" in text and f"{k['late_orders']} late" in text
    assert "The incidents that cost the most time:" in text and "Tip for tomorrow:" in text
    assert "Saturday" in report["tip"] or "No forecast" in report["tip"]
    assert len(report["top"]) <= 3
    files = list(tmp_path.glob("*.md"))
    assert len(files) == 1 and files[0].read_text(encoding="utf-8").startswith("Day report, 2026-10-02")
    assert s.state_message()["shift_lead"]["report"]["text"] == text


def test_a_day_without_a_realistic_clock_has_no_report():
    s = LiveSession(LAYOUT, scenario="calm", shift_lead=True)
    for _ in range(100):
        s.step()
    assert s.lead.report is None


def test_the_public_demo_uses_the_template_and_no_llm():
    model = ScriptedModel(["never"])
    app = create_app(LAYOUT, scenario="realistic_friday", public=True, shift_lead=True, agent_model=model)
    s = app.state.hub.session
    finish(s)
    assert s.lead.report["made_by"] == "template" and not s.lead.outbox
    assert model.calls == []


def test_with_an_llm_the_report_is_rewritten_in_plain_words(tmp_path):
    model = ScriptedModel(["The plan (rules) needs no words here.", "A good Friday: almost every order was on time."])
    app = create_app(LAYOUT, scenario="realistic_friday", shift_lead=True, agent_model=model,
                     reports_dir=str(tmp_path))
    hub = app.state.hub
    s = hub.session

    async def go():
        finish(s)
        await hub.desk.watch()                        # hands the plan and the report to the LLM (background)
        for _ in range(50):
            await asyncio.sleep(0)
            if s.lead.report.get("explained"):
                return

    asyncio.run(go())
    report = s.lead.report
    assert report["explained"] == "A good Friday: almost every order was on time."
    assert report["made_by"] == "template (written by the AI)"
    saved = next(tmp_path.glob("*.md")).read_text(encoding="utf-8")
    assert "In plain words (written by the AI):" in saved and f"{s.sim.kpis()['late_orders']} late" in saved
    assert all(call["tools"] is None for call in model.calls)          # it only writes: no tools
