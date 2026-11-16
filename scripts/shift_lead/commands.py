"""
The chat without an AI: a few plain commands, understood by simple rules (the public demo never runs an LLM).

    order 2 coke / bring me 3 apples / I want a soda   -> orders, delivered by the robots you watch
    report / how are the robots?                        -> every robot in one line
    where is R3 / R3                                    -> one robot
    tickets                                             -> what the cleaners and technicians are doing
    help                                                -> these examples

Everyday names and plurals work ("2 cokes" -> 2 soda). Everything goes through the same fleet tools as
the AI, with the same checks. Anything else gets a short help text.
"""

import re

from scripts.fleet_tools import SYNONYMS, ToolInputError

HELP = "Try: order 2 soda · report · where is R3 · tickets · help"
NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
           "eight": 8, "nine": 9, "ten": 10, "some": 1}
ORDER = re.compile(r"^(?:please\s+)?(?:can you\s+)?(?:order|send|bring|deliver|get|i want|i need|i'd like)\s+"
                   r"(?:me\s+|us\s+)?(?:(?P<n>\d+|an|a|one|two|three|four|five|six|seven|eight|nine|ten|some)\s+)?"
                   r"(?:(?:bottles?|cans?|packs?|boxes?|pieces?|bags?)\s+of\s+)?(?P<item>[a-z][a-z ]*?)"
                   r"(?:\s+(?:for me|for us|please))*$")
ROBOT = re.compile(r"\b(?:r|robot\s*)(\d{1,2})\b")


def answer(tools, text, max_quantity=10):
    """The reply for one chat line. Changes only happen through the tools."""
    t = " ".join(str(text).lower().replace("?", " ").replace("!", " ").replace(",", " ").split()).strip(" .")
    if not t or t in ("help", "hi", "hello", "what can you do"):
        return f"I understand a few commands here. {HELP}"
    m = ORDER.match(t)
    if m:
        return _order(tools, m, max_quantity)
    if any(w in t for w in ("report", "status", "how are", "robots doing", "all robots", "fleet")):
        return _report(tools)
    if "ticket" in t or "cleaner" in t or "technician" in t:
        return _tickets(tools)
    r = ROBOT.search(t)
    if r:
        return _robot(tools, f"R{r.group(1)}")
    return f"I don't understand '{text}'. There is no AI in this demo, only a few commands. {HELP}"


def _order(tools, m, max_quantity):
    n = m.group("n")
    quantity = int(n) if n and n.isdigit() else NUMBERS.get(n or "a", 1)
    if not 1 <= quantity <= max_quantity:
        return f"I can order 1 to {max_quantity} at a time here."
    item = _item(tools, m.group("item").strip())
    try:
        result = tools.create_order(item, quantity=quantity)
    except ToolInputError as error:
        return str(error).split(" The items are:")[0] + " " + _menu(tools)
    ids = result.get("orders") or [result["order"]]
    note = f" {result['note']}" if result.get("note") else ""
    return (f"OK: {quantity} {result['item']} ordered ({', '.join(ids)}), from shelf {result['shelf']}, "
            f"due by {result['deadline']}.{note} Watch the robots pick it up on the map.")


def _item(tools, word):
    """'cokes' -> 'coke' (-> soda), 'apple' -> 'apples': only words that really exist, nothing guessed."""
    known = {i for items in tools.catalogue().values() for i in items}
    for candidate in (word, word + "s", word + "es", word[:-1], word[:-2] if word.endswith("es") else word):
        if candidate in known or candidate in SYNONYMS:
            return candidate
    return word


def _menu(tools):
    return "We have: " + "; ".join(f"{g}: {', '.join(items)}" for g, items in tools.catalogue().items()) + "."


def _report(tools):
    r = tools.robot_report()
    lines = [f"Robots at {r['time']}:"]
    for x in r["robots"]:
        load = f", carrying {x['carrying']}" if x["carrying"] else ""
        problem = f" ({x['problem']})" if x["problem"] else ""
        lines.append(f"- {x['id']} at {x['cell']}: {x['doing']}{load}, battery {x['battery']:.0f}%, "
                     f"{x['delivered_today']} delivered today{problem}")
    f = r["fleet"]
    on_time = "-" if f["on_time_rate"] is None else f"{f['on_time_rate'] * 100:.0f}%"
    lines.append(f"Orders: {f['delivered']} delivered, {f['waiting']} waiting, {f['in_progress']} on the way, "
                 f"{f['late']} late, {on_time} on time.")
    return "\n".join(lines)


def _robot(tools, rid):
    try:
        x = tools.get_robot(rid)
    except ToolInputError as error:
        return str(error)
    order = x["order"]
    doing = f"{x['doing']}" + (f" with {order['id']} ({order['item']}, due {order['deadline']})" if order else "")
    return (f"{x['id']} is at {x['cell']}: {doing}. Battery {x['battery']:.0f}%, "
            f"{x['deliveries_today']} delivered today" + (" (lost: a technician is on the way)" if x["lost"] else "") + ".")


def _tickets(tools):
    try:
        t = tools.list_tickets()
    except ToolInputError as error:
        return str(error)
    staff = "; ".join(f"{s['id']} {s['doing']}" for s in t["staff"])
    if not t["tickets"]:
        return f"No open tickets. Staff: {staff}."
    open_ = "; ".join(f"{x['id']} {x['kind']} {x['what']} ({x['status']}{', ' + x['assigned'] if x['assigned'] else ''})"
                      for x in t["tickets"])
    return f"Open tickets: {open_}. Staff: {staff}."
