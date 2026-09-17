"""Shared filesystem locations. Everything derives from the repo root."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS_DIR = REPO_ROOT / "tasks"
PREREG_DIR = REPO_ROOT / "prereg"


def runs_dir() -> Path:
    """Directory for run artifacts. Override with STORM_RUNS_DIR (tests use tmp dirs)."""
    return Path(os.environ.get("STORM_RUNS_DIR", REPO_ROOT / "runs"))


def run_dir(run_id: str) -> Path:
    return runs_dir() / run_id
