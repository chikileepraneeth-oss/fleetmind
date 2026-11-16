"""
The end-of-day report: what happened today, in a few lines, when a realistic day closes.

    orders: delivered, on time, late, not done
    the 3 incidents that cost the most time, and why (the decision and its reason)
    one tip for tomorrow, from tomorrow's forecast

The template version needs no LLM (the public demo uses only this one). With an LLM switched on,
the LLM rewrites the same facts in plain language, in the background; the facts don't change.
Every report is saved as a text file in data/reports/.
"""

import datetime
import os

from scripts.demand import clock
from scripts.fleet_tools import ToolInputError
from scripts.shift_lead.plan import busy_periods


def top_incidents(incidents, n=3):
    """The incidents that cost the most: minutes until solved, plus 10 per order that went late meanwhile."""
    done = [i for i in incidents if i.minutes is not None]
    done.sort(key=lambda i: (-(i.minutes + 10 * (i.late_caused or 0)), i.tick))
    return done[:n]


def tomorrow_tip(tools, today):
    """One tip for tomorrow from the forecast, or a plain tip if there is no forecast."""
    if not today:
        return "No forecast for tomorrow: approve the morning plan when it comes."
    tomorrow = (datetime.date.fromisoformat(today) + datetime.timedelta(days=1)).isoformat()
    try:
        f = tools.get_forecast(tomorrow)
    except (ToolInputError, Exception):                # no history or no LightGBM: still a report
        return "No forecast for tomorrow: approve the morning plan when it comes."
    periods = busy_periods(f)
    if not periods:
        return (f"Tomorrow ({f['weekday']}) about {f['expected_orders_total']} orders and no hour above what the fleet "
                f"delivers: a good day for restocking and repairs that can wait.")
    first = periods[0]
    return (f"Tomorrow ({f['weekday']}) about {f['expected_orders_total']} orders, the peak at {f['peak_hour']}. "
            f"The first busy period starts at {first[0]:02d}:00: have every robot charged by then.")


def make_report(session, lead):
    sim = session.sim
    k = sim.kpis()
    plan = lead.plan or {}
    date = plan.get("date") or (sim.demand.date(sim.config.demand_day).isoformat() if sim.demand else "")
    not_done = k["orders_waiting"] + k["orders_in_progress"]
    on_time = "-" if k["on_time_rate"] is None else f"{k['on_time_rate'] * 100:.0f}%"
    top = top_incidents(lead.incidents)
    counts = lead.counts
    lines = [
        f"Day report, {date} ({session.scenario}, seed {session.seed}), closed at {clock(sim.tick)}",
        "",
        f"Orders: {k['orders_delivered']} delivered, {on_time} on time, {k['late_orders']} late, {not_done} not done.",
        f"Incidents: {len(lead.incidents)} ({counts['rules']} by rules, {counts['llm']} by the AI, "
        f"{counts['nobody']} only written down). Tickets done: {sim.staff.done_count if sim.staff else 0}.",
        f"Shift plan: {plan.get('status', 'none')}.",
        "",
        "The incidents that cost the most time:",
    ]
    if top:
        for i in top:
            late = f", {i.late_caused} orders late meanwhile" if i.late_caused else ""
            lines.append(f"- {clock(i.tick)} {i.detail}: {i.minutes} min{late}. {i.decision}: {i.reason}")
    else:
        lines.append("- none: a quiet day.")
    tip = tomorrow_tip(lead.tools, date)
    lines += ["", f"Tip for tomorrow: {tip}"]
    return {"date": date, "made_by": "template", "text": "\n".join(lines), "tip": tip,
            "facts": {"delivered": k["orders_delivered"], "late": k["late_orders"], "not_done": not_done,
                      "on_time": on_time},
            "top": [i.to_dict() for i in top]}


def save_report(report, folder, scenario, seed):
    os.makedirs(folder, exist_ok=True)
    name = f"{report['date'] or 'day'}_{scenario}_{seed}.md"
    path = os.path.join(folder, name)
    text = report["text"] + (f"\n\nIn plain words (written by the AI):\n{report['explained']}" if report.get("explained") else "")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    return path


def explain_prompt(report):
    return ("You are the shift manager of a robot warehouse. Write today's day report for the team in plain "
            "language, 4 to 6 short sentences. Use only these facts, keep every number exactly, and end with the "
            f"tip for tomorrow.\n\n{report['text']}")
