"""Sandbox builder: task fixture -> isolated workspace per run (BUILD-GUIDE §6.3).

Task JSON "fixture" shape:
  {"files": {"inputs/q1.txt": "42", ...},        # text files, UTF-8
   "sqlite_seed": ["CREATE TABLE ...", ...]}      # statements run into task.db
The sandbox lives under runs/<run_id>/sandboxes/<task_id>/ so every side effect
of a run is captured under its own run directory. Rebuilding over an existing
sandbox wipes seeded artifacts first (task.db + fixture files) so resume/retry
stays deterministic.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def build_sandbox(task: dict, sandbox_dir: Path) -> Path:
    sandbox_dir = Path(sandbox_dir)
    sandbox_dir.mkdir(parents=True, exist_ok=True)
    root = sandbox_dir.resolve()
    fixture = task.get("fixture") or {}
    for rel, content in (fixture.get("files") or {}).items():
        target = (root / rel).resolve()
        if not target.is_relative_to(root):
            raise ValueError(f"fixture path escapes sandbox: {rel}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.unlink()
        target.write_text(str(content), encoding="utf-8")
    seed = fixture.get("sqlite_seed") or []
    if seed:
        db = sandbox_dir / "task.db"
        if db.exists():
            db.unlink()  # never seed on top of a previous run's database
        conn = sqlite3.connect(db)
        try:
            for stmt in seed:
                conn.execute(stmt)
            conn.commit()
        finally:
            conn.close()
    return sandbox_dir
