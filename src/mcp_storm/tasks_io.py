"""Task loading: tasks/*.json with validation (BUILD-GUIDE §5.3)."""

from __future__ import annotations

import json
from pathlib import Path

from mcp_storm.paths import TASKS_DIR

REQUIRED_KEYS = ("id", "prompt", "servers", "fixture", "grader", "expected_calls")


def load_tasks(tasks_dir: Path | None = None) -> list[dict]:
    directory = Path(tasks_dir) if tasks_dir else TASKS_DIR
    tasks: list[dict] = []
    for path in sorted(directory.glob("*.json")):
        task = json.loads(path.read_text(encoding="utf-8"))
        missing = [k for k in REQUIRED_KEYS if k not in task]
        if missing:
            raise ValueError(f"{path}: missing keys {missing}")
        if not task["grader"]:
            raise ValueError(f"{path}: empty grader")
        tasks.append(task)
    ids = [t["id"] for t in tasks]
    if len(ids) != len(set(ids)):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        raise ValueError(f"duplicate task ids: {dupes}")
    return tasks


def load_task(task_id: str, tasks_dir: Path | None = None) -> dict:
    for task in load_tasks(tasks_dir):
        if task["id"] == task_id:
            return task
    raise KeyError(f"no task with id {task_id!r}")
