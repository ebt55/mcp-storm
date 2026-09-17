"""Worker: execute ONE matrix cell run end-to-end (BUILD-GUIDE §6.3).

Per run: fresh sandbox -> persistent proxy sessions -> AsyncSqliteSaver
checkpointer (one sqlite file per run) -> ainvoke under the 600s wall cap ->
grade -> classify -> dump messages.json + meter.json + manifest.json -> append
runs/index.jsonl. Timeouts kill the recorded process trees before teardown.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.errors import GraphRecursionError

from mcp_storm.agent.callbacks import MeterCallbackHandler
from mcp_storm.agent.client import build_servers, connect_servers
from mcp_storm.agent.graph import build_graph, invoke_config
from mcp_storm.grading.grader import grade
from mcp_storm.grading.outcomes import classify, load_refusal_phrases
from mcp_storm.paths import REPO_ROOT
from mcp_storm.runner.sandbox import build_sandbox

WALL_CAP_S = 600


def dump_messages(messages: list, path: Path) -> None:
    """(role, first 2k chars of content, tool_calls) — §3.4 messages.json."""
    out = []
    for m in messages:
        content = getattr(m, "content", "")
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False, default=str)
        entry: dict[str, Any] = {
            "role": getattr(m, "type", "unknown"),
            "content": content[:2000],
        }
        tool_calls = getattr(m, "tool_calls", None)
        if tool_calls:
            entry["tool_calls"] = [
                {"name": tc.get("name"), "args": json.dumps(tc.get("args", {}),
                                                             ensure_ascii=False, default=str)[:500]}
                for tc in tool_calls
            ]
        if getattr(m, "status", None):
            entry["status"] = m.status
        if getattr(m, "name", None):
            entry["tool_name"] = m.name
        out.append(entry)
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")


def _kill_recorded_trees(run_directory: Path) -> None:
    from mcp_storm.runner.watchdog import kill_tree

    for manifest_path in Path(run_directory).glob("manifest.*.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        pid = manifest.get("proxy_pid")
        if pid:
            kill_tree(int(pid))


async def run_one(
    *,
    run_id: str,
    task: dict,
    model_name: str,
    model_factory: Callable[[], Any],
    mode: str,
    dose: float,
    seed: str,
    runs_dir: Path,
    backend: str = "fake",
) -> dict[str, Any]:
    """Execute one run and return its index record. Raises nothing the matrix
    can't survive: crashes become outcome=crashed records."""
    from mcp_storm import prices
    from mcp_storm.proxy.ledger import read_run_ledger

    run_directory = Path(runs_dir) / run_id
    run_directory.mkdir(parents=True, exist_ok=True)
    sandbox = build_sandbox(task, run_directory / "sandboxes" / task["id"])
    started = time.time()

    meter = MeterCallbackHandler(run_id=run_id, model=model_name)
    result: dict[str, Any] = {
        "run_id": run_id, "model": model_name, "mode": mode, "dose": dose,
        "task_id": task["id"], "seed": seed, "backend": backend,
        "started_ts": started,
    }
    exception: BaseException | None = None
    timed_out = False
    recursion = False
    escalated = False
    final_messages: list = []
    outcome_state: dict = {}

    try:
        servers = build_servers(
            backend, sandbox,
            run_env={
                "STORM_RUN_ID": run_id,
                "STORM_TASK_ID": task["id"],
                "STORM_LEDGER_DIR": str(run_directory),
                "STORM_MODE": mode,
                "STORM_DOSE": str(dose),
                "STORM_SEED": seed,
            },
        )
        model = model_factory()
        stack, tools, _client = await connect_servers(servers)
        try:
            saver_ctx = AsyncSqliteSaver.from_conn_string(str(run_directory / "checkpoints.db"))
            saver = await stack.enter_async_context(saver_ctx)
            graph = build_graph(model, tools, checkpointer=saver)
            config = invoke_config(run_id, task["id"], thread_id=f"{run_id}-thread")
            config["callbacks"] = [meter]
            final_state = await asyncio.wait_for(
                graph.ainvoke(
                    {"messages": [HumanMessage(content=task["prompt"])],
                     "run_id": run_id, "task_id": task["id"]},
                    config=config,
                ),
                timeout=WALL_CAP_S,
            )
            final_messages = final_state.get("messages", [])
            escalated = bool(final_state.get("escalated"))
        finally:
            try:  # bounded teardown: hung sessions must not outlive the cap
                await asyncio.wait_for(stack.aclose(), timeout=30)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                _kill_recorded_trees(run_directory)
    except GraphRecursionError:
        recursion = True
    except asyncio.TimeoutError:
        timed_out = True
    except BaseException as exc:  # noqa: BLE001 - crashes are data, not failures
        exception = exc
    finally:
        if timed_out or exception is not None:
            _kill_recorded_trees(run_directory)

    grader_result = grade(task, sandbox)
    refusal_phrases = load_refusal_phrases()
    outcome_state = classify(
        exception=exception,
        timed_out=timed_out,
        recursion_error=recursion,
        escalated=escalated,
        grader_result=grader_result,
        final_messages=final_messages,
        refusal_phrases=refusal_phrases,
    )

    meter_summary = meter.summary()
    (run_directory / "meter.json").write_text(
        json.dumps(meter_summary, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    dump_messages(final_messages, run_directory / "messages.json")
    try:
        ledger = read_run_ledger(run_directory)
    except OSError:
        ledger = []
    manifest = {
        "run_id": run_id, "task_id": task["id"], "model": model_name,
        "mode": mode, "dose": dose, "seed": seed, "backend": backend,
        "prices_hash": prices.prices_hash(), "prices_fetched": prices.FETCHED,
        "git_sha": _git_sha(), "tool_calls": len(ledger),
        "escalated": outcome_state["escalated"],
        "outcome": outcome_state["outcome"],
    }
    (run_directory / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    cost = meter.cost_usd(prices) if model_name in prices.PRICES else 0.0
    result.update(
        {
            **outcome_state,
            "grader_failures": grader_result["failures"][:10],
            "tokens_in": meter_summary["totals"]["input_tokens"],
            "tokens_out": meter_summary["totals"]["output_tokens"],
            "tool_calls": len(ledger),
            "cost_usd": round(cost, 6),
            "wall_s": round(time.time() - started, 2),
            "finished_ts": time.time(),
        }
    )
    _append_index(Path(runs_dir), result)
    return result


def _git_sha() -> str:
    import subprocess

    try:
        proc = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                              text=True, shell=False, cwd=str(REPO_ROOT))
        return proc.stdout.strip() if proc.returncode == 0 else "unknown"
    except OSError:
        return "unknown"


def _append_index(runs_dir: Path, record: dict) -> None:
    import threading

    lock = getattr(_append_index, "_lock", None)
    if lock is None:
        lock = threading.Lock()
        _append_index._lock = lock
    runs_dir.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n"
    with lock:
        import os as _os

        fd = _os.open(runs_dir / "index.jsonl", _os.O_WRONLY | _os.O_APPEND
                      | _os.O_CREAT | _os.O_BINARY)
        try:
            _os.write(fd, line.encode("utf-8"))
        finally:
            _os.close(fd)


def read_index(runs_dir: Path) -> list[dict]:
    path = Path(runs_dir) / "index.jsonl"
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def completed_run_ids(runs_dir: Path) -> set[str]:
    return {r["run_id"] for r in read_index(runs_dir)}
