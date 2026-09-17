"""Matrix runner: cells, offline gate, resume, 4 async workers, spend breaker
(BUILD-GUIDE §6.1, §6.3).

Laws encoded:
- The runner REFUSES to start unless the offline suite passed on the current
  commit (gate file runs/.offline_gate.json with the git SHA, §2.2).
- Resume is manifest-driven: runs present in runs/index.jsonl are skipped.
- Every run checks the spend breaker ($25 warn / $40 hard abort, §2.3).
- No paid run before the prereg lock tag v0.1-prereg exists (§2.4).
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from mcp_storm import prices
from mcp_storm.paths import REPO_ROOT
from mcp_storm.runner.worker import completed_run_ids, run_one

MODES = ("latency", "hard_error", "malformed", "partial", "dropped")
DOSES = (0.05, 0.10, 0.20, 0.40, 0.70)
REPLICATES = 2
WORKERS = 4
PREREG_TAG = "v0.1-prereg"


@dataclass(frozen=True)
class Cell:
    model: str
    mode: str  # "clean" for dose 0, else a fault mode
    dose: float
    task_id: str
    replicate: int

    @property
    def run_id(self) -> str:
        mode = self.mode
        dose = "0" if self.mode == "clean" else str(int(self.dose * 100))
        tag = self.model.replace(":", "-")
        return f"{tag}-{mode}-{dose}-{self.task_id}-r{self.replicate}"


def enumerate_cells(models: list[str], tasks: list[dict]) -> list[Cell]:
    cells: list[Cell] = []
    for model in models:
        for task in tasks:
            for rep in range(1, REPLICATES + 1):
                cells.append(Cell(model, "clean", 0.0, task["id"], rep))
                for mode in MODES:
                    for dose in DOSES:
                        cells.append(Cell(model, mode, dose, task["id"], rep))
    return cells


def _git_sha() -> str:
    proc = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                          text=True, shell=False, cwd=str(REPO_ROOT))
    return proc.stdout.strip() if proc.returncode == 0 else "unknown"


def git_tag_exists(tag: str) -> bool:
    proc = subprocess.run(["git", "tag", "-l", tag], capture_output=True, text=True,
                          shell=False, cwd=str(Path(__file).parents[2]))
    return proc.stdout.strip() == tag


def _working_tree_dirty() -> bool:
    proc = subprocess.run(["git", "status", "--porcelain"], capture_output=True,
                          text=True, shell=False, cwd=str(REPO_ROOT))
    return bool(proc.stdout.strip())


def write_offline_gate(runs: Path) -> bool:
    """Run the offline suite; stamp runs/.offline_gate.json with sha+rc+dirty."""
    rc = subprocess.run([sys.executable, "-m", "pytest", "-q"],
                        cwd=str(REPO_ROOT)).returncode
    gate = {"git_sha": _git_sha(), "pytest_rc": rc, "dirty": _working_tree_dirty(),
            "ts": __import__("time").time()}
    runs.mkdir(parents=True, exist_ok=True)
    (runs / ".offline_gate.json").write_text(json.dumps(gate), encoding="utf-8")
    return rc == 0 and not gate["dirty"]


def check_offline_gate(runs: Path) -> None:
    path = Path(runs) / ".offline_gate.json"
    if not path.exists():
        raise RuntimeError(
            "no offline gate: run `storm matrix gate` (offline pytest must pass on this commit)"
        )
    gate = json.loads(path.read_text(encoding="utf-8"))
    if gate.get("pytest_rc") != 0:
        raise RuntimeError("offline gate stamped with a FAILING pytest run")
    if gate.get("git_sha") != _git_sha():
        raise RuntimeError(
            f"offline gate is stale: gate sha {gate.get('git_sha')} != HEAD {_git_sha()}; "
            "re-run `storm matrix gate`"
        )
    if gate.get("dirty") or _working_tree_dirty():
        raise RuntimeError(
            "offline gate must be stamped on a clean working tree "
            "(commit, then `storm matrix gate`)"
        )


def check_prereg_lock() -> None:
    if not git_tag_exists(PREREG_TAG):
        raise RuntimeError(
            f"prereg law: no paid run before tag {PREREG_TAG} exists (BUILD-GUIDE §2.4)"
        )


class MatrixLock:
    """One matrix at a time: two windows would double-run (and double-spend)
    every pending cell."""

    def __init__(self, runs: Path) -> None:
        self.path = Path(runs) / ".matrix.lock"
        self._held = False

    def __enter__(self) -> MatrixLock:
        import os

        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f'{{"pid": {os.getpid()}, "ts": {__import__("time").time()}}}'.encode())
            os.close(fd)
            self._held = True
        except FileExistsError as exc:
            raise RuntimeError(
                f"another matrix holds {self.path} (delete it if stale)"
            ) from exc
        return self

    def __exit__(self, *exc) -> None:
        if self._held:
            try:
                self.path.unlink()
            except OSError:
                pass


async def run_matrix(
    *,
    models: list[str],
    tasks: list[dict],
    runs: Path,
    backend: str = "real",
    model_factory: Callable[[str], Callable[[], object]] | None = None,
    limit: int | None = None,
    workers: int = WORKERS,
    dry_run: bool = False,
) -> list[dict]:
    """Execute the matrix. model_factory(model_name) -> zero-arg factory so
    offline callers can inject FakeChatModels; paid runs use models.get_model."""
    check_offline_gate(runs)
    if backend == "real":
        check_prereg_lock()
    refusal_phrases = load_refusal_phrases_strict()

    cells = enumerate_cells(models, tasks)
    done = completed_run_ids(runs)
    pending = [c for c in cells if c.run_id not in done]
    if limit:
        pending = pending[:limit]

    tracker = prices.SpendTracker(runs_dir=runs)
    task_by_id = {t["id"]: t for t in tasks}
    matrix_lock = MatrixLock(runs)
    matrix_lock.__enter__()  # released by the finally below
    factory = model_factory or _default_model_factory
    queue: asyncio.Queue[Cell] = asyncio.Queue()
    for cell in pending:
        queue.put_nowait(cell)
    results: list[dict] = []
    stopped = {"abort": False}

    async def worker() -> None:
        from mcp_storm.prices import BreakerTrip

        while not queue.empty() and not stopped["abort"]:
            try:
                cell = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                tracker.gate(cell.run_id)
            except BreakerTrip as exc:
                stopped["abort"] = True
                print(f"[matrix] {exc}; draining active workers", flush=True)
                return
            try:
                record = await run_one(
                    run_id=cell.run_id,
                    task=task_by_id[cell.task_id],
                    model_name=cell.model,
                    model_factory=factory(cell.model),
                    mode="passthrough" if cell.mode == "clean" else cell.mode,
                    dose=0.0 if cell.mode == "clean" else cell.dose,
                    seed=f"{cell.task_id}-r{cell.replicate}",
                    runs_dir=runs,
                    backend=backend,
                    refusal_phrases=refusal_phrases,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - isolate: matrix survives
                import traceback

                traceback.print_exc()
                print(f"[matrix] {cell.run_id} HARD-FAILED: {exc!r}", flush=True)
                continue
            results.append(record)
            print(f"[matrix] {cell.run_id} -> {record['outcome']} "
                  f"(${record['cost_usd']})", flush=True)
            if record.get("cost_usd"):
                tracker.record({"kind": "run_cost", "run_id": cell.run_id,
                                "cost_usd": record["cost_usd"],
                                "ts": __import__("time").time()})

    try:
        await asyncio.gather(*[worker() for _ in range(workers)])
    finally:
        matrix_lock.__exit__(None, None, None)
    return results


def load_refusal_phrases_strict() -> list[str]:
    """Fail fast: if the prereg list is unreadable the matrix must not start."""
    from mcp_storm.grading.outcomes import load_refusal_phrases

    return load_refusal_phrases()


def _default_model_factory(model_name: str) -> Callable[[], object]:
    from mcp_storm.agent.models import get_model

    get_model(model_name)  # fail fast on unknown names
    return lambda: get_model(model_name)
