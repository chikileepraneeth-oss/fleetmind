"""
The FleetMind MCP server: the fleet's tools for an AI agent (or any MCP client).

MCP ("Model Context Protocol") is a standard way for an AI to discover and call
tools. The server lists its tools (name, description, inputs); the client calls
them and gets back plain data, or an error with a clear message.

It runs inside the live website (web/server.py), at http://127.0.0.1:8000/mcp,
so the tools see and change the SAME warehouse as the browser:
    - every tool waits for the simulation lock, like the browser's buttons
    - a tool that changes something (pause, aisle, order) sends the new state to
      every open browser tab at once, so you see what the agent did
    - over MQTT, a paused robot gets the real VDA 5050 startPause / stopPause

The tool logic is in scripts/fleet_tools.py (no MCP code there, easy to test).
Try the tools by hand: python -m scripts.mcp_try
"""

import asyncio
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from scripts.fleet_tools import FleetTools, ToolInputError

INSTRUCTIONS = (
    "Tools for the FleetMind warehouse: 5 robots carry items from 40 shelves to 2 packing stations. "
    "Start with get_fleet_status. Times are simulation minutes from 06:00 on realistic days. "
    "pause_robot and close_aisle change the live warehouse: say why in `reason`."
)

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
CHANGE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
RISKY = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)


def build_mcp(hub):
    """An MCP server whose tools act on hub.session (the live warehouse)."""
    mcp = MCPServer(name="fleetmind", title="FleetMind warehouse fleet", instructions=INSTRUCTIONS, version="1.0")
    tools = FleetTools(hub.session)

    async def run(method, *args, changes=False):
        async with hub.lock:
            try:
                result = method(*args)
            except ToolInputError as error:
                raise ToolError(str(error)) from None
            message = hub.session.state_message() if changes else None
        if message:
            await hub.broadcast([message])          # every browser tab sees the change at once
        return result

    @mcp.tool(annotations=READ)
    async def get_fleet_status() -> dict[str, Any]:
        """The whole fleet in one look: time, every robot (where, what it does, battery, load),
        orders (waiting, late, delivered), problems (lost, paused or broken robots, blocked cells,
        closed aisles) and the names of the aisles."""
        return await run(tools.get_fleet_status)

    @mcp.tool(annotations=READ)
    async def get_robot(robot_id: str) -> dict[str, Any]:
        """One robot in detail: cell, task, battery, load, its order (item, shelf, deadline),
        planned route, connection, and its last events. robot_id like "R3"."""
        return await run(tools.get_robot, robot_id)

    @mcp.tool(annotations=CHANGE)
    async def create_order(item: str, priority: str = "normal", deadline_minutes: int | None = None) -> dict[str, Any]:
        """Add a customer order for one item (e.g. "apples"). priority: "high", "normal" or "low".
        deadline_minutes: time to deliver from now (default: 60 high, 100 normal, 200 low)."""
        return await run(tools.create_order, item, priority, deadline_minutes, changes=True)

    @mcp.tool(annotations=RISKY)
    async def pause_robot(robot_id: str, reason: str = "") -> dict[str, Any]:
        """Stop a robot where it is. An order it has not picked up yet goes back to the queue;
        an item it carries stays on it. Other robots drive around it. Undo with resume_robot."""
        return await run(tools.pause_robot, robot_id, reason, changes=True)

    @mcp.tool(annotations=CHANGE)
    async def resume_robot(robot_id: str) -> dict[str, Any]:
        """Let a paused robot work again (it finishes its delivery if it carries an item)."""
        return await run(tools.resume_robot, robot_id, changes=True)

    @mcp.tool(annotations=RISKY)
    async def close_aisle(aisle: str, reason: str = "") -> dict[str, Any]:
        """Close an aisle (e.g. "BC" = between shelf rows B and C) so no robot drives in.
        Says which shelves can no longer be reached. Undo with reopen_aisle.
        The aisle names are in get_fleet_status."""
        return await run(tools.close_aisle, aisle, reason, changes=True)

    @mcp.tool(annotations=CHANGE)
    async def reopen_aisle(aisle: str) -> dict[str, Any]:
        """Open a closed aisle again (e.g. "BC"), so robots may drive and pick there.
        The closed aisles are listed in get_fleet_status under problems.closed_aisles."""
        return await run(tools.reopen_aisle, aisle, changes=True)

    @mcp.tool(annotations=READ)
    async def get_forecast(date: str | None = None) -> dict[str, Any]:
        """Expected orders for each hour (06:00-22:00) of a day, from the LightGBM forecast,
        with the peak hour and the hours above the fleet's capacity. date: YYYY-MM-DD
        (default: the simulated day). Takes a few seconds the first time."""
        # the forecast trains a model for a few seconds: don't hold the simulation lock meanwhile
        try:
            return await asyncio.to_thread(tools.get_forecast, date)
        except ToolInputError as error:
            raise ToolError(str(error)) from None

    return mcp
