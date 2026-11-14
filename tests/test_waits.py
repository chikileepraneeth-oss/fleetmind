"""
Tests for the wait report (scripts/waits.py).
"""

from scripts.record import build_simulation
from scripts.waits import why_waiting, wait_report, summary, REASONS
from tests.test_traffic import broken_in_front


def test_reason_behind_a_broken_robot():
    sim, r1, r2 = broken_in_front()        # R2 is broken right in front of R1
    # (no step: one step later R1 is already driving around R2)
    reason, blocker = why_waiting(sim, r1)
    assert reason == "behind a broken robot" and blocker == "R2"


def test_report_adds_up():
    sim = build_simulation("normal", "layouts/main_warehouse.json")
    waits = wait_report(sim, 300)
    assert waits                                             # robots do wait sometimes
    for w in waits:
        assert w.ticks == sum(w.reasons.values())            # every waiting tick has one reason
        assert w.reason in REASONS
    all_ticks, long_ticks = summary(waits, min_wait=8)
    assert sum(all_ticks.values()) == sum(w.ticks for w in waits)
    assert sum(long_ticks.values()) <= sum(all_ticks.values())
