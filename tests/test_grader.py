"""Grader golden (BUILD-GUIDE §5.3, Sun Oct 18 KC): 25/25 PASS on hand-built
perfect sandboxes; 25/25 FAIL on corrupted sandboxes."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from golden.solutions import SOLUTIONS

from mcp_storm.grading.grader import grade
from mcp_storm.runner.sandbox import build_sandbox
from mcp_storm.tasks_io import load_tasks

TASKS = load_tasks()
TASK_IDS = [t["id"] for t in TASKS]


def _perfect_sandbox(tmp_path: Path, task: dict) -> Path:
    sandbox = build_sandbox(task, tmp_path / "perfect" / task["id"])
    SOLUTIONS[task["id"]](sandbox)
    return sandbox


def _corrupt(sandbox: Path, task: dict) -> None:
    """Deterministic corruption derived from the grader itself: remove every
    file the grader references, wipe every table it reads, and drop every table
    it expects to exist. Any single hit must flip the verdict to FAIL."""
    seen_files: set[str] = set()
    seen_tables: set[str] = set()
    for check in task["grader"]:
        if "path" in check:
            seen_files.add(check["path"])
        if "table" in check:
            seen_tables.add(check["table"])
        q = check.get("query", "")
        for token in q.replace("(", " ").replace(")", " ").split():
            if token.isidentifier() and token not in {"SELECT", "FROM", "WHERE", "COUNT",
                                                      "ORDER", "BY", "AND", "AS"}:
                seen_tables.add(token)
    for rel in seen_files:
        target = sandbox / rel
        if target.exists():
            target.unlink()
    db = sandbox / "task.db"
    if seen_tables and db.exists():
        conn = sqlite3.connect(db)
        for table in seen_tables:
            try:
                conn.execute(f"DROP TABLE IF EXISTS {table}")
            except sqlite3.Error:
                pass
        conn.commit()
        conn.close()


@pytest.mark.parametrize("task_id", TASK_IDS)
def test_perfect_sandbox_passes(task_id: str, tmp_path: Path):
    task = next(t for t in TASKS if t["id"] == task_id)
    sandbox = _perfect_sandbox(tmp_path, task)
    result = grade(task, sandbox)
    assert result["passed"], f"{task_id} should PASS: {result['failures']}"


@pytest.mark.parametrize("task_id", TASK_IDS)
def test_corrupted_sandbox_fails(task_id: str, tmp_path: Path):
    task = next(t for t in TASKS if t["id"] == task_id)
    sandbox = _perfect_sandbox(tmp_path, task)
    _corrupt(sandbox, task)
    result = grade(task, sandbox)
    assert not result["passed"], f"{task_id} corrupted sandbox must FAIL"


def test_distribution_9_8_8():
    prefixes = {"fs": 0, "db": 0, "xs": 0}
    for task_id in TASK_IDS:
        prefixes[task_id.split("-")[0]] += 1
    assert prefixes == {"fs": 9, "db": 8, "xs": 8}


def test_outcomes_classifier():
    from langchain_core.messages import AIMessage, HumanMessage
    from langgraph.errors import GraphRecursionError

    from mcp_storm.grading.outcomes import classify

    ok = classify(grader_result={"passed": True, "failures": []})
    assert ok == {"outcome": "completed", "escalated": False, "refusal": False}
    bad = classify(grader_result={"passed": False, "failures": ["x"]})
    assert bad["outcome"] == "wrong_answer"
    loop = classify(exception=GraphRecursionError(40), grader_result={"passed": False, "failures": []})
    assert loop["outcome"] == "loop"
    to = classify(timed_out=True, grader_result={"passed": True, "failures": []})
    assert to["outcome"] == "timeout"
    crash = classify(exception=RuntimeError("boom"), grader_result={"passed": True, "failures": []})
    assert crash["outcome"] == "crashed"
    esc = classify(grader_result={"passed": True, "failures": []}, escalated=True)
    assert esc["escalated"] is True and esc["outcome"] == "completed"
    refusal = classify(
        grader_result={"passed": False, "failures": ["x"]},
        final_messages=[HumanMessage("do it"), AIMessage("I'm sorry, but I can't help with that.")],
        refusal_phrases=["I'm sorry, but I can't"],
    )
    assert refusal["refusal"] is True and refusal["outcome"] == "wrong_answer"
