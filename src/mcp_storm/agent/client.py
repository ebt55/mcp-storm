"""Agent-side MCP client config (BUILD-GUIDE §4).

The proxy is named as the server command; it spawns the pinned real server as a
child. Persistent sessions via AsyncExitStack + load_mcp_tools(session) -- the
adapters' get_tools() would spawn a NEW session (new proxy process) per tool
call, resetting call_seq and degenerating the §3.3 fault keying (DEVIATIONS #4).

Env is MERGED, never replaced (§3.5.5). The client timeout is 45s as
read_timeout_seconds (DEVIATIONS #3). Backends:
- "real": uvx-pinned mcp-server-filesystem / mcp-server-sqlite (bring-up + paid)
- "fake": in-repo offline equivalents with the same tool names (offline tests)
storm-ops is always the in-repo server.
"""

from __future__ import annotations

import os
import sys
from contextlib import AsyncExitStack
from datetime import timedelta
from pathlib import Path

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

from mcp_storm.paths import REPO_ROOT

CLIENT_TIMEOUT_S = 45

# Server pins (day-1 bring-up 2026-09-18; in every manifest + DEVIATIONS):
# - sqlite: official Python server via uvx. It is archived upstream and crashes
#   under the modern mcp SDK, so the uvx environment pins its era's SDK
#   (--with mcp==1.9.4); --db-path arg verified live.
# - filesystem: the official server is TypeScript-only upstream now (the Python
#   package is gone from the registry), so the pinned npm package is VENDORED
#   and spawned with node directly -- still a direct .exe invocation, no npx,
#   no shell. Positional allowed-dir args verified live (§3.6 duty).
SQLITE_PIN = "mcp-server-sqlite@2025.4.25"
SQLITE_MCP_CONSTRAINT = "mcp==1.9.4"
FILESYSTEM_PIN = "@modelcontextprotocol/server-filesystem@2025.7.1"
FILESYSTEM_ENTRY = (
    REPO_ROOT / "vendor" / "filesystem" / "node_modules"
    / "@modelcontextprotocol" / "server-filesystem" / "dist" / "index.js"
)


def storm_env(run_env: dict[str, str] | None = None) -> dict[str, str]:
    """os.environ | STORM_* (merge, §3.5.5 -- replacing breaks uv/uvx on Windows)."""
    env = dict(os.environ)
    if run_env:
        env.update(run_env)
    return env


def build_servers(
    backend: str,
    sandbox: Path,
    run_env: dict[str, str] | None = None,
) -> dict[str, dict]:
    sandbox = Path(sandbox)
    env = storm_env(run_env)
    base: dict[str, dict] = {
        "transport": "stdio",
        "env": env,
        "session_kwargs": {"read_timeout_seconds": timedelta(seconds=CLIENT_TIMEOUT_S)},
    }
    # --spawn-cwd <sandbox>: the real TS filesystem server resolves RELATIVE tool
    # paths against its own cwd, so the child runs inside the sandbox.
    # (--spawn-cwd must precede --spawn-args, which is argparse.REMAINDER.)
    proxy_prefix = ["run", "--directory", str(REPO_ROOT), "python", "-m", "mcp_storm.proxy"]
    if backend == "real":
        fs_args = [*proxy_prefix, "--server-name", "filesystem", "--spawn-cwd", str(sandbox),
                   "--spawn-cmd", "node",
                   "--spawn-args", str(FILESYSTEM_ENTRY), str(sandbox)]
        db_args = [*proxy_prefix, "--server-name", "sqlite", "--spawn-cwd", str(sandbox),
                   "--spawn-cmd", "uvx",
                   "--spawn-args", "--with", SQLITE_MCP_CONSTRAINT, SQLITE_PIN,
                   "--db-path", str(sandbox / "task.db")]
    elif backend == "fake":
        fs_args = [*proxy_prefix, "--server-name", "filesystem", "--spawn-cwd", str(sandbox),
                   "--spawn-cmd", sys.executable,
                   "--spawn-args", "-m", "mcp_storm.testing.fake_fs_server", str(sandbox)]
        db_args = [*proxy_prefix, "--server-name", "sqlite", "--spawn-cwd", str(sandbox),
                   "--spawn-cmd", sys.executable,
                   "--spawn-args", "-m", "mcp_storm.testing.fake_sqlite_server",
                   "--db-path", str(sandbox / "task.db")]
    else:
        raise ValueError(f"backend must be 'real' or 'fake', got {backend!r}")
    # sys.executable for in-repo children: bare "python" resolves to the system
    # interpreter inside uv-spawned processes (venv is NOT on their PATH)
    ops_args = [*proxy_prefix, "--server-name", "storm-ops", "--spawn-cwd", str(sandbox),
                "--spawn-cmd", sys.executable,
                "--spawn-args", "-m", "mcp_storm.servers.storm_ops",
                "--oracle", str(sandbox / "oracle.jsonl")]
    return {
        "filesystem": {**base, "command": "uv", "args": fs_args},
        "sqlite": {**base, "command": "uv", "args": db_args},
        "storm-ops": {**base, "command": "uv", "args": ops_args},
    }


async def connect_servers(
    servers: dict[str, dict],
) -> tuple[AsyncExitStack, list[BaseTool], MultiServerMCPClient]:
    """Open one persistent session per server and return (stack, tools, client).
    Keep the stack open for the run's lifetime; closing it tears down the
    proxy+server process trees."""
    client = MultiServerMCPClient(servers)
    stack = AsyncExitStack()
    tools: list[BaseTool] = []
    for name, conn in servers.items():
        session = await stack.enter_async_context(client.session(name))
        tools.extend(await load_mcp_tools(session, server_name=name))
    return stack, tools, client
