"""Grader: run a task's checks against a sandbox. Pure Python + stdlib sqlite3;
no LLM judging (§5.3).

Check types (see scripts/gen_tasks.py for authoring):
  file_exists / file_absent /
  file_contains (loose substring -- only for prose, never numeric answers) /
  file_contains_all(ordered?) / file_has_line (exact stripped line) /
  file_lines (exact, order-sensitive, blank lines ignored) /
  file_number (numeric equality: parses a number, compares by value -- numeric
  answers must never be graded by substring; '14550' must NOT satisfy 4550) /
  file_json (JSON object containing the given field->value subset) /
  table_exists (case-insensitive, like SQLite) / row_count /
  sql_rows (order-insensitive multiset) / sql_scalar
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


def _read_text(sandbox: Path, path: str) -> str:
    target = (sandbox / path)
    if not target.is_file():
        raise FileNotFoundError(path)
    return target.read_text(encoding="utf-8")


def _rows(sandbox: Path, query: str) -> list[dict]:
    db = sandbox / "task.db"
    if not db.exists():
        raise sqlite3.OperationalError("no task.db in sandbox")
    # read-only URI: grading must never create or mutate the sandbox
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(query).fetchall()]
    finally:
        conn.close()


def _parse_number(text: str) -> float | int:
    return float(text.strip())


def _run_check(sandbox: Path, check: dict) -> str | None:
    """Return None when the check passes, else a one-line failure reason."""
    ctype = check["type"]
    if ctype == "file_exists":
        return None if (sandbox / check["path"]).exists() else f"missing {check['path']}"
    if ctype == "file_absent":
        return None if not (sandbox / check["path"]).exists() else f"should be absent: {check['path']}"
    if ctype == "file_contains":
        try:
            text = _read_text(sandbox, check["path"])
        except FileNotFoundError as exc:
            return str(exc)
        return None if check["text"] in text else f"{check['path']} lacks {check['text']!r}"
    if ctype == "file_has_line":
        try:
            text = _read_text(sandbox, check["path"])
        except FileNotFoundError as exc:
            return str(exc)
        lines = [ln.strip() for ln in text.splitlines()]
        return None if check["line"] in lines else f"{check['path']} has no line {check['line']!r}"
    if ctype == "file_number":
        try:
            text = _read_text(sandbox, check["path"])
        except FileNotFoundError as exc:
            return str(exc)
        try:
            got = _parse_number(text)
        except ValueError:
            return f"{check['path']} is not a single number: {text.strip()!r}"
        return (None if got == float(check["equals"])
                else f"{check['path']} = {got} != {check['equals']}")
    if ctype == "file_contains_all":
        try:
            text = _read_text(sandbox, check["path"])
        except FileNotFoundError as exc:
            return str(exc)
        missing = [t for t in check["texts"] if t not in text]
        if missing:
            return f"{check['path']} missing {missing}"
        if check.get("ordered"):
            pos = [text.find(t) for t in check["texts"]]
            if pos != sorted(pos):
                return f"{check['path']} has {check['texts']} out of order"
        return None
    if ctype == "file_lines":
        try:
            text = _read_text(sandbox, check["path"])
        except FileNotFoundError as exc:
            return str(exc)
        got = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return None if got == check["lines"] else f"{check['path']} lines {got} != {check['lines']}"
    if ctype == "file_json":
        try:
            obj = json.loads(_read_text(sandbox, check["path"]))
        except FileNotFoundError as exc:
            return str(exc)
        except ValueError as exc:
            return f"{check['path']} not valid JSON: {exc}"
        if not isinstance(obj, dict):
            return f"{check['path']} is not a JSON object"
        for key, expected in check["fields"].items():
            if obj.get(key) != expected:
                return f"{check['path']}[{key!r}] = {obj.get(key)!r} != {expected!r}"
        return None
    if ctype == "table_exists":
        try:
            got = _rows(sandbox, "SELECT name FROM sqlite_master WHERE type='table'")
        except sqlite3.Error as exc:
            return f"table_exists {check['table']}: {exc}"
        names = {str(r["name"]).lower() for r in got}
        return (None if check["table"].lower() in names
                else f"table {check['table']} missing (have {sorted(names)})")
    if ctype == "row_count":
        try:
            got = _rows(sandbox, f"SELECT COUNT(*) AS n FROM {check['table']}")
        except sqlite3.Error as exc:
            return f"row_count {check['table']}: {exc}"
        n = got[0]["n"] if got else -1
        return None if n == check["equals"] else f"{check['table']} has {n} rows != {check['equals']}"
    if ctype == "sql_rows":
        try:
            got = _rows(sandbox, check["query"])
        except sqlite3.Error as exc:
            return f"{check['query']!r}: {exc}"
        def norm(rows: list[dict]) -> list[str]:
            return sorted(json.dumps(r, sort_keys=True, default=str) for r in rows)
        return (None if norm(got) == norm(check["rows"])
                else f"{check['query']!r} -> {got} != {check['rows']}")
    if ctype == "sql_scalar":
        try:
            got = _rows(sandbox, check["query"])
        except sqlite3.Error as exc:
            return f"{check['query']!r}: {exc}"
        if not got or len(got) != 1 or len(got[0]) != 1:
            return f"{check['query']!r} did not yield exactly one scalar (got {got})"
        value = next(iter(got[0].values()))
        return None if value == check["equals"] else f"{check['query']!r} -> {value!r} != {check['equals']!r}"
    return f"unknown check type {ctype!r}"


def grade(task: dict, sandbox: Path) -> dict[str, Any]:
    """Run all checks; {'passed': bool, 'failures': [reason, ...]}."""
    failures: list[str] = []
    sandbox = Path(sandbox)
    if not sandbox.exists():
        return {"passed": False, "failures": ["sandbox does not exist"]}
    for check in task.get("grader", []):
        try:
            reason = _run_check(sandbox, check)
        except Exception as exc:  # noqa: BLE001 - grader must never crash the runner
            reason = f"grader error on {check}: {exc}"
        if reason is not None:
            failures.append(reason)
    return {"passed": not failures, "failures": failures}
