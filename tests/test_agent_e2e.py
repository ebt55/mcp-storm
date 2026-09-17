"""Agent end-to-end, offline: FakeChatModel + fake servers THROUGH the proxy
(BUILD-GUIDE §5.1, offline-first law). Covers: dose-0 completion, dropped-mode
ToolNode surfacing (the §3.6 duty), escalation path."""

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

TASK_FS = {
    "id": "fs-smoke",
    "prompt": "Create notes/a.txt containing 'alpha', notes/b.txt containing "
    "the line 'revenue: 42000', and notes/c.txt listing a.txt and b.txt.",
    "servers": ["filesystem"],
    "fixture": {"files": {}},
}


def scripted_model(messages: list[AIMessage]) -> GenericFakeChatModel:
    return GenericFakeChatModel(messages=iter(messages))


def write_call(call_id: str, path: str, content: str) -> dict:
    return {
        "name": "write_file",
        "args": {"path": path, "content": content},
        "id": call_id,
        "type": "tool_call",
    }


@pytest.fixture()
def run_paths(tmp_path: Path):
    run_id = "e2e-run"
    runs = tmp_path / "runs"
    ledger = runs / run_id
    sandbox = ledger / "sandboxes" / "fs-smoke"
    return run_id, runs, ledger, sandbox


async def run_agent(tmp_path, run_id, ledger_dir, sandbox, model, storm_over):
    task = dict(TASK_FS)
    task["fixture"] = {"files": {"inputs/.keep": ""}}
    build_sandbox(task, sandbox)
    servers = build_servers("fake", sandbox, run_env={
        "STORM_RUN_ID": run_id,
        "STORM_TASK_ID": task["id"],
        "STORM_LEDGER_DIR": str(ledger_dir),
        **storm_over,
    })
    # keep test runtime bounded when responses are dropped
    for name in servers:
        servers[name]["session_kwargs"]["read_timeout_seconds"] = timedelta(seconds=3)
    stack, tools, _client = await connect_servers(servers)
    try:
        graph = build_graph(model, tools)
        config = invoke_config(run_id, task["id"], thread_id=f"{run_id}-t")
        final = await graph.ainvoke(
            {"messages": [HumanMessage(content=task["prompt"])],
             "run_id": run_id, "task_id": task["id"]},
            config=config,
        )
        return final
    finally:
        await stack.aclose()


async def test_dose0_run_completes_and_meters(tmp_path, run_paths):
    run_id, runs, ledger, sandbox = run_paths
    # turn 1: create_directory (write_file does not create parents -- real
    # server semantics; ToolNode runs concurrent calls, so dir must be its own turn)
    model = scripted_model([
        AIMessage(content="", tool_calls=[{
            "name": "create_directory", "args": {"path": "notes"},
            "id": "c0", "type": "tool_call",
        }]),
        AIMessage(content="continue"),
        AIMessage(content="", tool_calls=[
            write_call("c1", "notes/a.txt", "alpha"),
            write_call("c2", "notes/b.txt", "revenue: 42000"),
            write_call("c3", "notes/c.txt", "a.txt\nb.txt"),
        ], usage_metadata={"input_tokens": 900, "output_tokens": 40, "total_tokens": 940}),
        AIMessage(content="continue"),
        AIMessage(content="All three files written. DONE."),
        AIMessage(content="done"),
    ])
    final = await run_agent(tmp_path, run_id, ledger, sandbox, model, {})
    assert (sandbox / "notes" / "a.txt").read_text(encoding="utf-8") == "alpha"
    assert "revenue: 42000" in (sandbox / "notes" / "b.txt").read_text(encoding="utf-8")
    rows = ledger_rows(tmp_path, run_id)
    fs_rows = [r for r in rows if r["server"] == "filesystem"]
    assert len(fs_rows) == 4
    assert {r["tool"] for r in fs_rows} == {"create_directory", "write_file"}
    assert all(r["fault_mode_injected"] == "none" for r in fs_rows)
    assert final.get("escalated") is not True
    assert final.get("eval_verdict") == "done"


