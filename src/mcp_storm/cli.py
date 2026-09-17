"""storm CLI (BUILD-GUIDE §2.6, §13).

`storm selftest` = byte-framing golden + grader golden + meter-join +
spend-breaker unit checks, all offline. Pieces still under construction report
themselves as PENDING and fail the selftest until implemented (the matrix
runner requires a green selftest, §2.2).
"""

from __future__ import annotations

import subprocess
import sys

import typer

app = typer.Typer(no_args_is_help=True, help="mcp-storm control CLI")


def _run(cmd: list[str]) -> tuple[int, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, shell=False)
    return proc.returncode, (proc.stdout + proc.stderr)


@app.command()
def selftest() -> None:
    """Offline golden checks (framing, grader, meter-join, spend breaker)."""
    from mcp_storm.paths import REPO_ROOT

    checks = {
        "byte-framing golden": [sys.executable, "-m", "pytest", "-q",
                                "tests/test_framing.py", "tests/test_faults.py"],
        "grader golden": [sys.executable, "-m", "pytest", "-q", "tests/test_grader.py"],
        "meter join": [sys.executable, "-m", "pytest", "-q", "tests/test_meter.py"],
        "spend breaker": [sys.executable, "-m", "pytest", "-q", "tests/test_spend.py"],
    }
    failures = []
    for name, cmd in checks.items():
        test_path = REPO_ROOT / cmd[-1].replace("tests/", "tests/")
        if not test_path.exists():
            typer.echo(f"PEND  {name}: test module not built yet ({cmd[-1]})")
            failures.append(name)
            continue
        rc, out = _run([*cmd[:-1], str(test_path)])
        if rc == 0:
            typer.echo(f"PASS  {name}")
        else:
            typer.echo(f"FAIL  {name}\n{out[-2000:]}")
            failures.append(name)
    if failures:
        typer.echo(f"selftest: {len(failures)} check(s) pending or failing: {failures}")
        raise typer.Exit(code=1)
    typer.echo("selftest: all offline golden checks green")


@app.command()
def report(all_runs: bool = typer.Option(False, "--all", help="regenerate every number from runs/")) -> None:
    """Regenerate report artifacts from committed run records (§2.1)."""
    typer.echo("storm report: runner/reports land with the matrix phase (§6)")
    raise typer.Exit(code=1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
