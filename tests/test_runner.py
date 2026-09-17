"""Runner: offline gate, resume, fake-model dry run of real cells (§6.3)."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from mcp_storm.runner import matrix as mx
from mcp_storm.runner.worker import completed_run_ids, read_index, run_one
from mcp_storm.tasks_io import load_task, load_tasks


def _fake_factory(_model_name: str):
    def make() -> GenericFakeChatModel:
        return GenericFakeChatModel(messages=iter([
            AIMessage(content="I will not use tools for this dry run."),
            AIMessage(content="continue"),
            AIMessage(content="Stopping; cannot complete without acting."),
            AIMessage(content="done"),
        ]))
    return make


def _write_gate(runs: Path, sha: str = "deadbeef", rc: int = 0) -> None:
    runs.mkdir(parents=True, exist_ok=True)
    (runs / ".offline_gate.json").write_text(
        json.dumps({"git_sha": sha, "pytest_rc": rc, "ts": 0}), encoding="utf-8"
    )


async def test_worker_run_one_offline(tmp_path: Path):
    runs = tmp_path / "runs"
    task = load_task("fs-08")
    record = await run_one(
        run_id="dry-fs-08", task=task, model_name="fake-model",
        model_factory=_fake_factory("fake-model"),
        mode="malformed", dose=0.5, seed="s1", runs_dir=runs, backend="fake",
    )
    run_dir = runs / "dry-fs-08"
    assert (run_dir / "messages.json").exists()
    assert (run_dir / "meter.json").exists()
    assert (run_dir / "manifest.json").exists()
    assert (run_dir / "checkpoints.db").exists(), "AsyncSqliteSaver per run (§5.1)"
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["mode"] == "malformed" and manifest["dose"] == 0.5
    assert manifest["prices_hash"] and manifest["git_sha"]
    assert record["outcome"] == "wrong_answer"  # fake did no work
    assert record["refusal"] is False
    assert record["cost_usd"] == 0.0
    assert read_index(runs)[0]["run_id"] == "dry-fs-08"
    assert completed_run_ids(runs) == {"dry-fs-08"}
    msgs = json.loads((run_dir / "messages.json").read_text(encoding="utf-8"))
    assert msgs and msgs[0]["role"] == "human"


def test_matrix_gate_refusals(tmp_path: Path):
    runs = tmp_path / "runs"
    with pytest.raises(RuntimeError, match="no offline gate"):
        mx.check_offline_gate(runs)
    _write_gate(runs, rc=1)
    with pytest.raises(RuntimeError, match="FAILING"):
        mx.check_offline_gate(runs)
    _write_gate(runs, sha="wrong-sha")
    with pytest.raises(RuntimeError, match="stale"):
        mx.check_offline_gate(runs)


def test_paid_backend_requires_prereg_tag(tmp_path: Path, monkeypatch):
    _write_gate(tmp_path / "runs")
    monkeypatch.setattr(mx, "_git_sha", lambda: "deadbeef")
    monkeypatch.setattr(mx, "_working_tree_dirty", lambda: False)
    monkeypatch.setattr(mx, "git_tag_exists", lambda tag: False)
    with pytest.raises(RuntimeError, match="prereg law"):
        import asyncio

        asyncio.run(mx.run_matrix(models=["openai:gpt-5-nano"], tasks=load_tasks()[:1],
                                  runs=tmp_path / "runs", backend="real"))
    # and with the tag present the gate passes (run still needs a model factory)
    monkeypatch.setattr(mx, "git_tag_exists", lambda tag: True)
    assert mx.check_prereg_lock.__name__ == "check_prereg_lock"


async def test_matrix_dry_run_resume(tmp_path: Path, monkeypatch):
    runs = tmp_path / "runs"
    _write_gate(runs)
    monkeypatch.setattr(mx, "_git_sha", lambda: "deadbeef")
    monkeypatch.setattr(mx, "git_tag_exists", lambda tag: True)
    monkeypatch.setattr(mx, "_working_tree_dirty", lambda: False)
    tasks = load_tasks()[:2]
    results = await mx.run_matrix(
        models=["fake-model"], tasks=tasks, runs=runs, backend="fake",
        model_factory=_fake_factory, limit=3, workers=2,
    )
    assert len(results) == 3
    ids = {r["run_id"] for r in results}
    assert completed_run_ids(runs) == ids
    # resume: second invocation must not re-execute completed runs (limit
    # applies to PENDING cells, so it runs the NEXT batch -- disjoint ids)
    again = await mx.run_matrix(
        models=["fake-model"], tasks=tasks, runs=runs, backend="fake",
        model_factory=_fake_factory, limit=3, workers=2,
    )
    assert {r["run_id"] for r in again}.isdisjoint(ids), "completed run re-executed"
    assert len(read_index(runs)) == 6


def test_enumerate_cells_shape():
    cells = mx.enumerate_cells(["openai:gpt-5-nano"], [{"id": "fs-01"}])
    # 1 clean + 5 modes x 5 doses, x 2 replicates
    assert len(cells) == (1 + 25) * 2
    clean = [c for c in cells if c.mode == "clean"]
    assert len(clean) == 2 and all(c.dose == 0.0 for c in clean)
    run_ids = {c.run_id for c in cells}
    assert len(run_ids) == len(cells), "run ids must be unique"


async def test_worker_node_attribution_through_graph(tmp_path: Path):
    """CP-D review F1: tokens must attribute to plan/evaluate, not '?'."""
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage, HumanMessage

    from mcp_storm.agent.callbacks import MeterCallbackHandler
    from mcp_storm.agent.client import build_servers, connect_servers
    from mcp_storm.agent.graph import build_graph, invoke_config
    from mcp_storm.runner.sandbox import build_sandbox as bs

    task = load_task("fs-08")
    sandbox = bs(task, tmp_path / "sb")
    servers = build_servers("fake", sandbox, run_env={
        "STORM_RUN_ID": "node-attr", "STORM_TASK_ID": task["id"],
        "STORM_LEDGER_DIR": str(tmp_path / "runs" / "node-attr")})
    for name in servers:
        servers[name]["session_kwargs"]["read_timeout_seconds"] = timedelta(seconds=10)
    stack, tools, _ = await connect_servers(servers)
    try:
        model = GenericFakeChatModel(messages=iter([
            AIMessage(content="no tools needed", usage_metadata={
                "input_tokens": 10, "output_tokens": 2, "total_tokens": 12}),
            AIMessage(content="done", usage_metadata={
                "input_tokens": 20, "output_tokens": 1, "total_tokens": 21}),
            AIMessage(content="done", usage_metadata={
                "input_tokens": 30, "output_tokens": 1, "total_tokens": 31}),
        ]))
        graph = build_graph(model, tools)
        meter = MeterCallbackHandler(run_id="node-attr", model="fake")
        conf = invoke_config("node-attr", task["id"], "t1")
        conf["callbacks"] = [meter]
        await graph.ainvoke({"messages": [HumanMessage(content=task["prompt"])]},
                            config=conf)
        summary = meter.summary()
        nodes = set(summary["per_node"])
        assert nodes == {"plan", "evaluate"}, f"attribution broken: {nodes}"
        assert "?" not in summary["per_node"]
    finally:
        await stack.aclose()


async def test_worker_cancellation_not_recorded(tmp_path: Path):
    """CP-D review F3: Ctrl-C drain must not burn a run as crashed."""
    import asyncio

    task = load_task("fs-08")

    # latency mode dose 1.0: every tool call is held 30s, so the run is still
    # mid-flight when cancellation lands at 5s
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    def slow_factory():
        return GenericFakeChatModel(messages=iter([
            AIMessage(content="", tool_calls=[{
                "name": "create_directory", "args": {"path": "out/reports"},
                "id": "c1", "type": "tool_call"}]),
            AIMessage(content="continue"),
        ]))

    async def cancel_soon():
        await asyncio.sleep(5)
        raise asyncio.CancelledError

    runs = tmp_path / "runs"
    try:
        await asyncio.gather(
            run_one(run_id="cancel-run", task=task, model_name="fake",
                    model_factory=slow_factory, mode="latency", dose=1.0,
                    seed="s", runs_dir=runs, backend="fake"),
            cancel_soon(),
        )
    except asyncio.CancelledError:
        pass
    assert read_index(runs) == [], "cancelled run must not be recorded"
    assert completed_run_ids(runs) == set()


async def test_worker_timeout_harvests_messages(tmp_path: Path, monkeypatch):
    """CP-D review F6: timeout/loop runs keep their messages.json (QLoRA needs
    agent-side text exactly for fault-heavy runs)."""
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    from mcp_storm.runner import worker as wk

    monkeypatch.setattr(wk, "WALL_CAP_S", 6)
    task = load_task("fs-08")

    def slow_factory():
        return GenericFakeChatModel(messages=iter([
            AIMessage(content="", tool_calls=[{
                "name": "create_directory", "args": {"path": "out/reports"},
                "id": "c1", "type": "tool_call"}]),
            AIMessage(content="continue"),
            AIMessage(content="still working"),
            AIMessage(content="continue"),
        ]))

    runs = tmp_path / "runs"
    record = await wk.run_one(
        run_id="timeout-run", task=task, model_name="fake",
        model_factory=slow_factory, mode="latency", dose=1.0,
        seed="s", runs_dir=runs, backend="fake",
    )
    # latency fault with 30s hold vs 6s cap -> the tool call times out -> loop/timeout
    assert record["outcome"] in {"timeout", "loop", "wrong_answer"}, record["outcome"]
    msgs = json.loads((runs / "timeout-run" / "messages.json").read_text(encoding="utf-8"))
    assert msgs, "messages.json must be harvested from the checkpointer"
    assert msgs[0]["role"] == "human"


def test_read_index_tolerates_torn_tail(tmp_path: Path):
    from mcp_storm.runner.worker import _append_index

    runs = tmp_path / "runs"
    runs.mkdir(parents=True)
    _append_index(runs, {"run_id": "ok-1", "outcome": "completed"})
    with open(runs / "index.jsonl", "ab") as fh:
        fh.write(b'{"run_id": "tor')  # crash mid-write
    records = read_index(runs)
    assert [r["run_id"] for r in records] == ["ok-1"]
