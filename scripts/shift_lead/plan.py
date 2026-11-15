"""
The shift plan: made at 06:00 from the forecast, approved once by a person, then the fleet follows it.

The forecast (get_forecast) says how many orders each hour brings and which hours are above
what the fleet can deliver (about 2.1 orders per robot per hour). Each busy period (one or more
hours in a row above that) gets three steps:

    charge          the hour before it starts: every robot with nothing to do charges to 95%
    park            during it: robots with nothing to do wait near the packing stations
    restock pause   during it: restockers start no new trips into the aisles

The rules make the plan. With an LLM switched on (never in the public demo), the LLM may write
the explanation for the person, but the steps stay the same and still only run through the
tools, after the person clicked Approve.
"""

from scripts.shift_lead.playbooks import ToolBox

CHARGE_TARGET = 95
CHARGE_BEFORE_HOURS = 1


def busy_periods(forecast):
    """[(first hour, hour after the last), ...] of the hours above capacity, joined when they follow each other."""
    hours = sorted(int(h[:2]) for h in forecast["hours_over_capacity"])
    periods = []
    for h in hours:
        if periods and periods[-1][1] == h:
            periods[-1][1] = h + 1
        else:
            periods.append([h, h + 1])
    return [tuple(p) for p in periods]


def make_plan(tools, made_by="rules"):
    """The plan for the simulated day, from the forecast. Nothing is changed yet."""
    forecast = tools.get_forecast()
    expected = {int(h["hour"][:2]): h["expected_orders"] for h in forecast["hours"]}
    capacity = forecast["capacity_per_hour_estimate"]
    steps = []
    for start, end in busy_periods(forecast):
        peak = max(expected[h] for h in range(start, end))
        charge_from = max(6, start - CHARGE_BEFORE_HOURS)
        if charge_from < start:
            steps.append({"kind": "charge", "start": f"{charge_from:02d}:00", "end": f"{start:02d}:00",
                          "target": CHARGE_TARGET,
                          "why": f"Up to {peak:.0f} orders an hour are expected from {start:02d}:00, more than the "
                                 f"about {capacity:.0f} the fleet delivers: every robot starts it with a full battery."})
        steps.append({"kind": "park", "start": f"{start:02d}:00", "end": f"{end:02d}:00",
                      "why": "Robots with nothing to do wait near the packing stations, so the next order is close."})
        steps.append({"kind": "restock_pause", "start": f"{start:02d}:00", "end": f"{end:02d}:00",
                      "why": "Restockers stay out of the aisles while the robots are busiest."})
    periods = ", ".join(f"{s:02d}:00-{e:02d}:00" for s, e in busy_periods(forecast)) or "none"
    text = (f"{forecast['weekday']} {forecast['date']}: about {forecast['expected_orders_total']} orders, the peak at "
            f"{forecast['peak_hour']}. Busy periods (above about {capacity:.0f} orders an hour): {periods}. "
            + (f"{len(steps)} steps below." if steps else "No busy period: nothing to prepare."))
    return {"date": forecast["date"], "made_by": made_by, "text": text, "steps": steps, "status": "waiting",
            "periods": [list(p) for p in busy_periods(forecast)], "actions": []}


def apply_plan(tools, plan):
    """After the person's Approve: carry out every step through the tools. Returns the tool calls."""
    box = ToolBox(tools)
    for step in plan["steps"]:
        if step["kind"] == "charge":
            box.call("plan_charging", start=step["start"], end=step["end"], target=step["target"])
        elif step["kind"] == "park":
            box.call("plan_parking", start=step["start"], end=step["end"])
        elif step["kind"] == "restock_pause":
            box.call("plan_restock_pause", start=step["start"], end=step["end"])
    return box.calls


def explain_prompt(plan):
    lines = "\n".join(f"- {s['kind']} {s['start']}-{s['end']}: {s['why']}" for s in plan["steps"])
    return (f"You are the shift manager of a robot warehouse. Explain today's shift plan to the people on the floor "
            f"in 3 or 4 short, plain sentences. Don't change the plan and don't add steps.\n\n"
            f"Forecast: {plan['text']}\nSteps:\n{lines or '- none'}")
