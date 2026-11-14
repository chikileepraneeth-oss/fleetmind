"""
The fleet's tools: what a person or an AI agent may ask or change.

    get_fleet_status()                      the whole fleet in one look
    get_robot(robot_id)                     one robot in detail
    create_order(item, priority, deadline)  a new customer order
    pause_robot(robot_id, reason)           stop a robot where it is (resume_robot to continue)
    close_aisle(aisle, reason)              close an aisle (reopen_aisle to open it again)
    get_forecast(date)                      expected orders per hour for a day

This file has no MCP code in it (web/mcp_server.py is the thin MCP layer on top),
so every tool can be tested on its own. Every tool returns plain data, and a
wrong request raises ToolInputError with a message that says what to do instead.

Aisles have names, so an agent can say "close aisle BC":
    the floor cells next to a shelf row, between two shelf rows (or at the end).
    "AB" = between shelf rows A and B, "A" = the aisle on the other side of A, ...
"""

import difflib
from datetime import date as Date

from scripts.classes import FLOOR, OrderStatus, Priority, SHELF
from scripts.demand import START_DATE, DemandModel, clock
from scripts.orders import DEADLINE_TICKS
from scripts.record import snapshot

ORDERS_PER_ROBOT_HOUR = 2.1      # measured: 5 robots deliver about 10-11 orders an hour (README, section 14)
MAX_DEADLINE_MINUTES = 24 * 60


class ToolInputError(ValueError):
    """The request can't be done. The message says why and what to do instead."""


def find_aisles(warehouse):
    """
    {name: {"cells": [...], "shelves": [...]}} for every aisle: a run of floor
    cells directly above or below shelf cells, named after the shelf rows it touches.
    """
    wh = warehouse
    aisles = {}
    for r in range(wh.height):
        run = []
        for c in range(wh.width + 1):
            touching = set()
            if c < wh.width and wh.cell_type((r, c)) == FLOOR:
                for dr in (-1, 1):
                    n = (r + dr, c)
                    if wh.is_inside(n) and wh.cell_type(n) == SHELF:
                        touching.add(wh.shelf_at[n])
            if touching:
                run.append(((r, c), touching))
                continue
            if run:
                shelves = sorted({s for _, ts in run for s in ts})
                name = "".join(sorted({s[0] for s in shelves}))
                if name in aisles:                     # the same rows from another side: number it
                    name = f"{name}{sum(1 for k in aisles if k.startswith(name)) + 1}"
                aisles[name] = {"cells": [cell for cell, _ in run], "shelves": shelves}
                run = []
    return aisles


