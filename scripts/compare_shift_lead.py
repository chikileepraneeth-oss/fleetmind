"""
Does the Shift Lead help? 30 seeds of two days, three versions, the same seeds for each.

    days        realistic_friday   the normal busy Friday
                messy_day          the same demand, 4 times more breakdowns and blocked cells, lost robots

    versions    none          no Shift Lead: the old fleet (fixed repair and blocked-cell times, no people)
                rules         cleaners, technicians, tickets, playbooks and the case log; the morning plan
                              is approved at 06:00 (as if a person clicked Approve)
                rules+llm     the same, plus the manager (an LLM) for what no playbook covers and for
                              3 or more incidents at once. Default: the ScriptedModel (no key, no cost,
                              it does not really think: a stand-in). --live: the real model from .env.
                              Risky actions are approved, as if a person said yes.

The LLM never makes a tick wait: after every tick it gets a few turns of the event loop, the same
for every run, so the runs stay repeatable.

    minutes lost to incidents = robot-minutes broken or lost + cell-minutes blocked by a spill, box or pallet

    python -m scripts.compare_shift_lead               # 30 seeds, both days (a few minutes)
    python -m scripts.compare_shift_lead --seeds 5     # quicker
    python -m scripts.compare_shift_lead --live        # rules+llm with the real model (costs tokens)

The numbers go to data/shift_lead_results.csv (one row per run).
"""

import argparse
import asyncio
import csv
import os
import statistics
from types import SimpleNamespace

from scripts.bus import InMemoryBroker
from scripts.shift_lead.staff import CLEANABLE
from web.session import LiveSession

LAYOUT = "layouts/main_warehouse.json"
DAYS = ["realistic_friday", "messy_day"]
VERSIONS = ["none", "rules", "rules+llm"]
TURNS_PER_TICK = 30                 # event-loop turns the LLM gets after every tick (never a wait)
RESULTS = "data/shift_lead_results.csv"
SCRIPT = ["1. Read the tickets and the fleet. 2. Keep the most urgent orders moving.",
          "The playbooks already opened the right tickets; nothing else to change.",
          "DONE: every incident has a ticket or the fleet works around it."]


def make_session(day, version, seed):
    needs_link = day == "messy_day"                    # lost robots only exist with the robots' messages
    s = LiveSession(LAYOUT, scenario=day, shift_lead=version != "none",
                    broker=InMemoryBroker(keep_log=False) if needs_link else None,
                    link_options={"state_timeout": 0} if needs_link else None)
    s.load_scenario(day, seed)
    return s


async def run_day(day, version, seed, live=False):
    s = make_session(day, version, seed)
    lead = s.lead
    model, agents = None, []
    if version == "rules+llm":
        from scripts.agent import FleetAgent, ScriptedModel, model_from_env
        from web.mcp_server import build_mcp

        async def nobody_watching(messages):
            return None

        if live:
            from dotenv import load_dotenv
            load_dotenv()
            model = model_from_env()
        else:
            model = ScriptedModel(SCRIPT * 200)
        hub = SimpleNamespace(session=s, lock=asyncio.Lock(), broadcast=nobody_watching)
        mcp = build_mcp(hub)
        lead.manager_allowed = True

        async def approve(request):
            return True, "approved in the evaluation"

    lost_minutes = 0
    while not s.finished:
        s.step()
        sim = s.sim
        if lead and lead.plan and lead.plan["status"] == "waiting":
            lead.answer_plan(True)                     # the person's one click at 06:00
        lost_minutes += sum(1 for r in sim._robots() if r.status.value == "error")
        lost_minutes += sum(1 for c in sim.warehouse.blocked if sim.block_reasons.get(c) in CLEANABLE)
        if version == "rules+llm":
            while lead.outbox:
                task = lead.outbox.popleft()
                if task.get("kind") in ("plan", "report"):
                    continue                           # words only: they don't change the fleet
                agent = FleetAgent(mcp, model, ask_approval=approve)
                agents.append(asyncio.create_task(agent.handle(task["text"])))
            for _ in range(TURNS_PER_TICK):
                await asyncio.sleep(0)
    for task in agents:
        task.cancel()
    k = s.sim.kpis()
    s.close()
    return {
        "day": day, "version": version, "seed": seed,
        "delivered": k["orders_delivered"], "on_time": round(100 * (k["on_time_rate"] or 0), 1),
        "late": k["late_orders"], "minutes_lost": lost_minutes,
        "llm_calls": len(model.calls) if model is not None and hasattr(model, "calls") else 0,
        "by_rules": lead.counts["rules"] if lead else 0, "by_llm": lead.counts["llm"] if lead else 0,
    }


def mean_se(values):
    m = statistics.mean(values)
    se = statistics.stdev(values) / len(values) ** 0.5 if len(values) > 1 else 0.0
    return m, se


def main():
    parser = argparse.ArgumentParser(description="Compare no Shift Lead, rules only, and rules + LLM.")
    parser.add_argument("--seeds", type=int, default=30)
    parser.add_argument("--days", nargs="+", default=DAYS, choices=DAYS)
    parser.add_argument("--live", action="store_true", help="rules+llm with the real model from .env (costs tokens)")
    parser.add_argument("--out", default=RESULTS)
    args = parser.parse_args()

    rows = []
    for day in args.days:
        for version in VERSIONS:
            for seed in range(1, args.seeds + 1):
                rows.append(asyncio.run(run_day(day, version, seed, live=args.live)))
            print(f"  {day} / {version}: done", flush=True)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    metrics = [("delivered", "delivered"), ("on_time", "on time %"), ("late", "late"),
               ("minutes_lost", "minutes lost"), ("llm_calls", "LLM calls")]
    print(f"\n{args.seeds} seeds, average +/- standard error\n")
    print(f"| Day | Version | " + " | ".join(name for _, name in metrics) + " |")
    print("|---|---|" + "---|" * len(metrics))
    for day in args.days:
        for version in VERSIONS:
            mine = [r for r in rows if r["day"] == day and r["version"] == version]
            cells = []
            for key, _ in metrics:
                m, se = mean_se([r[key] for r in mine])
                cells.append(f"{m:.1f} +/-{se:.1f}")
            print(f"| {day} | {version} | " + " | ".join(cells) + " |")
    print(f"\nSaved {len(rows)} runs to {args.out}")


if __name__ == "__main__":
    main()
