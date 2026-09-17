"""§3.6 day-1 falsification: what does the client actually see on a dropped response?

The fake server's `hang` tool never answers tools/call. With the real mcp SDK
ClientSession and a read timeout, the client must RAISE (not hang) -- these tests
pin the observed exception type and the elapsed bound. Observed behavior is
recorded in prereg/DEVIATIONS.md; it defines what `dropped` means to the agent.
The 45s production variant is `slow`-marked and was run once manually:
McpError at ~45-46s, within the 50s bound.
"""

from __future__ import annotations

import sys
import time
from datetime import timedelta

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def _run_call(params: StdioServerParameters, read_timeout_s: float, tool: str,
                    arguments: dict) -> tuple[float, str]:
    """Call one tool; return (elapsed, exception-type-or-NO-EXCEPTION). Teardown
    exceptions from the stdio task groups are swallowed AFTER the outcome is
    captured -- they are SDK cleanup noise, not the behavior under test."""
    outcome = ("NOT-RUN", 0.0)
    try:
        async with stdio_client(params) as (read, write), ClientSession(
            read, write, read_timeout_seconds=timedelta(seconds=read_timeout_s)
        ) as session:
            await session.initialize()
            t0 = time.monotonic()
            try:
                await session.call_tool(tool, arguments)
            except BaseException as exc:  # noqa: BLE001 - observing the type is the point
                outcome = (time.monotonic() - t0, type(exc).__name__)
            else:
                outcome = (time.monotonic() - t0, "NO-EXCEPTION")
    except BaseException:  # noqa: BLE001, S110 - teardown noise after a captured outcome
        pass
    return outcome


def test_client_raises_on_timeout_fast_variant():
    """Observed day 1 (mcp 1.30.0): read timeout surfaces as McpError wrapping
    "Timed out while waiting for response to ClientRequest" -- NOT TimeoutError."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_storm.testing.fake_server"],
    )
    elapsed, exc_name = anyio.run(_run_call, params, 2.0, "hang", {})
    assert exc_name != "NO-EXCEPTION", "client did not raise; dropped would be a hang"
    assert elapsed <= 3.5, f"timeout fired late: {elapsed:.2f}s"
    assert exc_name == "McpError", f"unexpected exception type: {exc_name}"


@pytest.mark.slow
def test_client_raises_on_timeout_production_45s():
    """The production read_timeout_seconds=45 bound: raise within <=50s (§3.6)."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_storm.testing.fake_server"],
    )
    elapsed, exc_name = anyio.run(_run_call, params, 45.0, "hang", {})
    assert exc_name == "McpError"
    assert elapsed <= 50.0, f"45s client timeout did not fire within 50s: {elapsed:.2f}s"
    print(f"\nOBSERVED dropped-mode behavior: {exc_name} after {elapsed:.2f}s at read_timeout=45")


def test_proxy_dropped_mode_client_times_out(tmp_path):
    """Full §3.6 stack: proxy(dropped) -> fake server answers -> proxy swallows ->
    SDK client raises on read timeout; elapsed bounded by the client timeout."""

    from conftest import storm_env

    env = storm_env(tmp_path, mode="dropped", dose="1.0")
    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m", "mcp_storm.proxy", "--server-name", "fake",
            "--spawn-cmd", sys.executable,
            "--spawn-args", "-m", "mcp_storm.testing.fake_server",
        ],
        env=env,
    )
    elapsed, exc_name = anyio.run(_run_call, params, 2.0, "echo", {"text": "swallowed"})
    assert exc_name == "McpError", f"proxy-dropped response surfaced as {exc_name}"
    assert elapsed <= 3.5
