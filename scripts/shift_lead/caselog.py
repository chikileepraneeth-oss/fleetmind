"""
The case log: every incident the Shift Lead handled, and how well it went.

Each solved incident is saved (in memory for the live website, and in data/incidents.csv):
kind, where, when, the decision, rules or LLM, which option was used, what was done, and
the result: minutes until it was solved, and orders that went late meanwhile.

Before a playbook runs, the Shift Lead asks the log: "for this kind of incident, which option
worked best so far?" No ML model, just averages anyone can check:

    score = minutes until solved + 10 for every order that went late meanwhile   (lower is better)

    cleaning (spill, box, pallet)   "nearest"    the closest free cleaner, also one on the way back
                                    "from desk"  only a cleaner who is back at the desk
    robot_broken                    "give away"  its order goes to another robot at once
                                    "keep"       it keeps its order until the repair is done

An option with fewer than MIN_CASES cases is tried first (the one with the fewest cases),
so every option gets measured. After that, the best average score wins.
"""

import csv
import datetime
import os
import statistics

from scripts.shift_lead.staff import CLEANABLE

OPTIONS = {
    "clean": ["nearest", "from desk"],
    "robot_broken": ["give away", "keep"],
}
MIN_CASES = 3
LATE_WEIGHT = 10
COLUMNS = ["date", "scenario", "seed", "id", "kind", "cell", "tick", "decision", "handled_by", "option",
           "actions", "minutes", "late_caused", "ticket"]


def group_of(kind):
    """Spills, boxes and pallets are learned together: it is the same cleaning job."""
    return "clean" if kind in CLEANABLE else kind


def score(case):
    return float(case["minutes"]) + LATE_WEIGHT * float(case["late_caused"] or 0)


class CaseLog:

    def __init__(self, path=None, load=True):
        """path=None: only in memory (tests, the comparison script). Else append every case to that CSV."""
        self.path = path
        self.cases = []
        if path and load and os.path.exists(path):
            with open(path, newline="", encoding="utf-8") as f:
                self.cases = [row for row in csv.DictReader(f) if row.get("minutes") not in ("", None)]

    def add(self, incident, scenario="", seed=""):
        case = {
            "date": datetime.date.today().isoformat(), "scenario": scenario, "seed": seed,
            "id": incident.id, "kind": incident.kind, "cell": list(incident.cell) if incident.cell else "",
            "tick": incident.tick, "decision": incident.decision, "handled_by": incident.handled_by,
            "option": incident.option or "", "actions": " ".join(a["tool"] for a in incident.actions),
            "minutes": incident.minutes, "late_caused": incident.late_caused or 0, "ticket": incident.ticket or "",
        }
        self.cases.append(case)
        if self.path:
            new = not os.path.exists(self.path)
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=COLUMNS)
                if new:
                    writer.writeheader()
                writer.writerow(case)
        return case

    def stats(self, kind):
        """{option: (cases, average score)} for this kind of incident."""
        group = group_of(kind)
        out = {}
        for option in OPTIONS.get(group, []):
            scores = [score(c) for c in self.cases if group_of(c["kind"]) == group and c["option"] == option]
            out[option] = (len(scores), round(statistics.mean(scores), 1) if scores else None)
        return out

    def best(self, kind):
        """
        {"option", "why"} for this kind of incident, or None if it has no options.
        Too few cases for an option: try that one (so it gets measured). Else the lowest average score.
        """
        group = group_of(kind)
        if group not in OPTIONS:
            return None
        stats = self.stats(kind)
        untried = [o for o in OPTIONS[group] if stats[o][0] < MIN_CASES]
        if untried:
            option = min(untried, key=lambda o: (stats[o][0], OPTIONS[group].index(o)))
            return {"option": option,
                    "why": f"Trying '{option}' to learn: only {stats[option][0]} earlier case(s) with it."}
        option = min(OPTIONS[group], key=lambda o: (stats[o][1], OPTIONS[group].index(o)))
        others = ", ".join(f"'{o}' {s[1]} ({s[0]} cases)" for o, s in stats.items() if o != option)
        return {"option": option,
                "why": f"Earlier cases: '{option}' scored best, {stats[option][1]} ({stats[option][0]} cases), "
                       f"against {others}. Score = minutes until solved + {LATE_WEIGHT} per late order."}

    def recent(self, limit=20):
        return self.cases[-limit:]
