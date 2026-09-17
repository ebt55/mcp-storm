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


# ------------------------------------------------- strictness regressions (review)
def _task(task_id: str) -> dict:
    return next(t for t in TASKS if t["id"] == task_id)


def _with_file(sandbox: Path, rel: str, content: str) -> Path:
    target = sandbox / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return sandbox


def test_numeric_superstrings_do_not_pass(tmp_path: Path):
    """'14550' must NOT satisfy max=4550; '19' must not satisfy 9 (review F1)."""
    for task_id, rel, wrong, right in [
        ("fs-04", "out/max.txt", "14550\n", "4550\n"),
        ("fs-06", "out/words.txt", "19\n", "9\n"),
        ("fs-07", "out/sum.txt", "145\n", "45\n"),
        ("db-03", "out/value.txt", "830\n", "83\n"),
        ("db-07", "out/avg.txt", "1149\n", "149\n"),
    ]:
        task = _task(task_id)
        base = _perfect_sandbox(tmp_path / "w", task)
        (base / rel).write_text(wrong, encoding="utf-8")
        assert not grade(task, base)["passed"], f"{task_id}: {wrong!r} wrongly passes"
        (base / rel).write_text(right, encoding="utf-8")
        assert grade(task, base)["passed"], f"{task_id}: {right!r} should pass"
        # float formatting is fine for numeric answers
        (base / rel).write_text(right.strip() + ".0\n", encoding="utf-8")
        assert grade(task, base)["passed"], f"{task_id}: float form should pass"


def test_hedged_single_answers_do_not_pass(tmp_path: Path):
    """db-04: listing every name must not pass as 'top'; fs-08: NOT READY."""
    task = _task("db-04")
    base = _perfect_sandbox(tmp_path / "h1", task)
    (base / "out/top.txt").write_text("ann\nben\ncyd\n", encoding="utf-8")
    assert not grade(task, base)["passed"]
    task = _task("xs-06")
    base = _perfect_sandbox(tmp_path / "h2", task)
    (base / "out/best.txt").write_text("east\nwest\nnorth\n", encoding="utf-8")
    assert not grade(task, base)["passed"]
    task = _task("fs-08")
    base = _perfect_sandbox(tmp_path / "h3", task)
    (base / "out/reports/status.txt").write_text("NOT READY\n", encoding="utf-8")
    assert not grade(task, base)["passed"]
    task = _task("fs-01")
    base = _perfect_sandbox(tmp_path / "h4", task)
    (base / "notes/b.txt").write_text("The revenue: 42000 dollars\n", encoding="utf-8")
    assert not grade(task, base)["passed"], "exact-line requirement must be enforced"


def test_db02_id_contract_graded(tmp_path: Path):
    task = _task("db-02")
    base = _perfect_sandbox(tmp_path / "idc", task)
    import sqlite3

    conn = sqlite3.connect(base / "task.db")
    conn.execute("UPDATE metrics SET id = 99")
    conn.commit()
    conn.close()
    assert not grade(task, base)["passed"], "id 1,2,3 in filename order is part of the task"


def test_grading_does_not_create_task_db(tmp_path: Path):
    task = _task("fs-01")
    sandbox = build_sandbox(task, tmp_path / "ro" / "fs-01")
    assert not (sandbox / "task.db").exists()
    grade(task, sandbox)
    assert not (sandbox / "task.db").exists(), "grader must not create task.db"
    for t in TASKS:  # empty-sandbox grade across all tasks never raises
        grade(t, build_sandbox(t, tmp_path / "empty" / t["id"]))


def test_refusal_phrases_default_loads_from_prereg():
    from mcp_storm.grading.outcomes import load_refusal_phrases

    phrases = load_refusal_phrases()
    assert phrases and any("can't" in p or "cannot" in p for p in phrases)
