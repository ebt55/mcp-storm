"""Worker: execute ONE matrix cell run end-to-end (BUILD-GUIDE §6.3).

Per run: WIPE the run dir (re-runs start from nothing) -> fresh sandbox ->
persistent proxy sessions -> AsyncSqliteSaver checkpointer (one sqlite file per
run) -> ainvoke under the 600s wall cap -> grade -> classify -> dump
messages.json (harvested from the checkpointer if the run died) + meter.json +
manifest.json -> append runs/index.jsonl. Timeouts/crashes kill the recorded
process trees. Cancellation (Ctrl-C drain) raises through: it is not a dataset
row and resume must retry it.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.errors import GraphRecursionError

from mcp_storm.agent.callbacks import MeterCallbackHandler
from mcp_storm.agent.client import build_servers, connect_servers
from mcp_storm.agent.graph import build_graph, invoke_config
from mcp_storm.grading.grader import grade
from mcp_storm.grading.outcomes import classify
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
                {"name": tc.get("name"),
                 "args": json.dumps(tc.get("args", {}), ensure_ascii=False,
                                    default=str)[:500]}
                for tc in tool_calls
            ]
        if getattr(m, "status", None):
            entry["status"] = m.status
        if getattr(m, "name", None):
            entry["tool_name"] = m.name
        out.append(entry)
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")


def _kill_recorded_trees(run_directory: Path) -> None:
    """Kill every proxy tree the run spawned. Startup manifests exist from the
    moment each proxy spawned its child, so hard-killed proxies are found too."""
    from mcp_storm.runner.watchdog import kill_tree

    for manifest_path in Path(run_directory).glob("manifest.*.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for key in ("proxy_pid", "child_pid"):
            pid = manifest.get(key)
            if pid:
                kill_tree(int(pid))


def _git_sha() -> str:
    try:
        proc = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                              text=True, shell=False, cwd=str(REPO_ROOT))
        return proc.stdout.strip() if proc.returncode == 0 else "unknown"
    except OSError:
        return "unknown"


async def _harvest_messages(graph, config: dict) -> list:
    """Timeout/loop/crash runs still have full history in the checkpointer;
    the QLoRA task needs agent-side text precisely for fault-heavy runs."""
    try:
        state = await asyncio.wait_for(graph.aget_state(config), timeout=15)
        values = getattr(state, "values", None) or {}
        return list(values.get("messages", []))
    except Exception:  # noqa: BLE001 - best effort, never blocks the record
        return []


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
    refusal_phrases: list[str] | None = None,
) -> dict[str, Any]:
    """Execute one run and return its index record. Cancellation propagates
    (CancelledError); every other failure becomes outcome=crashed data."""
    from mcp_storm import prices
    from mcp_storm.proxy.ledger import read_run_ledger

    run_directory = Path(runs_dir) / run_id
    if run_directory.exists():
        # A re-run of the same run_id (§7 harness-bug exclusion) starts from
        # nothing: inherited checkpoints poison the thread and leftover
        # artifacts corrupt the grade.
        shutil.rmtree(run_directory)
    run_directory.mkdir(parents=True, exist_ok=True)
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
    graph = None
    invoke_conf: dict | None = None
    sandbox_dir = run_directory / "sandboxes" / task["id"]

    try:
        sandbox = build_sandbox(task, sandbox_dir)
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
            invoke_conf = invoke_config(run_id, task["id"], thread_id=f"{run_id}-thread")
            invoke_conf["callbacks"] = [meter]
            final_state = await asyncio.wait_for(
                graph.ainvoke(
                    {"messages": [HumanMessage(content=task["prompt"])],
                     "run_id": run_id, "task_id": task["id"]},
                    config=invoke_conf,
                ),
                timeout=WALL_CAP_S,
            )
            final_messages = final_state.get("messages", [])
            escalated = bool(final_state.get("escalated"))
        finally:
            # harvest BEFORE closing the stack: the checkpointer saver lives
            # inside it, and timeout/loop/crash runs still need their messages
            if not final_messages and graph is not None and invoke_conf is not None:
                final_messages = await _harvest_messages(graph, invoke_conf)
            try:  # bounded teardown: hung sessions must not outlive the cap
                await asyncio.wait_for(stack.aclose(), timeout=30)
            except Exception:  # noqa: BLE001
                _kill_recorded_trees(run_directory)
    except GraphRecursionError:
        recursion = True
    except asyncio.TimeoutError:
        timed_out = True
    except asyncio.CancelledError:
        # Ctrl-C / drain: the run never finished -- no index row, resume retries
        _kill_recorded_trees(run_directory)
        raise
    except BaseException as exc:  # noqa: BLE001 - crashes are data, not failures
        exception = exc
    finally:
        if timed_out or exception is not None:
            _kill_recorded_trees(run_directory)

    grader_result = grade(task, sandbox_dir)
    if refusal_phrases is None:
        from mcp_storm.grading.outcomes import load_refusal_phrases

        try:
            refusal_phrases = load_refusal_phrases()
        except (OSError, ValueError):
            refusal_phrases = []
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


def _append_index(runs_dir: Path, record: dict) -> None:
    import threading

    lock = getattr(_append_index, "_lock", None)
    if lock is None:
        lock = threading.Lock()
        _append_index._lock = lock
    runs_dir.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n"
    with lock:
        fd = os.open(runs_dir / "index.jsonl", os.O_WRONLY | os.O_APPEND
                     | os.O_CREAT | os.O_BINARY)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)


def read_index(runs_dir: Path) -> list[dict]:
    path = Path(runs_dir) / "index.jsonl"
    if not path.exists():
        return []
    records = []
    torn = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except ValueError:  # torn tail from a crash mid-write: skip, resume lives
            torn += 1
    if torn:
        print(f"[index] skipped {torn} torn line(s) in {path.name}", flush=True)
    return records


def completed_run_ids(runs_dir: Path) -> set[str]:
    return {r["run_id"] for r in read_index(runs_dir)}
