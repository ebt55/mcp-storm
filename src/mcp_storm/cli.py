"""storm CLI (BUILD-GUIDE §2.6, §13).

`storm selftest` = byte-framing golden + grader golden + meter-join +
spend-breaker unit checks, all offline. `storm matrix gate|run` drives the
experiment matrix (gate = offline suite green on this commit, §2.2).
"""

from __future__ import annotations

import asyncio
import subprocess
import sys

import typer

from mcp_storm.paths import runs_dir

app = typer.Typer(no_args_is_help=True, help="mcp-storm control CLI")
matrix_app = typer.Typer(no_args_is_help=True, help="experiment matrix runner")
app.add_typer(matrix_app, name="matrix")

SELFTEST_MODULES = {
    "byte-framing golden": ("tests/test_framing.py", "tests/test_faults.py"),
    "grader golden": ("tests/test_grader.py",),
    "meter join": ("tests/test_meter.py",),
    "spend breaker": ("tests/test_spend.py",),
}


def _pytest(targets: tuple[str, ...]) -> tuple[int, str]:
    from mcp_storm.paths import REPO_ROOT

    cmd = [sys.executable, "-m", "pytest", "-q", *targets]
    proc = subprocess.run(cmd, capture_output=True, text=True, shell=False, cwd=str(REPO_ROOT))
    return proc.returncode, (proc.stdout + proc.stderr)


@app.command()
def selftest() -> None:
    """Offline golden checks (framing, grader, meter-join, spend breaker)."""
    failures = []
    for name, targets in SELFTEST_MODULES.items():
        rc, out = _pytest(targets)
        if rc == 0:
            typer.echo(f"PASS  {name}")
        else:
            typer.echo(f"FAIL  {name}\n{out[-2000:]}")
            failures.append(name)
    if failures:
        typer.echo(f"selftest: failing: {failures}")
        raise typer.Exit(code=1)
    typer.echo("selftest: all offline golden checks green")


@matrix_app.command("gate")
def matrix_gate() -> None:
    """Run the offline suite and stamp runs/.offline_gate.json (§2.2)."""
    from mcp_storm.runner.matrix import write_offline_gate

    if write_offline_gate(runs_dir()):
        typer.echo(f"gate: offline suite green, stamped {runs_dir() / '.offline_gate.json'}")
    else:
        typer.echo("gate: OFFLINE SUITE FAILED -- matrix locked")
        raise typer.Exit(code=1)


@matrix_app.command("run")
def matrix_run(
    models: str = typer.Option("openai:gpt-5-nano,openai:gpt-5-mini",
                               "--models", help="comma-separated model names"),
    backend: str = typer.Option("real", "--backend", help="real | fake"),
    limit: int = typer.Option(None, "--limit", help="run at most N pending cells"),
    workers: int = typer.Option(4, "--workers"),
    modes: str = typer.Option(None, "--modes",
                              help="comma-separated modes (clean,latency,hard_error,"
                                   "malformed,partial,dropped)"),
    doses: str = typer.Option(None, "--doses", help="comma-separated doses (0.05,...)"),
    replicates: str = typer.Option(None, "--replicates", help="comma-separated replicate numbers"),
) -> None:
    """Execute pending matrix cells (resume via runs/index.jsonl)."""
    from mcp_storm.runner.matrix import run_matrix
    from mcp_storm.tasks_io import load_tasks

    async def go():
        return await run_matrix(
            models=[m.strip() for m in models.split(",") if m.strip()],
            tasks=load_tasks(),
            runs=runs_dir(),
            backend=backend,
            limit=limit,
            workers=workers,
            modes=[m.strip() for m in modes.split(",")] if modes else None,
            doses=[float(d) for d in doses.split(",")] if doses else None,
            replicates=[int(r) for r in replicates.split(",")] if replicates else None,
        )

    results = asyncio.run(go())
    outcomes: dict[str, int] = {}
    for record in results:
        outcomes[record["outcome"]] = outcomes.get(record["outcome"], 0) + 1
    typer.echo(f"matrix: {len(results)} runs -> {outcomes}")


@app.command()
def report(all_runs: bool = typer.Option(False, "--all", help="regenerate every number from runs/")) -> None:
    """Regenerate report artifacts from committed run records (§2.1)."""
    from mcp_storm.meter.report import join_meter_to_ledger, load_meter, write_report
    from mcp_storm.proxy.ledger import read_run_ledger

    runs = runs_dir()
    count = 0
    for run_path in sorted(runs.glob("*/meter.json")):
        run_dir = run_path.parent
        try:
            meter = load_meter(run_dir)
            joined = join_meter_to_ledger(meter, read_run_ledger(run_dir))
            write_report(run_dir, meter, joined)
            count += 1
        except (OSError, ValueError) as exc:
            typer.echo(f"skip {run_dir.name}: {exc}")
    typer.echo(f"report: regenerated meter reports for {count} run(s)")
    if count == 0:
        typer.echo("report: no runs recorded yet (matrix has not launched)")
        raise typer.Exit(code=1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
