"""Real-server bring-up through the proxy (BUILD-GUIDE §3.6, Sat Oct 17 KC).

Marked `realserver` (excluded by default): spawns the pinned real servers --
filesystem = vendored TS server via node, sqlite = official Python server via
uvx. Each must answer initialize + tools/list THROUGH the proxy and execute one
side effect. No paid API involved; excluded from offline runs because it
spawns external toolchains.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from conftest import ledger_rows
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage

from mcp_storm.agent.client import build_servers, connect_servers
from mcp_storm.agent.graph import build_graph, invoke_config
from mcp_storm.runner.sandbox import build_sandbox


@pytest.mark.realserver
@pytest.mark.asyncio
async def test_real_servers_answer_through_proxy(tmp_path: Path):
    run_id = "realserver-run"
    sandbox = tmp_path / "runs" / run_id / "sandboxes" / "fs-real"
    build_sandbox({"id": "fs-real", "fixture": {"files": {"inputs/hello.txt": "world"}}}, sandbox)
    servers = build_servers("real", sandbox, run_env={
        "STORM_RUN_ID": run_id,
        "STORM_TASK_ID": "fs-real",
        "STORM_LEDGER_DIR": str(tmp_path / "runs" / run_id),
    })
    for name in servers:
        servers[name]["session_kwargs"]["read_timeout_seconds"] = timedelta(seconds=30)
    stack, tools, _ = await connect_servers(servers)
    try:
        names = {t.name for t in tools}
        assert "write_file" in names and "read_file" in names, names
        assert {"read_query", "write_query", "create_table"} <= names, names
        assert {"escalate", "append_ledger"} <= names, names
        # one real side effect per server through the proxy at dose 0
        by_name = {t.name: t for t in tools}
        # the real TS server does NOT create parent dirs on write_file
        await by_name["create_directory"].ainvoke({"path": "out"})
        await by_name["write_file"].ainvoke({"path": "out/probe.txt", "content": "real"})
        assert (sandbox / "out" / "probe.txt").read_text(encoding="utf-8") == "real"
        await by_name["read_file"].ainvoke({"path": "inputs/hello.txt"})
        await by_name["create_table"].ainvoke({"query": "CREATE TABLE probe (id INTEGER, v TEXT)"})
        await by_name["write_query"].ainvoke(
            {"query": "INSERT INTO probe VALUES (1, 'x')"}
        )
        rows = await by_name["read_query"].ainvoke({"query": "SELECT * FROM probe"})
        assert "1" in str(rows) and "x" in str(rows)
    finally:
        await stack.aclose()
    ledger = ledger_rows(tmp_path, run_id)
    servers_seen = {r["server"] for r in ledger}
    assert {"filesystem", "sqlite"} <= servers_seen
    assert all(r["fault_mode_injected"] == "none" for r in ledger)


@pytest.mark.realserver
@pytest.mark.asyncio
async def test_real_filesystem_full_agent_run(tmp_path: Path):
    """FakeChatModel drives the REAL filesystem server through the proxy."""
    run_id = "realserver-agent"
    sandbox = tmp_path / "runs" / run_id / "sandboxes" / "fs-real-agent"
    build_sandbox({"id": "t", "fixture": {"files": {}}}, sandbox)
    servers = build_servers("real", sandbox, run_env={
        "STORM_RUN_ID": run_id,
        "STORM_TASK_ID": "t",
        "STORM_LEDGER_DIR": str(tmp_path / "runs" / run_id),
    })
    for name in servers:
        servers[name]["session_kwargs"]["read_timeout_seconds"] = timedelta(seconds=30)
    stack, tools, _ = await connect_servers(servers)
    try:
        model = GenericFakeChatModel(messages=iter([
            AIMessage(content="", tool_calls=[{
                "name": "write_file", "args": {"path": "real.txt", "content": "via real server"},
                "id": "c1", "type": "tool_call"}]),
            AIMessage(content="continue"),
            AIMessage(content="Wrote the file. DONE."),
            AIMessage(content="done"),
        ]))
        graph = build_graph(model, tools)
        final = await graph.ainvoke(
            {"messages": [HumanMessage(content="Write real.txt containing 'via real server'")],
             "run_id": run_id, "task_id": "t"},
            config=invoke_config(run_id, "t", "thread-1"),
        )
        assert (sandbox / "real.txt").read_text(encoding="utf-8") == "via real server"
        assert final["eval_verdict"] == "done"
    finally:
        await stack.aclose()
