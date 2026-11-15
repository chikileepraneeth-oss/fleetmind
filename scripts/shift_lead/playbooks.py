"""
Playbooks: what the Shift Lead does for each incident it knows, decided by rules, at once.

    spill, box, pallet   a cleaning ticket; the cell stays closed; robots reroute around it
    robot_broken         a repair ticket; its order goes to another robot
    robot_lost           a restart ticket; its order is already back in the queue
    shelf_empty          a restock ticket first, so the next orders for it don't fail
    battery_critical     send it to the nearest free charger

Every incident gets one of three decisions, with the reason in one sentence:

    "robots can work around it"   no person needed
    "send a person"               a ticket for a cleaner or technician
    "needs a manager"             no playbook fits: escalate (scripts/shift_lead/lead.py)

A playbook only gets a ToolBox: the same fleet tools a person or the AI uses
(scripts/fleet_tools.py). It never touches the simulation itself.
"""

from scripts.fleet_tools import ToolInputError
from scripts.shift_lead.staff import CLEAN_MINUTES, RESTART_MINUTES, RESTOCK_MINUTES

WORK_AROUND = "robots can work around it"
SEND_PERSON = "send a person"
NEEDS_MANAGER = "needs a manager"


class ToolBox:
    """The fleet tools, with every call written down (tool, input, ok, short result)."""

    def __init__(self, tools):
        self._tools = tools
        self.calls = []

    def call(self, name, **arguments):
        try:
            result = getattr(self._tools, name)(**arguments)
            self.calls.append({"tool": name, "input": arguments, "ok": True, "result": _short(result)})
            return result
        except ToolInputError as error:
            self.calls.append({"tool": name, "input": arguments, "ok": False, "result": str(error)})
            return None


def clean(box, incident, advice=None):
    cells = [list(c) for c in (incident.cells or (incident.cell,))]
    tickets = []
    for cell in cells:                                   # one ticket per cell: two cleaners can share a big spill
        ticket = box.call("open_ticket", kind="clean", cell=cell, what=incident.kind)
        box.call("reroute_around", cell=cell)
        if ticket:
            tickets.append(ticket["ticket"]["id"])
    minutes = CLEAN_MINUTES.get(incident.kind, 6)
    where = cells[0] if len(cells) == 1 else f"{len(cells)} cells from {cells[0]}"
    if not tickets:
        return WORK_AROUND, f"The {incident.kind} at {where} could not get a ticket; robots plan around it."
    incident.ticket = ", ".join(tickets)
    tip = f" {advice}" if advice else ""
    return SEND_PERSON, (f"A {incident.kind} does not go away by itself: a cleaner cleans it "
                         f"({minutes} min per cell once there), and robots plan around {where} meanwhile.{tip}")


def robot_broken(box, incident, advice=None):
    rid = incident.robot
    robot = box.call("get_robot", robot_id=rid)
    ticket = box.call("open_ticket", kind="repair", robot_id=rid)
    if ticket:
        incident.ticket = ticket["ticket"]["id"]
    gave = ""
    if robot and robot.get("order") and not robot.get("carrying"):
        if box.call("reassign_order", robot_id=rid, reason="it broke down"):
            gave = f" Its order {robot['order']['id']} goes to another robot, so it doesn't wait for the repair."
    elif robot and robot.get("carrying"):
        gave = f" It keeps {robot['carrying']} and delivers it after the repair."
    return SEND_PERSON, f"{rid} can't repair itself: a technician walks there.{gave}"


def robot_lost(box, incident, advice=None):
    rid = incident.robot
    ticket = box.call("open_ticket", kind="restart", robot_id=rid)
    if ticket:
        incident.ticket = ticket["ticket"]["id"]
    return SEND_PERSON, (f"{rid} has no signal: a technician walks to {list(incident.cell)} and restarts it "
                         f"({RESTART_MINUTES} min there). Its order is already back in the queue, and robots "
                         f"drive around its cell.")


def shelf_empty(box, incident, advice=None):
    ticket = box.call("open_ticket", kind="restock", shelf=incident.shelf)
    if ticket:
        incident.ticket = ticket["ticket"]["id"]
    return SEND_PERSON, (f"Shelf {incident.shelf} is empty, so new orders for it would fail: "
                         f"restock it first ({RESTOCK_MINUTES} min at the shelf).")


def battery_critical(box, incident, advice=None):
    rid = incident.robot
    sent = box.call("send_to_charger", robot_id=rid)
    if sent:
        return WORK_AROUND, f"{rid} is at {sent['battery']}%: it drives to the charger at {sent['charger']} now."
    return WORK_AROUND, f"{rid} is low on battery but busy: the battery check sends it to charge after this job."


PLAYBOOKS = {
    "spill": clean,
    "box": clean,
    "pallet": clean,
    "robot_broken": robot_broken,
    "robot_lost": robot_lost,
    "shelf_empty": shelf_empty,
    "battery_critical": battery_critical,
}


def run_playbook(tools, incident, advice=None):
    """Run the playbook for this incident. Returns (decision, reason, calls), or None if no playbook fits."""
    playbook = PLAYBOOKS.get(incident.kind)
    if playbook is None:
        return None
    box = ToolBox(tools)
    decision, reason = playbook(box, incident, advice)
    return decision, reason, box.calls


def _short(result, limit=160):
    text = str(result)
    return text if len(text) <= limit else text[:limit] + " ..."