class FleetTools:

    def __init__(self, session):
        self.session = session              # web.session.LiveSession (its simulation changes on reset)
        self._forecasts = {}                # day -> forecast, so a repeated question is instant

    @property
    def sim(self):
        return self.session.sim

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def get_fleet_status(self):
        sim, s = self.sim, self.session
        frame = snapshot(sim)
        links = s.link.link_status() if s.link else {}
        k = sim.kpis()
        waiting = list(sim.queue)
        robots = []
        for r in frame["robots"]:
            robots.append({
                "id": r["id"], "cell": r["pos"], "doing": self._doing(r), "battery": r["battery"],
                "carrying": r["carrying"], "order": r["order"],
                **({"link": links[r["id"]]["link"]} if r["id"] in links else {}),
            })
        aisles = find_aisles(sim.warehouse)
        return {
            "scenario": s.scenario,
            "time": clock(sim.tick) if s.realistic else f"tick {sim.tick}",
            "tick": sim.tick,
            "running": s.running,
            "day_over": s.finished,
            "talks_to_robots_over": "MQTT" if s.link else "direct (no MQTT)",
            "robots": robots,
            "orders": {
                "waiting": len(waiting),
                "waiting_high_priority": sum(1 for o in waiting if o.priority == Priority.HIGH),
                "waiting_past_deadline": sum(1 for o in waiting if o.is_late(sim.tick)),
                "in_progress": k["orders_in_progress"],
                "delivered": k["orders_delivered"],
                "late": k["late_orders"],
                "on_time_rate": k["on_time_rate"],
                "avg_delivery_minutes" if s.realistic else "avg_delivery_ticks": k["avg_delivery_time"],
            },
            "problems": {
                "lost_robots": sorted(sim.lost),
                "paused_robots": sorted(sim.paused),
                "broken_robots": [r["id"] for r in frame["robots"] if r["status"] == "error" and r["task"] != "lost"],
                "blocked_cells": frame["blocked"],
                "closed_aisles": sorted(sim.closed_aisles),
            },
            "aisles": {name: a["shelves"] for name, a in aisles.items()},
        }

    def get_robot(self, robot_id):
        sim = self.sim
        rid = self._robot_id(robot_id)
        r = next(x for x in snapshot(sim)["robots"] if x["id"] == rid)
        robot = sim.warehouse.robots[rid]
        info = {
            "id": rid, "cell": r["pos"], "doing": self._doing(r), "status": r["status"],
            "battery": r["battery"], "carrying": r["carrying"],
            "target_cell": r["target"], "planned_route": r["path"][:20],
            "repair_ticks_left": r["repair"], "paused": rid in sim.paused, "lost": rid in sim.lost,
            "deliveries_today": sim.deliveries_by.get(rid, 0),
            "order": None,
        }
        if robot.order is not None:
            o = robot.order
            info["order"] = {
                "id": o.order_id, "item": o.item, "shelf": o.pickup, "packing_station": list(o.dropoff),
                "priority": o.priority.name.lower(), "status": o.status.value,
                "deadline": clock(o.deadline) if self.session.realistic else f"tick {o.deadline}",
                "late": o.is_late(sim.tick),
            }
        if self.session.link:
            info.update(self.session.link.link_status()[rid])
        recent = [e for e in sim.events if e[1] == rid][-8:]
        info["recent_events"] = [f"{t}: {name.replace('_', ' ')} {details}".strip() for t, _, name, details in recent]
        return info

    def get_forecast(self, date=None):
        """Expected orders for each opening hour of a day, from the LightGBM forecast."""
        from scripts.forecast import forecast_next_day, load_history   # pandas + LightGBM: load only when asked

        sim = self.sim
        history = load_history()
        last_day = int(history["day"].max())
        if date is None:
            day = sim.config.demand_day if sim.config.demand == "realistic" else last_day + 1
        else:
            try:
                day = (Date.fromisoformat(str(date)) - START_DATE).days
            except ValueError:
                raise ToolInputError(f"'{date}' is not a date. Use the form YYYY-MM-DD, e.g. 2026-10-02.") from None
            if day <= last_day:
                raise ToolInputError(
                    f"{date} is in the order history already (it ends on {START_DATE.fromordinal(START_DATE.toordinal() + last_day)}). "
                    f"Ask for a later day.")
            if day > last_day + 14:
                raise ToolInputError("The forecast only looks up to 14 days past the end of the order history.")
        if day not in self._forecasts:
            self._forecasts[day] = forecast_next_day(history, day)
        hourly = self._forecasts[day]
        working = [rid for rid in sim.warehouse.robots if rid not in sim.lost]
        capacity = round(len(working) * ORDERS_PER_ROBOT_HOUR, 1)
        demand = DemandModel()
        peak = max(hourly, key=hourly.get)
        return {
            "date": demand.date(day).isoformat(),
            "weekday": demand.date(day).strftime("%A"),
            "expected_orders_total": round(sum(hourly.values())),
            "hours": [{"hour": f"{h:02d}:00", "expected_orders": round(v, 1)} for h, v in hourly.items()],
            "peak_hour": f"{peak:02d}:00",
            "robots_working": len(working),
            "capacity_per_hour_estimate": capacity,
            "hours_over_capacity": [f"{h:02d}:00" for h, v in hourly.items() if v > capacity],
            "note": "Capacity is an estimate (about 2.1 orders per robot per hour, measured in this warehouse). "
                    "Big surprise deliveries can't be forecast.",
        }

    # ------------------------------------------------------------------
    # Changing things
    # ------------------------------------------------------------------

    def create_order(self, item, priority="normal", deadline_minutes=None):
        sim = self._open_day()
        name = str(item).strip().lower()
        stock = {i: n for shelf in sim.warehouse.shelves.values() for i, n in shelf.items.items()}
        if name not in stock:
            close = difflib.get_close_matches(name, stock, n=3)
            hint = f" Did you mean: {', '.join(close)}?" if close else f" Items: {', '.join(sorted(stock))}."
            raise ToolInputError(f"There is no item '{item}' in the warehouse.{hint}")
        if stock[name] <= 0:
            raise ToolInputError(f"'{name}' is out of stock (0 left on shelf {sim.warehouse.find_shelf_with(name).shelf_id}).")
        try:
            level = Priority[str(priority).strip().upper()]
        except KeyError:
            raise ToolInputError(f"priority must be 'high', 'normal' or 'low', not '{priority}'.") from None
        if deadline_minutes is None:
            deadline = DEADLINE_TICKS[level]
        else:
            if isinstance(deadline_minutes, bool) or not isinstance(deadline_minutes, (int, float)) \
                    or not 1 <= deadline_minutes <= MAX_DEADLINE_MINUTES:
                raise ToolInputError(f"deadline_minutes must be a number from 1 to {MAX_DEADLINE_MINUTES}.")
            deadline = int(deadline_minutes)
        order = sim.add_order(name, level, deadline)
        return {
            "order": order.order_id, "item": name, "shelf": order.pickup, "priority": level.name.lower(),
            "deadline": clock(order.deadline) if self.session.realistic else f"tick {order.deadline}",
            "position_in_queue": len(sim.queue),
            "stock_left_after_pick": stock[name] - 1,
        }

    def pause_robot(self, robot_id, reason=""):
        sim = self._open_day()
        rid = self._robot_id(robot_id)
        robot = sim.warehouse.robots[rid]
        if rid in sim.lost:
            raise ToolInputError(f"{rid} is lost (no signal), so it can't be paused. It is already standing still.")
        if rid in sim.paused:
            raise ToolInputError(f"{rid} is already paused. Use resume_robot to let it work again.")
        order = robot.order.order_id if robot.order else None
        carrying = robot.carrying
        sim.pause_robot(rid, reason)
        returned = order if order and not carrying else None
        return {
            "robot": rid, "paused": True, "cell": list(robot.position),
            "order_back_in_queue": returned,
            "keeps_item": carrying,
            "note": f"{rid} stands still and the others drive around it."
                    + (f" It delivers {carrying} after resume_robot." if carrying else ""),
        }

    def resume_robot(self, robot_id):
        sim = self._open_day()
        rid = self._robot_id(robot_id)
        if rid not in sim.paused:
            raise ToolInputError(f"{rid} is not paused.")
        sim.resume_robot(rid)
        robot = sim.warehouse.robots[rid]
        return {"robot": rid, "paused": False, "continues_with": robot.task or "waiting for an order"}

    def close_aisle(self, aisle, reason=""):
        sim = self._open_day()
        name, info = self._aisle(aisle)
        if name in sim.closed_aisles:
            raise ToolInputError(f"Aisle {name} is already closed. Use reopen_aisle to open it.")
        # shelves that can't be reached from ANY open spot any more: their orders would wait forever
        closed_after = {c for cells in sim.closed_aisles.values() for c in cells} | set(info["cells"])
        cut_off = [sid for sid in info["shelves"]
                   if not [c for c in sim.warehouse.access_cells(sim.warehouse.shelves[sid])
                           if c not in closed_after and c not in sim.warehouse.blocked]]
        robots_inside = sorted(sim.warehouse.occupied[c] for c in info["cells"] if c in sim.warehouse.occupied)
        sim.close_aisle(name, info["cells"], reason)
        return {
            "aisle": name, "closed": True, "cells": [list(c) for c in info["cells"]],
            "shelves_next_to_it": info["shelves"],
            "shelves_cut_off": cut_off,
            "robots_inside": robots_inside,
            "note": (f"Orders for shelves {', '.join(cut_off)} can't be picked until an aisle next to them opens. "
                     if cut_off else "Every shelf can still be reached from another aisle. ")
                    + ("Robots inside drive out; nobody drives in." if robots_inside else "Robots drive around it."),
        }

    def reopen_aisle(self, aisle):
        sim = self._open_day()
        name, _ = self._aisle(aisle)
        if name not in sim.closed_aisles:
            raise ToolInputError(f"Aisle {name} is not closed.")
        sim.reopen_aisle(name)
        return {"aisle": name, "closed": False}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _open_day(self):
        if self.session.finished:
            raise ToolInputError("The working day is over and the warehouse is closed. Reset to start the day again.")
        return self.sim

    def _robot_id(self, robot_id):
        robots = sorted(self.sim.warehouse.robots, key=lambda x: int(x[1:]) if x[1:].isdigit() else 0)
        rid = str(robot_id).strip().upper()
        if rid.isdigit():
            rid = f"R{rid}"
        if rid not in self.sim.warehouse.robots:
            raise ToolInputError(f"There is no robot '{robot_id}'. The robots are: {', '.join(robots)}.")
        return rid

    def _aisle(self, aisle):
        aisles = find_aisles(self.sim.warehouse)
        name = str(aisle).strip().upper().removeprefix("AISLE").strip()
        if name not in aisles:
            listing = "; ".join(f"{n} (next to {', '.join(a['shelves'][:1] + a['shelves'][-1:])})" for n, a in aisles.items())
            raise ToolInputError(f"There is no aisle '{aisle}'. The aisles are: {listing}.")
        return name, aisles[name]

    @staticmethod
    def _doing(r):
        if r["task"] == "lost":
            return "lost (no signal)"
        if r["status"] == "error":
            return f"broken ({r['repair']} ticks of repair left)"
        return {"idle": "idle", "to_home": "going to its parking spot", "to_pickup": "going to a shelf",
                "to_dropoff": "carrying to a packing station", "to_charger": "going to charge",
                "charging": "charging", "wait_charger": "waiting for a free charger",
                "paused": "paused"}.get(r["task"], r["task"])
