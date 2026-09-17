"""Offline stdio echo MCP server for proxy framing/fault tests (offline-first law).

Tools:
- echo(text)          -> echoes text back (deterministic bytes for golden tests)
- echo_json(payload)  -> json.dumps(payload) text (arrays exercise malformed/partial)
- sleep_echo(text, delay_s) -> sleeps then echoes (exercises hold paths, small delays)
- hang()              -> NEVER answers tools/call (day-1 dropped/timeout falsification)

Run: python -m mcp_storm.testing.fake_server
"""

from __future__ import annotations

import asyncio
import json

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("storm-fake-echo")


@mcp.tool()
def echo(text: str) -> str:
    """Echo the text back."""
    return text


@mcp.tool()
def echo_json(payload: dict) -> str:
    """Echo a JSON payload as compact text (arrays survive verbatim)."""
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


@mcp.tool()
def echo_array(items: list) -> str:
    """Echo a list as a compact JSON array text (malformed/partial variant b)."""
    return json.dumps(items, separators=(",", ":"), ensure_ascii=False)


@mcp.tool()
def sleep_echo(text: str, delay_s: float = 0.1) -> str:
    """Sleep delay_s, then echo."""
    asyncio.get_running_loop()  # fail loudly if run outside async context
    import time

    time.sleep(delay_s)
    return text


@mcp.tool()
async def hang() -> str:
    """Never respond: falsifies client timeout behavior on dropped responses."""
    import anyio

    await anyio.sleep_forever()
    raise NotImplementedError


if __name__ == "__main__":
    mcp.run()