async def test_dropped_mode_surfaces_error_toolmessage(tmp_path, run_paths):
    """§3.6 duty: dropped response -> client McpError -> ToolNode converts to an
    error ToolMessage; the run continues and terminates instead of hanging."""
    run_id, runs, ledger, sandbox = run_paths
    model = scripted_model([
        AIMessage(content="", tool_calls=[write_call("c1", "inputs/a.txt", "alpha")]),
        AIMessage(content="continue"),
        AIMessage(content="The tool call timed out; I will stop here."),
        AIMessage(content="done"),
    ])
    final = await run_agent(
        tmp_path, run_id, ledger, sandbox, model,
        {"STORM_MODE": "dropped", "STORM_DOSE": "1.0"},
    )
    tool_msgs = [m for m in final["messages"] if m.type == "tool"]
    assert tool_msgs, "ToolNode did not surface anything for the dropped call"
    dropped_msg = tool_msgs[0]
    assert dropped_msg.status == "error" or "Error" in str(dropped_msg.content)
    rows = [r for r in ledger_rows(tmp_path, run_id)
            if r["server"] == "filesystem" and r["tool"] == "write_file"]
    assert len(rows) == 1 and rows[0]["forwarded"] is False
    assert rows[0]["fault_mode_injected"] == "dropped"
    # NOTE: dropped swallows the RESPONSE, not the side effect -- the child
    # already executed the call (this asymmetry is part of what `dropped`
    # measures; see §3.3)


async def test_escalation_path(tmp_path, run_paths):
    run_id, runs, ledger, sandbox = run_paths
    model = scripted_model([
        AIMessage(content="", tool_calls=[{
            "name": "escalate",
            "args": {"reason": "tools keep failing"},
            "id": "e1",
            "type": "tool_call",
        }]),
        AIMessage(content="continue"),
        AIMessage(content="Escalated to operator; stopping."),
        AIMessage(content="done"),
    ])
    final = await run_agent(tmp_path, run_id, ledger, sandbox, model, {})
    # escalate TOOL was called even though the evaluator's final word is done
    # (CP-B review finding: tool-side escalation must surface in state)
    assert final.get("escalated") is True
    assert final.get("eval_verdict") == "done"
    oracle = sandbox / "oracle.jsonl"
    assert oracle.exists()
    assert '"kind": "escalate"' in oracle.read_text(encoding="utf-8") or '"kind":"escalate"' in oracle.read_text(encoding="utf-8")


def test_verdict_parser_first_word_only():
    """CP-B review: substring matching turned 'I cannot continue -- escalate'
    into continue and 'abandoned' into done."""
    from mcp_storm.agent.nodes import parse_verdict

    assert parse_verdict("done") == "done"
    assert parse_verdict("Done.") == "done"
    assert parse_verdict("CONTINUE working") == "continue"
    assert parse_verdict("escalate now") == "escalate"
    assert parse_verdict("Escalate.") == "escalate"
    assert parse_verdict("I cannot continue -- escalate to a human") == "continue"  # first word wins, documented
    assert parse_verdict("The task is complete\nmore text") == "continue"  # 'The' is not a verdict
    assert parse_verdict("") == "continue"
    assert parse_verdict("\n\n") == "continue"


async def test_graded_task_end_to_end(tmp_path, run_paths):
    """Real task (fs-01) -> fake backend -> agent -> objective grade -> completed."""
    from mcp_storm.grading.grader import grade
    from mcp_storm.grading.outcomes import classify
    from mcp_storm.tasks_io import load_task

    run_id, runs, ledger, sandbox = run_paths
    task = load_task("fs-01")
    sandbox = build_sandbox(task, Path(sandbox))
    model = scripted_model([
        AIMessage(content="", tool_calls=[{
            "name": "create_directory", "args": {"path": "notes"},
            "id": "c0", "type": "tool_call"}]),
        AIMessage(content="continue"),
        AIMessage(content="", tool_calls=[
            write_call("c1", "notes/a.txt", "alpha"),
            write_call("c2", "notes/b.txt", "revenue: 42000"),
            write_call("c3", "notes/c.txt", "a.txt\nb.txt"),
        ]),
        AIMessage(content="continue"),
        AIMessage(content="All three files written. DONE."),
        AIMessage(content="done"),
    ])
    final = await run_agent(tmp_path, run_id, ledger, sandbox, model, {})
    result = grade(task, sandbox)
    outcome = classify(
        grader_result=result,
        escalated=bool(final.get("escalated")),
        final_messages=final["messages"],
    )
    assert result["passed"], result["failures"]
    assert outcome["outcome"] == "completed"
    assert outcome["refusal"] is False
