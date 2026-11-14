"""
Try the fleet's MCP tools by hand, before any AI agent uses them.

The live website must be running (it serves the tools at /mcp):
    python -m web.server

Then, in another terminal:
    python -m scripts.mcp_try                       # a tour: every tool once, plus wrong inputs
    python -m scripts.mcp_try list                  # the tools and their inputs
    python -m scripts.mcp_try call get_robot robot_id=R2
    python -m scripts.mcp_try call close_aisle aisle=BC "reason=box fell"
    python -m scripts.mcp_try --url http://127.0.0.1:8001/mcp ...

Every answer is printed as the MCP client receives it: the data, or ERROR and the message.
"""

import argparse
import asyncio
import json

from mcp import Client

DEFAULT_URL = "http://127.0.0.1:8000/mcp"

TOUR = [
    ("The whole fleet", "get_fleet_status", {}),
    ("One robot", "get_robot", {"robot_id": "R2"}),
    ("Wrong robot: a clear error", "get_robot", {"robot_id": "R9"}),
    ("A new urgent order", "create_order", {"item": "apples", "priority": "high"}),
    ("Typo in the item: suggestions", "create_order", {"item": "banana"}),
    ("Pause a robot", "pause_robot", {"robot_id": "R3", "reason": "trying the tools"}),
    ("Pause it again: a clear error", "pause_robot", {"robot_id": "R3"}),
    ("Resume it", "resume_robot", {"robot_id": "R3"}),
    ("Close an aisle", "close_aisle", {"aisle": "BC", "reason": "trying the tools"}),
    ("An aisle that doesn't exist", "close_aisle", {"aisle": "XY"}),
    ("Reopen the aisle", "reopen_aisle", {"aisle": "BC"}),
    ("The forecast (a few seconds the first time)", "get_forecast", {}),
    ("A wrong date", "get_forecast", {"date": "next friday"}),
]


def show(result, short=False):
    if result.is_error:
        text = " ".join(c.text for c in result.content if c.type == "text")
        print(f"  ERROR: {text}")
        return
    data = result.structured_content
    if data is None:
        data = "\n".join(c.text for c in result.content if c.type == "text")
    text = json.dumps(data, indent=2, ensure_ascii=False) if not isinstance(data, str) else data
    lines = text.splitlines()
    if short and len(lines) > 24:
        lines = lines[:24] + [f"  ... ({len(lines) - 24} more lines)"]
    print("\n".join("  " + line for line in lines))


def parse_args(pairs):
    """key=value pairs -> dict (numbers become numbers)."""
    out = {}
    for pair in pairs:
        key, _, value = pair.partition("=")
        try:
            out[key] = json.loads(value)
        except json.JSONDecodeError:
            out[key] = value
    return out


async def main_async(args):
    async with Client(args.url) as client:
        if args.command == "list":
            tools = await client.list_tools()
            for tool in tools.tools:
                inputs = ", ".join(tool.input_schema.get("properties", {}))
                kind = "reads" if tool.annotations and tool.annotations.read_only_hint else "changes"
                print(f"{tool.name}({inputs})  [{kind}]\n    {' '.join(tool.description.split())}\n")
            return
        if args.command == "call":
            print(f"{args.tool} {parse_args(args.pairs)}")
            show(await client.call_tool(args.tool, parse_args(args.pairs)))
            return
        tools = await client.list_tools()
        print(f"Connected to {args.url}: {len(tools.tools)} tools: {', '.join(t.name for t in tools.tools)}")
        for i, (title, tool, arguments) in enumerate(TOUR, 1):
            print(f"\n{i}. {title}:  {tool}({', '.join(f'{k}={v!r}' for k, v in arguments.items())})")
            show(await client.call_tool(tool, arguments), short=True)


def main():
    parser = argparse.ArgumentParser(description="Try the FleetMind MCP tools by hand.")
    parser.add_argument("--url", default=DEFAULT_URL)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("tour")
    sub.add_parser("list")
    call = sub.add_parser("call")
    call.add_argument("tool")
    call.add_argument("pairs", nargs="*", help="key=value")
    args = parser.parse_args()
    try:
        asyncio.run(main_async(args))
    except Exception as error:                      # e.g. the website isn't running
        print(f"Could not reach the tools at {args.url}: {error!r}\nIs the website running? python -m web.server")


if __name__ == "__main__":
    main()
